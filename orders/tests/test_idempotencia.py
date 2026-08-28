import shutil
import tempfile
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, override_settings

from cart.services import agregar_producto
from inventory.models import MovimientoInventario
from orders.exceptions import (
    GeneracionNumeroPedidoAgotada,
    TokenIdempotenciaInvalido,
)
from orders.models import Cliente, DetallePedido, ModalidadEntrega, Pedido
from orders.services import crear_pedido_desde_carrito
from orders.tests.helpers import crear_carrito_checkout, datos_comprador
import orders.services as orders_services


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class IdempotenciaPedidoTests(TestCase):
    def test_replay_secuencial_devuelve_mismo_pedido_sin_efectos(self):
        carrito, _ = crear_carrito_checkout()
        argumentos = {
            "session_key": carrito.session_key,
            "token_idempotencia": carrito.token_checkout,
            "datos_comprador": datos_comprador(),
            "modalidad_entrega": ModalidadEntrega.RETIRO,
        }
        primero = crear_pedido_desde_carrito(**argumentos)
        segundo = crear_pedido_desde_carrito(**argumentos)
        self.assertTrue(primero.creado)
        self.assertFalse(segundo.creado)
        self.assertEqual(primero.pedido.pk, segundo.pedido.pk)
        self.assertEqual(Pedido.objects.count(), 1)

    def test_token_de_otra_sesion_no_revela_pedido(self):
        carrito, _ = crear_carrito_checkout(session_key="sesion-original")
        token = carrito.token_checkout
        crear_pedido_desde_carrito(
            session_key="sesion-original",
            token_idempotencia=token,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        )
        with self.assertRaises(TokenIdempotenciaInvalido):
            crear_pedido_desde_carrito(
                session_key="sesion-ajena",
                token_idempotencia=token,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

    def test_replay_antiguo_no_consume_nuevo_carrito_de_misma_sesion(self):
        carrito, producto = crear_carrito_checkout(session_key="sesion-recompra")
        token = carrito.token_checkout
        primero = crear_pedido_desde_carrito(
            session_key="sesion-recompra",
            token_idempotencia=token,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        )
        nuevo_item = agregar_producto(
            session_key="sesion-recompra", producto_id=producto.pk, cantidad=1
        )
        nuevo_item.carrito.refresh_from_db()
        token_nuevo = nuevo_item.carrito.token_checkout
        cantidad_nueva = nuevo_item.cantidad
        producto.inventario.refresh_from_db()
        stock_antes_replay = producto.inventario.cantidad_disponible
        movimientos_antes_replay = MovimientoInventario.objects.count()
        replay = crear_pedido_desde_carrito(
            session_key="sesion-recompra",
            token_idempotencia=token,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        )
        self.assertEqual(replay.pedido.pk, primero.pedido.pk)
        self.assertFalse(replay.creado)
        nuevo_item.refresh_from_db()
        nuevo_item.carrito.refresh_from_db()
        self.assertTrue(nuevo_item.carrito.items.exists())
        self.assertEqual(nuevo_item.cantidad, cantidad_nueva)
        self.assertEqual(nuevo_item.carrito.token_checkout, token_nuevo)
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock_antes_replay)
        self.assertEqual(
            MovimientoInventario.objects.count(), movimientos_antes_replay
        )

    def test_colision_de_numero_publico_reintenta_constraint_especifica(self):
        carrito_a, producto = crear_carrito_checkout(session_key="numero-a")
        pedido_a = crear_pedido_desde_carrito(
            session_key="numero-a",
            token_idempotencia=carrito_a.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        carrito_b = agregar_producto(
            session_key="numero-b", producto_id=producto.pk, cantidad=1
        ).carrito
        carrito_b.refresh_from_db()
        with patch(
            "orders.services._generar_numero_pedido",
            side_effect=(pedido_a.numero_pedido, "YC-000000000001"),
        ):
            pedido_b = crear_pedido_desde_carrito(
                session_key="numero-b",
                token_idempotencia=carrito_b.token_checkout,
                datos_comprador=datos_comprador(dni="20.000.001"),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            ).pedido
        self.assertEqual(pedido_b.numero_pedido, "YC-000000000001")

    def test_colision_nombrada_de_token_revierte_y_recupera_pedido(self):
        carrito_original, producto = crear_carrito_checkout(
            session_key="sesion-colision-token"
        )
        pedido_original = crear_pedido_desde_carrito(
            session_key=carrito_original.session_key,
            token_idempotencia=carrito_original.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        cliente_original = pedido_original.cliente
        cliente_original.refresh_from_db()
        datos_originales = (
            cliente_original.nombre,
            cliente_original.apellido,
            cliente_original.telefono,
        )
        item_nuevo = agregar_producto(
            session_key="sesion-colision-token",
            producto_id=producto.pk,
            cantidad=1,
        )
        carrito_nuevo = item_nuevo.carrito
        carrito_nuevo.token_checkout = pedido_original.token_idempotencia
        carrito_nuevo.save(update_fields=("token_checkout",))
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        consultas = {"cantidad": 0}
        buscar_original = orders_services._buscar_pedido_por_token

        def ocultar_hasta_colision(token):
            consultas["cantidad"] += 1
            if consultas["cantidad"] <= 2:
                return None
            return buscar_original(token)

        with patch(
            "orders.services._buscar_pedido_por_token",
            side_effect=ocultar_hasta_colision,
        ):
            resultado = crear_pedido_desde_carrito(
                session_key="sesion-colision-token",
                token_idempotencia=pedido_original.token_idempotencia,
                datos_comprador=datos_comprador(
                    dni="20.000.001",
                    nombre="Cliente transitorio",
                ),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        self.assertFalse(resultado.creado)
        self.assertEqual(resultado.pedido.pk, pedido_original.pk)
        self.assertGreaterEqual(consultas["cantidad"], 3)
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(DetallePedido.objects.count(), 1)
        self.assertEqual(Cliente.objects.count(), 1)
        cliente_original.refresh_from_db()
        self.assertEqual(
            (
                cliente_original.nombre,
                cliente_original.apellido,
                cliente_original.telefono,
            ),
            datos_originales,
        )
        self.assertTrue(Pedido.objects.filter(pk=pedido_original.pk).exists())
        self.assertTrue(item_nuevo.__class__.objects.filter(pk=item_nuevo.pk).exists())
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_integridad_ajena_al_token_se_propaga_y_revierte(self):
        carrito, producto = crear_carrito_checkout(
            session_key="integridad-no-token"
        )
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        with patch(
            "orders.services._generar_numero_pedido",
            return_value="NUMERO_INVALIDO",
        ):
            with self.assertRaises(IntegrityError):
                crear_pedido_desde_carrito(
                    session_key=carrito.session_key,
                    token_idempotencia=carrito.token_checkout,
                    datos_comprador=datos_comprador(),
                    modalidad_entrega=ModalidadEntrega.RETIRO,
                )
        self.assertFalse(Pedido.objects.exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertTrue(carrito.__class__.objects.filter(pk=carrito.pk).exists())
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_agota_cinco_intentos_de_numero_y_revierte(self):
        carrito_existente, producto = crear_carrito_checkout(
            session_key="numero-existente"
        )
        pedido_existente = crear_pedido_desde_carrito(
            session_key=carrito_existente.session_key,
            token_idempotencia=carrito_existente.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        nuevo = agregar_producto(
            session_key="numero-agotado",
            producto_id=producto.pk,
            cantidad=1,
        )
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        with patch(
            "orders.services._generar_numero_pedido",
            return_value=pedido_existente.numero_pedido,
        ) as generar:
            with self.assertRaises(GeneracionNumeroPedidoAgotada):
                crear_pedido_desde_carrito(
                    session_key=nuevo.carrito.session_key,
                    token_idempotencia=nuevo.carrito.token_checkout,
                    datos_comprador=datos_comprador(dni="20.000.002"),
                    modalidad_entrega=ModalidadEntrega.RETIRO,
                )
        self.assertEqual(generar.call_count, 5)
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertTrue(nuevo.__class__.objects.filter(pk=nuevo.pk).exists())
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)
