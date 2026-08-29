import shutil
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.admin import ProductoAdmin
from catalog.models import Producto
from inventory.admin import InventarioAdmin, MovimientoInventarioAdmin
from inventory.exceptions import (
    CapacidadInventarioExcedida,
    CantidadMovimientoInvalida,
    ObservacionObligatoria,
    StockInsuficiente,
)
from inventory.models import Inventario, MovimientoInventario, TipoMovimientoInventario
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
class InventoryAdminTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administradora = get_user_model().objects.create_superuser(
            username="administradora",
            email="",
            password=None,
        )
        cls.lectora = get_user_model().objects.create_user(
            username="lectora",
            is_staff=True,
            password=None,
        )
        cls.lectora.user_permissions.add(
            Permission.objects.get(
                codename="view_inventario",
                content_type__app_label="inventory",
            )
        )

    def setUp(self):
        self.client.force_login(self.administradora)
        self.inventario_admin = admin.site._registry[Inventario]
        self.movimiento_admin = admin.site._registry[MovimientoInventario]
        self.producto_admin = admin.site._registry[Producto]
        self.request = RequestFactory().get("/admin/inventory/inventario/")
        self.request.user = self.administradora

    def crear_inventario(self, *, nombre="Canarias", activo=True):
        inventario = crear_inventario_de_prueba(nombre=nombre)
        if not activo:
            inventario.producto.activo = False
            inventario.producto.save(update_fields=("activo",))
        return inventario

    def url_inventario(self, inventario):
        return reverse(
            "admin:inventory_inventario_change",
            args=(inventario.pk,),
        )

    def url_operacion(self, inventario, operacion):
        return reverse(
            f"admin:inventory_inventario_{operacion}",
            args=(inventario.pk,),
        )


class InventarioAdminConfiguracionTests(InventoryAdminTestBase):
    def test_inventario_esta_registrado_y_listado_es_accesible(self):
        self.assertTrue(admin.site.is_registered(Inventario))
        self.assertIsInstance(self.inventario_admin, InventarioAdmin)

        response = self.client.get(
            reverse("admin:inventory_inventario_changelist")
        )
        self.assertEqual(response.status_code, 200)

    def test_listado_busqueda_filtro_y_select_related_estan_configurados(self):
        self.assertEqual(
            self.inventario_admin.list_display,
            (
                "nombre_producto",
                "peso_producto",
                "cantidad_disponible",
                "producto_activo",
            ),
        )
        self.assertEqual(
            self.inventario_admin.search_fields,
            ("producto__nombre", "producto__peso"),
        )
        self.assertEqual(
            self.inventario_admin.list_filter,
            ("producto__activo",),
        )
        queryset = self.inventario_admin.get_queryset(self.request)
        self.assertIn("producto", queryset.query.select_related)

    def test_productos_activos_e_inactivos_permanecen_visibles(self):
        activo = self.crear_inventario(nombre="Canarias")
        inactivo = self.crear_inventario(nombre="Baldo", activo=False)

        response = self.client.get(
            reverse("admin:inventory_inventario_changelist")
        )

        self.assertContains(response, activo.producto.nombre)
        self.assertContains(response, inactivo.producto.nombre)

    def test_urls_de_operaciones_preceden_a_las_urls_estandar(self):
        nombres = [patron.name for patron in self.inventario_admin.get_urls()[:4]]

        self.assertEqual(
            nombres,
            [
                "inventory_inventario_ingreso_mercaderia",
                "inventory_inventario_venta_presencial",
                "inventory_inventario_ajuste_positivo",
                "inventory_inventario_ajuste_negativo",
            ],
        )


