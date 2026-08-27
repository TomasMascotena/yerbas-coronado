from concurrent.futures import ThreadPoolExecutor
import shutil
import tempfile
from threading import Barrier

from django.db import connections
from django.test import TransactionTestCase, override_settings

from cart.exceptions import StockInsuficienteParaCarrito
from cart.models import Carrito, ItemCarrito
from cart.services import (
    agregar_producto,
    establecer_cantidad_item,
    obtener_o_crear_carrito,
)
from cart.tests.helpers import crear_producto_con_stock
from inventory.models import MovimientoInventario


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ConcurrenciaCarritoTests(TransactionTestCase):
    def ejecutar_en_paralelo(self, operacion_a, operacion_b):
        barrera = Barrier(2)

        def ejecutar(operacion):
            connections.close_all()
            try:
                barrera.wait(timeout=10)
                return operacion()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = (
                executor.submit(ejecutar, operacion_a),
                executor.submit(ejecutar, operacion_b),
            )
            return [futuro.result(timeout=15) for futuro in futuros]

    def test_dos_creaciones_concurrentes_dejan_un_solo_carrito(self):
        resultados = self.ejecutar_en_paralelo(
            lambda: obtener_o_crear_carrito("sesion-concurrente").pk,
            lambda: obtener_o_crear_carrito("sesion-concurrente").pk,
        )

        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(Carrito.objects.count(), 1)

    def test_dos_agregados_concurrentes_no_duplican_item(self):
        producto = crear_producto_con_stock(stock=10)

        resultados = self.ejecutar_en_paralelo(
            lambda: agregar_producto(
                session_key="sesion-agregado",
                producto_id=producto.pk,
                cantidad=1,
            ).pk,
            lambda: agregar_producto(
                session_key="sesion-agregado",
                producto_id=producto.pk,
                cantidad=1,
            ).pk,
        )

        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(ItemCarrito.objects.count(), 1)
        self.assertEqual(ItemCarrito.objects.get().cantidad, 2)

    def test_dos_incrementos_concurrentes_no_pierden_actualizaciones(self):
        producto = crear_producto_con_stock(stock=20)
        item = agregar_producto(
            session_key="sesion-incrementos",
            producto_id=producto.pk,
            cantidad=1,
        )

        self.ejecutar_en_paralelo(
            lambda: agregar_producto(
                session_key="sesion-incrementos",
                producto_id=producto.pk,
                cantidad=2,
            ).pk,
            lambda: agregar_producto(
                session_key="sesion-incrementos",
                producto_id=producto.pk,
                cantidad=3,
            ).pk,
        )

        item.refresh_from_db()
        self.assertEqual(item.cantidad, 6)

    def test_agregar_y_establecer_concurrentemente_no_generan_deadlock(self):
        producto = crear_producto_con_stock(stock=10)
        inventario = producto.inventario
        inventario.refresh_from_db()
        item = agregar_producto(
            session_key="sesion-operaciones-cruzadas",
            producto_id=producto.pk,
            cantidad=1,
        )
        movimientos_iniciales = MovimientoInventario.objects.count()

        resultados = self.ejecutar_en_paralelo(
            lambda: agregar_producto(
                session_key="sesion-operaciones-cruzadas",
                producto_id=producto.pk,
                cantidad=2,
            ).pk,
            lambda: establecer_cantidad_item(
                session_key="sesion-operaciones-cruzadas",
                item_id=item.pk,
                cantidad=5,
            ).pk,
        )

        item.refresh_from_db()
        inventario.refresh_from_db()
        self.assertEqual(resultados, [item.pk, item.pk])
        self.assertIn(item.cantidad, (5, 7))
        self.assertLessEqual(item.cantidad, inventario.cantidad_disponible)
        self.assertEqual(ItemCarrito.objects.count(), 1)
        self.assertEqual(inventario.cantidad_disponible, 10)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )

    def test_carrera_que_supera_stock_permite_solo_un_incremento(self):
        producto = crear_producto_con_stock(stock=5)
        item = agregar_producto(
            session_key="sesion-stock",
            producto_id=producto.pk,
            cantidad=1,
        )

        def incrementar():
            try:
                agregar_producto(
                    session_key="sesion-stock",
                    producto_id=producto.pk,
                    cantidad=3,
                )
                return "exitosa"
            except StockInsuficienteParaCarrito:
                return "stock_insuficiente"

        resultados = self.ejecutar_en_paralelo(incrementar, incrementar)

        item.refresh_from_db()
        self.assertCountEqual(
            resultados,
            ["exitosa", "stock_insuficiente"],
        )
        self.assertEqual(item.cantidad, 4)

    def test_fallo_concurrente_no_modifica_inventario_ni_movimientos(self):
        producto = crear_producto_con_stock(stock=5)
        inventario = producto.inventario
        agregar_producto(
            session_key="sesion-sin-reserva",
            producto_id=producto.pk,
            cantidad=1,
        )
        movimientos_iniciales = MovimientoInventario.objects.count()

        def incrementar():
            try:
                agregar_producto(
                    session_key="sesion-sin-reserva",
                    producto_id=producto.pk,
                    cantidad=3,
                )
            except StockInsuficienteParaCarrito:
                pass

        self.ejecutar_en_paralelo(incrementar, incrementar)

        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 5)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )
