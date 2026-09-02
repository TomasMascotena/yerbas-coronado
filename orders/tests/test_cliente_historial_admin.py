from datetime import timedelta
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
from django.utils import timezone

from orders.admin import ClienteAdmin, PedidoAdmin, PedidoClienteInline
from orders.models import Cliente, ModalidadEntrega, Pedido
from orders.services import crear_pedido_desde_carrito
from orders.tests.helpers import crear_carrito_checkout, datos_comprador


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ClienteHistorialAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administradora = get_user_model().objects.create_superuser(
            username="administradora-historial-clientes",
            email="",
            password=None,
        )
        cls.lectora_ambos = get_user_model().objects.create_user(
            username="lectora-clientes-pedidos",
            is_staff=True,
            password=None,
        )
        cls.solo_clientes = get_user_model().objects.create_user(
            username="lectora-solo-clientes",
            is_staff=True,
            password=None,
        )
        cls.solo_pedidos = get_user_model().objects.create_user(
            username="lectora-solo-pedidos",
            is_staff=True,
            password=None,
        )
        permisos = {
            codigo: Permission.objects.get(
                codename=codigo,
                content_type__app_label="orders",
            )
            for codigo in ("view_cliente", "view_pedido")
        }
        cls.lectora_ambos.user_permissions.add(*permisos.values())
        cls.solo_clientes.user_permissions.add(permisos["view_cliente"])
        cls.solo_pedidos.user_permissions.add(permisos["view_pedido"])

    def setUp(self):
        self.client.force_login(self.administradora)
        self.cliente_admin = admin.site._registry[Cliente]
        self.pedido_admin = admin.site._registry[Pedido]

    def crear_pedido(
        self,
        *,
        session_key,
        dni,
        nombre="Ana",
        apellido="Coronado",
        telefono="341 555 1234",
        cantidad=2,
    ):
        carrito, _ = crear_carrito_checkout(
            session_key=session_key,
            cantidad=cantidad,
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
        return resultado.pedido

    def url_cliente(self, cliente):
        return reverse("admin:orders_cliente_change", args=(cliente.pk,))

    def url_pedido(self, pedido):
        return reverse("admin:orders_pedido_change", args=(pedido.pk,))

    def inline_formset_pedidos(self, response):
        return next(
            inline
            for inline in response.context["inline_admin_formsets"]
            if inline.opts.model is Pedido
        ).formset

    def test_historial_configura_campos_orden_y_protecciones_de_solo_lectura(self):
        inline = PedidoClienteInline(Cliente, admin.site)
        request = RequestFactory().get("/admin/orders/cliente/1/change/")
        request.user = self.administradora

        self.assertEqual(
            inline.fields,
            (
                "fecha_hora_creacion",
                "estado",
                "modalidad_entrega",
                "cantidad_total",
                "importe_total",
            ),
        )
        self.assertEqual(inline.readonly_fields, inline.fields)
        self.assertEqual(inline.ordering, ("-fecha_hora_creacion", "-pk"))
        self.assertEqual(inline.extra, 0)
        self.assertFalse(inline.can_delete)
        self.assertTrue(inline.show_change_link)
        self.assertFalse(inline.has_add_permission(request))
        self.assertFalse(inline.has_change_permission(request))
        self.assertFalse(inline.has_delete_permission(request))
        self.assertTrue(inline.has_view_permission(request))
        self.assertEqual(
            inline.get_queryset(request).query.order_by,
            ("-fecha_hora_creacion", "-pk"),
        )

    def test_detalle_muestra_solo_pedidos_del_cliente_sin_filas_vacias(self):
        primero = self.crear_pedido(
            session_key="historial-asociado-1",
            dni="40111222",
            cantidad=2,
        )
        segundo = self.crear_pedido(
            session_key="historial-asociado-2",
            dni="40111222",
            cantidad=3,
        )
        ajeno = self.crear_pedido(
            session_key="historial-ajeno",
            dni="40222333",
        )

        response = self.client.get(self.url_cliente(primero.cliente))
        formset = self.inline_formset_pedidos(response)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historial de Pedidos")
        self.assertContains(response, primero.numero_pedido, count=1)
        self.assertContains(response, segundo.numero_pedido, count=1)
        self.assertNotContains(response, ajeno.numero_pedido)
        self.assertEqual(
            list(formset.queryset),
            [segundo, primero],
        )
        self.assertEqual(formset.total_form_count(), formset.initial_form_count())
        self.assertNotContains(response, 'class="add-row"')
        self.assertNotContains(response, f"{formset.prefix}-0-estado")
        self.assertNotContains(response, "Marcar como entregado")
        self.assertNotContains(response, "Cancelar Pedido")

    def test_historial_usa_orden_reciente_y_desempate_por_pk_descendente(self):
        anterior = self.crear_pedido(
            session_key="historial-orden-anterior",
            dni="41333444",
        )
        empate_uno = self.crear_pedido(
            session_key="historial-orden-empate-1",
            dni="41333444",
        )
        empate_dos = self.crear_pedido(
            session_key="historial-orden-empate-2",
            dni="41333444",
        )
        instante = timezone.now() - timedelta(hours=1)
        Pedido.objects.filter(pk=anterior.pk).update(
            fecha_hora_creacion=instante - timedelta(days=1)
        )
        Pedido.objects.filter(pk__in=(empate_uno.pk, empate_dos.pk)).update(
            fecha_hora_creacion=instante
        )

        response = self.client.get(self.url_cliente(anterior.cliente))
        pedidos = list(self.inline_formset_pedidos(response).queryset)

        self.assertEqual(
            [pedido.pk for pedido in pedidos],
            [empate_dos.pk, empate_uno.pk, anterior.pk],
        )

    def test_navegacion_cliente_pedido_usa_admin_existente_con_permiso(self):
        pedido = self.crear_pedido(
            session_key="historial-enlace-pedido",
            dni="42444555",
        )
        self.client.force_login(self.lectora_ambos)

        response = self.client.get(self.url_cliente(pedido.cliente))

        self.assertContains(
            response,
            f'href="{self.url_pedido(pedido)}"',
        )
        self.assertContains(response, pedido.numero_pedido, count=1)
        self.assertEqual(self.client.get(self.url_pedido(pedido)).status_code, 200)

    def test_sin_permiso_pedido_no_muestra_historial_ni_enlaces(self):
        pedido = self.crear_pedido(
            session_key="historial-sin-permiso-pedido",
            dni="43555666",
        )
        self.client.force_login(self.solo_clientes)

        response = self.client.get(self.url_cliente(pedido.cliente))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Historial de Pedidos")
        self.assertNotContains(response, pedido.numero_pedido)
        self.assertNotContains(response, self.url_pedido(pedido))
        self.assertEqual(self.client.get(self.url_pedido(pedido)).status_code, 403)

    def test_pedido_enlaza_cliente_actual_solo_con_permiso(self):
        pedido = self.crear_pedido(
            session_key="historial-enlace-cliente",
            dni="44666777",
            nombre="Nombre histórico",
            apellido="Apellido histórico",
        )
        url_cliente = self.url_cliente(pedido.cliente)

        self.client.force_login(self.lectora_ambos)
        autorizado = self.client.get(self.url_pedido(pedido))
        self.assertContains(autorizado, "Cliente actual asociado")
        self.assertContains(autorizado, f'href="{url_cliente}"')
        self.assertContains(autorizado, pedido.cliente.dni)
        self.assertContains(autorizado, pedido.nombre_cliente)
        self.assertContains(autorizado, pedido.apellido_cliente)
        self.assertEqual(self.client.get(url_cliente).status_code, 200)

        self.client.force_login(self.solo_pedidos)
        no_autorizado = self.client.get(self.url_pedido(pedido))
        self.assertEqual(no_autorizado.status_code, 200)
        self.assertContains(no_autorizado, "Cliente actual asociado")
        self.assertContains(no_autorizado, "Cliente asociado")
        self.assertNotContains(no_autorizado, f'href="{url_cliente}"')
        self.assertEqual(self.client.get(url_cliente).status_code, 403)

    def test_checkout_posterior_preserva_snapshots_y_asociacion_historica(self):
        anterior = self.crear_pedido(
            session_key="historial-snapshot-anterior",
            dni="45777888",
            nombre="Nombre anterior",
            apellido="Apellido anterior",
            telefono="341 100 1000",
        )
        reciente = self.crear_pedido(
            session_key="historial-snapshot-reciente",
            dni="45777888",
            nombre="Nombre actual",
            apellido="Apellido actual",
            telefono="341 200 2000",
        )
        anterior.refresh_from_db()
        reciente.refresh_from_db()
        cliente = reciente.cliente

        self.assertEqual(anterior.cliente_id, cliente.pk)
        self.assertEqual(reciente.cliente_id, cliente.pk)
        self.assertEqual(anterior.nombre_cliente, "Nombre anterior")
        self.assertEqual(anterior.apellido_cliente, "Apellido anterior")
        self.assertEqual(anterior.telefono_cliente, "341 100 1000")

        self.client.force_login(self.lectora_ambos)
        detalle_cliente = self.client.get(self.url_cliente(cliente))
        self.assertContains(detalle_cliente, "Nombre actual")
        self.assertContains(detalle_cliente, "Apellido actual")
        self.assertContains(detalle_cliente, anterior.numero_pedido)
        self.assertContains(detalle_cliente, reciente.numero_pedido)

        detalle_anterior = self.client.get(self.url_pedido(anterior))
        self.assertContains(detalle_anterior, "Nombre anterior")
        self.assertContains(detalle_anterior, "Apellido anterior")
        self.assertContains(detalle_anterior, "341 100 1000")
        anterior.refresh_from_db()
        self.assertEqual(anterior.nombre_cliente, "Nombre anterior")

    def test_historial_no_introduce_n_mas_uno(self):
        primero = self.crear_pedido(
            session_key="historial-consultas-1",
            dni="46888999",
        )
        self.client.get(self.url_cliente(primero.cliente))
        with CaptureQueriesContext(connection) as consultas_un_pedido:
            self.client.get(self.url_cliente(primero.cliente))

        for indice in range(2, 6):
            self.crear_pedido(
                session_key=f"historial-consultas-{indice}",
                dni="46888999",
            )
        with CaptureQueriesContext(connection) as consultas_cinco_pedidos:
            self.client.get(self.url_cliente(primero.cliente))

        self.assertEqual(
            len(consultas_un_pedido),
            len(consultas_cinco_pedidos),
        )

    def test_datos_actuales_y_snapshots_se_escapan_en_navegacion(self):
        nombre = 'Ana <script>alert("cliente")</script>'
        apellido = 'Coronado <img src=x onerror="alert(1)">'
        pedido = self.crear_pedido(
            session_key="historial-autoescape",
            dni="47999000",
            nombre=nombre,
            apellido=apellido,
        )

        for response in (
            self.client.get(self.url_cliente(pedido.cliente)),
            self.client.get(self.url_pedido(pedido)),
        ):
            self.assertContains(response, html.escape(nombre))
            self.assertContains(response, html.escape(apellido))
            self.assertNotContains(response, '<script>alert("cliente")</script>')
            self.assertNotContains(response, '<img src=x onerror="alert(1)">')

    def test_admins_conservan_protecciones_y_transiciones_existentes(self):
        pedido = self.crear_pedido(
            session_key="historial-regresion-admins",
            dni="48111000",
        )
        request = RequestFactory().get(self.url_cliente(pedido.cliente))
        request.user = self.administradora

        self.assertIsInstance(self.cliente_admin, ClienteAdmin)
        self.assertFalse(self.cliente_admin.has_add_permission(request))
        self.assertFalse(
            self.cliente_admin.has_change_permission(request, pedido.cliente)
        )
        self.assertFalse(
            self.cliente_admin.has_delete_permission(request, pedido.cliente)
        )
        self.assertIsInstance(self.pedido_admin, PedidoAdmin)
        detalle_pedido = self.client.get(self.url_pedido(pedido))
        self.assertContains(
            detalle_pedido,
            reverse(
                "admin:orders_pedido_marcar_entregado",
                args=(pedido.pk,),
            ),
        )
        self.assertContains(
            detalle_pedido,
            reverse("admin:orders_pedido_cancelar", args=(pedido.pk,)),
        )