class InventarioAdminProteccionTests(InventoryAdminTestBase):
    def test_no_permite_crear_inventario_desde_admin(self):
        self.assertFalse(self.inventario_admin.has_add_permission(self.request))

        response = self.client.get(reverse("admin:inventory_inventario_add"))
        self.assertEqual(response.status_code, 403)

    def test_detalle_es_consultable_pero_no_editable(self):
        inventario = self.crear_inventario()

        response = self.client.get(self.url_inventario(inventario))
        formulario = self.inventario_admin.get_form(
            self.request,
            obj=inventario,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            self.inventario_admin.has_change_permission(
                self.request,
                inventario,
            )
        )
        self.assertNotIn("cantidad_disponible", formulario.base_fields)
        self.assertNotIn("producto", formulario.base_fields)

    def test_post_crud_no_puede_modificar_stock_ni_producto(self):
        inventario = self.crear_inventario()
        otro = self.crear_inventario(nombre="Baldo")

        response = self.client.post(
            self.url_inventario(inventario),
            {
                "cantidad_disponible": 100,
                "producto": otro.producto.pk,
            },
        )

        self.assertEqual(response.status_code, 403)
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 0)
        self.assertNotEqual(inventario.producto_id, otro.producto_id)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_no_permite_eliminar_inventario_individual_o_masivamente(self):
        inventario = self.crear_inventario()
        eliminar_url = reverse(
            "admin:inventory_inventario_delete",
            args=(inventario.pk,),
        )

        self.assertFalse(
            self.inventario_admin.has_delete_permission(
                self.request,
                inventario,
            )
        )
        self.assertEqual(self.client.get(eliminar_url).status_code, 403)
        self.assertNotIn(
            "delete_selected",
            self.inventario_admin.get_actions(self.request),
        )
        self.assertTrue(Inventario.objects.filter(pk=inventario.pk).exists())

    def test_usuario_solo_lectura_no_ve_enlaces_y_no_puede_operar_por_url(self):
        inventario = self.crear_inventario()
        operacion_url = self.url_operacion(inventario, "ingreso_mercaderia")
        self.client.force_login(self.lectora)

        detalle = self.client.get(self.url_inventario(inventario))
        operacion = self.client.get(operacion_url)

        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(detalle, operacion_url)
        self.assertEqual(operacion.status_code, 403)

    def test_administradora_ve_los_cuatro_enlaces_de_operaciones(self):
        inventario = self.crear_inventario()

        response = self.client.get(self.url_inventario(inventario))

        for operacion in (
            "ingreso_mercaderia",
            "venta_presencial",
            "ajuste_positivo",
            "ajuste_negativo",
        ):
            with self.subTest(operacion=operacion):
                self.assertContains(
                    response,
                    self.url_operacion(inventario, operacion),
                )


