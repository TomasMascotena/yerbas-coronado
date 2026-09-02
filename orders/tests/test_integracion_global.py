from decimal import Decimal
import shutil
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from cart.models import Carrito, ItemCarrito
from cart.tests.helpers import crear_producto_con_stock
from inventory.models import MovimientoInventario, TipoMovimientoInventario
from orders.exceptions import TransicionPedidoInvalida
from orders.models import (
    Cliente,
    DetallePedido,
    EstadoPedido,
    ModalidadEntrega,
    Pedido,
)
from orders.services import cancelar_pedido, marcar_pedido_entregado


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(
    MEDIA_ROOT=MEDIA_ROOT_PRUEBAS,
    WHATSAPP_BUSINESS_NUMBER="5491112345678",
)
class IntegracionGlobalTests(TestCase):
    def datos_checkout(self, carrito):
        return {
            "token_checkout": str(carrito.token_checkout),
            "nombre": "Ana",
            "apellido": "Coronado",
            "dni": "87.654.321",
            "telefono": "+54 (11) 4567-8901",
            "modalidad_entrega": ModalidadEntrega.RETIRO,
            "observaciones": "Sin bolsa",
        }

    def agregar_desde_catalogo(self, producto, *, cantidad):
        url = reverse("cart:agregar_producto", args=(producto.pk,))
        for _ in range(cantidad):
            respuesta = self.client.post(url)
            self.assertRedirects(
                respuesta,
                reverse("cart:detalle"),
                fetch_redirect_response=False,
            )
        item = ItemCarrito.objects.select_related("carrito").get(
            producto=producto
        )
        item.carrito.refresh_from_db()
        return item.carrito, item

    def confirmar_checkout(self, carrito):
        respuesta = self.client.post(
            reverse("orders:checkout"),
            self.datos_checkout(carrito),
        )
        pedido = Pedido.objects.get()
        self.assertRedirects(
            respuesta,
            reverse("orders:confirmacion", args=(pedido.numero_pedido,)),
            fetch_redirect_response=False,
        )
        return pedido, respuesta

    def test_flujo_publico_completo_conserva_precios_y_genera_historial(self):
        producto = crear_producto_con_stock(
            nombre="Canarias Integración",
            stock=10,
        )

        catalogo = self.client.get(reverse("catalog:producto_list"))
        self.assertEqual(catalogo.status_code, 200)
        self.assertContains(catalogo, "Canarias Integración")

        carrito, item = self.agregar_desde_catalogo(producto, cantidad=3)
        self.assertEqual(item.cantidad, 3)
        self.assertEqual(item.precio_unitario_snapshot, Decimal("5000.00"))
        self.assertEqual(item.precio_desde_3_snapshot, Decimal("4500.00"))
        self.assertEqual(item.precio_desde_20_snapshot, Decimal("4000.00"))

        producto.__class__.objects.filter(pk=producto.pk).update(
            precio_unitario=Decimal("9000.00"),
            precio_desde_3=Decimal("8000.00"),
            precio_desde_20=Decimal("7000.00"),
        )
        resumen = self.client.get(reverse("cart:detalle"))
        checkout = self.client.get(reverse("orders:checkout"))
        self.assertContains(resumen, "$ 13.500,00")
        self.assertContains(checkout, "$ 13.500,00")
        self.assertNotContains(checkout, "$ 21.000,00")

        pedido, respuesta_checkout = self.confirmar_checkout(carrito)
        detalle = DetallePedido.objects.get(pedido=pedido)
        producto.inventario.refresh_from_db()

        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(pedido.cantidad_total, 3)
        self.assertEqual(pedido.importe_total, Decimal("13500.00"))
        self.assertEqual(detalle.nombre_producto, "Canarias Integración")
        self.assertEqual(detalle.cantidad, 3)
        self.assertEqual(detalle.precio_unitario_aplicado, Decimal("4500.00"))
        self.assertEqual(detalle.subtotal, Decimal("13500.00"))
        self.assertEqual(producto.inventario.cantidad_disponible, 7)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=item.pk).exists())
        self.assertNotIn(pedido.dni_cliente, respuesta_checkout["Location"])
        self.assertNotIn(pedido.telefono_cliente, respuesta_checkout["Location"])
        self.assertNotIn(
            str(carrito.token_checkout),
            respuesta_checkout["Location"],
        )

        movimiento = MovimientoInventario.objects.get(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        )
        self.assertEqual(movimiento.inventario_id, producto.inventario.pk)
        self.assertEqual(movimiento.cantidad, 3)

        confirmacion = self.client.get(
            reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        )
        self.assertEqual(confirmacion.status_code, 200)
        self.assertContains(confirmacion, "Canarias Integración")
        self.assertContains(confirmacion, "$ 13.500,00")
        self.assertNotContains(confirmacion, pedido.dni_cliente)
        self.assertNotContains(confirmacion, str(pedido.token_idempotencia))
        self.assertNotContains(confirmacion, pedido.huella_sesion_origen)

        mensaje = parse_qs(
            urlparse(confirmacion.context["enlace_whatsapp"]).query
        )["text"][0]
        self.assertIn("Canarias Integración", mensaje)
        self.assertIn("Cantidad: 3", mensaje)
        self.assertIn("ARS 4.500,00", mensaje)
        self.assertIn("ARS 13.500,00", mensaje)
        self.assertNotIn(pedido.dni_cliente, mensaje)
        self.assertNotIn(str(pedido.token_idempotencia), mensaje)
        self.assertNotIn(pedido.huella_sesion_origen, mensaje)

    def test_cancelacion_restituye_stock_y_preserva_confirmacion_y_admin(self):
        producto = crear_producto_con_stock(nombre="Producto Histórico", stock=8)
        carrito, _ = self.agregar_desde_catalogo(producto, cantidad=2)
        pedido, _ = self.confirmar_checkout(carrito)
        cliente = pedido.cliente

        producto.__class__.objects.filter(pk=producto.pk).update(
            nombre="Producto Actual",
            peso="500 g",
            precio_unitario=Decimal("9999.00"),
        )
        Cliente.objects.filter(pk=cliente.pk).update(
            nombre="Nombre Actual",
            telefono="999999",
        )

        cancelado = cancelar_pedido(pedido_id=pedido.pk)
        producto.inventario.refresh_from_db()
        self.assertEqual(cancelado.estado, EstadoPedido.CANCELADO)
        self.assertEqual(producto.inventario.cantidad_disponible, 8)
        self.assertEqual(
            list(
                MovimientoInventario.objects.filter(pedido=pedido)
                .order_by("tipo_movimiento")
                .values_list("tipo_movimiento", "cantidad")
            ),
            [
                (TipoMovimientoInventario.CANCELACION_PEDIDO, 2),
                (TipoMovimientoInventario.VENTA_PEDIDO, 2),
            ],
        )

        url_confirmacion = reverse(
            "orders:confirmacion", args=(pedido.numero_pedido,)
        )
        confirmacion = self.client.get(url_confirmacion)
        self.assertEqual(confirmacion.status_code, 200)
        self.assertContains(confirmacion, "Producto Histórico")
        self.assertContains(confirmacion, "Ana Coronado")
        self.assertNotContains(confirmacion, "Producto Actual")
        self.assertNotContains(confirmacion, "Nombre Actual")
        mensaje = parse_qs(
            urlparse(confirmacion.context["enlace_whatsapp"]).query
        )["text"][0]
        self.assertIn("Producto Histórico", mensaje)
        self.assertNotIn("Producto Actual", mensaje)

        administrador = get_user_model().objects.create_superuser(
            username="admin-integracion",
            email="admin@example.test",
            password="clave-prueba",
        )
        cliente_admin = Client()
        cliente_admin.force_login(administrador)
        pedido_admin = cliente_admin.get(
            reverse("admin:orders_pedido_change", args=(pedido.pk,))
        )
        historial_cliente = cliente_admin.get(
            reverse("admin:orders_cliente_change", args=(cliente.pk,))
        )
        self.assertEqual(pedido_admin.status_code, 200)
        self.assertEqual(historial_cliente.status_code, 200)
        self.assertContains(pedido_admin, "Producto Histórico")
        self.assertContains(pedido_admin, "Cancelación de pedido")
        self.assertContains(historial_cliente, pedido.numero_pedido, count=1)
        self.assertContains(
            historial_cliente,
            reverse("admin:orders_pedido_change", args=(pedido.pk,)),
        )

        cantidad_movimientos = MovimientoInventario.objects.filter(
            pedido=pedido
        ).count()
        with self.assertRaises(TransicionPedidoInvalida):
            cancelar_pedido(pedido_id=pedido.pk)
        with self.assertRaises(TransicionPedidoInvalida):
            marcar_pedido_entregado(pedido_id=pedido.pk)
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.CANCELADO)
        self.assertEqual(producto.inventario.cantidad_disponible, 8)
        self.assertEqual(
            MovimientoInventario.objects.filter(pedido=pedido).count(),
            cantidad_movimientos,
        )

    @override_settings(DEBUG=False)
    def test_fallo_posterior_de_whatsapp_no_revierte_checkout_confirmado(self):
        producto = crear_producto_con_stock(nombre="Producto Durable", stock=5)
        carrito, _ = self.agregar_desde_catalogo(producto, cantidad=2)
        pedido, _ = self.confirmar_checkout(carrito)
        producto.inventario.refresh_from_db()
        stock_confirmado = producto.inventario.cantidad_disponible
        movimientos_confirmados = MovimientoInventario.objects.filter(
            pedido=pedido
        ).count()

        cliente = Client(raise_request_exception=False)
        cliente.cookies = self.client.cookies
        with patch(
            "orders.views.construir_mensaje_whatsapp",
            side_effect=RuntimeError("fallo técnico simulado"),
        ):
            respuesta = cliente.get(
                reverse("orders:confirmacion", args=(pedido.numero_pedido,))
            )

        self.assertEqual(respuesta.status_code, 500)
        self.assertNotContains(
            respuesta,
            "fallo técnico simulado",
            status_code=500,
        )
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(
            producto.inventario.cantidad_disponible,
            stock_confirmado,
        )
        self.assertEqual(stock_confirmado, 3)
        self.assertEqual(
            MovimientoInventario.objects.filter(pedido=pedido).count(),
            movimientos_confirmados,
        )
        self.assertEqual(movimientos_confirmados, 1)
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())
