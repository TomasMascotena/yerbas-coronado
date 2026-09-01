import html
import shutil
import tempfile

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from orders.admin import ClienteAdmin, PedidoAdmin
from orders.models import Cliente, ModalidadEntrega, Pedido
from orders.services import crear_pedido_desde_carrito
from orders.tests.helpers import crear_carrito_checkout, datos_comprador


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ClienteAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administradora = get_user_model().objects.create_superuser(
            username="administradora-clientes",
            email="",
            password=None,
        )
        cls.lectora = get_user_model().objects.create_user(
            username="lectora-clientes",
            is_staff=True,
            password=None,
        )
        cls.lectora.user_permissions.add(
            Permission.objects.get(
                codename="view_cliente",
                content_type__app_label="orders",
            )
        )
        cls.personal_con_cambio = get_user_model().objects.create_user(
            username="personal-cambio-clientes",
            is_staff=True,
            password=None,
        )
        cls.personal_con_cambio.user_permissions.add(
            Permission.objects.get(
                codename="change_cliente",
                content_type__app_label="orders",
            )
        )
        cls.personal_sin_permiso = get_user_model().objects.create_user(
            username="personal-sin-clientes",
            is_staff=True,
            password=None,
        )

    def setUp(self):
        self.client.force_login(self.administradora)
        self.cliente_admin = admin.site._registry[Cliente]
        self.lista_url = reverse("admin:orders_cliente_changelist")

    def crear_cliente(
        self,
        *,
        session_key,
        dni,
        nombre="Ana",
        apellido="Coronado",
        telefono="341 555 1234",
    ):
        carrito, _ = crear_carrito_checkout(
            session_key=session_key,
            nombre=f"Producto {session_key}",
        )
        resultado = crear_pedido_desde_carrito(
            session_key=session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(
                dni=dni,
                nombre=nombre,
                apellido=apellido,
                telefono=telefono,
            ),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        )
        return resultado.pedido.cliente

    def url_detalle(self, cliente):
        return reverse("admin:orders_cliente_change", args=(cliente.pk,))

    def test_registro_y_configuracion_de_consulta(self):
        self.assertTrue(admin.site.is_registered(Cliente))
        self.assertIsInstance(self.cliente_admin, ClienteAdmin)
        self.assertEqual(
            self.cliente_admin.list_display,
            ("dni", "nombre", "apellido", "telefono"),
        )
        self.assertEqual(
            self.cliente_admin.search_fields,
            ("dni", "nombre", "apellido", "telefono"),
        )
        self.assertEqual(
            self.cliente_admin.ordering,
            ("apellido", "nombre", "dni"),
        )
        self.assertEqual(
            self.cliente_admin.readonly_fields,
            ("dni", "nombre", "apellido", "telefono"),
        )
        self.assertEqual(
            self.cliente_admin.fieldsets,
            (
                ("Identificación", {"fields": ("dni",)}),
                (
                    "Datos de contacto",
                    {"fields": ("nombre", "apellido", "telefono")},
                ),
            ),
        )
        self.assertIsNone(self.cliente_admin.actions)

    def test_listado_muestra_datos_actuales_y_orden_estable(self):
        primero = self.crear_cliente(
            session_key="cliente-orden-1",
            dni="30111222",
            nombre="Beatriz",
            apellido="Almada",
            telefono="341 111 1111",
        )
        segundo = self.crear_cliente(
            session_key="cliente-orden-2",
            dni="30111221",
            nombre="Ana",
            apellido="Almada",
            telefono="341 222 2222",
        )
        tercero = self.crear_cliente(
            session_key="cliente-orden-3",
            dni="30111223",
            nombre="Ana",
            apellido="Zárate",
            telefono="341 333 3333",
        )

        response = self.client.get(self.lista_url)

        self.assertEqual(response.status_code, 200)
        for cliente in (primero, segundo, tercero):
            self.assertContains(response, cliente.dni)
            self.assertContains(response, cliente.nombre)
            self.assertContains(response, cliente.apellido)
            self.assertContains(response, cliente.telefono)
        self.assertEqual(
            list(response.context["cl"].result_list),
            [segundo, primero, tercero],
        )

    def test_busqueda_por_cada_dato_actual(self):
        objetivo = self.crear_cliente(
            session_key="cliente-busqueda-objetivo",
            dni="31222333",
            nombre="Mariela",
            apellido="Quiroga",
            telefono="341 987 6543",
        )
        otro = self.crear_cliente(
            session_key="cliente-busqueda-otro",
            dni="32444555",
            nombre="Lucía",
            apellido="Benítez",
            telefono="351 111 2222",
        )

        for termino in ("31222333", "Mariela", "Quiroga", "987 6543"):
            with self.subTest(termino=termino):
                response = self.client.get(self.lista_url, {"q": termino})
                resultados = list(response.context["cl"].result_list)
                self.assertIn(objetivo, resultados)
                self.assertNotIn(otro, resultados)

    def test_detalle_muestra_datos_actuales_y_no_snapshots_anteriores(self):
        cliente = self.crear_cliente(
            session_key="cliente-datos-iniciales",
            dni="33555666",
            nombre="Nombre anterior",
            apellido="Apellido anterior",
            telefono="341 100 1000",
        )
        pedido_anterior = cliente.pedidos.get()
        cliente_actualizado = self.crear_cliente(
            session_key="cliente-datos-actuales",
            dni="33555666",
            nombre="Nombre actual",
            apellido="Apellido actual",
            telefono="341 200 2000",
        )

        response = self.client.get(self.url_detalle(cliente_actualizado))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "33555666")
        self.assertContains(response, "Nombre actual")
        self.assertContains(response, "Apellido actual")
        self.assertContains(response, "341 200 2000")
        pedido_anterior.refresh_from_db()
        self.assertEqual(pedido_anterior.nombre_cliente, "Nombre anterior")
        self.assertEqual(pedido_anterior.apellido_cliente, "Apellido anterior")
        self.assertEqual(pedido_anterior.telefono_cliente, "341 100 1000")

    def test_no_permite_alta_edicion_ni_eliminacion(self):
        cliente = self.crear_cliente(
            session_key="cliente-protecciones",
            dni="34666777",
        )
        request = RequestFactory().get(self.lista_url)
        request.user = self.administradora

        self.assertFalse(self.cliente_admin.has_add_permission(request))
        self.assertFalse(
            self.cliente_admin.has_change_permission(request, cliente)
        )
        self.assertFalse(
            self.cliente_admin.has_delete_permission(request, cliente)
        )
        self.assertEqual(
            self.client.get(reverse("admin:orders_cliente_add")).status_code,
            403,
        )

        detalle = self.client.get(self.url_detalle(cliente))
        self.assertEqual(detalle.status_code, 200)
        self.assertEqual(detalle.context["adminform"].form.fields, {})
        self.assertNotContains(detalle, "_save")

        respuesta_edicion = self.client.post(
            self.url_detalle(cliente),
            {
                "dni": "99999999",
                "nombre": "Alterado",
                "apellido": "Alterado",
                "telefono": "341 999 9999",
            },
        )
        self.assertEqual(respuesta_edicion.status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("admin:orders_cliente_delete", args=(cliente.pk,))
            ).status_code,
            403,
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.dni, "34666777")
        self.assertEqual(cliente.nombre, "Ana")

    def test_no_existe_eliminacion_masiva_ni_acciones_personalizadas(self):
        request = RequestFactory().get(self.lista_url)
        request.user = self.administradora

        self.assertEqual(self.cliente_admin.get_actions(request), {})

        response = self.client.get(self.lista_url)
        self.assertNotContains(response, "action-toggle")
        self.assertNotContains(response, "delete_selected")

    def test_permiso_view_cliente_habilita_solo_consulta(self):
        cliente = self.crear_cliente(
            session_key="cliente-permiso-vista",
            dni="35777888",
        )
        self.client.force_login(self.lectora)

        self.assertEqual(self.client.get(self.lista_url).status_code, 200)
        self.assertEqual(
            self.client.get(self.url_detalle(cliente)).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url_detalle(cliente),
                {"nombre": "No permitido"},
            ).status_code,
            403,
        )

    def test_permiso_change_cliente_da_acceso_pero_no_edicion(self):
        cliente = self.crear_cliente(
            session_key="cliente-permiso-cambio",
            dni="36888999",
        )
        self.client.force_login(self.personal_con_cambio)
        request = RequestFactory().get(self.url_detalle(cliente))
        request.user = self.personal_con_cambio

        self.assertTrue(self.cliente_admin.has_view_permission(request, cliente))
        self.assertFalse(
            self.cliente_admin.has_change_permission(request, cliente)
        )
        self.assertEqual(self.client.get(self.lista_url).status_code, 200)
        self.assertEqual(
            self.client.get(self.url_detalle(cliente)).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url_detalle(cliente),
                {"nombre": "No permitido"},
            ).status_code,
            403,
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Ana")

    def test_usuario_sin_permisos_no_puede_consultar(self):
        cliente = self.crear_cliente(
            session_key="cliente-sin-permiso",
            dni="37999000",
        )
        self.client.force_login(self.personal_sin_permiso)

        self.assertEqual(self.client.get(self.lista_url).status_code, 403)
        self.assertEqual(
            self.client.get(self.url_detalle(cliente)).status_code,
            403,
        )

    def test_listado_no_introduce_n_mas_uno(self):
        self.crear_cliente(
            session_key="cliente-consultas-1",
            dni="38111000",
        )
        self.client.get(self.lista_url)
        with CaptureQueriesContext(connection) as consultas_un_cliente:
            self.client.get(self.lista_url)

        for indice in range(2, 6):
            self.crear_cliente(
                session_key=f"cliente-consultas-{indice}",
                dni=f"3811100{indice}",
            )
        with CaptureQueriesContext(connection) as consultas_cinco_clientes:
            self.client.get(self.lista_url)

        self.assertEqual(
            len(consultas_un_cliente),
            len(consultas_cinco_clientes),
        )

    def test_contenido_del_cliente_se_escapa_en_listado_y_detalle(self):
        nombre = 'Ana <script>alert("nombre")</script>'
        apellido = 'Coronado <img src=x onerror="alert(1)">'
        cliente = self.crear_cliente(
            session_key="cliente-autoescape",
            dni="39222000",
            nombre=nombre,
            apellido=apellido,
        )

        for response in (
            self.client.get(self.lista_url),
            self.client.get(self.url_detalle(cliente)),
        ):
            self.assertContains(response, html.escape(nombre))
            self.assertContains(response, html.escape(apellido))
            self.assertNotContains(response, '<script>alert("nombre")</script>')
            self.assertNotContains(
                response,
                '<img src=x onerror="alert(1)">',
            )

    def test_pedido_admin_permanece_registrado_sin_cambios(self):
        self.assertTrue(admin.site.is_registered(Pedido))
        self.assertIsInstance(admin.site._registry[Pedido], PedidoAdmin)
