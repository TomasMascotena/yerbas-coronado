from decimal import Decimal
import shutil
import tempfile
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import TextField
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings

from inventory.exceptions import MovimientoInventarioInmutable
from inventory.models import MovimientoInventario, TipoMovimientoInventario
from orders.exceptions import HistorialPedidoInmutable
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
from orders.tests.helpers import (
    crear_carrito_checkout,
    crear_pedido_de_prueba,
    datos_comprador,
)


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ModelosPedidoTests(TestCase):
    def test_choices_coinciden_con_dominio(self):
        self.assertEqual(
            set(EstadoPedido.values), {"PENDIENTE", "ENTREGADO", "CANCELADO"}
        )
        self.assertEqual(
            set(ModalidadEntrega.values), {"RETIRO", "ENVIO_DOMICILIO"}
        )

    def test_cliente_rechaza_dni_no_canonico_en_aplicacion_y_postgresql(self):
        cliente = Cliente(
            dni="12.345.678", nombre="Ana", apellido="C", telefono="123456"
        )
        with self.assertRaises(ValidationError):
            cliente.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            cliente.save()

    def test_numero_token_y_snapshots_persistidos(self):
        pedido, _ = crear_pedido_de_prueba()
        self.assertRegex(pedido.numero_pedido, r"^YC-[0-9A-HJKMNP-TV-Z]{12}$")
        self.assertIsInstance(pedido.token_idempotencia, uuid.UUID)
        self.assertEqual(pedido.dni_cliente, "12345678")
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

    def test_subtotal_incoherente_es_rechazado_por_postgresql(self):
        pedido, producto = crear_pedido_de_prueba()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DetallePedido.objects.create(
                pedido=pedido,
                producto=producto,
                nombre_producto="Otro",
                peso_producto="1 kg",
                cantidad=2,
                precio_unitario_aplicado=Decimal("10.00"),
                subtotal=Decimal("21.00"),
            )

    def test_pedido_detalle_y_direccion_son_inmutables_por_instancia(self):
        carrito, _ = crear_carrito_checkout(session_key="inmutabilidad-direccion")
        pedido = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion_envio=DatosDireccionEnvio(
                calle="San Martín",
                numero="123",
                localidad="Posadas",
                provincia="Misiones",
            ),
        ).pedido
        pedido.observaciones = "cambio"
        with self.assertRaises(HistorialPedidoInmutable):
            pedido.save()
        detalle = pedido.detalles.get()
        detalle.cantidad = 99
        with self.assertRaises(HistorialPedidoInmutable):
            detalle.save()
        with self.assertRaises(HistorialPedidoInmutable):
            detalle.delete()
        direccion = pedido.direccion_envio
        direccion.calle = "Otra calle"
        with self.assertRaises(HistorialPedidoInmutable):
            direccion.save()
        with self.assertRaises(HistorialPedidoInmutable):
            direccion.delete()
        self.assertEqual(DireccionEnvio.objects.count(), 1)

    def test_snapshots_texto_extenso_actual_y_precio_exclusivo_del_item(self):
        carrito, producto = crear_carrito_checkout(
            session_key="snapshots-extensos",
            cantidad=2,
        )
        item = carrito.items.get()
        precio_snapshot = item.precio_unitario_snapshot
        nombre_actual = "Nombre actual " + "N" * 400
        peso_actual = "Presentación actual " + "P" * 400
        producto.__class__.objects.filter(pk=producto.pk).update(
            nombre=nombre_actual,
            peso=peso_actual,
            precio_unitario=Decimal("999999.00"),
            precio_desde_3=Decimal("888888.00"),
            precio_desde_20=Decimal("777777.00"),
        )
        pedido = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        detalle = pedido.detalles.get()
        self.assertEqual(detalle.nombre_producto, nombre_actual)
        self.assertEqual(detalle.peso_producto, peso_actual)
        self.assertGreater(len(detalle.nombre_producto), 255)
        self.assertGreater(len(detalle.peso_producto), 255)
        self.assertIsInstance(
            DetallePedido._meta.get_field("nombre_producto"),
            TextField,
        )
        self.assertIsInstance(
            DetallePedido._meta.get_field("peso_producto"),
            TextField,
        )
        self.assertEqual(detalle.precio_unitario_aplicado, precio_snapshot)
        self.assertNotEqual(
            detalle.precio_unitario_aplicado,
            Decimal("999999.00"),
        )

    def test_movimiento_historico_no_admite_modificacion_o_borrado(self):
        pedido, _ = crear_pedido_de_prueba()
        movimiento = MovimientoInventario.objects.get(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        )
        movimiento.observacion = "alterado"
        with self.assertRaises(MovimientoInventarioInmutable):
            movimiento.save()
        with self.assertRaises(MovimientoInventarioInmutable):
            movimiento.delete()

    def test_postgresql_exige_coherencia_y_unicidad_de_movimientos_de_pedido(self):
        pedido, producto = crear_pedido_de_prueba()
        inventario = producto.inventario
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimientoInventario.objects.create(
                inventario=inventario,
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
                cantidad=1,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimientoInventario.objects.create(
                inventario=inventario,
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.AJUSTE_POSITIVO,
                cantidad=1,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimientoInventario.objects.create(
                inventario=inventario,
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
                cantidad=1,
            )

    def test_queryset_delete_de_pedido_es_frenado_por_relaciones_protectivas(self):
        pedido, _ = crear_pedido_de_prueba()
        with self.assertRaises(ProtectedError):
            Pedido.objects.filter(pk=pedido.pk).delete()
