from concurrent.futures import ThreadPoolExecutor
import shutil
import tempfile
from threading import Barrier, Event, Lock
import time
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from cart.models import Carrito
from orders.exceptions import StockInsuficienteParaPedido, TransicionPedidoInvalida
from orders.models import EstadoPedido, ModalidadEntrega, Pedido
from orders.models import Cliente
from orders.services import (
    cancelar_pedido,
    crear_pedido_desde_carrito,
    marcar_pedido_entregado,
)
from orders.tests.helpers import (
    crear_carrito_checkout,
    crear_pedido_de_prueba,
    datos_comprador,
)
import orders.services as orders_services


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ConcurrenciaPedidoTests(TransactionTestCase):
    reset_sequences = True

    def _ejecutar_en_thread(self, barrera, funcion):
        close_old_connections()
        try:
            barrera.wait(timeout=10)
            return funcion()
        except Exception as error:
            return error
        finally:
            close_old_connections()

    def _esperar_backend_bloqueado(self, backend_pid):
        limite = time.monotonic() + 10
        while time.monotonic() < limite:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT cardinality(pg_blocking_pids(%s)) > 0",
                    (backend_pid,),
                )
                if cursor.fetchone()[0]:
                    return
            time.sleep(0.01)
        self.fail("El backend no llegó a esperar un bloqueo PostgreSQL.")

    def test_mismo_token_fuerza_espera_y_reconsulta_despues_del_commit(self):
        carrito, producto = crear_carrito_checkout(cantidad=4, stock=10)
        argumentos = {
            "session_key": carrito.session_key,
            "token_idempotencia": carrito.token_checkout,
            "datos_comprador": datos_comprador(),
            "modalidad_entrega": ModalidadEntrega.RETIRO,
        }
        consultas_iniciales = Barrier(2)
        pids_listos = (Event(), Event())
        ganador_bloqueado = Event()
        liberar_ganador = Event()
        sincronizacion = Lock()
        pids = {}
        ganador = {"pid": None}
        consultas_por_pid = {}
        primera_ausencia_por_pid = {}
        buscar_original = orders_services._buscar_pedido_por_token
        bloquear_original = orders_services._bloquear_carrito_para_checkout

        def buscar_instrumentado(token):
            pid = connection.connection.info.backend_pid
            consultas_por_pid[pid] = consultas_por_pid.get(pid, 0) + 1
            resultado = buscar_original(token)
            if consultas_por_pid[pid] == 1:
                primera_ausencia_por_pid[pid] = resultado is None
                consultas_iniciales.wait(timeout=10)
            return resultado

        def bloquear_instrumentado(session_key):
            resultado = bloquear_original(session_key)
            if resultado is not None:
                pid = connection.connection.info.backend_pid
                with sincronizacion:
                    if ganador["pid"] is None:
                        ganador["pid"] = pid
                        ganador_bloqueado.set()
                        es_ganador = True
                    else:
                        es_ganador = False
                if es_ganador:
                    liberar_ganador.wait(timeout=10)
            return resultado

        def ejecutar(indice):
            close_old_connections()
            try:
                connection.ensure_connection()
                pids[indice] = connection.connection.info.backend_pid
                pids_listos[indice].set()
                return crear_pedido_desde_carrito(**argumentos)
            except Exception as error:
                return error
            finally:
                close_old_connections()

        with patch(
            "orders.services._buscar_pedido_por_token",
            side_effect=buscar_instrumentado,
        ), patch(
            "orders.services._bloquear_carrito_para_checkout",
            side_effect=bloquear_instrumentado,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futuros = [executor.submit(ejecutar, indice) for indice in range(2)]
                self.assertTrue(pids_listos[0].wait(timeout=10))
                self.assertTrue(pids_listos[1].wait(timeout=10))
                self.assertTrue(ganador_bloqueado.wait(timeout=10))
                perdedor_pid = next(
                    pid for pid in pids.values() if pid != ganador["pid"]
                )
                self._esperar_backend_bloqueado(perdedor_pid)
                liberar_ganador.set()
                resultados = [f.result(timeout=20) for f in futuros]

        self.assertFalse(any(isinstance(r, Exception) for r in resultados))
        self.assertTrue(all(primera_ausencia_por_pid.values()))
        self.assertEqual(set(primera_ausencia_por_pid), set(pids.values()))
        self.assertGreaterEqual(consultas_por_pid[perdedor_pid], 2)
        self.assertEqual(sorted(r.creado for r in resultados), [False, True])
        self.assertEqual(Pedido.objects.count(), 1)
        pedido = Pedido.objects.get()
        self.assertEqual(pedido.detalles.count(), 1)
        self.assertEqual(pedido.movimientos_inventario.count(), 1)
        self.assertFalse(Carrito.objects.exists())
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 6)

    def test_dos_carritos_compiten_por_stock_y_solo_uno_confirma(self):
        carrito_a, producto = crear_carrito_checkout(
            session_key="sesion-stock-a", cantidad=4, stock=5
        )
        from cart.services import agregar_producto

        item_b = agregar_producto(
            session_key="sesion-stock-b", producto_id=producto.pk, cantidad=4
        )
        carrito_b = item_b.carrito
        barrera = Barrier(2)

        def confirmar(carrito):
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(
                    dni="20.000.00" + ("1" if carrito.pk == carrito_a.pk else "2")
                ),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = [
                executor.submit(
                    self._ejecutar_en_thread,
                    barrera,
                    lambda carrito=c: confirmar(carrito),
                )
                for c in (carrito_a, carrito_b)
            ]
            resultados = [f.result(timeout=20) for f in resultados]

        self.assertEqual(sum(not isinstance(r, Exception) for r in resultados), 1)
        self.assertEqual(
            sum(isinstance(r, StockInsuficienteParaPedido) for r in resultados), 1
        )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 1)
        self.assertEqual(Pedido.objects.count(), 1)

    def test_entrega_y_cancelacion_concurrentes_tienen_un_solo_ganador(self):
        pedido, producto = crear_pedido_de_prueba(cantidad=2, stock=10)
        barrera = Barrier(2)
        operaciones = (
            lambda: marcar_pedido_entregado(pedido_id=pedido.pk),
            lambda: cancelar_pedido(pedido_id=pedido.pk),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = [
                executor.submit(self._ejecutar_en_thread, barrera, operacion)
                for operacion in operaciones
            ]
            resultados = [f.result(timeout=20) for f in resultados]
        self.assertEqual(sum(not isinstance(r, Exception) for r in resultados), 1)
        self.assertEqual(
            sum(isinstance(r, TransicionPedidoInvalida) for r in resultados), 1
        )
        pedido.refresh_from_db()
        self.assertIn(pedido.estado, (EstadoPedido.ENTREGADO, EstadoPedido.CANCELADO))
        producto.inventario.refresh_from_db()
        esperado = 8 if pedido.estado == EstadoPedido.ENTREGADO else 10
        self.assertEqual(producto.inventario.cantidad_disponible, esperado)

    def test_dos_checkouts_con_mismo_dni_reutilizan_un_cliente(self):
        carrito_a, _ = crear_carrito_checkout(
            session_key="sesion-dni-a", nombre="Canarias DNI"
        )
        carrito_b, _ = crear_carrito_checkout(
            session_key="sesion-dni-b", nombre="Baldo DNI"
        )
        barrera = Barrier(2)

        def confirmar(carrito, nombre):
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(nombre=nombre),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = (
                executor.submit(
                    self._ejecutar_en_thread,
                    barrera,
                    lambda: confirmar(carrito_a, "Ana"),
                ),
                executor.submit(
                    self._ejecutar_en_thread,
                    barrera,
                    lambda: confirmar(carrito_b, "Beatriz"),
                ),
            )
            resultados = [f.result(timeout=20) for f in futuros]
        self.assertFalse(any(isinstance(r, Exception) for r in resultados))
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(
            {r.pedido.nombre_cliente for r in resultados}, {"Ana", "Beatriz"}
        )
        self.assertIn(Cliente.objects.get().nombre, {"Ana", "Beatriz"})

    def test_checkout_y_venta_presencial_serializan_el_mismo_stock(self):
        carrito, producto = crear_carrito_checkout(
            session_key="sesion-cruce-inventario", cantidad=4, stock=5
        )
        from inventory.exceptions import StockInsuficiente
        from inventory.services import registrar_venta_presencial

        barrera = Barrier(2)

        def checkout():
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        def venta():
            return registrar_venta_presencial(
                inventario_id=producto.inventario.pk,
                cantidad=4,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = (
                executor.submit(self._ejecutar_en_thread, barrera, checkout),
                executor.submit(self._ejecutar_en_thread, barrera, venta),
            )
            resultados = [f.result(timeout=20) for f in futuros]
        self.assertEqual(sum(not isinstance(r, Exception) for r in resultados), 1)
        self.assertEqual(
            sum(
                isinstance(r, (StockInsuficiente, StockInsuficienteParaPedido))
                for r in resultados
            ),
            1,
        )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 1)

    def test_checkout_y_ajuste_negativo_serializan_el_mismo_stock(self):
        carrito, producto = crear_carrito_checkout(
            session_key="sesion-ajuste-negativo", cantidad=4, stock=5
        )
        from inventory.exceptions import StockInsuficiente
        from inventory.services import registrar_ajuste_negativo

        barrera = Barrier(2)

        def checkout():
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        def ajuste():
            return registrar_ajuste_negativo(
                inventario_id=producto.inventario.pk,
                cantidad=4,
                observacion="Rotura",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = (
                executor.submit(self._ejecutar_en_thread, barrera, checkout),
                executor.submit(self._ejecutar_en_thread, barrera, ajuste),
            )
            resultados = [f.result(timeout=20) for f in futuros]
        self.assertEqual(sum(not isinstance(r, Exception) for r in resultados), 1)
        self.assertEqual(
            sum(
                isinstance(r, (StockInsuficiente, StockInsuficienteParaPedido))
                for r in resultados
            ),
            1,
        )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 1)

    def test_checkout_y_ajuste_positivo_conservan_ambos_efectos(self):
        carrito, producto = crear_carrito_checkout(
            session_key="sesion-ajuste-positivo", cantidad=8, stock=10
        )
        from inventory.services import registrar_ajuste_positivo

        barrera = Barrier(2)

        def checkout():
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        def ajuste():
            return registrar_ajuste_positivo(
                inventario_id=producto.inventario.pk,
                cantidad=5,
                observacion="Reconteo",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = (
                executor.submit(self._ejecutar_en_thread, barrera, checkout),
                executor.submit(self._ejecutar_en_thread, barrera, ajuste),
            )
            resultados = [f.result(timeout=20) for f in futuros]
        self.assertFalse(any(isinstance(r, Exception) for r in resultados))
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 7)

    def test_dos_cancelaciones_concurrentes_reponen_una_sola_vez(self):
        pedido, producto = crear_pedido_de_prueba(
            session_key="doble-cancelacion", cantidad=3, stock=10
        )
        barrera = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = (
                executor.submit(
                    self._ejecutar_en_thread,
                    barrera,
                    lambda: cancelar_pedido(pedido_id=pedido.pk),
                ),
                executor.submit(
                    self._ejecutar_en_thread,
                    barrera,
                    lambda: cancelar_pedido(pedido_id=pedido.pk),
                ),
            )
            resultados = [f.result(timeout=20) for f in futuros]
        self.assertEqual(sum(not isinstance(r, Exception) for r in resultados), 1)
        self.assertEqual(
            sum(isinstance(r, TransicionPedidoInvalida) for r in resultados), 1
        )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 10)
        self.assertEqual(
            pedido.movimientos_inventario.filter(
                tipo_movimiento="CANCELACION_PEDIDO"
            ).count(),
            1,
        )
