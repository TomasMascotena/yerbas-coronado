from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import shutil
import tempfile
from threading import Barrier, Event
import time
from unittest.mock import patch

from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from cart.exceptions import StockInsuficienteParaCarrito
from cart.models import Carrito, ItemCarrito
from cart.services import (
    agregar_producto,
    establecer_cantidad_item,
    obtener_carrito_vigente,
    obtener_o_crear_carrito,
)
import cart.services as cart_services
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

    def test_colision_reintenta_si_el_carrito_ganador_desaparece(self):
        barrera = Barrier(2)
        colision_detectada = Event()
        ganador_eliminado = Event()
        recuperar_original = cart_services._recuperar_carrito_ganador

        def recuperar_despues_de_eliminacion(session_key):
            colision_detectada.set()
            ganador_eliminado.wait(timeout=10)
            return recuperar_original(session_key)

        def crear():
            connections.close_all()
            try:
                barrera.wait(timeout=10)
                return obtener_o_crear_carrito("sesion-ganador-eliminado").pk
            finally:
                connections.close_all()

        with patch(
            "cart.services._recuperar_carrito_ganador",
            side_effect=recuperar_despues_de_eliminacion,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futuros = (executor.submit(crear), executor.submit(crear))
                self.assertTrue(colision_detectada.wait(timeout=10))
                ganador = Carrito.objects.get(
                    session_key="sesion-ganador-eliminado"
                )
                ganador_pk = ganador.pk
                ganador.delete()
                ganador_eliminado.set()
                resultados = [f.result(timeout=20) for f in futuros]

        self.assertIn(ganador_pk, resultados)
        self.assertEqual(Carrito.objects.count(), 1)
        recuperado = Carrito.objects.get()
        self.assertNotEqual(recuperado.pk, ganador_pk)
        self.assertIn(recuperado.pk, resultados)

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

    def test_expiracion_se_evalua_despues_de_esperar_el_bloqueo(self):
        carrito = obtener_o_crear_carrito("sesion-cruce-real")
        casi_vencido = timezone.now() - cart_services.DURACION_CARRITO + timedelta(
            seconds=1
        )
        Carrito.objects.filter(pk=carrito.pk).update(
            ultima_actividad=casi_vencido
        )
        bloqueo_adquirido = Event()
        backend_lector_listo = Event()
        liberar_bloqueo = Event()
        reloj_consultado = Event()
        backend_lector = {}

        def mantener_bloqueo():
            connections.close_all()
            try:
                with transaction.atomic():
                    Carrito.objects.select_for_update().get(pk=carrito.pk)
                    bloqueo_adquirido.set()
                    liberar_bloqueo.wait(timeout=10)
            finally:
                connections.close_all()

        def ahora_posterior():
            reloj_consultado.set()
            return casi_vencido + cart_services.DURACION_CARRITO

        def leer_carrito():
            connections.close_all()
            try:
                connection = connections["default"]
                connection.ensure_connection()
                backend_lector["pid"] = connection.connection.info.backend_pid
                backend_lector_listo.set()
                return obtener_carrito_vigente("sesion-cruce-real")
            finally:
                connections.close_all()

        def esperar_espera_postgresql():
            limite = time.monotonic() + 10
            while time.monotonic() < limite:
                with connections["default"].cursor() as cursor:
                    cursor.execute(
                        "SELECT cardinality(pg_blocking_pids(%s)) > 0",
                        (backend_lector["pid"],),
                    )
                    if cursor.fetchone()[0]:
                        return
                time.sleep(0.01)
            self.fail("El lector no llegó a esperar el bloqueo del Carrito.")

        with ThreadPoolExecutor(max_workers=2) as executor:
            bloqueador = executor.submit(mantener_bloqueo)
            self.assertTrue(bloqueo_adquirido.wait(timeout=10))
            with patch("cart.services._ahora", side_effect=ahora_posterior):
                lector = executor.submit(leer_carrito)
                self.assertTrue(backend_lector_listo.wait(timeout=10))
                esperar_espera_postgresql()
                self.assertFalse(reloj_consultado.is_set())
                liberar_bloqueo.set()
                bloqueador.result(timeout=10)
                self.assertIsNone(lector.result(timeout=10))
        self.assertTrue(reloj_consultado.is_set())
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())
