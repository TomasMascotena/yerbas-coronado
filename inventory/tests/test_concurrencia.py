from concurrent.futures import ThreadPoolExecutor
import shutil
import tempfile
from threading import Barrier

from django.db import connections
from django.test import TransactionTestCase, override_settings

from inventory.exceptions import StockInsuficiente
from inventory.models import MovimientoInventario, TipoMovimientoInventario
from inventory.services import (
    registrar_ingreso_mercaderia,
    registrar_venta_presencial,
)
from inventory.tests.helpers import crear_inventario_de_prueba


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ConcurrenciaMovimientoInventarioTests(TransactionTestCase):
    def test_dos_ventas_concurrentes_no_consumen_mas_stock_del_disponible(self):
        inventario = crear_inventario_de_prueba()
        registrar_ingreso_mercaderia(
            inventario_id=inventario.pk,
            cantidad=5,
        )
        barrera = Barrier(2)

        def vender():
            connections.close_all()
            try:
                barrera.wait(timeout=10)
                registrar_venta_presencial(
                    inventario_id=inventario.pk,
                    cantidad=4,
                )
                return "exitosa"
            except StockInsuficiente:
                return "stock_insuficiente"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(lambda _: vender(), range(2)))

        inventario.refresh_from_db()
        ventas = MovimientoInventario.objects.filter(
            inventario=inventario,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PRESENCIAL,
        )
        self.assertCountEqual(
            resultados,
            ["exitosa", "stock_insuficiente"],
        )
        self.assertEqual(inventario.cantidad_disponible, 1)
        self.assertEqual(ventas.count(), 1)
        self.assertEqual(ventas.get().cantidad, 4)
