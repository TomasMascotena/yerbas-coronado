import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.tests.helpers import datos_producto
from inventory.models import Inventario


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class InventarioTests(TestCase):
    def setUp(self):
        self.producto = crear_producto_con_inventario(
            producto=Producto(**datos_producto())
        )

    def test_rechaza_cantidad_negativa_en_aplicacion(self):
        inventario = self.producto.inventario
        inventario.cantidad_disponible = -1

        with self.assertRaises(ValidationError):
            inventario.full_clean()

    def test_rechaza_cantidad_negativa_en_base_de_datos(self):
        inventario = self.producto.inventario
        inventario.cantidad_disponible = -1

        with self.assertRaises(IntegrityError), transaction.atomic():
            inventario.save(update_fields=("cantidad_disponible",))

    def test_no_permite_dos_inventarios_para_el_mismo_producto(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inventario.objects.create(producto=self.producto)
