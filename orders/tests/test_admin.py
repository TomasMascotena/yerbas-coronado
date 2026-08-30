from datetime import timedelta
import shutil
import tempfile
import warnings

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cart.services import agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from inventory.admin import InventarioAdmin, MovimientoInventarioAdmin
from inventory.models import Inventario, MovimientoInventario
from orders.admin import (
    DetallePedidoInline,
    DireccionEnvioInline,
    MovimientoPedidoInline,
    PedidoAdmin,
)
from orders.models import EstadoPedido, ModalidadEntrega, Pedido
from orders.services import (
    DatosDireccionEnvio,
    cancelar_pedido,
    crear_pedido_desde_carrito,
)
from orders.tests.helpers import crear_carrito_checkout, datos_comprador


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class PedidoAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administradora = get_user_model().objects.create_superuser(
            username="administradora-pedidos",
            email="",
            password=None,
        )
        cls.personal_sin_permiso = get_user_model().objects.create_user(
            username="personal-sin-pedidos",
            is_staff=True,
            password=None,
        )
        cls.lectora = get_user_model().objects.create_user(
            username="lectora-pedidos",
            is_staff=True,
            password=None,
        )
        cls.lectora.user_permissions.add(
            Permission.objects.get(
                codename="view_pedido",
                content_type__app_label="orders",
            )
        )

    def setUp(self):
        self.client.force_login(self.administradora)
        self.pedido_admin = admin.site._registry[Pedido]
        self.request = RequestFactory().get("/admin/orders/pedido/")
        self.request.user = self.administradora
        self.lista_url = reverse("admin:orders_pedido_changelist")

    def crear_pedido(
        self,
        *,
        session_key,
        dni,
        nombre="Ana",
        apellido="Coronado",
        telefono="+54 11 4567 8901",
        modalidad=ModalidadEntrega.RETIRO,
        direccion=None,
        cantidad=2,
        nombre_producto="Canarias",
    ):
        carrito, producto = crear_carrito_checkout(
            session_key=session_key,
            cantidad=cantidad,
            stock=20,
            nombre=nombre_producto,
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
        return resultado.pedido, producto

    def crear_pedido_con_dos_productos(self):
        carrito, primero = crear_carrito_checkout(
            session_key="admin-dos-productos",
            cantidad=2,
            stock=20,
            nombre="Canarias",
        )
        segundo = crear_producto_con_stock(nombre="Baldo", stock=20)
        agregar_producto(
            session_key=carrito.session_key,
            producto_id=segundo.pk,
            cantidad=3,
        )
        carrito.refresh_from_db()
        resultado = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(dni="22333444"),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        )
        return resultado.pedido, primero, segundo

    def url_detalle(self, pedido):
        return reverse("admin:orders_pedido_change", args=(pedido.pk,))

    def test_registro_y_configuracion_de_consulta(self):
        self.assertTrue(admin.site.is_registered(Pedido))
        self.assertIsInstance(self.pedido_admin, PedidoAdmin)
        self.assertEqual(
            self.pedido_admin.list_display,
            (
                "numero_pedido",
                "fecha_hora_creacion",
                "estado",
                "nombre_cliente",
                "apellido_cliente",
                "dni_cliente",
                "modalidad_entrega",
                "cantidad_total",
                "importe_total",
            ),
        )
        self.assertEqual(
            self.pedido_admin.search_fields,
            (
                "numero_pedido",
                "dni_cliente",
                "nombre_cliente",
                "apellido_cliente",
                "telefono_cliente",
            ),
        )
        self.assertEqual(
            self.pedido_admin.list_filter,
            (
                "estado",
                "modalidad_entrega",
                ("fecha_hora_creacion", admin.DateFieldListFilter),
            ),
        )
        self.assertEqual(
            self.pedido_admin.ordering,
            ("-fecha_hora_creacion", "-pk"),
        )

    def test_acceso_exige_autenticacion_y_permiso_estandar_de_vista(self):
        self.client.logout()
        anonima = self.client.get(self.lista_url)
        self.assertEqual(anonima.status_code, 302)
        self.assertIn(reverse("admin:login"), anonima.url)

        self.client.force_login(self.personal_sin_permiso)
        self.assertEqual(self.client.get(self.lista_url).status_code, 403)

        self.client.force_login(self.lectora)
        self.assertEqual(self.client.get(self.lista_url).status_code, 200)

    def test_permiso_de_vista_del_pedido_incluye_todos_los_inlines_historicos(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-lectora-detalle",
            dni="20999111",
            modalidad=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion=DatosDireccionEnvio(
                calle="Belgrano",
                numero="321",
                localidad="Rosario",
                provincia="Santa Fe",
            ),
            nombre_producto="Canarias lectura completa",
        )
        cancelar_pedido(pedido_id=pedido.pk)
        self.client.force_login(self.lectora)

        response = self.client.get(self.url_detalle(pedido))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dirección de envío histórica")
        self.assertContains(response, "Belgrano")
        self.assertContains(response, "Detalles históricos del Pedido")
        self.assertContains(response, producto.nombre)
        self.assertContains(response, "Movimientos de Inventario asociados")
        self.assertContains(response, "Venta por pedido")
        self.assertContains(response, "Cancelación de pedido")
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'class="add-row"')

    def test_listado_muestra_pedidos_y_aplica_orden_descendente(self):
        anterior, _ = self.crear_pedido(
            session_key="admin-anterior",
            dni="20111222",
            nombre_producto="Canarias anterior",
        )
        reciente, _ = self.crear_pedido(
            session_key="admin-reciente",
            dni="20111223",
            nombre_producto="Canarias reciente",
        )
        Pedido.objects.filter(pk=anterior.pk).update(
            fecha_hora_creacion=timezone.now() - timedelta(days=2)
        )

        response = self.client.get(self.lista_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, anterior.numero_pedido)
        self.assertContains(response, reciente.numero_pedido)
        resultados = list(response.context["cl"].result_list)
        self.assertEqual(
            [pedido.pk for pedido in resultados],
            [reciente.pk, anterior.pk],
        )

    def test_busqueda_por_numero_y_datos_historicos_del_comprador(self):
        objetivo, _ = self.crear_pedido(
            session_key="admin-busqueda-objetivo",
            dni="24555666",
            nombre="Mariela",
            apellido="Suarez",
            telefono="+54 341 555 0123",
            nombre_producto="Amanda búsqueda",
        )
        otro, _ = self.crear_pedido(
            session_key="admin-busqueda-otro",
            dni="24777888",
            nombre="Pedro",
            apellido="Gomez",
            telefono="+54 11 4444 5555",
            nombre_producto="Baldo búsqueda",
        )

        consultas = (
            objetivo.numero_pedido,
            objetivo.dni_cliente,
            objetivo.nombre_cliente,
            objetivo.apellido_cliente,
            objetivo.telefono_cliente,
        )
        for consulta in consultas:
            with self.subTest(consulta=consulta):
                response = self.client.get(self.lista_url, {"q": consulta})
                self.assertContains(response, objetivo.numero_pedido)
                self.assertNotContains(response, otro.numero_pedido)

    def test_filtros_por_estado_modalidad_y_fecha(self):
        retiro, _ = self.crear_pedido(
            session_key="admin-filtro-retiro",
            dni="26777888",
            nombre_producto="Retiro filtro",
        )
        envio, _ = self.crear_pedido(
            session_key="admin-filtro-envio",
            dni="26777889",
            modalidad=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion=DatosDireccionEnvio(
                calle="San Martín",
                numero="123",
                localidad="Rosario",
                provincia="Santa Fe",
            ),
            nombre_producto="Envío filtro",
        )
        cancelar_pedido(pedido_id=retiro.pk)
        Pedido.objects.filter(pk=retiro.pk).update(
            fecha_hora_creacion=timezone.now() - timedelta(days=10)
        )

        por_estado = self.client.get(self.lista_url, {"estado": EstadoPedido.CANCELADO})
        self.assertContains(por_estado, retiro.numero_pedido)
        self.assertNotContains(por_estado, envio.numero_pedido)

        por_modalidad = self.client.get(
            self.lista_url,
            {"modalidad_entrega": ModalidadEntrega.ENVIO_DOMICILIO},
        )
        self.assertContains(por_modalidad, envio.numero_pedido)
        self.assertNotContains(por_modalidad, retiro.numero_pedido)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            por_fecha = self.client.get(
                self.lista_url,
                {
                    "fecha_hora_creacion__gte": (
                        timezone.localdate() - timedelta(days=1)
                    ).isoformat()
                },
            )
        self.assertContains(por_fecha, envio.numero_pedido)
        self.assertNotContains(por_fecha, retiro.numero_pedido)

    def test_detalle_muestra_cabecera_comprador_totales_y_un_producto(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-detalle",
            dni="27888999",
            nombre="Lucía",
            apellido="Paz",
            telefono="341 555 6789",
            cantidad=2,
            nombre_producto="Canarias detalle",
        )

        response = self.client.get(self.url_detalle(pedido))

        self.assertEqual(response.status_code, 200)
        for valor in (
            pedido.numero_pedido,
            pedido.nombre_cliente,
            pedido.apellido_cliente,
            pedido.dni_cliente,
            pedido.telefono_cliente,
            producto.nombre,
            pedido.cantidad_total,
        ):
            with self.subTest(valor=valor):
                self.assertContains(response, str(valor))
        self.assertEqual(
            response.context["original"].importe_total,
            pedido.importe_total,
        )
        self.assertContains(response, "10000,00")

    def test_detalle_muestra_multiples_productos_y_movimientos_de_venta(self):
        pedido, primero, segundo = self.crear_pedido_con_dos_productos()

        response = self.client.get(self.url_detalle(pedido))

        self.assertContains(response, primero.nombre)
        self.assertContains(response, segundo.nombre)
        self.assertEqual(pedido.detalles.count(), 2)
        self.assertEqual(pedido.movimientos_inventario.count(), 2)
        self.assertContains(response, "Venta por pedido", count=2)

    def test_direccion_solo_aparece_para_envio_a_domicilio(self):
        retiro, _ = self.crear_pedido(
            session_key="admin-sin-direccion",
            dni="28888111",
            nombre_producto="Retiro sin dirección",
        )
        envio, _ = self.crear_pedido(
            session_key="admin-con-direccion",
            dni="28888112",
            modalidad=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion=DatosDireccionEnvio(
                calle="Córdoba",
                numero="456",
                localidad="Rosario",
                provincia="Santa Fe",
                piso="3",
                departamento="B",
            ),
            nombre_producto="Envío con dirección",
        )

        respuesta_retiro = self.client.get(self.url_detalle(retiro))
        respuesta_envio = self.client.get(self.url_detalle(envio))

        self.assertNotContains(respuesta_retiro, "Dirección de envío histórica")
        self.assertContains(respuesta_envio, "Dirección de envío histórica")
        self.assertContains(respuesta_envio, "Córdoba")
        self.assertContains(respuesta_envio, "456")

    def test_cancelado_muestra_venta_y_movimiento_compensatorio(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-cancelado",
            dni="29888111",
            nombre_producto="Pedido cancelado",
        )
        cancelar_pedido(pedido_id=pedido.pk)

        response = self.client.get(self.url_detalle(pedido))

        self.assertContains(response, "Cancelado")
        self.assertContains(response, "Venta por pedido")
        self.assertContains(response, "Cancelación de pedido")

    def test_detalle_usa_snapshots_aunque_cambien_cliente_y_producto(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-snapshots",
            dni="30888111",
            nombre="Nombre histórico",
            apellido="Apellido histórico",
            nombre_producto="Producto histórico",
        )
        pedido.cliente.nombre = "Nombre actual"
        pedido.cliente.apellido = "Apellido actual"
        pedido.cliente.save(update_fields=("nombre", "apellido"))
        producto.nombre = "Producto actual"
        producto.peso = "250 g"
        producto.save(update_fields=("nombre", "peso"))

        response = self.client.get(self.url_detalle(pedido))

        self.assertContains(response, "Nombre histórico")
        self.assertContains(response, "Apellido histórico")
        self.assertContains(response, "Producto histórico")
        self.assertContains(response, pedido.detalles.get().peso_producto)

    def test_no_permite_crear_editar_ni_eliminar_individualmente(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-proteccion-individual",
            dni="31888111",
            nombre_producto="Pedido protegido",
        )
        detalle_url = self.url_detalle(pedido)
        eliminar_url = reverse("admin:orders_pedido_delete", args=(pedido.pk,))

        self.assertFalse(self.pedido_admin.has_add_permission(self.request))
        self.assertFalse(self.pedido_admin.has_change_permission(self.request, pedido))
        self.assertFalse(self.pedido_admin.has_delete_permission(self.request, pedido))
        self.assertEqual(
            self.client.get(reverse("admin:orders_pedido_add")).status_code,
            403,
        )
        respuesta_post = self.client.post(
            detalle_url,
            {"estado": EstadoPedido.ENTREGADO},
        )
        self.assertEqual(respuesta_post.status_code, 403)
        self.assertEqual(self.client.get(eliminar_url).status_code, 403)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

        respuesta_get = self.client.get(detalle_url)
        self.assertNotContains(respuesta_get, 'name="_save"')
        self.assertNotContains(respuesta_get, eliminar_url)

    def test_inlines_historicos_no_permiten_altas_cambios_ni_bajas(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-proteccion-inlines",
            dni="32888111",
            modalidad=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion=DatosDireccionEnvio(
                calle="Mitre",
                numero="789",
                localidad="Rosario",
                provincia="Santa Fe",
            ),
            nombre_producto="Pedido con inlines",
        )
        for clase_inline in (
            DireccionEnvioInline,
            DetallePedidoInline,
            MovimientoPedidoInline,
        ):
            with self.subTest(inline=clase_inline.__name__):
                inline = clase_inline(Pedido, admin.site)
                self.assertFalse(inline.has_add_permission(self.request, pedido))
                self.assertFalse(inline.has_change_permission(self.request, pedido))
                self.assertFalse(inline.has_delete_permission(self.request, pedido))

        response = self.client.get(self.url_detalle(pedido))
        self.assertNotContains(response, 'name="detalles-0-DELETE"')
        self.assertNotContains(response, 'name="movimientos_inventario-0-DELETE"')
        self.assertNotContains(response, 'class="add-row"')

    def test_no_hay_acciones_masivas_ni_eliminacion_por_post_forzado(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-proteccion-masiva",
            dni="33888111",
            nombre_producto="Pedido acción masiva",
        )

        self.assertNotIn("delete_selected", self.pedido_admin.get_actions(self.request))
        response = self.client.post(
            self.lista_url,
            {
                "action": "delete_selected",
                "_selected_action": [pedido.pk],
                "index": 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Pedido.objects.filter(pk=pedido.pk).exists())

    def test_querysets_precargan_relaciones_del_listado_y_los_inlines(self):
        queryset = self.pedido_admin.get_queryset(self.request)
        self.assertIn("cliente", queryset.query.select_related)
        self.assertIn("direccion_envio", queryset.query.select_related)

        detalle_inline = DetallePedidoInline(Pedido, admin.site)
        movimiento_inline = MovimientoPedidoInline(Pedido, admin.site)
        self.assertIn(
            "producto",
            detalle_inline.get_queryset(self.request).query.select_related,
        )
        movimiento_select_related = movimiento_inline.get_queryset(
            self.request
        ).query.select_related
        self.assertIn("inventario", movimiento_select_related)

    def test_admins_existentes_de_inventario_conservan_su_configuracion(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-regresion-inventario",
            dni="34888111",
            nombre_producto="Pedido integración inventario",
        )
        movimiento = pedido.movimientos_inventario.get()
        inventario_admin = admin.site._registry[Inventario]
        movimiento_admin = admin.site._registry[MovimientoInventario]

        self.assertIsInstance(inventario_admin, InventarioAdmin)
        self.assertIsInstance(movimiento_admin, MovimientoInventarioAdmin)
        self.assertFalse(inventario_admin.has_change_permission(self.request))
        self.assertFalse(movimiento_admin.has_change_permission(self.request))
        self.assertEqual(
            self.client.get(
                reverse("admin:inventory_inventario_changelist")
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "admin:inventory_movimientoinventario_change",
                    args=(movimiento.pk,),
                )
            ).status_code,
            200,
        )
