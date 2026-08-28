import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from inventory.models import MovimientoInventario, TipoMovimientoInventario
from inventory.services import registrar_ingreso_mercaderia
from inventory.tests.helpers import crear_inventario_de_prueba


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class MovimientoInventarioModelTests(TestCase):
    def setUp(self):
        self.inventario = crear_inventario_de_prueba()

    def test_persiste_movimiento_valido_relacionado_con_un_inventario(self):
        movimiento = registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=3,
        )

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.inventario, self.inventario)
        self.assertEqual(self.inventario.movimientos.count(), 1)

    def test_fecha_hora_se_genera_automaticamente_y_es_timezone_aware(self):
        inicio = timezone.now()
        movimiento = registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=1,
        )
        fin = timezone.now()

        self.assertLessEqual(inicio, movimiento.fecha_hora)
        self.assertLessEqual(movimiento.fecha_hora, fin)
        self.assertTrue(timezone.is_aware(movimiento.fecha_hora))

    def test_enum_contiene_exactamente_los_seis_tipos_congelados(self):
        self.assertEqual(
            set(TipoMovimientoInventario.values),
            {
                "INGRESO_MERCADERIA",
                "VENTA_PEDIDO",
                "VENTA_PRESENCIAL",
                "CANCELACION_PEDIDO",
                "AJUSTE_POSITIVO",
                "AJUSTE_NEGATIVO",
            },
        )

    def test_pedido_es_fk_opcional_y_protectiva(self):
        campo = MovimientoInventario._meta.get_field("pedido")
        self.assertTrue(campo.null)
        self.assertEqual(campo.remote_field.on_delete.__name__, "PROTECT")

    def test_aplicacion_rechaza_cantidad_cero_o_negativa(self):
        for cantidad in (0, -1):
            with self.subTest(cantidad=cantidad):
                movimiento = MovimientoInventario(
                    inventario=self.inventario,
                    tipo_movimiento=TipoMovimientoInventario.INGRESO_MERCADERIA,
                    cantidad=cantidad,
                )

                with self.assertRaises(ValidationError):
                    movimiento.full_clean()

    def test_postgresql_rechaza_cantidad_cero_o_negativa(self):
        for cantidad in (0, -1):
            with self.subTest(cantidad=cantidad):
                movimiento = MovimientoInventario(
                    inventario=self.inventario,
                    tipo_movimiento=TipoMovimientoInventario.INGRESO_MERCADERIA,
                    cantidad=cantidad,
                )

                with self.assertRaises(IntegrityError), transaction.atomic():
                    movimiento.save()

    def test_aplicacion_y_postgresql_rechazan_tipo_fuera_del_enum(self):
        movimiento = MovimientoInventario(
            inventario=self.inventario,
            tipo_movimiento="TIPO_INVALIDO",
            cantidad=1,
        )
        with self.assertRaises(ValidationError):
            movimiento.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            movimiento.save()

    def test_inventario_con_movimientos_no_se_elimina_en_cascada(self):
        registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=1,
        )

        with self.assertRaises(ProtectedError):
            self.inventario.delete()
