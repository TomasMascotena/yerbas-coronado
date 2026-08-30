from concurrent.futures import ThreadPoolExecutor
import shutil
import tempfile
from threading import Barrier, Event, Lock
import time
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from cart.models import Carrito
from inventory.models import MovimientoInventario, TipoMovimientoInventario
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
        exitosos = [
            (carrito, resultado)
            for carrito, resultado in zip(
                (carrito_a, carrito_b),
                resultados,
            )
            if not isinstance(resultado, Exception)
        ]
        fallidos = [
            carrito
            for carrito, resultado in zip(
                (carrito_a, carrito_b),
                resultados,
            )
            if isinstance(resultado, StockInsuficienteParaPedido)
        ]
        self.assertEqual(len(exitosos), 1)
        self.assertEqual(len(fallidos), 1)
        carrito_ganador, resultado_ganador = exitosos[0]
        carrito_perdedor = fallidos[0]
        self.assertFalse(
            Carrito.objects.filter(pk=carrito_ganador.pk).exists()
        )
        self.assertTrue(
            Carrito.objects.filter(pk=carrito_perdedor.pk).exists()
        )
        item_perdedor = carrito_perdedor.items.get()
        self.assertEqual(item_perdedor.cantidad, 4)
        self.assertFalse(
            Pedido.objects.filter(
                token_idempotencia=carrito_perdedor.token_checkout
            ).exists()
        )
        venta = MovimientoInventario.objects.get(
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        )
        self.assertEqual(venta.pedido, resultado_ganador.pedido)
        self.assertEqual(venta.inventario_id, producto.inventario.pk)
        self.assertEqual(venta.cantidad, 4)

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
        cliente_primero_creado = Event()
        liberar_primero = Event()
        segundo_conectado = Event()
        pids = {}
        obtener_cliente_original = (
            orders_services._obtener_o_crear_cliente_bloqueado
        )

        def obtener_cliente_instrumentado(comprador):
            resultado = obtener_cliente_original(comprador)
            if comprador.nombre == "Ana":
                cliente_primero_creado.set()
                if not liberar_primero.wait(timeout=10):
                    raise TimeoutError("No se liberó el primer Checkout.")
            return resultado

        def confirmar(carrito, *, nombre, apellido, telefono):
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(
                    nombre=nombre,
                    apellido=apellido,
                    telefono=telefono,
                ),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        def ejecutar(clave, funcion):
            close_old_connections()
            try:
                connection.ensure_connection()
                pids[clave] = connection.connection.info.backend_pid
                if clave == "segundo":
                    segundo_conectado.set()
                return funcion()
            except Exception as error:
                return error
            finally:
                close_old_connections()

        with patch(
            "orders.services._obtener_o_crear_cliente_bloqueado",
            side_effect=obtener_cliente_instrumentado,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                primero = executor.submit(
                    ejecutar,
                    "primero",
                    lambda: confirmar(
                        carrito_a,
                        nombre="Ana",
                        apellido="Primera",
                        telefono="111111",
                    ),
                )
                self.assertTrue(cliente_primero_creado.wait(timeout=10))
                segundo = executor.submit(
                    ejecutar,
                    "segundo",
                    lambda: confirmar(
                        carrito_b,
                        nombre="Beatriz",
                        apellido="Segunda",
                        telefono="222222",
                    ),
                )
                self.assertTrue(segundo_conectado.wait(timeout=10))
                self._esperar_backend_bloqueado(pids["segundo"])
                liberar_primero.set()
                resultados = [
                    primero.result(timeout=20),
                    segundo.result(timeout=20),
                ]

        self.assertFalse(any(isinstance(r, Exception) for r in resultados))
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Pedido.objects.count(), 2)
        self.assertEqual(
            {
                (
                    resultado.pedido.nombre_cliente,
                    resultado.pedido.apellido_cliente,
                    resultado.pedido.telefono_cliente,
                )
                for resultado in resultados
            },
            {
                ("Ana", "Primera", "111111"),
                ("Beatriz", "Segunda", "222222"),
            },
        )
        cliente = Cliente.objects.get()
        self.assertEqual(
            (cliente.nombre, cliente.apellido, cliente.telefono),
            ("Beatriz", "Segunda", "222222"),
        )

    def test_checkouts_multiproducto_inversos_usan_orden_canonico_sin_deadlock(self):
        from cart.services import agregar_producto
        from cart.tests.helpers import crear_producto_con_stock

        carrito_a, producto_1 = crear_carrito_checkout(
            session_key="orden-inverso-a",
            nombre="Producto uno inverso",
            cantidad=1,
            stock=4,
        )
        producto_2 = crear_producto_con_stock(
            nombre="Producto dos inverso",
            stock=4,
        )
        agregar_producto(
            session_key=carrito_a.session_key,
            producto_id=producto_2.pk,
            cantidad=1,
        )
        carrito_a.refresh_from_db()

        carrito_b = agregar_producto(
            session_key="orden-inverso-b",
            producto_id=producto_2.pk,
            cantidad=1,
        ).carrito
        agregar_producto(
            session_key=carrito_b.session_key,
            producto_id=producto_1.pk,
            cantidad=1,
        )
        carrito_b.refresh_from_db()

        self.assertEqual(
            tuple(
                carrito_a.items.order_by("pk").values_list(
                    "producto_id",
                    flat=True,
                )
            ),
            (producto_1.pk, producto_2.pk),
        )
        self.assertEqual(
            tuple(
                carrito_b.items.order_by("pk").values_list(
                    "producto_id",
                    flat=True,
                )
            ),
            (producto_2.pk, producto_1.pk),
        )

        barrera = Barrier(2)
        registrar_orden = Lock()
        ordenes_productos = []
        ordenes_inventarios = []
        obtener_productos_original = orders_services._obtener_productos_bloqueados
        obtener_inventarios_original = (
            orders_services._obtener_inventarios_bloqueados
        )

        def obtener_productos_instrumentado(items):
            productos = obtener_productos_original(items)
            with registrar_orden:
                ordenes_productos.append(tuple(productos))
            return productos

        def obtener_inventarios_instrumentado(productos):
            inventarios = obtener_inventarios_original(productos)
            with registrar_orden:
                ordenes_inventarios.append(tuple(inventarios))
            return inventarios

        def confirmar(carrito, dni):
            return crear_pedido_desde_carrito(
                session_key=carrito.session_key,
                token_idempotencia=carrito.token_checkout,
                datos_comprador=datos_comprador(dni=dni),
                modalidad_entrega=ModalidadEntrega.RETIRO,
            )

        with patch(
            "orders.services._obtener_productos_bloqueados",
            side_effect=obtener_productos_instrumentado,
        ), patch(
            "orders.services._obtener_inventarios_bloqueados",
            side_effect=obtener_inventarios_instrumentado,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futuros = (
                    executor.submit(
                        self._ejecutar_en_thread,
                        barrera,
                        lambda: confirmar(carrito_a, "20.000.001"),
                    ),
                    executor.submit(
                        self._ejecutar_en_thread,
                        barrera,
                        lambda: confirmar(carrito_b, "20.000.002"),
                    ),
                )
                resultados = [f.result(timeout=20) for f in futuros]

        self.assertFalse(any(isinstance(r, Exception) for r in resultados))
        self.assertEqual(Pedido.objects.count(), 2)
        self.assertEqual(
            MovimientoInventario.objects.filter(
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
            ).count(),
            4,
        )
        for producto in (producto_1, producto_2):
            producto.inventario.refresh_from_db()
            self.assertEqual(producto.inventario.cantidad_disponible, 2)
        self.assertEqual(
            ordenes_productos,
            [(producto_1.pk, producto_2.pk)] * 2,
        )
        inventarios_esperados = tuple(
            sorted((producto_1.inventario.pk, producto_2.inventario.pk))
        )
        self.assertEqual(
            ordenes_inventarios,
            [inventarios_esperados] * 2,
        )

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
