from decimal import Decimal
import shutil
import tempfile
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from catalog.admin import ProductoAdmin
from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.models import Inventario


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ProductoAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administradora = get_user_model().objects.create_superuser(
            username="administradora",
            email="",
            password=None,
        )

    def setUp(self):
        self.client.force_login(self.administradora)
        self.producto_admin = admin.site._registry[Producto]
        self.request = RequestFactory().get("/admin/catalog/producto/")
        self.request.user = self.administradora
        self.lista_url = reverse("admin:catalog_producto_changelist")
        self.alta_url = reverse("admin:catalog_producto_add")

    def crear_producto(self, **cambios):
        return crear_producto_con_inventario(
            producto=Producto(**datos_producto(**cambios))
        )

    def datos_admin(self, **cambios):
        datos = {
            "nombre": "Canarias",
            "descripcion": "Yerba mate tradicional",
            "peso": "1 kg",
            "imagen": imagen_de_prueba("admin-producto.gif"),
            "precio_unitario": "5000.00",
            "precio_desde_3": "4500.00",
            "precio_desde_20": "4000.00",
            "activo": "on",
        }
        datos.update(cambios)
        return datos

    def test_producto_esta_registrado_e_inventario_no_es_crud_editable(self):
        self.assertTrue(admin.site.is_registered(Producto))
        self.assertIsInstance(self.producto_admin, ProductoAdmin)
        self.assertTrue(admin.site.is_registered(Inventario))
        inventario_admin = admin.site._registry[Inventario]
        self.assertFalse(inventario_admin.has_add_permission(self.request))
        self.assertFalse(inventario_admin.has_change_permission(self.request))
        self.assertFalse(inventario_admin.has_delete_permission(self.request))

    def test_administradora_puede_acceder_al_listado(self):
        response = self.client.get(self.lista_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.producto_admin.search_fields,
            ("nombre", "peso"),
        )
        self.assertEqual(self.producto_admin.list_filter, ("activo",))

    def test_listado_configura_la_informacion_comercial_y_el_stock(self):
        self.assertEqual(
            self.producto_admin.list_display,
            (
                "nombre",
                "peso",
                "precio_unitario",
                "precio_desde_3",
                "precio_desde_20",
                "cantidad_disponible_actual",
                "activo",
            ),
        )

    def test_admin_crea_producto_con_exactamente_un_inventario_en_cero(self):
        response = self.client.post(self.alta_url, self.datos_admin())

        self.assertEqual(response.status_code, 302)
        producto = Producto.objects.get()
        self.assertEqual(Inventario.objects.filter(producto=producto).count(), 1)
        self.assertEqual(producto.inventario.cantidad_disponible, 0)

    def test_admin_persiste_la_misma_instancia_construida_por_el_formulario(self):
        producto = Producto(
            **datos_producto(
                imagen=imagen_de_prueba("misma-instancia.gif")
            )
        )
        with patch(
            "catalog.admin.crear_producto_con_inventario",
            wraps=crear_producto_con_inventario,
        ) as servicio:
            self.producto_admin.save_model(
                self.request,
                producto,
                Mock(),
                False,
            )

        producto_recibido = servicio.call_args.kwargs["producto"]
        self.assertIs(producto_recibido, producto)
        self.assertEqual(producto.pk, Producto.objects.get().pk)
        self.assertEqual(producto_recibido.inventario.cantidad_disponible, 0)

    def test_admin_revierte_producto_si_falla_la_creacion_del_inventario(self):
        with patch.object(Inventario, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    self.alta_url,
                    self.datos_admin(imagen=imagen_de_prueba("rollback.gif")),
                )

        self.assertFalse(Producto.objects.exists())
        self.assertFalse(Inventario.objects.exists())

    def test_admin_edita_informacion_comercial_sin_modificar_inventario(self):
        producto = self.crear_producto()
        inventario_id = producto.inventario.pk
        cambio_url = reverse("admin:catalog_producto_change", args=(producto.pk,))

        response = self.client.post(
            cambio_url,
            self.datos_admin(
                nombre="Baldo",
                descripcion="Descripción modificada",
                peso="500 gr",
                imagen=imagen_de_prueba("reemplazo.gif"),
                precio_unitario="100.00",
                precio_desde_3="300.00",
                precio_desde_20="200.00",
            ),
        )

        self.assertEqual(response.status_code, 302)
        producto.refresh_from_db()
        self.assertEqual(producto.nombre, "Baldo")
        self.assertEqual(producto.descripcion, "Descripción modificada")
        self.assertEqual(producto.peso, "500 gr")
        self.assertTrue(producto.imagen.name.endswith("reemplazo.gif"))
        self.assertEqual(producto.precio_unitario, Decimal("100.00"))
        self.assertEqual(producto.precio_desde_3, Decimal("300.00"))
        self.assertEqual(producto.precio_desde_20, Decimal("200.00"))
        self.assertTrue(producto.activo)
        self.assertEqual(producto.inventario.pk, inventario_id)
        self.assertEqual(producto.inventario.cantidad_disponible, 0)

    def test_admin_permite_inactivar_y_reactivar_un_producto(self):
        producto = self.crear_producto()
        cambio_url = reverse("admin:catalog_producto_change", args=(producto.pk,))
        datos = self.datos_admin()
        datos.pop("imagen")
        datos.pop("activo")

        response = self.client.post(cambio_url, datos)
        self.assertEqual(response.status_code, 302)
        producto.refresh_from_db()
        self.assertFalse(producto.activo)

        datos["activo"] = "on"
        response = self.client.post(cambio_url, datos)
        self.assertEqual(response.status_code, 302)
        producto.refresh_from_db()
        self.assertTrue(producto.activo)

    def test_accion_masiva_inactiva_sin_modificar_inventario(self):
        primero = self.crear_producto()
        segundo = self.crear_producto(
            nombre="Baldo",
            imagen=imagen_de_prueba("baldo-inactivar.gif"),
        )

        response = self.client.post(
            self.lista_url,
            {
                "action": "inactivar_productos",
                "_selected_action": [primero.pk, segundo.pk],
                "index": 0,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Producto.objects.filter(pk=primero.pk).get().activo)
        self.assertFalse(Producto.objects.filter(pk=segundo.pk).get().activo)
        self.assertEqual(primero.inventario.cantidad_disponible, 0)
        self.assertEqual(segundo.inventario.cantidad_disponible, 0)

    def test_accion_masiva_activa_sin_modificar_inventario(self):
        primero = self.crear_producto(activo=False)
        segundo = self.crear_producto(
            nombre="Baldo",
            imagen=imagen_de_prueba("baldo-activar.gif"),
            activo=False,
        )

        response = self.client.post(
            self.lista_url,
            {
                "action": "activar_productos",
                "_selected_action": [primero.pk, segundo.pk],
                "index": 0,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Producto.objects.filter(pk=primero.pk).get().activo)
        self.assertTrue(Producto.objects.filter(pk=segundo.pk).get().activo)
        self.assertEqual(primero.inventario.cantidad_disponible, 0)
        self.assertEqual(segundo.inventario.cantidad_disponible, 0)

    def test_listado_muestra_el_stock_actual(self):
        producto = self.crear_producto()

        response = self.client.get(self.lista_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.producto_admin.cantidad_disponible_actual(producto),
            0,
        )
        self.assertContains(response, ">0<", html=False)

    def test_cantidad_disponible_no_es_editable_en_producto_admin(self):
        producto = self.crear_producto()
        formulario = self.producto_admin.get_form(
            self.request,
            obj=producto,
        )

        self.assertNotIn("cantidad_disponible", formulario.base_fields)
        self.assertIn(
            "cantidad_disponible_actual",
            self.producto_admin.readonly_fields,
        )

    def test_admin_no_permite_eliminacion_individual(self):
        producto = self.crear_producto()
        eliminar_url = reverse(
            "admin:catalog_producto_delete",
            args=(producto.pk,),
        )

        self.assertFalse(
            self.producto_admin.has_delete_permission(
                self.request,
                producto,
            )
        )
        response = self.client.get(eliminar_url)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())

    def test_admin_no_ofrece_eliminacion_masiva(self):
        acciones = self.producto_admin.get_actions(self.request)

        self.assertNotIn("delete_selected", acciones)
        self.assertIn("activar_productos", acciones)
        self.assertIn("inactivar_productos", acciones)

    def test_admin_mantiene_validaciones_al_crear(self):
        datos = self.datos_admin(precio_unitario="0.00")
        datos.pop("imagen")

        response = self.client.post(self.alta_url, datos)

        self.assertEqual(response.status_code, 200)
        errores = response.context["adminform"].form.errors
        self.assertIn("precio_unitario", errores)
        self.assertIn("imagen", errores)
        self.assertFalse(Producto.objects.exists())

    def test_admin_mantiene_unicidad_al_editar(self):
        existente = self.crear_producto()
        producto = self.crear_producto(
            nombre="Baldo",
            imagen=imagen_de_prueba("baldo-editar.gif"),
        )
        cambio_url = reverse("admin:catalog_producto_change", args=(producto.pk,))

        response = self.client.post(
            cambio_url,
            {
                "nombre": existente.nombre,
                "descripcion": producto.descripcion,
                "peso": existente.peso,
                "precio_unitario": producto.precio_unitario,
                "precio_desde_3": producto.precio_desde_3,
                "precio_desde_20": producto.precio_desde_20,
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)
        producto.refresh_from_db()
        self.assertEqual(producto.nombre, "Baldo")
        self.assertEqual(producto.inventario.cantidad_disponible, 0)
