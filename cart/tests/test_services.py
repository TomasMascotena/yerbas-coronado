from decimal import Decimal
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, override_settings
from django.utils import timezone

from cart.exceptions import (
    CantidadCarritoInvalida,
    CarritoNoPerteneceALaSesion,
    ItemCarritoNoEncontrado,
    ProductoNoDisponible,
    ProductoSinInventario,
    SesionNoDisponible,
    StockInsuficienteParaCarrito,
)
from cart.models import Carrito, ItemCarrito
from cart.services import (
    agregar_producto,
    eliminar_item,
    establecer_cantidad_item,
    obtener_carrito_vigente,
    obtener_o_crear_carrito,
    vaciar_carrito,
)
from cart.session import asegurar_session_key
from cart.tests.helpers import crear_item_directo, crear_producto_con_stock
from catalog.models import Producto
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.models import MovimientoInventario


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class SesionYCarritoServiceTests(TestCase):
    def test_integracion_crea_session_key_solo_al_solicitarla(self):
        session = SessionStore()
        self.assertIsNone(session.session_key)

        session_key = asegurar_session_key(session)

        self.assertIsNotNone(session_key)
        self.assertEqual(session_key, session.session_key)

    def test_session_key_invalida_se_rechaza(self):
        for session_key in (None, "", "   ", 123, "x" * 41):
            with self.subTest(session_key=session_key):
                with self.assertRaises(SesionNoDisponible):
                    obtener_o_crear_carrito(session_key)
        self.assertFalse(Carrito.objects.exists())

    def test_obtener_o_crear_reutiliza_carrito_vigente(self):
        primero = obtener_o_crear_carrito("sesion-reutilizada")
        segundo = obtener_o_crear_carrito("sesion-reutilizada")
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(Carrito.objects.count(), 1)

    def test_obtener_vigente_inexistente_devuelve_none(self):
        self.assertIsNone(obtener_carrito_vigente("sesion-sin-carrito"))


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class AgregarProductoServiceTests(TestCase):
    def setUp(self):
        self.producto = crear_producto_con_stock(stock=10)
        self.session_key = "sesion-agregar"

    def test_primera_incorporacion_crea_carrito_item_y_snapshots(self):
        item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=2,
        )

        self.assertEqual(Carrito.objects.count(), 1)
        self.assertEqual(ItemCarrito.objects.count(), 1)
        self.assertEqual(item.cantidad, 2)
        self.assertEqual(
            item.precio_unitario_snapshot,
            self.producto.precio_unitario,
        )
        self.assertEqual(
            item.precio_desde_3_snapshot,
            self.producto.precio_desde_3,
        )
        self.assertEqual(
            item.precio_desde_20_snapshot,
            self.producto.precio_desde_20,
        )

    def test_agregar_mismo_producto_incrementa_sin_duplicar(self):
        primero = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=2,
        )
        segundo = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=3,
        )

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(ItemCarrito.objects.count(), 1)
        self.assertEqual(segundo.cantidad, 5)

    def test_cantidades_invalidas_se_rechazan_sin_crear_carrito(self):
        for cantidad in (0, -1, True, False, "3", 3.0, None):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(CantidadCarritoInvalida):
                    agregar_producto(
                        session_key=self.session_key,
                        producto_id=self.producto.pk,
                        cantidad=cantidad,
                    )
        self.assertFalse(Carrito.objects.exists())

    def test_producto_inexistente_se_rechaza_sin_carrito_residual(self):
        with self.assertRaises(ProductoNoDisponible):
            agregar_producto(
                session_key=self.session_key,
                producto_id=999999,
                cantidad=1,
            )
        self.assertFalse(Carrito.objects.exists())

    def test_producto_inactivo_se_rechaza_sin_carrito_residual(self):
        self.producto.activo = False
        self.producto.save(update_fields=("activo",))

        with self.assertRaises(ProductoNoDisponible):
            agregar_producto(
                session_key=self.session_key,
                producto_id=self.producto.pk,
                cantidad=1,
            )
        self.assertFalse(Carrito.objects.exists())

    def test_producto_sin_inventario_se_rechaza(self):
        producto = Producto(**datos_producto(nombre="Sin inventario"))
        producto.full_clean()
        producto.save()
        movimientos = MovimientoInventario.objects.count()

        with self.assertRaises(ProductoSinInventario):
            agregar_producto(
                session_key=self.session_key,
                producto_id=producto.pk,
                cantidad=1,
            )
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_producto_sin_inventario_es_compatible_con_excepcion_padre(self):
        producto = Producto(**datos_producto(nombre="Sin inventario padre"))
        producto.full_clean()
        producto.save()

        with self.assertRaises(ProductoNoDisponible):
            agregar_producto(
                session_key=self.session_key,
                producto_id=producto.pk,
                cantidad=1,
            )

        self.assertFalse(Carrito.objects.exists())

    def test_stock_cero_o_insuficiente_se_rechaza_sin_carrito(self):
        sin_stock = crear_producto_con_stock(nombre="Baldo", stock=0)
        for producto, cantidad in ((sin_stock, 1), (self.producto, 11)):
            with self.subTest(producto=producto.nombre):
                with self.assertRaises(StockInsuficienteParaCarrito):
                    agregar_producto(
                        session_key=self.session_key,
                        producto_id=producto.pk,
                        cantidad=cantidad,
                    )
        self.assertFalse(Carrito.objects.exists())

    def test_cantidad_final_acumulada_se_valida_contra_stock(self):
        item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=7,
        )
        actividad = item.carrito.ultima_actividad

        with self.assertRaises(StockInsuficienteParaCarrito):
            agregar_producto(
                session_key=self.session_key,
                producto_id=self.producto.pk,
                cantidad=4,
            )

        item.refresh_from_db()
        item.carrito.refresh_from_db()
        self.assertEqual(item.cantidad, 7)
        self.assertEqual(item.carrito.ultima_actividad, actividad)

    def test_producto_inactivado_en_carrito_no_puede_incrementarse(self):
        item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=2,
        )
        actividad = item.carrito.ultima_actividad
        self.producto.activo = False
        self.producto.save(update_fields=("activo",))

        with self.assertRaises(ProductoNoDisponible):
            agregar_producto(
                session_key=self.session_key,
                producto_id=self.producto.pk,
                cantidad=1,
            )

        item.refresh_from_db()
        item.carrito.refresh_from_db()
        self.assertEqual(item.cantidad, 2)
        self.assertEqual(item.carrito.ultima_actividad, actividad)

    def test_fallo_tecnico_revierte_carrito_e_item(self):
        with patch.object(ItemCarrito, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                agregar_producto(
                    session_key=self.session_key,
                    producto_id=self.producto.pk,
                    cantidad=1,
                )

        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())

    def test_incrementar_no_modifica_snapshots_aunque_cambie_producto(self):
        item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=1,
        )
        snapshots = (
            item.precio_unitario_snapshot,
            item.precio_desde_3_snapshot,
            item.precio_desde_20_snapshot,
        )
        Producto.objects.filter(pk=self.producto.pk).update(
            precio_unitario=Decimal("6000.00"),
            precio_desde_3=Decimal("5500.00"),
            precio_desde_20=Decimal("5000.00"),
        )

        item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=1,
        )
        self.assertEqual(
            (
                item.precio_unitario_snapshot,
                item.precio_desde_3_snapshot,
                item.precio_desde_20_snapshot,
            ),
            snapshots,
        )

    def test_snapshots_son_decimal_con_dos_decimales(self):
        item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=1,
        )
        for valor in (
            item.precio_unitario_snapshot,
            item.precio_desde_3_snapshot,
            item.precio_desde_20_snapshot,
        ):
            self.assertIsInstance(valor, Decimal)
            self.assertEqual(valor.as_tuple().exponent, -2)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ModificarCarritoServiceTests(TestCase):
    def setUp(self):
        self.producto = crear_producto_con_stock(stock=10)
        self.item = agregar_producto(
            session_key="sesion-propia",
            producto_id=self.producto.pk,
            cantidad=3,
        )

    def test_establecer_cantidad_funciona_y_conserva_snapshots(self):
        snapshots = (
            self.item.precio_unitario_snapshot,
            self.item.precio_desde_3_snapshot,
            self.item.precio_desde_20_snapshot,
        )
        item = establecer_cantidad_item(
            session_key="sesion-propia",
            item_id=self.item.pk,
            cantidad=7,
        )

        self.assertEqual(item.cantidad, 7)
        self.assertEqual(
            (
                item.precio_unitario_snapshot,
                item.precio_desde_3_snapshot,
                item.precio_desde_20_snapshot,
            ),
            snapshots,
        )

    def test_establecer_cero_no_elimina_item(self):
        with self.assertRaises(CantidadCarritoInvalida):
            establecer_cantidad_item(
                session_key="sesion-propia",
                item_id=self.item.pk,
                cantidad=0,
            )
        self.assertTrue(ItemCarrito.objects.filter(pk=self.item.pk).exists())

    def test_establecer_rechaza_stock_insuficiente_e_inactividad(self):
        with self.assertRaises(StockInsuficienteParaCarrito):
            establecer_cantidad_item(
                session_key="sesion-propia",
                item_id=self.item.pk,
                cantidad=11,
            )
        self.producto.activo = False
        self.producto.save(update_fields=("activo",))
        with self.assertRaises(ProductoNoDisponible):
            establecer_cantidad_item(
                session_key="sesion-propia",
                item_id=self.item.pk,
                cantidad=2,
            )

    def test_establecer_producto_sin_inventario_revierte_sin_cambios(self):
        producto = Producto(**datos_producto(nombre="Item sin inventario"))
        producto.full_clean()
        producto.save()
        item = crear_item_directo(producto=producto, cantidad=2)
        actividad = item.carrito.ultima_actividad
        movimientos = MovimientoInventario.objects.count()

        with self.assertRaises(ProductoSinInventario):
            establecer_cantidad_item(
                session_key=item.carrito.session_key,
                item_id=item.pk,
                cantidad=3,
            )

        item.refresh_from_db()
        item.carrito.refresh_from_db()
        self.assertEqual(item.cantidad, 2)
        self.assertEqual(item.carrito.ultima_actividad, actividad)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_no_puede_modificarse_item_de_otra_sesion(self):
        obtener_o_crear_carrito("sesion-ajena")
        with self.assertRaises(CarritoNoPerteneceALaSesion):
            establecer_cantidad_item(
                session_key="sesion-ajena",
                item_id=self.item.pk,
                cantidad=1,
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.cantidad, 3)

    def test_item_inexistente_se_informa(self):
        with self.assertRaises(ItemCarritoNoEncontrado):
            establecer_cantidad_item(
                session_key="sesion-propia",
                item_id=999999,
                cantidad=1,
            )

    def test_eliminar_item_funciona_incluso_si_producto_esta_inactivo(self):
        self.producto.activo = False
        self.producto.save(update_fields=("activo",))
        carrito_id = self.item.carrito_id

        eliminar_item(session_key="sesion-propia", item_id=self.item.pk)

        self.assertFalse(ItemCarrito.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(Carrito.objects.filter(pk=carrito_id).exists())

    def test_eliminar_item_de_otra_sesion_se_rechaza(self):
        obtener_o_crear_carrito("sesion-ajena")
        with self.assertRaises(CarritoNoPerteneceALaSesion):
            eliminar_item(session_key="sesion-ajena", item_id=self.item.pk)
        self.assertTrue(ItemCarrito.objects.filter(pk=self.item.pk).exists())

    def test_vaciar_elimina_items_mantiene_carrito_y_es_idempotente(self):
        carrito = self.item.carrito
        otro = crear_producto_con_stock(nombre="Baldo")
        agregar_producto(
            session_key="sesion-propia",
            producto_id=otro.pk,
            cantidad=1,
        )

        resultado = vaciar_carrito("sesion-propia")
        actividad = resultado.ultima_actividad
        resultado = vaciar_carrito("sesion-propia")

        self.assertEqual(resultado.pk, carrito.pk)
        self.assertFalse(resultado.items.exists())
        resultado.refresh_from_db()
        self.assertEqual(resultado.ultima_actividad, actividad)

    def test_vaciar_sin_carrito_es_idempotente(self):
        self.assertIsNone(vaciar_carrito("sesion-inexistente"))

    def test_eliminar_y_reagregar_toma_snapshots_nuevos(self):
        eliminar_item(session_key="sesion-propia", item_id=self.item.pk)
        Producto.objects.filter(pk=self.producto.pk).update(
            precio_unitario=Decimal("6100.00"),
            precio_desde_3=Decimal("5600.00"),
            precio_desde_20=Decimal("5100.00"),
        )

        nuevo = agregar_producto(
            session_key="sesion-propia",
            producto_id=self.producto.pk,
            cantidad=1,
        )

        self.assertNotEqual(nuevo.pk, self.item.pk)
        self.assertEqual(nuevo.precio_unitario_snapshot, Decimal("6100.00"))

    def test_operaciones_no_modifican_inventario_ni_crean_movimientos(self):
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        stock_inicial = inventario.cantidad_disponible
        movimientos_iniciales = MovimientoInventario.objects.count()

        establecer_cantidad_item(
            session_key="sesion-propia",
            item_id=self.item.pk,
            cantidad=4,
        )
        eliminar_item(session_key="sesion-propia", item_id=self.item.pk)

        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, stock_inicial)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )

    def test_operaciones_exitosas_renuevan_actividad(self):
        pasado = timezone.now().replace(microsecond=0)
        Carrito.objects.filter(pk=self.item.carrito_id).update(
            ultima_actividad=pasado
        )
        futuro = pasado.replace(microsecond=1)
        with patch("cart.services._ahora", return_value=futuro):
            establecer_cantidad_item(
                session_key="sesion-propia",
                item_id=self.item.pk,
                cantidad=4,
            )
        self.item.carrito.refresh_from_db()
        self.assertEqual(self.item.carrito.ultima_actividad, futuro)
