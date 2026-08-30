from datetime import timedelta
import shutil
import tempfile
import warnings
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cart.services import agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from inventory.admin import InventarioAdmin, MovimientoInventarioAdmin
from inventory.exceptions import (
    CapacidadInventarioExcedida as CapacidadInventarioExcedidaInventory,
)
from inventory.models import (
    Inventario,
    MovimientoInventario,
    TipoMovimientoInventario,
)
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
    marcar_pedido_entregado,
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
        cls.operadora = get_user_model().objects.create_user(
            username="operadora-pedidos",
            is_staff=True,
            password=None,
        )
        cls.operadora.user_permissions.add(
            Permission.objects.get(
                codename="change_pedido",
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

    def url_entregar(self, pedido):
        return reverse(
            "admin:orders_pedido_marcar_entregado",
            args=(pedido.pk,),
        )

    def url_cancelar(self, pedido):
        return reverse("admin:orders_pedido_cancelar", args=(pedido.pk,))

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

    def test_superusuaria_ve_ambas_transiciones_en_pedido_pendiente(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-botones-pendiente",
            dni="35888111",
            nombre_producto="Pedido con transiciones",
        )

        response = self.client.get(self.url_detalle(pedido))

        self.assertContains(response, self.url_entregar(pedido))
        self.assertContains(response, self.url_cancelar(pedido))
        self.assertContains(response, "Marcar como entregado")
        self.assertContains(response, "Cancelar Pedido")

    def test_lectora_no_ve_botones_y_rutas_directas_responden_403(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-lectora-transiciones",
            dni="35888112",
            nombre_producto="Pedido sólo lectura",
        )
        self.client.force_login(self.lectora)

        detalle = self.client.get(self.url_detalle(pedido))

        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(detalle, self.url_entregar(pedido))
        self.assertNotContains(detalle, self.url_cancelar(pedido))
        self.assertEqual(self.client.get(self.url_entregar(pedido)).status_code, 403)
        self.assertEqual(self.client.get(self.url_cancelar(pedido)).status_code, 403)
        self.assertEqual(self.client.post(self.url_entregar(pedido)).status_code, 403)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

    def test_permiso_change_autoriza_operar_pero_no_edicion_directa(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-operadora-change",
            dni="35888113",
            nombre_producto="Pedido operable",
        )
        self.client.force_login(self.operadora)
        request = RequestFactory().get(self.url_detalle(pedido))
        request.user = self.operadora

        self.assertEqual(self.client.get(self.url_detalle(pedido)).status_code, 200)
        self.assertEqual(self.client.get(self.url_entregar(pedido)).status_code, 200)
        self.assertFalse(self.pedido_admin.has_change_permission(request, pedido))

        response = self.client.post(
            self.url_detalle(pedido),
            {"estado": EstadoPedido.CANCELADO},
        )
        self.assertEqual(response.status_code, 403)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

        transition = self.client.post(self.url_entregar(pedido))
        self.assertEqual(transition.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)

    def test_pedidos_terminales_no_muestran_transiciones(self):
        entregado, _ = self.crear_pedido(
            session_key="admin-terminal-entregado",
            dni="35888114",
            nombre_producto="Pedido entregado terminal",
        )
        cancelado, _ = self.crear_pedido(
            session_key="admin-terminal-cancelado",
            dni="35888115",
            nombre_producto="Pedido cancelado terminal",
        )
        marcar_pedido_entregado(pedido_id=entregado.pk)
        cancelar_pedido(pedido_id=cancelado.pk)

        for pedido in (entregado, cancelado):
            with self.subTest(estado=pedido.estado):
                response = self.client.get(self.url_detalle(pedido))
                self.assertNotContains(response, self.url_entregar(pedido))
                self.assertNotContains(response, self.url_cancelar(pedido))
                self.assertContains(response, "Estado terminal")

    def test_rutas_tienen_namespace_orden_y_404_limpio(self):
        nombres = [patron.name for patron in self.pedido_admin.get_urls()[:2]]
        self.assertEqual(
            nombres,
            [
                "orders_pedido_marcar_entregado",
                "orders_pedido_cancelar",
            ],
        )
        self.assertEqual(
            reverse("admin:orders_pedido_marcar_entregado", args=(123,)),
            "/admin/orders/pedido/123/marcar-entregado/",
        )

        for object_id in (999999, "abc"):
            for nombre in (
                "admin:orders_pedido_marcar_entregado",
                "admin:orders_pedido_cancelar",
            ):
                with self.subTest(object_id=object_id, nombre=nombre):
                    response = self.client.get(reverse(nombre, args=(object_id,)))
                    self.assertEqual(response.status_code, 404)

    def test_get_y_head_solo_presentan_confirmacion_sin_escrituras(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-metodos-seguros",
            dni="35888116",
            nombre_producto="Pedido métodos seguros",
        )
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        movimientos = set(
            MovimientoInventario.objects.values_list("pk", flat=True)
        )

        for metodo in (self.client.get, self.client.head):
            for url in (self.url_entregar(pedido), self.url_cancelar(pedido)):
                with self.subTest(metodo=metodo.__name__, url=url):
                    response = metodo(url)
                    self.assertEqual(response.status_code, 200)
                    pedido.refresh_from_db()
                    producto.inventario.refresh_from_db()
                    self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
                    self.assertEqual(
                        producto.inventario.cantidad_disponible,
                        stock,
                    )
                    self.assertEqual(
                        set(
                            MovimientoInventario.objects.values_list(
                                "pk", flat=True
                            )
                        ),
                        movimientos,
                    )

    def test_metodos_distintos_de_get_head_post_son_rechazados(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-metodo-no-permitido",
            dni="35888117",
            nombre_producto="Pedido método inválido",
        )

        for url in (self.url_entregar(pedido), self.url_cancelar(pedido)):
            with self.subTest(url=url):
                self.assertEqual(self.client.put(url).status_code, 405)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

    def test_post_sin_csrf_valido_es_rechazado(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-csrf",
            dni="35888118",
            nombre_producto="Pedido protegido por CSRF",
        )
        cliente_csrf = Client(enforce_csrf_checks=True)
        cliente_csrf.force_login(self.administradora)

        for url in (self.url_entregar(pedido), self.url_cancelar(pedido)):
            with self.subTest(url=url):
                self.assertEqual(cliente_csrf.post(url).status_code, 403)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)

    def test_endpoint_entrega_ignora_estado_enviado_por_navegador(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-destino-fijo",
            dni="35888119",
            nombre_producto="Pedido destino fijo",
        )

        response = self.client.post(
            self.url_entregar(pedido),
            {"estado": EstadoPedido.CANCELADO},
        )

        self.assertEqual(response.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        self.assertFalse(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).exists()
        )

    def test_confirmaciones_muestran_snapshots_y_advertencias_necesarias(self):
        pedido, _ = self.crear_pedido(
            session_key="admin-confirmaciones",
            dni="35888120",
            nombre="Compradora histórica",
            apellido="Confirmación",
            nombre_producto="Producto a restituir",
        )

        entrega = self.client.get(self.url_entregar(pedido))
        cancelacion = self.client.get(self.url_cancelar(pedido))

        for response in (entrega, cancelacion):
            self.assertContains(response, pedido.numero_pedido)
            self.assertContains(response, pedido.nombre_cliente)
            self.assertContains(response, pedido.apellido_cliente)
            self.assertContains(response, "Pendiente")
            self.assertContains(response, self.url_detalle(pedido))
        self.assertContains(entrega, "ENTREGADO de forma definitiva")
        self.assertContains(entrega, "no modificará el Inventario")
        self.assertContains(cancelacion, "CANCELADO de forma definitiva")
        self.assertContains(cancelacion, "restituirán al Inventario")
        self.assertContains(cancelacion, "Producto a restituir")

    def test_entrega_solo_cambia_estado_y_muestra_exito(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-entrega-exitosa",
            dni="35888121",
            modalidad=ModalidadEntrega.ENVIO_DOMICILIO,
            direccion=DatosDireccionEnvio(
                calle="Santa Fe",
                numero="1000",
                localidad="Rosario",
                provincia="Santa Fe",
            ),
            nombre_producto="Pedido entrega exitosa",
        )
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        movimientos = set(
            MovimientoInventario.objects.values_list("pk", flat=True)
        )
        snapshots = {
            "nombre": pedido.nombre_cliente,
            "importe": pedido.importe_total,
            "detalle": tuple(
                pedido.detalles.values_list(
                    "nombre_producto",
                    "peso_producto",
                    "cantidad",
                    "precio_unitario_aplicado",
                    "subtotal",
                )
            ),
            "direccion": tuple(
                pedido.direccion_envio.__dict__.get(campo)
                for campo in (
                    "calle",
                    "numero",
                    "localidad",
                    "provincia",
                )
            ),
        }

        response = self.client.post(self.url_entregar(pedido), follow=True)

        self.assertRedirects(response, self.url_detalle(pedido))
        self.assertContains(
            response,
            "El Pedido fue marcado como entregado correctamente.",
        )
        self.assertNotContains(response, self.url_entregar(pedido))
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(
            set(MovimientoInventario.objects.values_list("pk", flat=True)),
            movimientos,
        )
        self.assertEqual(pedido.nombre_cliente, snapshots["nombre"])
        self.assertEqual(pedido.importe_total, snapshots["importe"])
        self.assertEqual(
            tuple(
                pedido.detalles.values_list(
                    "nombre_producto",
                    "peso_producto",
                    "cantidad",
                    "precio_unitario_aplicado",
                    "subtotal",
                )
            ),
            snapshots["detalle"],
        )
        pedido.direccion_envio.refresh_from_db()
        self.assertEqual(
            tuple(
                getattr(pedido.direccion_envio, campo)
                for campo in ("calle", "numero", "localidad", "provincia")
            ),
            snapshots["direccion"],
        )

    def test_segunda_entrega_y_entrega_de_cancelado_son_controladas(self):
        entregado, producto_entregado = self.crear_pedido(
            session_key="admin-segunda-entrega",
            dni="35888122",
            nombre_producto="Pedido segunda entrega",
        )
        cancelado, producto_cancelado = self.crear_pedido(
            session_key="admin-entrega-cancelado",
            dni="35888123",
            nombre_producto="Pedido ya cancelado",
        )
        marcar_pedido_entregado(pedido_id=entregado.pk)
        cancelar_pedido(pedido_id=cancelado.pk)

        for pedido, producto, estado in (
            (entregado, producto_entregado, EstadoPedido.ENTREGADO),
            (cancelado, producto_cancelado, EstadoPedido.CANCELADO),
        ):
            producto.inventario.refresh_from_db()
            stock = producto.inventario.cantidad_disponible
            movimientos = MovimientoInventario.objects.filter(
                pedido=pedido
            ).count()
            with self.subTest(estado=estado):
                response = self.client.post(
                    self.url_entregar(pedido),
                    follow=True,
                )
                self.assertContains(
                    response,
                    "El Pedido ya no admite esa transición.",
                )
                self.assertNotContains(
                    response,
                    "marcado como entregado correctamente",
                )
                pedido.refresh_from_db()
                producto.inventario.refresh_from_db()
                self.assertEqual(pedido.estado, estado)
                self.assertEqual(producto.inventario.cantidad_disponible, stock)
                self.assertEqual(
                    MovimientoInventario.objects.filter(pedido=pedido).count(),
                    movimientos,
                )

    def test_cancelacion_restituye_stock_y_conserva_ventas(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-cancelacion-exitosa",
            dni="35888124",
            cantidad=3,
            nombre_producto="Pedido cancelación exitosa",
        )
        producto.inventario.refresh_from_db()
        stock_descontado = producto.inventario.cantidad_disponible
        ventas = set(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
            ).values_list("pk", flat=True)
        )

        response = self.client.post(self.url_cancelar(pedido), follow=True)

        self.assertRedirects(response, self.url_detalle(pedido))
        self.assertContains(
            response,
            "El Pedido fue cancelado y el Inventario fue restituido correctamente.",
        )
        self.assertNotContains(response, self.url_cancelar(pedido))
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.CANCELADO)
        self.assertEqual(
            producto.inventario.cantidad_disponible,
            stock_descontado + 3,
        )
        self.assertEqual(
            set(
                MovimientoInventario.objects.filter(
                    pedido=pedido,
                    tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
                ).values_list("pk", flat=True)
            ),
            ventas,
        )
        cancelacion = MovimientoInventario.objects.get(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
        )
        self.assertEqual(cancelacion.cantidad, 3)

    def test_cancelacion_multiproducto_restituye_cada_inventario(self):
        pedido, primero, segundo = self.crear_pedido_con_dos_productos()
        stocks_antes = {}
        for producto in (primero, segundo):
            producto.inventario.refresh_from_db()
            stocks_antes[producto.pk] = producto.inventario.cantidad_disponible

        response = self.client.post(self.url_cancelar(pedido))

        self.assertEqual(response.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.CANCELADO)
        for producto, cantidad in ((primero, 2), (segundo, 3)):
            producto.inventario.refresh_from_db()
            self.assertEqual(
                producto.inventario.cantidad_disponible,
                stocks_antes[producto.pk] + cantidad,
            )
        self.assertEqual(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).count(),
            2,
        )

    def test_segunda_cancelacion_y_cancelacion_de_entregado_no_reponen(self):
        cancelado, producto_cancelado = self.crear_pedido(
            session_key="admin-segunda-cancelacion",
            dni="35888125",
            nombre_producto="Pedido segunda cancelación",
        )
        entregado, producto_entregado = self.crear_pedido(
            session_key="admin-cancelar-entregado",
            dni="35888126",
            nombre_producto="Pedido entregado no cancelable",
        )
        cancelar_pedido(pedido_id=cancelado.pk)
        marcar_pedido_entregado(pedido_id=entregado.pk)

        for pedido, producto, estado in (
            (cancelado, producto_cancelado, EstadoPedido.CANCELADO),
            (entregado, producto_entregado, EstadoPedido.ENTREGADO),
        ):
            producto.inventario.refresh_from_db()
            stock = producto.inventario.cantidad_disponible
            compensatorios = MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).count()
            with self.subTest(estado=estado):
                response = self.client.post(
                    self.url_cancelar(pedido),
                    follow=True,
                )
                self.assertContains(
                    response,
                    "El Pedido ya no admite esa transición.",
                )
                pedido.refresh_from_db()
                producto.inventario.refresh_from_db()
                self.assertEqual(pedido.estado, estado)
                self.assertEqual(producto.inventario.cantidad_disponible, stock)
                self.assertEqual(
                    MovimientoInventario.objects.filter(
                        pedido=pedido,
                        tipo_movimiento=(
                            TipoMovimientoInventario.CANCELACION_PEDIDO
                        ),
                    ).count(),
                    compensatorios,
                )

    def test_historial_corrupto_se_informa_sin_cambios_parciales(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-historial-corrupto",
            dni="35888127",
            nombre_producto="Pedido historial corrupto",
        )
        MovimientoInventario.objects.filter(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
        ).delete()
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible

        response = self.client.post(self.url_cancelar(pedido), follow=True)

        self.assertContains(response, "historial de Inventario es inconsistente")
        self.assertNotContains(response, "cancelado y el Inventario fue restituido")
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertFalse(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).exists()
        )

    def test_capacidad_excedida_se_informa_sin_cambios_parciales(self):
        pedido, producto = self.crear_pedido(
            session_key="admin-capacidad-excedida",
            dni="35888128",
            nombre_producto="Pedido capacidad excedida",
        )
        capacidad_maxima = 9_223_372_036_854_775_807
        producto.inventario.cantidad_disponible = capacidad_maxima
        producto.inventario.save(update_fields=("cantidad_disponible",))

        response = self.client.post(self.url_cancelar(pedido), follow=True)

        self.assertContains(response, "se alcanzó su capacidad máxima")
        self.assertNotContains(response, "cancelado y el Inventario fue restituido")
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(producto.inventario.cantidad_disponible, capacidad_maxima)
        self.assertFalse(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).exists()
        )

    def test_fallo_controlado_en_segunda_restitucion_revierte_la_primera(self):
        pedido, primero, segundo = self.crear_pedido_con_dos_productos()
        productos = (primero, segundo)
        stocks = {}
        for producto in productos:
            producto.inventario.refresh_from_db()
            stocks[producto.pk] = producto.inventario.cantidad_disponible
        movimientos = set(
            MovimientoInventario.objects.values_list("pk", flat=True)
        )
        from orders import services as orders_services

        original = (
            orders_services._aplicar_cancelacion_pedido_sobre_inventario_bloqueado
        )
        llamadas = {"cantidad": 0}

        def restituir_con_fallo(**kwargs):
            llamadas["cantidad"] += 1
            if llamadas["cantidad"] == 2:
                raise CapacidadInventarioExcedidaInventory
            return original(**kwargs)

        with patch(
            "orders.services._aplicar_cancelacion_pedido_sobre_inventario_bloqueado",
            side_effect=restituir_con_fallo,
        ):
            response = self.client.post(self.url_cancelar(pedido), follow=True)

        self.assertContains(response, "se alcanzó su capacidad máxima")
        self.assertNotContains(response, "cancelado y el Inventario fue restituido")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        for producto in productos:
            producto.inventario.refresh_from_db()
            self.assertEqual(
                producto.inventario.cantidad_disponible,
                stocks[producto.pk],
            )
        self.assertEqual(
            set(MovimientoInventario.objects.values_list("pk", flat=True)),
            movimientos,
        )
