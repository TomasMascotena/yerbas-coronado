from datetime import timedelta
from decimal import Decimal
import shutil
import tempfile
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from cart.models import Carrito, ItemCarrito
from cart.tests.helpers import crear_item_directo, crear_producto_con_stock


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class CarritoModelTests(TestCase):
    def test_carrito_valido_tiene_fechas_aware_y_puede_estar_vacio(self):
        carrito = Carrito.objects.create(session_key="sesion-1")

        self.assertTrue(timezone.is_aware(carrito.creado_en))
        self.assertTrue(timezone.is_aware(carrito.ultima_actividad))
        self.assertTrue(carrito.esta_vacio)
        self.assertEqual(carrito.cantidad_lineas, 0)
        self.assertEqual(carrito.cantidad_total_unidades, 0)

    def test_session_key_vacia_falla_en_aplicacion(self):
        with self.assertRaises(ValidationError):
            Carrito(session_key="").full_clean()

    def test_session_key_vacia_falla_en_postgresql(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Carrito.objects.create(session_key="")

    def test_dos_carritos_no_pueden_compartir_session_key(self):
        Carrito.objects.create(session_key="sesion-unica")

        with self.assertRaises(ValidationError):
            Carrito(session_key="sesion-unica").full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Carrito.objects.create(session_key="sesion-unica")

    def test_representacion_de_carrito_es_util(self):
        carrito = Carrito.objects.create(session_key="sesion-legible")
        self.assertIn("sesion-legible", str(carrito))

    def test_token_checkout_es_uuid_unico(self):
        primero = Carrito.objects.create(session_key="sesion-token-a")
        segundo = Carrito.objects.create(session_key="sesion-token-b")
        self.assertIsInstance(primero.token_checkout, uuid.UUID)
        self.assertNotEqual(primero.token_checkout, segundo.token_checkout)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ItemCarritoModelTests(TestCase):
    def test_item_valido_relaciona_carrito_producto_y_snapshots(self):
        item = crear_item_directo(cantidad=2)

        self.assertIsInstance(item.carrito, Carrito)
        self.assertEqual(item.producto.nombre, "Canarias")
        self.assertEqual(item.precio_unitario_snapshot, Decimal("5000.00"))
        self.assertIn("Canarias", str(item))
        self.assertIn("2", str(item))

    def test_cantidad_cero_o_negativa_falla_en_aplicacion(self):
        carrito = Carrito.objects.create(session_key="sesion-cantidad-app")
        producto = crear_producto_con_stock()
        for cantidad in (0, -1):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(ValidationError):
                    crear_item_directo(
                        carrito=carrito,
                        producto=producto,
                        cantidad=cantidad,
                    )

    def test_cantidad_cero_o_negativa_falla_en_postgresql(self):
        carrito = Carrito.objects.create(session_key="sesion-cantidad-db")
        producto = crear_producto_con_stock()
        for cantidad in (0, -1):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        ItemCarrito.objects.create(
                            carrito=carrito,
                            producto=producto,
                            cantidad=cantidad,
                            precio_unitario_snapshot=Decimal("1.00"),
                            precio_desde_3_snapshot=Decimal("1.00"),
                            precio_desde_20_snapshot=Decimal("1.00"),
                        )

    def test_producto_no_puede_repetirse_en_mismo_carrito(self):
        item = crear_item_directo()
        duplicado = ItemCarrito(
            carrito=item.carrito,
            producto=item.producto,
            cantidad=1,
            precio_unitario_snapshot=Decimal("1.00"),
            precio_desde_3_snapshot=Decimal("1.00"),
            precio_desde_20_snapshot=Decimal("1.00"),
        )

        with self.assertRaises(ValidationError):
            duplicado.full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicado.save()

    def test_mismo_producto_puede_estar_en_carritos_diferentes(self):
        producto = crear_producto_con_stock()
        primero = crear_item_directo(
            carrito=Carrito.objects.create(session_key="sesion-a"),
            producto=producto,
        )
        segundo = crear_item_directo(
            carrito=Carrito.objects.create(session_key="sesion-b"),
            producto=producto,
        )

        self.assertNotEqual(primero.carrito_id, segundo.carrito_id)

    def test_snapshots_no_positivos_fallan_en_aplicacion(self):
        campos = (
            "precio_unitario_snapshot",
            "precio_desde_3_snapshot",
            "precio_desde_20_snapshot",
        )
        carrito = Carrito.objects.create(session_key="sesion-precios-app")
        producto = crear_producto_con_stock()
        for campo in campos:
            for valor in (Decimal("0.00"), Decimal("-1.00")):
                with self.subTest(campo=campo, valor=valor):
                    with self.assertRaises(ValidationError):
                        crear_item_directo(
                            carrito=carrito,
                            producto=producto,
                            **{campo: valor},
                        )

    def test_snapshots_no_positivos_fallan_en_postgresql(self):
        campos = (
            "precio_unitario_snapshot",
            "precio_desde_3_snapshot",
            "precio_desde_20_snapshot",
        )
        producto = crear_producto_con_stock()
        for indice, campo in enumerate(campos):
            carrito = Carrito.objects.create(session_key=f"sesion-db-{indice}")
            datos = {
                "precio_unitario_snapshot": Decimal("1.00"),
                "precio_desde_3_snapshot": Decimal("1.00"),
                "precio_desde_20_snapshot": Decimal("1.00"),
            }
            datos[campo] = Decimal("0.00")
            with self.subTest(campo=campo):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        ItemCarrito.objects.create(
                            carrito=carrito,
                            producto=producto,
                            cantidad=1,
                            **datos,
                        )

    def test_eliminar_carrito_elimina_sus_items(self):
        item = crear_item_directo()
        item_id = item.pk
        item.carrito.delete()
        self.assertFalse(ItemCarrito.objects.filter(pk=item_id).exists())

    def test_producto_referenciado_esta_protegido(self):
        item = crear_item_directo()
        with self.assertRaises(ProtectedError):
            item.producto.delete()

    def test_consultas_derivadas_suman_lineas_y_unidades_sin_n_mas_uno(self):
        carrito = Carrito.objects.create(session_key="sesion-consultas")
        crear_item_directo(
            carrito=carrito,
            producto=crear_producto_con_stock(nombre="Canarias"),
            cantidad=2,
        )
        crear_item_directo(
            carrito=carrito,
            producto=crear_producto_con_stock(nombre="Baldo"),
            cantidad=3,
        )

        self.assertEqual(carrito.cantidad_lineas, 2)
        self.assertEqual(carrito.cantidad_total_unidades, 5)
        self.assertFalse(carrito.esta_vacio)
        with self.assertNumQueries(1):
            items = list(carrito.items_con_detalle())
            [(item.producto.nombre, item.producto.inventario.pk) for item in items]

    def test_fechas_admiten_antiguedad_sin_volverse_naive(self):
        momento = timezone.now() - timedelta(hours=1)
        carrito = Carrito.objects.create(
            session_key="sesion-fecha",
            creado_en=momento,
            ultima_actividad=momento,
        )
        self.assertTrue(timezone.is_aware(carrito.ultima_actividad))