class InventarioAdminOperacionesTests(InventoryAdminTestBase):
    def test_ingreso_delega_en_servicio_y_aplica_post_redirect_get(self):
        inventario = self.crear_inventario()
        url = self.url_operacion(inventario, "ingreso_mercaderia")

        with patch(
            "inventory.admin.registrar_ingreso_mercaderia",
            wraps=registrar_ingreso_mercaderia,
        ) as servicio:
            response = self.client.post(
                url,
                {"cantidad": 5, "observacion": "  Lote recibido  "},
            )

        self.assertRedirects(response, self.url_inventario(inventario))
        servicio.assert_called_once_with(
            inventario_id=inventario.pk,
            cantidad=5,
            observacion="Lote recibido",
        )
        inventario.refresh_from_db()
        movimiento = MovimientoInventario.objects.get()
        self.assertEqual(inventario.cantidad_disponible, 5)
        self.assertEqual(
            movimiento.tipo_movimiento,
            TipoMovimientoInventario.INGRESO_MERCADERIA,
        )

    def test_venta_presencial_delega_y_puede_dejar_stock_en_cero(self):
        inventario = self.crear_inventario()
        registrar_ingreso_mercaderia(inventario_id=inventario.pk, cantidad=5)

        with patch(
            "inventory.admin.registrar_venta_presencial",
            wraps=registrar_venta_presencial,
        ) as servicio:
            response = self.client.post(
                self.url_operacion(inventario, "venta_presencial"),
                {"cantidad": 5, "observacion": ""},
            )

        self.assertEqual(response.status_code, 302)
        servicio.assert_called_once_with(
            inventario_id=inventario.pk,
            cantidad=5,
            observacion="",
        )
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 0)
        self.assertEqual(
            MovimientoInventario.objects.filter(
                tipo_movimiento=TipoMovimientoInventario.VENTA_PRESENCIAL
            ).count(),
            1,
        )

    def test_ajuste_positivo_delega_y_exige_observacion(self):
        inventario = self.crear_inventario()
        url = self.url_operacion(inventario, "ajuste_positivo")

        invalido = self.client.post(url, {"cantidad": 2, "observacion": "  "})
        self.assertEqual(invalido.status_code, 200)
        self.assertFalse(MovimientoInventario.objects.exists())

        with patch(
            "inventory.admin.registrar_ajuste_positivo",
            wraps=registrar_ajuste_positivo,
        ) as servicio:
            valido = self.client.post(
                url,
                {"cantidad": 2, "observacion": "  Sobrante  "},
            )

        self.assertEqual(valido.status_code, 302)
        servicio.assert_called_once_with(
            inventario_id=inventario.pk,
            cantidad=2,
            observacion="Sobrante",
        )
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 2)

    def test_ajuste_negativo_delega_y_exige_observacion(self):
        inventario = self.crear_inventario()
        registrar_ingreso_mercaderia(inventario_id=inventario.pk, cantidad=3)
        url = self.url_operacion(inventario, "ajuste_negativo")

        invalido = self.client.post(url, {"cantidad": 1, "observacion": ""})
        self.assertEqual(invalido.status_code, 200)
        self.assertEqual(MovimientoInventario.objects.count(), 1)

        with patch(
            "inventory.admin.registrar_ajuste_negativo",
            wraps=registrar_ajuste_negativo,
        ) as servicio:
            valido = self.client.post(
                url,
                {"cantidad": 1, "observacion": "  Rotura  "},
            )

        self.assertEqual(valido.status_code, 302)
        servicio.assert_called_once_with(
            inventario_id=inventario.pk,
            cantidad=1,
            observacion="Rotura",
        )
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 2)

    def test_stock_insuficiente_se_muestra_sin_cambios_parciales(self):
        inventario = self.crear_inventario()
        registrar_ingreso_mercaderia(inventario_id=inventario.pk, cantidad=2)

        response = self.client.post(
            self.url_operacion(inventario, "venta_presencial"),
            {"cantidad": 3, "observacion": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 2)
        self.assertEqual(MovimientoInventario.objects.count(), 1)

    def test_error_de_dominio_refresca_el_stock_mostrado(self):
        inventario = self.crear_inventario()
        registrar_ingreso_mercaderia(inventario_id=inventario.pk, cantidad=5)

        def cambiar_stock_y_fallar(**kwargs):
            Inventario.objects.filter(pk=inventario.pk).update(
                cantidad_disponible=2
            )
            raise StockInsuficiente

        with patch(
            "inventory.admin.registrar_venta_presencial",
            side_effect=cambiar_stock_y_fallar,
        ):
            response = self.client.post(
                self.url_operacion(inventario, "venta_presencial"),
                {"cantidad": 4, "observacion": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["inventario"].cantidad_disponible,
            2,
        )

    def test_cantidad_invalida_del_formulario_no_invoca_servicio(self):
        inventario = self.crear_inventario()
        with patch("inventory.admin.registrar_ingreso_mercaderia") as servicio:
            response = self.client.post(
                self.url_operacion(inventario, "ingreso_mercaderia"),
                {"cantidad": 0, "observacion": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("cantidad", response.context["form"].errors)
        servicio.assert_not_called()
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_cantidad_superior_a_bigint_es_error_de_formulario(self):
        inventario = self.crear_inventario()
        with patch("inventory.admin.registrar_ingreso_mercaderia") as servicio:
            response = self.client.post(
                self.url_operacion(inventario, "ingreso_mercaderia"),
                {"cantidad": 2**63, "observacion": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("cantidad", response.context["form"].errors)
        servicio.assert_not_called()
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 0)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_overflow_de_stock_se_muestra_y_revierte_sin_error_500(self):
        inventario = self.crear_inventario()
        registrar_ingreso_mercaderia(inventario_id=inventario.pk, cantidad=5)
        movimientos_iniciales = MovimientoInventario.objects.count()

        response = self.client.post(
            self.url_operacion(inventario, "ingreso_mercaderia"),
            {
                "cantidad": MAX_BIGINT_POSITIVO - 5 + 1,
                "observacion": "Ingreso extremo",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertContains(response, "capacidad máxima")
        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, 5)
        self.assertEqual(
            MovimientoInventario.objects.count(),
            movimientos_iniciales,
        )

    def test_excepciones_de_dominio_se_traducen_a_errores_de_formulario(self):
        inventario = self.crear_inventario()
        url = self.url_operacion(inventario, "ingreso_mercaderia")
        casos = (
            (CantidadMovimientoInvalida(), "cantidad"),
            (ObservacionObligatoria(), "observacion"),
            (StockInsuficiente(), None),
            (CapacidadInventarioExcedida(), None),
        )

        for error, campo in casos:
            with self.subTest(error=type(error).__name__):
                with patch(
                    "inventory.admin.registrar_ingreso_mercaderia",
                    side_effect=error,
                ):
                    response = self.client.post(
                        url,
                        {"cantidad": 1, "observacion": "Observación"},
                    )

                self.assertEqual(response.status_code, 200)
                if campo is None:
                    self.assertTrue(response.context["form"].non_field_errors())
                else:
                    self.assertIn(campo, response.context["form"].errors)

    def test_inventario_inexistente_devuelve_404(self):
        response = self.client.get(
            reverse(
                "admin:inventory_inventario_ingreso_mercaderia",
                args=(999999,),
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_identificador_de_inventario_malformado_devuelve_404(self):
        response = self.client.get(
            reverse(
                "admin:inventory_inventario_ingreso_mercaderia",
                args=("abc",),
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_vista_de_operacion_establece_namespace_del_admin(self):
        inventario = self.crear_inventario()

        response = self.client.get(
            self.url_operacion(inventario, "ingreso_mercaderia")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.current_app, admin.site.name)

    def test_las_cuatro_operaciones_funcionan_con_producto_inactivo(self):
        inventario = self.crear_inventario(activo=False)
        operaciones = (
            ("ingreso_mercaderia", 5, "", 5),
            ("venta_presencial", 1, "", 4),
            ("ajuste_positivo", 2, "Conteo", 6),
            ("ajuste_negativo", 1, "Rotura", 5),
        )

        for operacion, cantidad, observacion, stock_esperado in operaciones:
            with self.subTest(operacion=operacion):
                response = self.client.post(
                    self.url_operacion(inventario, operacion),
                    {"cantidad": cantidad, "observacion": observacion},
                )
                self.assertEqual(response.status_code, 302)
                inventario.refresh_from_db()
                inventario.producto.refresh_from_db()
                self.assertEqual(
                    inventario.cantidad_disponible,
                    stock_esperado,
                )
                self.assertFalse(inventario.producto.activo)


class MovimientoInventarioAdminTests(InventoryAdminTestBase):
    def setUp(self):
        super().setUp()
        self.inventario = self.crear_inventario()
        self.movimiento = registrar_ingreso_mercaderia(
            inventario_id=self.inventario.pk,
            cantidad=5,
            observacion="Lote inicial",
        )

    def test_historial_esta_registrado_y_listado_es_accesible(self):
        self.assertTrue(admin.site.is_registered(MovimientoInventario))
        self.assertIsInstance(
            self.movimiento_admin,
            MovimientoInventarioAdmin,
        )

        response = self.client.get(
            reverse("admin:inventory_movimientoinventario_changelist")
        )
        self.assertEqual(response.status_code, 200)

    def test_historial_configura_columnas_busqueda_filtros_y_relaciones(self):
        self.assertEqual(
            self.movimiento_admin.list_display,
            (
                "fecha_hora",
                "nombre_producto",
                "peso_producto",
                "tipo_movimiento",
                "cantidad",
                "observacion",
            ),
        )
        self.assertEqual(
            self.movimiento_admin.search_fields,
            (
                "inventario__producto__nombre",
                "inventario__producto__peso",
                "observacion",
            ),
        )
        self.assertEqual(
            self.movimiento_admin.list_select_related,
            ("inventario__producto",),
        )
        self.assertEqual(self.movimiento_admin.date_hierarchy, "fecha_hora")
        self.assertEqual(self.movimiento_admin.list_filter[0], "tipo_movimiento")

    def test_movimiento_puede_consultarse_pero_no_crearse_ni_modificarse(self):
        detalle_url = reverse(
            "admin:inventory_movimientoinventario_change",
            args=(self.movimiento.pk,),
        )

        self.assertEqual(self.client.get(detalle_url).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("admin:inventory_movimientoinventario_add")
            ).status_code,
            403,
        )
        response = self.client.post(
            detalle_url,
            {
                "cantidad": 99,
                "tipo_movimiento": TipoMovimientoInventario.AJUSTE_NEGATIVO,
                "inventario": self.inventario.pk,
                "observacion": "Alterada",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.movimiento.refresh_from_db()
        self.assertEqual(self.movimiento.cantidad, 5)
        self.assertEqual(
            self.movimiento.tipo_movimiento,
            TipoMovimientoInventario.INGRESO_MERCADERIA,
        )
        self.assertEqual(self.movimiento.observacion, "Lote inicial")

    def test_movimiento_no_puede_eliminarse_individual_o_masivamente(self):
        eliminar_url = reverse(
            "admin:inventory_movimientoinventario_delete",
            args=(self.movimiento.pk,),
        )

        self.assertEqual(self.client.get(eliminar_url).status_code, 403)
        self.assertNotIn(
            "delete_selected",
            self.movimiento_admin.get_actions(self.request),
        )
        self.assertTrue(
            MovimientoInventario.objects.filter(pk=self.movimiento.pk).exists()
        )


class ProductoAdminIntegracionYRegionalizacionTests(InventoryAdminTestBase):
    def test_producto_admin_mantiene_stock_readonly_y_enlaza_inventario(self):
        inventario = self.crear_inventario()
        producto = inventario.producto
        enlace = self.producto_admin.administrar_inventario(producto)
        url = self.url_inventario(inventario)

        self.assertIsInstance(self.producto_admin, ProductoAdmin)
        self.assertIn(
            "cantidad_disponible_actual",
            self.producto_admin.readonly_fields,
        )
        self.assertIn("administrar_inventario", self.producto_admin.readonly_fields)
        self.assertIn(url, enlace)
        response = self.client.get(
            reverse("admin:catalog_producto_change", args=(producto.pk,))
        )
        self.assertContains(response, url)

    def test_configuracion_regional_y_fecha_timezone_aware(self):
        inventario = self.crear_inventario()
        movimiento = registrar_ingreso_mercaderia(
            inventario_id=inventario.pk,
            cantidad=1,
        )

        self.assertEqual(settings.LANGUAGE_CODE, "es-ar")
        self.assertEqual(
            settings.TIME_ZONE,
            "America/Argentina/Buenos_Aires",
        )
        self.assertTrue(settings.USE_TZ)
        self.assertTrue(timezone.is_aware(movimiento.fecha_hora))
        self.assertEqual(
            timezone.get_current_timezone_name(),
            "America/Argentina/Buenos_Aires",
        )
