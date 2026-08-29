import shutil
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings

import inventory.services
from inventory.exceptions import (
    CapacidadInventarioExcedida,
    CantidadMovimientoInvalida,
    ObservacionObligatoria,
    StockInsuficiente,
)
from inventory.models import (
    Inventario,
    MovimientoInventario,
    TipoMovimientoInventario,
)
from inventory.services import (
    MAX_BIGINT_POSITIVO,
    registrar_ajuste_negativo,
    registrar_ajuste_positivo,
    registrar_ingreso_mercaderia,
    registrar_venta_presencial,
)
from inventory.tests.helpers import crear_inventario_de_prueba


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class MovimientosInventarioServiceTests(TestCase):
    def setUp(self):
        self.inventario = crear_inventario_de_prueba()

    def test_ingreso_suma_stock_y_genera_exactamente_un_movimiento(self):
        movimiento = registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 5)
        self.assertEqual(MovimientoInventario.objects.count(), 1)
        self.assertEqual(
            movimiento.tipo_movimiento,
            TipoMovimientoInventario.INGRESO_MERCADERIA,
        )
        self.assertEqual(movimiento.cantidad, 5)

    def test_ingreso_normaliza_observaciones_opcionales(self):
        for indice, (observacion, esperada) in enumerate(
            (
                (None, ""),
                ("", ""),
                ("   ", ""),
                ("  Lote recibido  ", "Lote recibido"),
            )
        ):
            with self.subTest(observacion=observacion):
                movimiento = registrar_ingreso_mercaderia(
                    inventario_id=self.inventario.pk,
                    cantidad=1,
                    observacion=observacion,
                )
                self.assertEqual(movimiento.observacion, esperada)
                self.assertEqual(MovimientoInventario.objects.count(), indice + 1)

    def test_venta_presencial_resta_y_genera_movimiento_absoluto(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        movimiento = registrar_venta_presencial(
            inventario_id=self.inventario.pk,
            cantidad=3,
        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 2)
        self.assertEqual(
            movimiento.tipo_movimiento,
            TipoMovimientoInventario.VENTA_PRESENCIAL,
        )
        self.assertEqual(movimiento.cantidad, 3)

    def test_venta_presencial_normaliza_observaciones_opcionales(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=4,
        )
        for observacion, esperada in (
            (None, ""),
            ("", ""),
            ("   ", ""),
            ("  Venta en feria  ", "Venta en feria"),
        ):
            with self.subTest(observacion=observacion):
                movimiento = registrar_venta_presencial(
                    inventario_id=self.inventario.pk,
                    cantidad=1,
                    observacion=observacion,
                )
                self.assertEqual(movimiento.observacion, esperada)

    def test_venta_presencial_puede_dejar_stock_exactamente_en_cero(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        registrar_venta_presencial(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 0)

    def test_venta_sin_stock_no_modifica_inventario_ni_crea_movimiento(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        with self.assertRaises(StockInsuficiente):
            registrar_venta_presencial(
                inventario_id=self.inventario.pk,
                cantidad=6,
            )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 5)
        self.assertEqual(MovimientoInventario.objects.count(), 1)

    def test_ajuste_positivo_suma_y_normaliza_observacion(self):
        movimiento = registrar_ajuste_positivo(
            inventario_id=self.inventario.pk,
            cantidad=2,
            observacion="  Sobrante de conteo  ",
        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 2)
        self.assertEqual(
            movimiento.tipo_movimiento,
            TipoMovimientoInventario.AJUSTE_POSITIVO,
        )
        self.assertEqual(movimiento.observacion, "Sobrante de conteo")

    def test_ajuste_negativo_resta_y_normaliza_observacion(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        movimiento = registrar_ajuste_negativo(
            inventario_id=self.inventario.pk,
            cantidad=2,
            observacion="  Rotura de paquete  ",
        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 3)
        self.assertEqual(
            movimiento.tipo_movimiento,
            TipoMovimientoInventario.AJUSTE_NEGATIVO,
        )
        self.assertEqual(movimiento.observacion, "Rotura de paquete")

    def test_ajuste_negativo_puede_dejar_stock_exactamente_en_cero(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        registrar_ajuste_negativo(
            inventario_id=self.inventario.pk,
            cantidad=5,
            observacion="Diferencia de conteo",
        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 0)

    def test_ajustes_rechazan_observacion_ausente_vacia_o_solo_espacios(self):
        for servicio in (registrar_ajuste_positivo, registrar_ajuste_negativo):
            for observacion in (None, "", "   "):
                with self.subTest(
                    servicio=servicio.__name__,
                    observacion=observacion,
                ):
                    with self.assertRaises(ObservacionObligatoria):
                        servicio(
                            inventario_id=self.inventario.pk,
                            cantidad=1,
                            observacion=observacion,
                        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 0)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_ajuste_negativo_insuficiente_no_deja_cambios_parciales(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=2,
        )

        with self.assertRaises(StockInsuficiente):
            registrar_ajuste_negativo(
                inventario_id=self.inventario.pk,
                cantidad=3,
                observacion="Faltante",
            )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 2)
        self.assertEqual(MovimientoInventario.objects.count(), 1)

    def test_servicios_rechazan_cantidades_que_no_son_enteros_positivos(self):
        servicios = (
            registrar_ingreso_mercaderia,
            registrar_venta_presencial,
            registrar_ajuste_positivo,
            registrar_ajuste_negativo,
        )
        for servicio in servicios:
            for cantidad in (0, -1, True, False, "3", 3.0, None):
                with self.subTest(
                    servicio=servicio.__name__,
                    cantidad=cantidad,
                ):
                    with self.assertRaises(CantidadMovimientoInvalida):
                        servicio(
                            inventario_id=self.inventario.pk,
                            cantidad=cantidad,
                            observacion="Observación válida",
                        )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 0)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_servicios_rechazan_cantidad_superior_a_bigint_sin_efectos(self):
        servicios = (
            registrar_ingreso_mercaderia,
            registrar_venta_presencial,
            registrar_ajuste_positivo,
            registrar_ajuste_negativo,
        )
        for servicio in servicios:
            with self.subTest(servicio=servicio.__name__):
                with self.assertRaises(CapacidadInventarioExcedida):
                    servicio(
                        inventario_id=self.inventario.pk,
                        cantidad=2**63,
                        observacion="Observación válida",
                    )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 0)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_movimiento_positivo_rechaza_overflow_y_revierte_completo(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )
        movimientos_iniciales = MovimientoInventario.objects.count()
        cantidad = MAX_BIGINT_POSITIVO - 5 + 1

        for servicio in (
            registrar_ingreso_mercaderia,
            registrar_ajuste_positivo,
        ):
            with self.subTest(servicio=servicio.__name__):
                with self.assertRaises(CapacidadInventarioExcedida):
                    servicio(
                        inventario_id=self.inventario.pk,
                        cantidad=cantidad,
                        observacion="Reconteo",
                    )

                self.inventario.refresh_from_db()
                self.assertEqual(self.inventario.cantidad_disponible, 5)
                self.assertEqual(
                    MovimientoInventario.objects.count(),
                    movimientos_iniciales,
                )

    def test_fallo_al_crear_movimiento_revierte_el_stock(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
        )

        with patch.object(MovimientoInventario, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                registrar_venta_presencial(
                    inventario_id=self.inventario.pk,
                    cantidad=2,
                )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 5)
        self.assertEqual(MovimientoInventario.objects.count(), 1)

    def test_fallo_al_guardar_inventario_no_crea_movimiento(self):
        with patch.object(Inventario, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                registrar_ingreso_mercaderia(
                    inventario_id=self.inventario.pk,
                    cantidad=2,
                )

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.cantidad_disponible, 0)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_tipos_de_pedido_no_tienen_servicios_publicos_provisionales(self):
        self.assertFalse(hasattr(inventory.services, "registrar_venta_pedido"))
        self.assertFalse(
            hasattr(inventory.services, "registrar_cancelacion_pedido")
        )
