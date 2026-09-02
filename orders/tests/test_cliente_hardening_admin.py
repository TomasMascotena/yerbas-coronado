from html.parser import HTMLParser
import shutil
import tempfile
from urllib.parse import quote, quote_plus

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from orders.models import EstadoPedido, ModalidadEntrega, Pedido
from orders.services import (
    DatosDireccionEnvio,
    crear_pedido_desde_carrito,
)
from orders.tests.helpers import crear_carrito_checkout, datos_comprador


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


class _ExtractorEnlaces(HTMLParser):
    def __init__(self):
        super().__init__()
        self.enlaces = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            atributos = dict(attrs)
            if "href" in atributos:
                self.enlaces.append(atributos["href"])


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ClienteAdminHardeningTests(TestCase):
    def setUp(self):
        self.administradora = get_user_model().objects.create_superuser(
            username="administradora-hardening-clientes",
            email="",
            password=None,
        )
        self.client.force_login(self.administradora)
        self.permisos = {
            codigo: Permission.objects.get(
                codename=codigo,
                content_type__app_label="orders",
            )
            for codigo in (
                "view_cliente",
                "change_cliente",
                "view_pedido",
                "change_pedido",
            )
        }

    def crear_pedido(
        self,
        *,
        session_key,
        dni,
        nombre="Ana",
        apellido="Coronado",
        telefono="341 555 1234",
        modalidad=ModalidadEntrega.RETIRO,
        direccion=None,
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
            modalidad_entrega=modalidad,
            direccion_envio=direccion,
        )
        return resultado.pedido

    def crear_usuario(self, nombre, *permisos):
        usuario = get_user_model().objects.create_user(
            username=f"matriz-{nombre}",
            is_staff=True,
            password=None,
        )
        usuario.user_permissions.add(
            *(self.permisos[codigo] for codigo in permisos)
        )
        return usuario

    def url_cliente(self, cliente):
        return reverse("admin:orders_cliente_change", args=(cliente.pk,))

    def url_pedido(self, pedido):
        return reverse("admin:orders_pedido_change", args=(pedido.pk,))

    def test_matriz_completa_de_permisos_cliente_pedido(self):
        pedido = self.crear_pedido(
            session_key="hardening-matriz-permisos",
            dni="50111222",
        )
        url_cliente = self.url_cliente(pedido.cliente)
        url_pedido = self.url_pedido(pedido)
        casos = (
            ("ninguno", (), False, False),
            ("vista-cliente", ("view_cliente",), True, False),
            ("cambio-cliente", ("change_cliente",), True, False),
            ("vista-pedido", ("view_pedido",), False, True),
            (
                "vista-ambos",
                ("view_cliente", "view_pedido"),
                True,
                True,
            ),
            (
                "cambio-ambos",
                ("change_cliente", "change_pedido"),
                True,
                True,
            ),
            (
                "todos",
                (
                    "view_cliente",
                    "change_cliente",
                    "view_pedido",
                    "change_pedido",
                ),
                True,
                True,
            ),
        )

        for nombre, permisos, consulta_cliente, consulta_pedido in casos:
            with self.subTest(caso=nombre):
                usuario = self.crear_usuario(nombre, *permisos)
                self.client.force_login(usuario)

                detalle_cliente = self.client.get(url_cliente)
                self.assertEqual(
                    detalle_cliente.status_code,
                    200 if consulta_cliente else 403,
                )
                if consulta_cliente:
                    if consulta_pedido:
                        self.assertContains(
                            detalle_cliente,
                            f'href="{url_pedido}"',
                        )
                        self.assertContains(
                            detalle_cliente,
                            pedido.numero_pedido,
                            count=1,
                        )
                    else:
                        self.assertNotContains(
                            detalle_cliente,
                            "Historial de Pedidos",
                        )
                        self.assertNotContains(
                            detalle_cliente,
                            pedido.numero_pedido,
                        )
                    self.assertEqual(
                        self.client.post(
                            url_cliente,
                            {"nombre": "Cambio prohibido"},
                        ).status_code,
                        403,
                    )

                detalle_pedido = self.client.get(url_pedido)
                self.assertEqual(
                    detalle_pedido.status_code,
                    200 if consulta_pedido else 403,
                )
                if consulta_pedido:
                    if consulta_cliente:
                        self.assertContains(
                            detalle_pedido,
                            f'href="{url_cliente}"',
                        )
                    else:
                        self.assertNotContains(
                            detalle_pedido,
                            f'href="{url_cliente}"',
                        )
                    self.assertEqual(
                        self.client.post(
                            url_pedido,
                            {"estado": EstadoPedido.CANCELADO},
                        ).status_code,
                        403,
                    )
                    url_transicion = reverse(
                        "admin:orders_pedido_marcar_entregado",
                        args=(pedido.pk,),
                    )
                    if "change_pedido" in permisos:
                        self.assertContains(detalle_pedido, url_transicion)
                    else:
                        self.assertNotContains(detalle_pedido, url_transicion)

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

    def test_post_manipulado_no_modifica_cliente_inline_ni_pedidos(self):
        pedido = self.crear_pedido(
            session_key="hardening-post-objetivo",
            dni="51222333",
        )
        ajeno = self.crear_pedido(
            session_key="hardening-post-ajeno",
            dni="52333444",
        )
        cantidad_pedidos = Pedido.objects.count()

        response = self.client.post(
            self.url_cliente(pedido.cliente),
            {
                "dni": "99999999",
                "nombre": "Cliente alterado",
                "apellido": "Apellido alterado",
                "telefono": "341 999 9999",
                "pedidos-TOTAL_FORMS": "2",
                "pedidos-INITIAL_FORMS": "1",
                "pedidos-MIN_NUM_FORMS": "0",
                "pedidos-MAX_NUM_FORMS": "1000",
                "pedidos-0-id": str(pedido.pk),
                "pedidos-0-cliente": str(ajeno.cliente_id),
                "pedidos-0-estado": EstadoPedido.CANCELADO,
                "pedidos-0-DELETE": "on",
                "pedidos-1-id": "",
                "pedidos-1-cliente": str(pedido.cliente_id),
                "pedidos-1-estado": EstadoPedido.ENTREGADO,
            },
        )

        self.assertEqual(response.status_code, 403)
        pedido.cliente.refresh_from_db()
        pedido.refresh_from_db()
        ajeno.refresh_from_db()
        self.assertEqual(pedido.cliente.dni, "51222333")
        self.assertEqual(pedido.cliente.nombre, "Ana")
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(pedido.cliente_id, pedido.cliente.pk)
        self.assertEqual(ajeno.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(Pedido.objects.count(), cantidad_pedidos)

    def test_endpoints_crud_y_eliminacion_masiva_permanecen_bloqueados(self):
        pedido = self.crear_pedido(
            session_key="hardening-endpoints-cliente",
            dni="53444555",
        )
        cliente = pedido.cliente
        url_alta = reverse("admin:orders_cliente_add")
        url_detalle = self.url_cliente(cliente)
        url_eliminar = reverse(
            "admin:orders_cliente_delete",
            args=(cliente.pk,),
        )
        url_lista = reverse("admin:orders_cliente_changelist")

        for metodo, url, datos in (
            (self.client.get, url_alta, None),
            (self.client.post, url_alta, {"dni": "54555666"}),
            (self.client.post, url_detalle, {"nombre": "Alterado"}),
            (self.client.get, url_eliminar, None),
            (self.client.post, url_eliminar, {"post": "yes"}),
        ):
            with self.subTest(metodo=metodo.__name__, url=url):
                response = metodo(url, datos) if datos is not None else metodo(url)
                self.assertEqual(response.status_code, 403)

        masiva = self.client.post(
            url_lista,
            {
                "action": "delete_selected",
                "_selected_action": [cliente.pk],
                "index": 0,
            },
        )
        self.assertEqual(masiva.status_code, 200)
        cliente.refresh_from_db()
        self.assertEqual(cliente.dni, "53444555")

    def test_parametros_no_permiten_mezclar_historiales_de_clientes(self):
        primero = self.crear_pedido(
            session_key="hardening-aislamiento-1",
            dni="55666777",
        )
        segundo = self.crear_pedido(
            session_key="hardening-aislamiento-2",
            dni="55666777",
        )
        ajeno = self.crear_pedido(
            session_key="hardening-aislamiento-ajeno",
            dni="56777888",
        )

        response = self.client.get(
            self.url_cliente(primero.cliente),
            {
                "cliente": ajeno.cliente_id,
                "pedido": ajeno.pk,
                "pedidos-0-id": ajeno.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, primero.numero_pedido)
        self.assertContains(response, segundo.numero_pedido)
        self.assertNotContains(response, ajeno.numero_pedido)
        formset = next(
            inline.formset
            for inline in response.context["inline_admin_formsets"]
            if inline.opts.model is Pedido
        )
        self.assertEqual(
            {pedido.pk for pedido in formset.queryset},
            {primero.pk, segundo.pk},
        )

    def test_datos_tecnicos_y_personales_no_se_exponen_en_urls_propias(self):
        direccion = DatosDireccionEnvio(
            calle="Calle Privada",
            numero="742",
            localidad="Rosario",
            provincia="Santa Fe",
        )
        pedido = self.crear_pedido(
            session_key="hardening-privacidad",
            dni="57888999",
            telefono="341 555 7788",
            modalidad=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion=direccion,
        )

        for response in (
            self.client.get(self.url_cliente(pedido.cliente)),
            self.client.get(self.url_pedido(pedido)),
        ):
            contenido = response.content.decode()
            self.assertNotIn(str(pedido.token_idempotencia), contenido)
            self.assertNotIn(pedido.huella_sesion_origen, contenido)
            self.assertNotIn("token_idempotencia", contenido)
            self.assertNotIn("huella_sesion_origen", contenido)

            extractor = _ExtractorEnlaces()
            extractor.feed(contenido)
            for enlace in extractor.enlaces:
                self.assertNotIn(pedido.dni_cliente, enlace)
                self.assertNotIn(pedido.telefono_cliente, enlace)
                self.assertNotIn("3415557788", enlace)
                self.assertNotIn(quote(pedido.telefono_cliente), enlace)
                self.assertNotIn(quote_plus(pedido.telefono_cliente), enlace)
                self.assertNotIn(direccion.calle, enlace)
                self.assertNotIn(quote(direccion.calle), enlace)
                self.assertNotIn(quote_plus(direccion.calle), enlace)
                self.assertNotIn(direccion.localidad, enlace)

    def test_checkout_actualiza_cliente_sin_alterar_dni_telefono_historicos(self):
        anterior = self.crear_pedido(
            session_key="hardening-snapshot-anterior",
            dni="58999000",
            nombre="Nombre anterior",
            apellido="Apellido anterior",
            telefono="341 100 1000",
        )
        reciente = self.crear_pedido(
            session_key="hardening-snapshot-reciente",
            dni="58.999.000",
            nombre="Nombre actual",
            apellido="Apellido actual",
            telefono="341 200 2000",
        )
        anterior.refresh_from_db()
        reciente.refresh_from_db()
        cliente = reciente.cliente

        self.assertEqual(anterior.cliente_id, reciente.cliente_id)
        self.assertEqual(cliente.dni, "58999000")
        self.assertEqual(cliente.telefono, "341 200 2000")
        self.assertEqual(anterior.dni_cliente, "58999000")
        self.assertEqual(anterior.telefono_cliente, "341 100 1000")
        self.assertEqual(reciente.telefono_cliente, "341 200 2000")

        detalle_cliente = self.client.get(self.url_cliente(cliente))
        detalle_anterior = self.client.get(self.url_pedido(anterior))
        self.assertContains(detalle_cliente, "341 200 2000")
        self.assertContains(detalle_cliente, anterior.numero_pedido)
        self.assertContains(detalle_cliente, reciente.numero_pedido)
        self.assertContains(detalle_anterior, "341 100 1000")
        self.assertContains(detalle_anterior, "Nombre anterior")
        self.assertNotContains(detalle_anterior, "341 200 2000")

    def test_consultas_historial_comparan_escenarios_equivalentes(self):
        uno = self.crear_pedido(
            session_key="hardening-consultas-uno",
            dni="59111000",
        )
        cinco = self.crear_pedido(
            session_key="hardening-consultas-cinco-1",
            dni="59222000",
        )
        for indice in range(2, 6):
            self.crear_pedido(
                session_key=f"hardening-consultas-cinco-{indice}",
                dni="59222000",
            )
        url_uno = self.url_cliente(uno.cliente)
        url_cinco = self.url_cliente(cinco.cliente)
        self.client.get(url_uno)
        self.client.get(url_cinco)

        with CaptureQueriesContext(connection) as consultas_uno:
            response_uno = self.client.get(url_uno)
        with CaptureQueriesContext(connection) as consultas_cinco:
            response_cinco = self.client.get(url_cinco)

        self.assertEqual(response_uno.status_code, 200)
        self.assertEqual(response_cinco.status_code, 200)
        self.assertGreater(len(consultas_uno), 0)
        self.assertEqual(len(consultas_uno), len(consultas_cinco))
