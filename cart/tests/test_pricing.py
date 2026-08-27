from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
import shutil
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from cart.models import Carrito, ItemCarrito
from cart.pricing import (
    EscalaPrecio,
    EstadoPrecioCarritoInvalido,
    LineaCalculadaCarrito,
    ResumenCarrito,
    calcular_resumen,
    calcular_subtotal,
    determinar_escala,
    seleccionar_precio_snapshot,
)
from cart.services import (
    DURACION_CARRITO,
    agregar_producto,
    eliminar_item,
    obtener_resumen_carrito,
)
from cart.tests.helpers import crear_producto_con_stock
from catalog.models import Producto
from inventory.models import MovimientoInventario
from inventory.services import registrar_venta_presencial


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


def item_puro(
    *,
    pk=1,
    producto_id=1,
    cantidad=1,
    unitario=Decimal("5000.00"),
    desde_3=Decimal("4500.00"),
    desde_20=Decimal("4000.00"),
):
    return SimpleNamespace(
        pk=pk,
        producto_id=producto_id,
        cantidad=cantidad,
        precio_unitario_snapshot=unitario,
        precio_desde_3_snapshot=desde_3,
        precio_desde_20_snapshot=desde_20,
    )


class MotorPrecioPuroTests(TestCase):
    def test_enumeracion_contiene_exactamente_las_tres_escalas(self):
        self.assertEqual(
            tuple(escala.value for escala in EscalaPrecio),
            ("UNITARIO", "DESDE_3", "DESDE_20"),
        )

    def test_determina_los_umbrales_exactos(self):
        casos = (
            (0, None),
            (1, EscalaPrecio.UNITARIO),
            (2, EscalaPrecio.UNITARIO),
            (3, EscalaPrecio.DESDE_3),
            (19, EscalaPrecio.DESDE_3),
            (20, EscalaPrecio.DESDE_20),
            (21, EscalaPrecio.DESDE_20),
            (100, EscalaPrecio.DESDE_20),
        )
        for cantidad, esperada in casos:
            with self.subTest(cantidad=cantidad):
                self.assertEqual(determinar_escala(cantidad), esperada)

    def test_determinar_escala_rechaza_negativos_booleanos_y_no_enteros(self):
        for cantidad in (-1, True, False, "3", 3.0, None):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(EstadoPrecioCarritoInvalido):
                    determinar_escala(cantidad)

    def test_cada_escala_selecciona_su_snapshot(self):
        item = item_puro()
        casos = (
            (EscalaPrecio.UNITARIO, Decimal("5000.00")),
            (EscalaPrecio.DESDE_3, Decimal("4500.00")),
            (EscalaPrecio.DESDE_20, Decimal("4000.00")),
        )
        for escala, precio in casos:
            with self.subTest(escala=escala):
                self.assertEqual(
                    seleccionar_precio_snapshot(item, escala),
                    precio,
                )

    def test_escala_desconocida_se_rechaza(self):
        with self.assertRaises(EstadoPrecioCarritoInvalido):
            seleccionar_precio_snapshot(item_puro(), "DESCONOCIDA")

    def test_subtotal_multiplica_y_cuantiza_con_half_up(self):
        subtotal = calcular_subtotal(
            precio_aplicado=Decimal("10.005"),
            cantidad=3,
        )
        self.assertEqual(subtotal, Decimal("30.02"))
        self.assertIsInstance(subtotal, Decimal)
        self.assertEqual(subtotal.as_tuple().exponent, -2)

    def test_motor_rechaza_cantidad_de_item_invalida(self):
        for cantidad in (0, -1, True, "1", 1.0):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(EstadoPrecioCarritoInvalido):
                    calcular_resumen(
                        carrito_id=1,
                        items=(item_puro(cantidad=cantidad),),
                    )

    def test_motor_rechaza_snapshots_no_positivos_o_no_decimal(self):
        for precio in (Decimal("0.00"), Decimal("-1.00"), 10.0, "10.00"):
            with self.subTest(precio=precio):
                with self.assertRaises(EstadoPrecioCarritoInvalido):
                    calcular_resumen(
                        carrito_id=1,
                        items=(item_puro(desde_3=precio),),
                    )

    def test_resumen_puro_usa_una_escala_global_y_decimal(self):
        resumen = calcular_resumen(
            carrito_id=7,
            items=(
                item_puro(pk=2, producto_id=2, cantidad=1),
                item_puro(pk=1, producto_id=1, cantidad=2),
            ),
        )

        self.assertEqual(resumen.escala_aplicada, EscalaPrecio.DESDE_3)
        self.assertEqual([linea.item_id for linea in resumen.lineas], [1, 2])
        self.assertTrue(
            all(
                linea.precio_aplicado == Decimal("4500.00")
                for linea in resumen.lineas
            )
        )
        self.assertEqual(resumen.importe_total, Decimal("13500.00"))
        self.assertIsInstance(resumen.importe_total, Decimal)
        self.assertEqual(resumen.importe_total.as_tuple().exponent, -2)

    def test_carrito_vacio_tiene_resultado_monetario_coherente(self):
        resumen = calcular_resumen(carrito_id=9, items=())
        self.assertEqual(
            resumen,
            ResumenCarrito(
                carrito_id=9,
                cantidad_lineas=0,
                cantidad_total_unidades=0,
                escala_aplicada=None,
                lineas=(),
                importe_total=Decimal("0.00"),
            ),
        )

    def test_objetos_de_resultado_son_inmutables(self):
        linea = LineaCalculadaCarrito(
            item_id=1,
            producto_id=1,
            cantidad=1,
            precio_aplicado=Decimal("1.00"),
            subtotal=Decimal("1.00"),
        )
        resumen = calcular_resumen(carrito_id=None, items=())
        with self.assertRaises(FrozenInstanceError):
            linea.cantidad = 2
        with self.assertRaises(FrozenInstanceError):
            resumen.importe_total = Decimal("1.00")

    def test_coleccion_se_materializa_una_sola_vez(self):
        class ColeccionContada:
            def __init__(self):
                self.iteraciones = 0

            def __iter__(self):
                self.iteraciones += 1
                return iter((item_puro(),))

        items = ColeccionContada()
        resumen = calcular_resumen(carrito_id=1, items=items)
        self.assertEqual(items.iteraciones, 1)
        self.assertEqual(resumen.cantidad_total_unidades, 1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ResumenCarritoIntegracionTests(TestCase):
    def setUp(self):
        self.session_key = "sesion-resumen"
        self.producto = crear_producto_con_stock(stock=30)

    def agregar(self, *, producto=None, cantidad=1):
        return agregar_producto(
            session_key=self.session_key,
            producto_id=(producto or self.producto).pk,
            cantidad=cantidad,
        )

    def test_sesion_sin_carrito_devuelve_resumen_vacio_sin_id(self):
        resumen = obtener_resumen_carrito("sesion-inexistente")
        self.assertIsNone(resumen.carrito_id)
        self.assertEqual(resumen.cantidad_lineas, 0)
        self.assertEqual(resumen.cantidad_total_unidades, 0)
        self.assertIsNone(resumen.escala_aplicada)
        self.assertEqual(resumen.lineas, ())
        self.assertEqual(resumen.importe_total, Decimal("0.00"))

    def test_carrito_persistido_vacio_conserva_su_id(self):
        carrito = Carrito.objects.create(session_key=self.session_key)
        resumen = obtener_resumen_carrito(self.session_key)
        self.assertEqual(resumen.carrito_id, carrito.pk)
        self.assertEqual(resumen.cantidad_lineas, 0)
        self.assertEqual(resumen.importe_total, Decimal("0.00"))

    def test_una_linea_calcula_cantidad_precio_y_subtotal(self):
        item = self.agregar(cantidad=2)
        resumen = obtener_resumen_carrito(self.session_key)
        linea = resumen.lineas[0]

        self.assertEqual(resumen.cantidad_lineas, 1)
        self.assertEqual(resumen.cantidad_total_unidades, 2)
        self.assertEqual(resumen.escala_aplicada, EscalaPrecio.UNITARIO)
        self.assertEqual(linea.item_id, item.pk)
        self.assertEqual(linea.producto_id, self.producto.pk)
        self.assertEqual(linea.cantidad, 2)
        self.assertEqual(linea.precio_aplicado, Decimal("5000.00"))
        self.assertEqual(linea.subtotal, Decimal("10000.00"))
        self.assertEqual(resumen.importe_total, Decimal("10000.00"))

    def test_varias_lineas_comparten_escala_global_desde_3(self):
        self.agregar(cantidad=2)
        baldo = crear_producto_con_stock(nombre="Baldo", stock=30)
        self.agregar(producto=baldo, cantidad=1)

        resumen = obtener_resumen_carrito(self.session_key)

        self.assertEqual(resumen.cantidad_lineas, 2)
        self.assertEqual(resumen.cantidad_total_unidades, 3)
        self.assertEqual(resumen.escala_aplicada, EscalaPrecio.DESDE_3)
        self.assertTrue(
            all(
                linea.precio_aplicado == Decimal("4500.00")
                for linea in resumen.lineas
            )
        )
        self.assertEqual(resumen.importe_total, Decimal("13500.00"))

    def test_composicion_de_veinte_unidades_usa_desde_20(self):
        self.agregar(cantidad=10)
        baldo = crear_producto_con_stock(nombre="Baldo", stock=30)
        self.agregar(producto=baldo, cantidad=10)

        resumen = obtener_resumen_carrito(self.session_key)

        self.assertEqual(resumen.cantidad_total_unidades, 20)
        self.assertEqual(resumen.escala_aplicada, EscalaPrecio.DESDE_20)
        self.assertEqual(resumen.importe_total, Decimal("80000.00"))

    def test_lineas_se_ordenan_por_pk_y_lineas_no_equivale_a_unidades(self):
        primero = self.agregar(cantidad=4)
        baldo = crear_producto_con_stock(nombre="Baldo", stock=30)
        segundo = self.agregar(producto=baldo, cantidad=2)

        resumen = obtener_resumen_carrito(self.session_key)

        self.assertEqual(
            [linea.item_id for linea in resumen.lineas],
            [primero.pk, segundo.pk],
        )
        self.assertEqual(resumen.cantidad_lineas, 2)
        self.assertEqual(resumen.cantidad_total_unidades, 6)

    def test_cambiar_precios_actuales_no_afecta_snapshots_ni_resumen(self):
        item = self.agregar(cantidad=3)
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

        resumen = obtener_resumen_carrito(self.session_key)
        item.refresh_from_db()

        self.assertEqual(
            (
                item.precio_unitario_snapshot,
                item.precio_desde_3_snapshot,
                item.precio_desde_20_snapshot,
            ),
            snapshots,
        )
        self.assertEqual(
            resumen.lineas[0].precio_aplicado,
            Decimal("4500.00"),
        )

    def test_incrementar_cantidad_conserva_snapshots_y_recalcula_escala(self):
        item = self.agregar(cantidad=2)
        snapshots = (
            item.precio_unitario_snapshot,
            item.precio_desde_3_snapshot,
            item.precio_desde_20_snapshot,
        )

        item = self.agregar(cantidad=1)
        resumen = obtener_resumen_carrito(self.session_key)

        self.assertEqual(
            (
                item.precio_unitario_snapshot,
                item.precio_desde_3_snapshot,
                item.precio_desde_20_snapshot,
            ),
            snapshots,
        )
        self.assertEqual(resumen.escala_aplicada, EscalaPrecio.DESDE_3)
        self.assertEqual(
            resumen.lineas[0].precio_aplicado,
            item.precio_desde_3_snapshot,
        )

    def test_cambio_de_cantidad_recalcula_y_reutiliza_snapshots(self):
        canarias = self.agregar(cantidad=2)
        resumen_unitario = obtener_resumen_carrito(self.session_key)
        baldo = crear_producto_con_stock(nombre="Baldo", stock=30)
        item_baldo = self.agregar(producto=baldo, cantidad=1)
        resumen_desde_3 = obtener_resumen_carrito(self.session_key)
        eliminar_item(session_key=self.session_key, item_id=item_baldo.pk)
        resumen_final = obtener_resumen_carrito(self.session_key)

        self.assertEqual(
            resumen_unitario.lineas[0].precio_aplicado,
            canarias.precio_unitario_snapshot,
        )
        self.assertTrue(
            all(
                linea.precio_aplicado == Decimal("4500.00")
                for linea in resumen_desde_3.lineas
            )
        )
        self.assertEqual(
            resumen_final.lineas[0].precio_aplicado,
            canarias.precio_unitario_snapshot,
        )

    def test_eliminar_y_reagregar_utiliza_snapshots_nuevos(self):
        item = self.agregar()
        eliminar_item(session_key=self.session_key, item_id=item.pk)
        Producto.objects.filter(pk=self.producto.pk).update(
            precio_unitario=Decimal("6100.00"),
            precio_desde_3=Decimal("5600.00"),
            precio_desde_20=Decimal("5100.00"),
        )
        nuevo = self.agregar()

        resumen = obtener_resumen_carrito(self.session_key)

        self.assertNotEqual(item.pk, nuevo.pk)
        self.assertEqual(
            resumen.lineas[0].precio_aplicado,
            Decimal("6100.00"),
        )

    def test_productos_pueden_tener_snapshots_sin_relacion_matematica(self):
        self.agregar(cantidad=2)
        baldo = crear_producto_con_stock(nombre="Baldo", stock=30)
        Producto.objects.filter(pk=baldo.pk).update(
            precio_unitario=Decimal("3000.00"),
            precio_desde_3=Decimal("3500.00"),
            precio_desde_20=Decimal("4500.00"),
        )
        self.agregar(producto=baldo, cantidad=1)

        resumen = obtener_resumen_carrito(self.session_key)
        precios = {linea.producto_id: linea.precio_aplicado for linea in resumen.lineas}

        self.assertEqual(precios[self.producto.pk], Decimal("4500.00"))
        self.assertEqual(precios[baldo.pk], Decimal("3500.00"))

    def test_producto_inactivo_permanece_y_conserva_calculo(self):
        item = self.agregar(cantidad=2)
        self.producto.activo = False
        self.producto.save(update_fields=("activo",))

        resumen = obtener_resumen_carrito(self.session_key)

        self.assertEqual(len(resumen.lineas), 1)
        self.assertEqual(resumen.lineas[0].item_id, item.pk)
        self.assertEqual(resumen.lineas[0].subtotal, Decimal("10000.00"))

    def test_stock_cero_no_modifica_cantidad_precio_ni_linea(self):
        item = self.agregar(cantidad=2)
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        registrar_venta_presencial(
            inventario_id=inventario.pk,
            cantidad=inventario.cantidad_disponible,
        )

        resumen = obtener_resumen_carrito(self.session_key)

        item.refresh_from_db()
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 0)
        self.assertEqual(item.cantidad, 2)
        self.assertEqual(len(resumen.lineas), 1)
        self.assertEqual(resumen.lineas[0].precio_aplicado, Decimal("5000.00"))

    def test_resumen_no_modifica_inventario_movimientos_ni_actividad(self):
        item = self.agregar(cantidad=2)
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        stock = inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        actividad = item.carrito.ultima_actividad

        obtener_resumen_carrito(self.session_key)

        inventario.refresh_from_db()
        item.carrito.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)
        self.assertEqual(item.carrito.ultima_actividad, actividad)

    def test_carrito_con_menos_de_seis_horas_sigue_vigente(self):
        item = self.agregar()
        ahora = timezone.now()
        actividad = ahora - DURACION_CARRITO + timedelta(seconds=1)
        Carrito.objects.filter(pk=item.carrito_id).update(
            ultima_actividad=actividad
        )

        with patch("cart.services._ahora", return_value=ahora):
            resumen = obtener_resumen_carrito(self.session_key)

        self.assertEqual(resumen.carrito_id, item.carrito_id)
        self.assertEqual(len(resumen.lineas), 1)

    def test_carrito_en_seis_horas_expira_sin_efectos_de_inventario(self):
        item = self.agregar()
        inventario = self.producto.inventario
        inventario.refresh_from_db()
        stock = inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        ahora = timezone.now()
        Carrito.objects.filter(pk=item.carrito_id).update(
            ultima_actividad=ahora - DURACION_CARRITO
        )

        with patch("cart.services._ahora", return_value=ahora):
            resumen = obtener_resumen_carrito(self.session_key)

        inventario.refresh_from_db()
        self.assertIsNone(resumen.carrito_id)
        self.assertEqual(resumen.lineas, ())
        self.assertEqual(resumen.importe_total, Decimal("0.00"))
        self.assertFalse(Carrito.objects.filter(pk=item.carrito_id).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=item.pk).exists())
        self.assertEqual(inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_consultas_son_constantes_y_no_bloquean_ni_cargan_producto(self):
        self.agregar(cantidad=2)
        for nombre in ("Baldo", "Playadito"):
            producto = crear_producto_con_stock(nombre=nombre, stock=30)
            self.agregar(producto=producto, cantidad=1)

        with CaptureQueriesContext(connection) as consultas:
            resumen = obtener_resumen_carrito(self.session_key)

        sql = " ".join(consulta["sql"] for consulta in consultas.captured_queries)
        self.assertEqual(len(consultas), 2)
        self.assertEqual(resumen.cantidad_lineas, 3)
        self.assertNotIn("FOR UPDATE", sql.upper())
        self.assertNotIn("catalog_producto", sql)
        self.assertNotIn("inventory_inventario", sql)
