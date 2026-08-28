import shutil
import tempfile

from django.test import TestCase, override_settings

from cart.services import agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from inventory.models import MovimientoInventario, TipoMovimientoInventario
from orders.exceptions import (
    CapacidadInventarioExcedida,
    HistorialMovimientosCorrupto,
    TransicionPedidoInvalida,
)
from orders.models import EstadoPedido, ModalidadEntrega
from orders.services import (
    cancelar_pedido,
    crear_pedido_desde_carrito,
    marcar_pedido_entregado,
)
from orders.tests.helpers import (
    crear_carrito_checkout,
    crear_pedido_de_prueba,
    datos_comprador,
)


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class TransicionesPedidoTests(TestCase):
    def _crear_pedido_dos_productos(self, session_key="pedido-dos-productos"):
        carrito, primero = crear_carrito_checkout(
            session_key=session_key,
            nombre=f"Primero {session_key}",
            cantidad=2,
            stock=10,
        )
        segundo = crear_producto_con_stock(
            nombre=f"Segundo {session_key}",
            stock=12,
        )
        agregar_producto(
            session_key=session_key,
            producto_id=segundo.pk,
            cantidad=3,
        )
        carrito.refresh_from_db()
        pedido = crear_pedido_desde_carrito(
            session_key=session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        return pedido, (primero, segundo)

    def _stocks(self, productos):
        resultado = {}
        for producto in productos:
            producto.inventario.refresh_from_db()
            resultado[producto.inventario.pk] = (
                producto.inventario.cantidad_disponible
            )
        return resultado

    def _assert_cancelacion_corrupta(self, pedido, productos, stocks):
        with self.assertRaises(HistorialMovimientosCorrupto):
            cancelar_pedido(pedido_id=pedido.pk)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertFalse(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).exists()
        )
        self.assertEqual(self._stocks(productos), stocks)

    def test_entregar_solo_cambia_estado(self):
        pedido, producto = crear_pedido_de_prueba(cantidad=2, stock=10)
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        resultado = marcar_pedido_entregado(pedido_id=pedido.pk)
        producto.inventario.refresh_from_db()
        self.assertEqual(resultado.estado, EstadoPedido.ENTREGADO)
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)
        with self.assertRaises(TransicionPedidoInvalida):
            marcar_pedido_entregado(pedido_id=pedido.pk)

    def test_cancelar_restituye_y_crea_movimiento_compensatorio(self):
        pedido, producto = crear_pedido_de_prueba(cantidad=3, stock=10)
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 7)
        resultado = cancelar_pedido(pedido_id=pedido.pk)
        producto.inventario.refresh_from_db()
        self.assertEqual(resultado.estado, EstadoPedido.CANCELADO)
        self.assertEqual(producto.inventario.cantidad_disponible, 10)
        cancelacion = MovimientoInventario.objects.get(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
        )
        self.assertEqual(cancelacion.cantidad, 3)
        with self.assertRaises(TransicionPedidoInvalida):
            cancelar_pedido(pedido_id=pedido.pk)

    def test_cancelacion_con_overflow_revierte_sin_movimiento(self):
        pedido, producto = crear_pedido_de_prueba(cantidad=1, stock=2)
        producto.inventario.cantidad_disponible = 9_223_372_036_854_775_807
        producto.inventario.save(update_fields=("cantidad_disponible",))
        movimientos = MovimientoInventario.objects.count()
        with self.assertRaises(CapacidadInventarioExcedida):
            cancelar_pedido(pedido_id=pedido.pk)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_cancelacion_multinventario_restituye_todos(self):
        pedido, productos = self._crear_pedido_dos_productos("cancelacion-multiple")
        cancelar_pedido(pedido_id=pedido.pk)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.CANCELADO)
        self.assertEqual(
            sorted(self._stocks(productos).values()),
            [10, 12],
        )
        self.assertEqual(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).count(),
            2,
        )

    def test_fallo_en_segunda_restitucion_revierte_la_primera(self):
        pedido, productos = self._crear_pedido_dos_productos("fallo-restitucion")
        stocks = self._stocks(productos)
        movimientos = set(
            MovimientoInventario.objects.values_list("pk", flat=True)
        )
        from orders import services as orders_services
        from unittest.mock import patch

        original = (
            orders_services._aplicar_cancelacion_pedido_sobre_inventario_bloqueado
        )
        llamadas = {"cantidad": 0}

        def restituir_con_fallo(**kwargs):
            llamadas["cantidad"] += 1
            if llamadas["cantidad"] == 2:
                raise RuntimeError("Fallo en segundo Inventario")
            return original(**kwargs)

        with patch(
            "orders.services._aplicar_cancelacion_pedido_sobre_inventario_bloqueado",
            side_effect=restituir_con_fallo,
        ):
            with self.assertRaises(RuntimeError):
                cancelar_pedido(pedido_id=pedido.pk)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(self._stocks(productos), stocks)
        self.assertEqual(
            set(MovimientoInventario.objects.values_list("pk", flat=True)),
            movimientos,
        )

    def test_cancelacion_revierte_si_estado_no_actualiza_exactamente_una_fila(self):
        pedido, producto = crear_pedido_de_prueba(
            session_key="estado-no-actualizado",
            cantidad=2,
            stock=10,
        )
        stocks = self._stocks((producto,))
        movimientos = set(
            MovimientoInventario.objects.values_list("pk", flat=True)
        )
        from unittest.mock import patch

        with patch(
            "django.db.models.query.QuerySet.update",
            return_value=0,
        ):
            with self.assertRaises(TransicionPedidoInvalida):
                cancelar_pedido(pedido_id=pedido.pk)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(self._stocks((producto,)), stocks)
        self.assertEqual(
            set(MovimientoInventario.objects.values_list("pk", flat=True)),
            movimientos,
        )

    def test_entregado_no_puede_cancelarse(self):
        pedido, _ = crear_pedido_de_prueba(session_key="entregado-terminal")
        marcar_pedido_entregado(pedido_id=pedido.pk)
        with self.assertRaises(TransicionPedidoInvalida):
            cancelar_pedido(pedido_id=pedido.pk)

    def test_cancelado_no_puede_entregarse(self):
        pedido, _ = crear_pedido_de_prueba(session_key="cancelado-terminal")
        cancelar_pedido(pedido_id=pedido.pk)
        with self.assertRaises(TransicionPedidoInvalida):
            marcar_pedido_entregado(pedido_id=pedido.pk)

    def test_movimiento_faltante_detecta_historial_corrupto(self):
        pedido, producto = crear_pedido_de_prueba(session_key="movimiento-faltante")
        MovimientoInventario.objects.filter(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        ).delete()
        self._assert_cancelacion_corrupta(
            pedido,
            (producto,),
            self._stocks((producto,)),
        )

    def test_cantidad_de_movimiento_incoherente_se_detecta(self):
        pedido, producto = crear_pedido_de_prueba(
            session_key="cantidad-incoherente", cantidad=2
        )
        MovimientoInventario.objects.filter(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        ).update(cantidad=3)
        self._assert_cancelacion_corrupta(
            pedido,
            (producto,),
            self._stocks((producto,)),
        )

    def test_detalle_sin_movimiento_se_detecta(self):
        pedido, productos = self._crear_pedido_dos_productos("detalle-sin-movimiento")
        movimiento = MovimientoInventario.objects.filter(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        ).order_by("pk").first()
        MovimientoInventario.objects.filter(pk=movimiento.pk).delete()
        self._assert_cancelacion_corrupta(
            pedido,
            productos,
            self._stocks(productos),
        )

    def test_movimiento_sin_detalle_se_detecta(self):
        pedido, producto = crear_pedido_de_prueba(session_key="movimiento-sin-detalle")
        extra = crear_producto_con_stock(nombre="Inventario inesperado", stock=5)
        MovimientoInventario.objects.create(
            inventario=extra.inventario,
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
            cantidad=1,
        )
        productos = (producto, extra)
        self._assert_cancelacion_corrupta(
            pedido,
            productos,
            self._stocks(productos),
        )

    def test_inventario_inesperado_se_detecta(self):
        pedido, producto = crear_pedido_de_prueba(session_key="inventario-inesperado")
        extra = crear_producto_con_stock(nombre="Destino inesperado", stock=5)
        MovimientoInventario.objects.filter(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        ).update(inventario_id=extra.inventario.pk)
        productos = (producto, extra)
        self._assert_cancelacion_corrupta(
            pedido,
            productos,
            self._stocks(productos),
        )
