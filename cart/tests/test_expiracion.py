from datetime import timedelta
from decimal import Decimal
import shutil
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from cart.exceptions import ItemCarritoNoEncontrado
from cart.exceptions import ProductoNoDisponible
from cart.models import Carrito, ItemCarrito
from cart.services import (
    DURACION_CARRITO,
    agregar_producto,
    eliminar_item,
    establecer_cantidad_item,
    obtener_carrito_vigente,
    vaciar_carrito,
)
from cart.tests.helpers import crear_producto_con_stock
from catalog.models import Producto
from inventory.models import MovimientoInventario


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ExpiracionCarritoTests(TestCase):
    def setUp(self):
        self.ahora = timezone.now()
        self.producto = crear_producto_con_stock(stock=10)
        self.item = agregar_producto(
            session_key="sesion-expiracion",
            producto_id=self.producto.pk,
            cantidad=2,
        )
        self.carrito = self.item.carrito

    def fijar_actividad(self, momento):
        Carrito.objects.filter(pk=self.carrito.pk).update(
            ultima_actividad=momento
        )
        self.carrito.ultima_actividad = momento

    def test_menos_de_seis_horas_continua_vigente_sin_renovar(self):
        actividad = self.ahora - DURACION_CARRITO + timedelta(seconds=1)
        self.fijar_actividad(actividad)

        with patch("cart.services._ahora", return_value=self.ahora):
            resultado = obtener_carrito_vigente("sesion-expiracion")

        self.assertEqual(resultado.pk, self.carrito.pk)
        resultado.refresh_from_db()
        self.assertEqual(resultado.ultima_actividad, actividad)

    def test_exactamente_seis_horas_expira_y_elimina_items(self):
        self.fijar_actividad(self.ahora - DURACION_CARRITO)
        item_id = self.item.pk

        with patch("cart.services._ahora", return_value=self.ahora):
            resultado = obtener_carrito_vigente("sesion-expiracion")

        self.assertIsNone(resultado)
        self.assertFalse(Carrito.objects.filter(pk=self.carrito.pk).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=item_id).exists())

    def test_mas_de_seis_horas_expira(self):
        self.fijar_actividad(
            self.ahora - DURACION_CARRITO - timedelta(seconds=1)
        )
        with patch("cart.services._ahora", return_value=self.ahora):
            self.assertIsNone(obtener_carrito_vigente("sesion-expiracion"))

    def test_agregar_item_nuevo_renueva_actividad(self):
        actividad = self.ahora - timedelta(hours=1)
        self.fijar_actividad(actividad)
        otro = crear_producto_con_stock(nombre="Baldo")

        with patch("cart.services._ahora", return_value=self.ahora):
            agregar_producto(
                session_key="sesion-expiracion",
                producto_id=otro.pk,
                cantidad=1,
            )

        self.carrito.refresh_from_db()
        self.assertEqual(self.carrito.ultima_actividad, self.ahora)

    def test_incrementar_renueva_actividad(self):
        self.fijar_actividad(self.ahora - timedelta(hours=1))
        with patch("cart.services._ahora", return_value=self.ahora):
            agregar_producto(
                session_key="sesion-expiracion",
                producto_id=self.producto.pk,
                cantidad=1,
            )
        self.carrito.refresh_from_db()
        self.assertEqual(self.carrito.ultima_actividad, self.ahora)

    def test_establecer_cantidad_renueva_actividad(self):
        self.fijar_actividad(self.ahora - timedelta(hours=1))
        with patch("cart.services._ahora", return_value=self.ahora):
            establecer_cantidad_item(
                session_key="sesion-expiracion",
                item_id=self.item.pk,
                cantidad=3,
            )
        self.carrito.refresh_from_db()
        self.assertEqual(self.carrito.ultima_actividad, self.ahora)

    def test_establecer_misma_cantidad_no_renueva_actividad(self):
        actividad = self.ahora - timedelta(hours=1)
        self.fijar_actividad(actividad)
        with patch("cart.services._ahora", return_value=self.ahora):
            establecer_cantidad_item(
                session_key="sesion-expiracion",
                item_id=self.item.pk,
                cantidad=2,
            )
        self.carrito.refresh_from_db()
        self.assertEqual(self.carrito.ultima_actividad, actividad)

    def test_eliminar_item_renueva_actividad(self):
        self.fijar_actividad(self.ahora - timedelta(hours=1))
        with patch("cart.services._ahora", return_value=self.ahora):
            eliminar_item(
                session_key="sesion-expiracion",
                item_id=self.item.pk,
            )
        self.carrito.refresh_from_db()
        self.assertEqual(self.carrito.ultima_actividad, self.ahora)

    def test_vaciar_no_vacio_renueva_y_vacio_no_renueva(self):
        self.fijar_actividad(self.ahora - timedelta(hours=1))
        with patch("cart.services._ahora", return_value=self.ahora):
            carrito = vaciar_carrito("sesion-expiracion")
        self.assertEqual(carrito.ultima_actividad, self.ahora)

        despues = self.ahora + timedelta(minutes=1)
        with patch("cart.services._ahora", return_value=despues):
            carrito = vaciar_carrito("sesion-expiracion")
        carrito.refresh_from_db()
        self.assertEqual(carrito.ultima_actividad, self.ahora)

    def test_acceder_tras_expirar_no_recupera_snapshots_antiguos(self):
        snapshot_antiguo = self.item.precio_unitario_snapshot
        carrito_anterior = self.carrito.pk
        self.fijar_actividad(self.ahora - DURACION_CARRITO)
        Producto.objects.filter(pk=self.producto.pk).update(
            precio_unitario=Decimal("7000.00"),
            precio_desde_3=Decimal("6500.00"),
            precio_desde_20=Decimal("6000.00"),
        )

        with patch("cart.services._ahora", return_value=self.ahora):
            nuevo = agregar_producto(
                session_key="sesion-expiracion",
                producto_id=self.producto.pk,
                cantidad=1,
            )

        self.assertNotEqual(nuevo.carrito_id, carrito_anterior)
        self.assertNotEqual(nuevo.precio_unitario_snapshot, snapshot_antiguo)
        self.assertEqual(nuevo.precio_unitario_snapshot, Decimal("7000.00"))
        self.assertEqual(Carrito.objects.count(), 1)
        self.assertEqual(ItemCarrito.objects.count(), 1)

    def test_operar_item_de_carrito_expirado_no_lo_reutiliza(self):
        self.fijar_actividad(self.ahora - DURACION_CARRITO)
        with patch("cart.services._ahora", return_value=self.ahora):
            with self.assertRaises(ItemCarritoNoEncontrado):
                establecer_cantidad_item(
                    session_key="sesion-expiracion",
                    item_id=self.item.pk,
                    cantidad=1,
                )
        self.assertFalse(Carrito.objects.exists())

    def test_establecer_al_cruzar_vencimiento_elimina_y_confirma_carrito(self):
        casi_vencido = self.ahora - DURACION_CARRITO + timedelta(seconds=1)
        momento_del_bloqueo = casi_vencido + DURACION_CARRITO
        self.fijar_actividad(casi_vencido)
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        stock_inicial = inventario.cantidad_disponible
        movimientos_iniciales = MovimientoInventario.objects.count()

        with patch("cart.services._ahora", return_value=momento_del_bloqueo):
            with self.assertRaises(ItemCarritoNoEncontrado):
                establecer_cantidad_item(
                    session_key="sesion-expiracion",
                    item_id=self.item.pk,
                    cantidad=3,
                )

        inventario.refresh_from_db()
        self.assertFalse(Carrito.objects.filter(pk=self.carrito.pk).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(inventario.cantidad_disponible, stock_inicial)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )

    def test_eliminar_al_cruzar_vencimiento_elimina_y_confirma_carrito(self):
        casi_vencido = self.ahora - DURACION_CARRITO + timedelta(seconds=1)
        momento_del_bloqueo = casi_vencido + DURACION_CARRITO
        self.fijar_actividad(casi_vencido)
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        stock_inicial = inventario.cantidad_disponible
        movimientos_iniciales = MovimientoInventario.objects.count()

        with patch("cart.services._ahora", return_value=momento_del_bloqueo):
            with self.assertRaises(ItemCarritoNoEncontrado):
                eliminar_item(
                    session_key="sesion-expiracion",
                    item_id=self.item.pk,
                )

        inventario.refresh_from_db()
        self.assertFalse(Carrito.objects.filter(pk=self.carrito.pk).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(inventario.cantidad_disponible, stock_inicial)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )

    def test_expiracion_no_modifica_inventario_ni_crea_movimientos(self):
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        stock_inicial = inventario.cantidad_disponible
        movimientos_iniciales = MovimientoInventario.objects.count()
        self.fijar_actividad(self.ahora - DURACION_CARRITO)

        with patch("cart.services._ahora", return_value=self.ahora):
            obtener_carrito_vigente("sesion-expiracion")

        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, stock_inicial)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )

    def test_agregar_invalido_tras_expirar_confirma_borrado_sin_carrito_nuevo(self):
        self.fijar_actividad(self.ahora - DURACION_CARRITO)
        with patch("cart.services._ahora", return_value=self.ahora):
            with self.assertRaises(ProductoNoDisponible):
                agregar_producto(
                    session_key="sesion-expiracion",
                    producto_id=999999,
                    cantidad=1,
                )
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())
