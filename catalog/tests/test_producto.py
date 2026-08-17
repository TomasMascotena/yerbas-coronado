from decimal import Decimal
import shutil
import tempfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.models import Inventario


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class CrearProductoConInventarioTests(TestCase):
    def test_crea_producto_valido_activo_con_un_inventario_en_cero(self):
        producto = crear_producto_con_inventario(**datos_producto())

        producto.refresh_from_db()
        self.assertTrue(producto.activo)
        self.assertEqual(Producto.objects.count(), 1)
        self.assertEqual(Inventario.objects.filter(producto=producto).count(), 1)
        self.assertEqual(producto.inventario.cantidad_disponible, 0)

    def test_descripcion_es_opcional(self):
        producto = crear_producto_con_inventario(
            **datos_producto(descripcion="")
        )

        self.assertEqual(producto.descripcion, "")

    def test_rechaza_nombre_y_peso_duplicados(self):
        crear_producto_con_inventario(**datos_producto())

        with self.assertRaises(ValidationError):
            crear_producto_con_inventario(
                **datos_producto(imagen=imagen_de_prueba("duplicado.gif"))
            )

    def test_la_base_de_datos_rechaza_nombre_y_peso_duplicados(self):
        Producto.objects.create(**datos_producto())

        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(
                **datos_producto(imagen=imagen_de_prueba("duplicado-sql.gif"))
            )

    def test_permite_mismo_nombre_con_distinto_peso(self):
        crear_producto_con_inventario(**datos_producto())

        crear_producto_con_inventario(
            **datos_producto(
                peso="500 gr",
                imagen=imagen_de_prueba("otro-peso.gif"),
            )
        )

        self.assertEqual(Producto.objects.count(), 2)

    def test_permite_distinto_nombre_con_mismo_peso(self):
        crear_producto_con_inventario(**datos_producto())

        crear_producto_con_inventario(
            **datos_producto(
                nombre="Baldo",
                imagen=imagen_de_prueba("otro-nombre.gif"),
            )
        )

        self.assertEqual(Producto.objects.count(), 2)

    def test_imagen_es_obligatoria(self):
        with self.assertRaises(ValidationError):
            crear_producto_con_inventario(**datos_producto(imagen=None))

    def test_no_impone_relacion_matematica_entre_los_precios(self):
        producto = crear_producto_con_inventario(
            **datos_producto(
                precio_unitario=Decimal("100.00"),
                precio_desde_3=Decimal("300.00"),
                precio_desde_20=Decimal("200.00"),
            )
        )

        self.assertEqual(producto.precio_unitario, Decimal("100.00"))
        self.assertEqual(producto.precio_desde_3, Decimal("300.00"))
        self.assertEqual(producto.precio_desde_20, Decimal("200.00"))

    def test_hace_rollback_del_producto_si_falla_el_inventario(self):
        with patch.object(Inventario, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                crear_producto_con_inventario(**datos_producto())

        self.assertFalse(Producto.objects.exists())
        self.assertFalse(Inventario.objects.exists())


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class RestriccionesDePrecioTests(TestCase):
    def assert_precio_invalido(self, campo, valor):
        with self.assertRaises(ValidationError):
            crear_producto_con_inventario(**datos_producto(**{campo: valor}))

    def test_rechaza_precio_unitario_cero_o_negativo(self):
        for valor in (Decimal("0.00"), Decimal("-1.00")):
            with self.subTest(valor=valor):
                self.assert_precio_invalido("precio_unitario", valor)

    def test_rechaza_precio_desde_3_cero_o_negativo(self):
        for valor in (Decimal("0.00"), Decimal("-1.00")):
            with self.subTest(valor=valor):
                self.assert_precio_invalido("precio_desde_3", valor)

    def test_rechaza_precio_desde_20_cero_o_negativo(self):
        for valor in (Decimal("0.00"), Decimal("-1.00")):
            with self.subTest(valor=valor):
                self.assert_precio_invalido("precio_desde_20", valor)

    def test_la_base_de_datos_rechaza_cada_precio_no_positivo(self):
        for campo in (
            "precio_unitario",
            "precio_desde_3",
            "precio_desde_20",
        ):
            with self.subTest(campo=campo):
                producto = Producto(
                    **datos_producto(
                        **{
                            campo: Decimal("0.00"),
                            "imagen": imagen_de_prueba(f"{campo}.gif"),
                        }
                    )
                )

                with self.assertRaises(IntegrityError), transaction.atomic():
                    producto.save()
