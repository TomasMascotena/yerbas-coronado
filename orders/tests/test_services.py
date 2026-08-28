from decimal import Decimal
from datetime import timedelta
import shutil
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from cart.models import Carrito, ItemCarrito
from cart.services import agregar_producto
from cart.tests.helpers import crear_item_directo, crear_producto_con_stock
from catalog.models import Producto
from catalog.tests.helpers import datos_producto
from inventory.models import MovimientoInventario, TipoMovimientoInventario
from orders.exceptions import (
    CarritoModificado,
    CarritoExpirado,
    CarritoInexistente,
    CarritoVacio,
    DatosCompradorInvalidos,
    DireccionEnvioInvalida,
    ItemCarritoCorrupto,
    PricingCorrupto,
    ProductoNoDisponible,
    ProductoSinInventario,
    StockInsuficienteParaPedido,
)
from orders.models import (
    Cliente,
    DetallePedido,
    DireccionEnvio,
    EstadoPedido,
    ModalidadEntrega,
    Pedido,
)
from orders.services import (
    DatosDireccionEnvio,
    crear_pedido_desde_carrito,
)
from orders.tests.helpers import crear_carrito_checkout, datos_comprador


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class CrearPedidoTests(TestCase):
    def _argumentos(self, carrito, **cambios):
        argumentos = {
            "session_key": carrito.session_key,
            "token_idempotencia": carrito.token_checkout,
            "datos_comprador": datos_comprador(),
            "modalidad_entrega": ModalidadEntrega.RETIRO,
        }
        argumentos.update(cambios)
        return argumentos

    def _crear_carrito_dos_productos(self, session_key):
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
        return carrito, (primero, segundo)

    def _snapshot_rollback(self, carrito, productos):
        for producto in productos:
            producto.inventario.refresh_from_db()
        return {
            "stocks": {
                producto.inventario.pk: producto.inventario.cantidad_disponible
                for producto in productos
            },
            "movimientos": set(
                MovimientoInventario.objects.values_list("pk", flat=True)
            ),
            "items": set(carrito.items.values_list("pk", flat=True)),
        }

    def _assert_rollback_completo(
        self,
        carrito,
        productos,
        snapshot,
        *,
        clientes_esperados=0,
    ):
        self.assertEqual(Cliente.objects.count(), clientes_esperados)
        self.assertFalse(Pedido.objects.exists())
        self.assertFalse(DireccionEnvio.objects.exists())
        self.assertFalse(DetallePedido.objects.exists())
        self.assertEqual(
            set(MovimientoInventario.objects.values_list("pk", flat=True)),
            snapshot["movimientos"],
        )
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())
        self.assertEqual(
            set(ItemCarrito.objects.filter(carrito=carrito).values_list("pk", flat=True)),
            snapshot["items"],
        )
        for producto in productos:
            producto.inventario.refresh_from_db()
            self.assertEqual(
                producto.inventario.cantidad_disponible,
                snapshot["stocks"][producto.inventario.pk],
            )

    def test_crea_pedido_completo_descuenta_stock_y_consume_carrito(self):
        carrito, producto = crear_carrito_checkout(cantidad=3, stock=10)
        resultado = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
            observaciones="  Sin bolsa  ",
        )
        pedido = resultado.pedido
        self.assertTrue(resultado.creado)
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(pedido.observaciones, "Sin bolsa")
        self.assertEqual(pedido.cantidad_total, 3)
        self.assertEqual(pedido.importe_total, Decimal("13500.00"))
        detalle = pedido.detalles.get()
        self.assertEqual(detalle.precio_unitario_aplicado, Decimal("4500.00"))
        self.assertEqual(detalle.subtotal, Decimal("13500.00"))
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 7)
        movimiento = MovimientoInventario.objects.get(
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO
        )
        self.assertEqual(movimiento.pedido, pedido)
        self.assertEqual(movimiento.cantidad, 3)
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())

    def test_envio_crea_exactamente_una_direccion_normalizada(self):
        carrito, _ = crear_carrito_checkout()
        resultado = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion_envio=DatosDireccionEnvio(
                calle="  San Martín ",
                numero=" 123 ",
                localidad=" Posadas ",
                provincia=" Misiones ",
            ),
        )
        self.assertEqual(DireccionEnvio.objects.count(), 1)
        self.assertEqual(resultado.pedido.direccion_envio.calle, "San Martín")

    def test_retiro_rechaza_direccion_y_envio_la_exige(self):
        carrito, _ = crear_carrito_checkout()
        direccion = DatosDireccionEnvio(
            calle="A", numero="1", localidad="B", provincia="C"
        )
        with self.assertRaises(DireccionEnvioInvalida):
            crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
                direccion_envio=direccion,
            )
        with self.assertRaises(DireccionEnvioInvalida):
            crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
            )

    def test_normaliza_dni_y_telefono_y_rechaza_unicode_o_caracteres(self):
        carrito, _ = crear_carrito_checkout()
        for comprador in (
            datos_comprador(dni="１２３４５６７８"),
            datos_comprador(dni="123/456"),
            datos_comprador(telefono="+54 abc 123456"),
        ):
            with self.subTest(comprador=comprador):
                with self.assertRaises(DatosCompradorInvalidos):
                    crear_pedido_desde_carrito(
                        session_key=carrito.session_key,
                        token_idempotencia=carrito.token_checkout,
                        datos_comprador=comprador,
                        modalidad_entrega=ModalidadEntrega.RETIRO,
                    )

    def test_stock_insuficiente_no_deja_efectos_parciales(self):
        carrito, producto = crear_carrito_checkout(cantidad=5, stock=5)
        producto.inventario.cantidad_disponible = 4
        producto.inventario.save(update_fields=("cantidad_disponible",))
        with self.assertRaises(StockInsuficienteParaPedido):
            crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )
        self.assertFalse(Pedido.objects.exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())

    def test_producto_inactivo_no_crea_pedido(self):
        carrito, producto = crear_carrito_checkout()
        producto.activo = False
        producto.save(update_fields=("activo",))
        with self.assertRaises(ProductoNoDisponible):
            crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )
        self.assertFalse(Pedido.objects.exists())

    def test_fallo_en_detalle_revierte_todo(self):
        carrito, producto = crear_carrito_checkout(stock=10)
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        with patch("orders.services.DetallePedido.save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                crear_pedido_desde_carrito(
                    session_key=carrito.session_key,
                    token_idempotencia=carrito.token_checkout,
                    datos_comprador=datos_comprador(),
                    modalidad_entrega=ModalidadEntrega.RETIRO,
                )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertFalse(Pedido.objects.exists())
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())

    def test_token_obsoleto_conserva_carrito(self):
        carrito, _ = crear_carrito_checkout()
        token_anterior = carrito.token_checkout
        carrito.token_checkout = __import__("uuid").uuid4()
        carrito.save(update_fields=("token_checkout",))
        with self.assertRaises(CarritoModificado):
            crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=token_anterior,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())

    def test_reutiliza_cliente_actualiza_datos_y_preserva_snapshots(self):
        carrito, producto = crear_carrito_checkout(session_key="sesion-cliente-a")
        primero = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(nombre="Ana", telefono="123456"),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        from cart.services import agregar_producto

        segundo_carrito = agregar_producto(
            session_key="sesion-cliente-b", producto_id=producto.pk, cantidad=1
        ).carrito
        segundo_carrito.refresh_from_db()
        segundo = crear_pedido_desde_carrito(
            session_key=segundo_carrito.session_key,
            token_idempotencia=segundo_carrito.token_checkout,
            datos_comprador=datos_comprador(nombre="Beatriz", telefono="654321"),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.nombre, "Beatriz")
        self.assertEqual(cliente.telefono, "654321")
        self.assertEqual(primero.nombre_cliente, "Ana")
        self.assertEqual(segundo.nombre_cliente, "Beatriz")

    def test_escala_global_se_aplica_a_todas_las_lineas(self):
        carrito, _ = crear_carrito_checkout(
            session_key="sesion-escala", cantidad=1, nombre="Canarias"
        )
        from cart.services import agregar_producto
        from cart.tests.helpers import crear_producto_con_stock

        otro = crear_producto_con_stock(nombre="Baldo", stock=20)
        agregar_producto(
            session_key=carrito.session_key, producto_id=otro.pk, cantidad=2
        )
        carrito.refresh_from_db()
        pedido = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        self.assertEqual(pedido.detalles.count(), 2)
        self.assertEqual(
            {d.precio_unitario_aplicado for d in pedido.detalles.all()},
            {Decimal("4500.00")},
        )

    def test_carrito_expirado_se_elimina_antes_de_informar_error(self):
        carrito, _ = crear_carrito_checkout(session_key="sesion-expirada")
        ahora = timezone.now()
        Carrito.objects.filter(pk=carrito.pk).update(
            ultima_actividad=ahora - timedelta(hours=6)
        )
        with patch("orders.services._ahora", return_value=ahora):
            with self.assertRaises(CarritoExpirado):
                crear_pedido_desde_carrito(
                    session_key=carrito.session_key,
                    token_idempotencia=carrito.token_checkout,
                    datos_comprador=datos_comprador(),
                    modalidad_entrega=ModalidadEntrega.RETIRO,
                )
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())
        self.assertFalse(Pedido.objects.exists())

    def test_fallo_en_movimiento_revierte_stock_pedido_cliente_y_carrito(self):
        carrito, producto = crear_carrito_checkout(
            session_key="sesion-fallo-movimiento", stock=10
        )
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        with patch(
            "orders.services._aplicar_venta_pedido_sobre_inventario_bloqueado",
            side_effect=RuntimeError,
        ):
            with self.assertRaises(RuntimeError):
                crear_pedido_desde_carrito(
                    session_key=carrito.session_key,
                    token_idempotencia=carrito.token_checkout,
                    datos_comprador=datos_comprador(),
                    modalidad_entrega=ModalidadEntrega.RETIRO,
                )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertFalse(Pedido.objects.exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())

    def test_carrito_inexistente_no_deja_efectos(self):
        with self.assertRaises(CarritoInexistente):
            crear_pedido_desde_carrito(
                session_key="sesion-inexistente",
                token_idempotencia=__import__("uuid").uuid4(),
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(Pedido.objects.exists())

    def test_carrito_vacio_no_deja_efectos_y_se_conserva(self):
        carrito = Carrito.objects.create(session_key="sesion-vacia")
        with self.assertRaises(CarritoVacio):
            crear_pedido_desde_carrito(**self._argumentos(carrito))
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(Pedido.objects.exists())

    def test_producto_sin_inventario_conserva_carrito_e_item(self):
        carrito = Carrito.objects.create(session_key="sesion-sin-inventario")
        producto = Producto(**datos_producto(nombre="Sin Inventario Checkout"))
        producto.full_clean()
        producto.save()
        item = crear_item_directo(carrito=carrito, producto=producto, cantidad=1)
        carrito.refresh_from_db()
        with self.assertRaises(ProductoSinInventario):
            crear_pedido_desde_carrito(**self._argumentos(carrito))
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())
        self.assertTrue(ItemCarrito.objects.filter(pk=item.pk).exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(Pedido.objects.exists())

    def test_item_estructuralmente_invalido_no_deja_efectos(self):
        carrito, producto = crear_carrito_checkout(session_key="item-corrupto")
        snapshot = self._snapshot_rollback(carrito, (producto,))
        with patch(
            "orders.services._validar_items",
            side_effect=ItemCarritoCorrupto("Item corrupto"),
        ):
            with self.assertRaises(ItemCarritoCorrupto):
                crear_pedido_desde_carrito(**self._argumentos(carrito))
        self._assert_rollback_completo(carrito, (producto,), snapshot)

    def test_pricing_corrupto_revierte_cliente_nuevo(self):
        carrito, producto = crear_carrito_checkout(session_key="pricing-corrupto")
        snapshot = self._snapshot_rollback(carrito, (producto,))
        with patch(
            "orders.services._calcular_resumen_pedido",
            side_effect=PricingCorrupto("Pricing corrupto"),
        ):
            with self.assertRaises(PricingCorrupto):
                crear_pedido_desde_carrito(**self._argumentos(carrito))
        self._assert_rollback_completo(carrito, (producto,), snapshot)

    def test_fallo_al_crear_pedido_revierte_actualizacion_de_cliente(self):
        cliente = Cliente.objects.create(
            dni="12345678",
            nombre="Nombre anterior",
            apellido="Apellido anterior",
            telefono="111111",
        )
        carrito, producto = crear_carrito_checkout(session_key="fallo-pedido")
        snapshot = self._snapshot_rollback(carrito, (producto,))
        with patch("orders.services.Pedido.save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                crear_pedido_desde_carrito(
                    **self._argumentos(
                        carrito,
                        datos_comprador=datos_comprador(
                            nombre="Nombre nuevo",
                            telefono="222222",
                        ),
                    )
                )
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Nombre anterior")
        self.assertEqual(cliente.telefono, "111111")
        self._assert_rollback_completo(
            carrito,
            (producto,),
            snapshot,
            clientes_esperados=1,
        )

    def test_fallo_al_crear_direccion_revierte_todo(self):
        carrito, producto = crear_carrito_checkout(session_key="fallo-direccion")
        snapshot = self._snapshot_rollback(carrito, (producto,))
        direccion = DatosDireccionEnvio(
            calle="San Martín",
            numero="123",
            localidad="Posadas",
            provincia="Misiones",
        )
        with patch("orders.services.DireccionEnvio.save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                crear_pedido_desde_carrito(
                    **self._argumentos(
                        carrito,
                        modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
                        direccion_envio=direccion,
                    )
                )
        self._assert_rollback_completo(carrito, (producto,), snapshot)

    def test_fallo_en_segundo_detalle_revierte_el_primero(self):
        carrito, productos = self._crear_carrito_dos_productos("fallo-detalle-dos")
        snapshot = self._snapshot_rollback(carrito, productos)
        guardar_original = DetallePedido.save
        llamadas = {"cantidad": 0}

        def guardar_con_fallo(instancia, *args, **kwargs):
            llamadas["cantidad"] += 1
            if llamadas["cantidad"] == 2:
                raise RuntimeError("Fallo en segundo Detalle")
            return guardar_original(instancia, *args, **kwargs)

        with patch("orders.services.DetallePedido.save", new=guardar_con_fallo):
            with self.assertRaises(RuntimeError):
                crear_pedido_desde_carrito(**self._argumentos(carrito))
        self._assert_rollback_completo(carrito, productos, snapshot)

    def test_fallo_en_segundo_inventario_revierte_primer_movimiento_y_stock(self):
        carrito, productos = self._crear_carrito_dos_productos("fallo-inventario-dos")
        snapshot = self._snapshot_rollback(carrito, productos)
        from orders import services as orders_services

        aplicar_original = (
            orders_services._aplicar_venta_pedido_sobre_inventario_bloqueado
        )
        llamadas = {"cantidad": 0}

        def aplicar_con_fallo(**kwargs):
            llamadas["cantidad"] += 1
            if llamadas["cantidad"] == 2:
                raise RuntimeError("Fallo en segundo Inventario")
            return aplicar_original(**kwargs)

        with patch(
            "orders.services._aplicar_venta_pedido_sobre_inventario_bloqueado",
            side_effect=aplicar_con_fallo,
        ):
            with self.assertRaises(RuntimeError):
                crear_pedido_desde_carrito(**self._argumentos(carrito))
        self._assert_rollback_completo(carrito, productos, snapshot)
