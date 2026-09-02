from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from playwright.sync_api import expect

from inventory.models import MovimientoInventario, TipoMovimientoInventario
from orders.models import Cliente, EstadoPedido, ModalidadEntrega
from orders.services import crear_pedido_desde_carrito
from orders.tests.helpers import crear_carrito_checkout, datos_comprador

from e2e.base import BrowserE2ETestCase, VIEWPORT_ESCRITORIO, VIEWPORT_MOVIL


PASSWORD_ADMIN = "clave-e2e-segura"


class AdministracionE2ETests(BrowserE2ETestCase):
    def crear_usuario(self, username, *permisos, superuser=False):
        if superuser:
            return get_user_model().objects.create_superuser(
                username=username,
                email="admin@example.test",
                password=PASSWORD_ADMIN,
            )
        usuario = get_user_model().objects.create_user(
            username=username,
            is_staff=True,
            password=PASSWORD_ADMIN,
        )
        usuario.user_permissions.add(
            *(
                Permission.objects.get(
                    codename=permiso,
                    content_type__app_label="orders",
                )
                for permiso in permisos
            )
        )
        return usuario

    def crear_pedido(self, *, session_key, dni, nombre_producto):
        carrito, producto = crear_carrito_checkout(
            session_key=session_key,
            cantidad=2,
            stock=20,
            nombre=nombre_producto,
        )
        pedido = crear_pedido_desde_carrito(
            session_key=session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(
                dni=dni,
                nombre="Ana Histórica",
                apellido="Prueba",
                telefono="+54 11 4000 0000",
            ),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido
        return pedido, producto

    def test_consulta_cliente_pedido_navegacion_y_responsive(self):
        administradora = self.crear_usuario("admin-consulta-e2e", superuser=True)
        pedido, _ = self.crear_pedido(
            session_key="e2e-admin-consulta",
            dni="80111222",
            nombre_producto="Consulta E2E",
        )
        Cliente.objects.filter(pk=pedido.cliente_id).update(
            nombre="Nombre Actual E2E",
            telefono="+54 11 4999 9999",
        )
        self.iniciar_sesion_admin(
            self.page,
            usuario=administradora.username,
            password=PASSWORD_ADMIN,
        )

        self.page.goto(self.url(reverse("admin:orders_cliente_changelist")))
        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)
        self.page.locator('input[name="q"]').fill("80111222")
        self.page.get_by_role("button", name="Buscar").click()
        self.page.get_by_role("link", name="80111222").click()
        self.assert_un_h1()
        expect(self.page.get_by_text("Nombre Actual E2E", exact=True)).to_be_visible()
        expect(
            self.page.get_by_text("Historial de Pedidos", exact=True)
        ).to_be_visible()
        fila_pedido = self.page.get_by_role("row").filter(
            has_text=pedido.numero_pedido
        )
        expect(fila_pedido).to_be_visible()
        self.page.get_by_role("link", name="Ver", exact=True).click()

        expect(
            self.page.get_by_role("heading", name="Cliente actual asociado")
        ).to_be_visible()
        expect(
            self.page.get_by_role("heading", name="Comprador histórico")
        ).to_be_visible()
        expect(self.page.get_by_text("Nombre Actual E2E", exact=False)).to_be_visible()
        expect(self.page.get_by_text("Ana Histórica", exact=True)).to_be_visible()
        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)
        self.page.go_back()
        expect(
            self.page.get_by_text("Historial de Pedidos", exact=True)
        ).to_be_visible()
        self.assertEqual(self.page.locator('input[name="_save"]').count(), 0)
        self.assertEqual(self.page.locator(".deletelink").count(), 0)
        self.assertEqual(
            self.page.get_by_role("link", name="Agregar cliente").count(),
            0,
        )

        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        expect(
            self.page.get_by_role("row").filter(has_text=pedido.numero_pedido)
        ).to_be_visible()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)
        self.assert_sin_errores_consola()

    def test_entrega_mediante_confirmacion_admin_y_csrf_real(self):
        administradora = self.crear_usuario("admin-entrega-e2e", superuser=True)
        pedido, producto = self.crear_pedido(
            session_key="e2e-admin-entrega",
            dni="80222333",
            nombre_producto="Entrega E2E",
        )
        producto.inventario.refresh_from_db()
        stock_antes = producto.inventario.cantidad_disponible
        movimientos_antes = MovimientoInventario.objects.filter(pedido=pedido).count()
        self.iniciar_sesion_admin(
            self.page,
            usuario=administradora.username,
            password=PASSWORD_ADMIN,
        )

        self.page.goto(
            self.url(reverse("admin:orders_pedido_change", args=(pedido.pk,)))
        )
        self.page.get_by_role("link", name="Marcar como entregado").click()
        expect(
            self.page.get_by_role("heading", name="Marcar Pedido como entregado")
        ).to_be_visible()
        expect(
            self.page.get_by_text("no modificará el Inventario", exact=False)
        ).to_be_visible()
        self.page.get_by_role(
            "button", name="Confirmar: Marcar como entregado"
        ).click()

        expect(
            self.page.get_by_text(
                "El Pedido fue marcado como entregado correctamente."
            )
        ).to_be_visible()
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        self.assertEqual(producto.inventario.cantidad_disponible, stock_antes)
        self.assertEqual(
            MovimientoInventario.objects.filter(pedido=pedido).count(),
            movimientos_antes,
        )
        expect(self.page.get_by_text("Estado terminal", exact=True)).to_be_visible()
        self.assertEqual(
            self.page.get_by_role("link", name="Marcar como entregado").count(),
            0,
        )
        self.assertEqual(
            self.page.get_by_role("link", name="Cancelar Pedido").count(),
            0,
        )
        self.assert_sin_errores_consola()

    def test_cancelacion_restituye_inventario_y_muestra_movimiento(self):
        administradora = self.crear_usuario("admin-cancelacion-e2e", superuser=True)
        pedido, producto = self.crear_pedido(
            session_key="e2e-admin-cancelacion",
            dni="80333444",
            nombre_producto="Cancelación E2E",
        )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 18)
        self.iniciar_sesion_admin(
            self.page,
            usuario=administradora.username,
            password=PASSWORD_ADMIN,
        )

        self.page.goto(
            self.url(reverse("admin:orders_pedido_change", args=(pedido.pk,)))
        )
        self.page.get_by_role("link", name="Cancelar Pedido").click()
        expect(self.page.get_by_role("heading", name="Cancelar Pedido")).to_be_visible()
        expect(
            self.page.get_by_role("listitem").filter(has_text="Cancelación E2E")
        ).to_be_visible()
        self.page.get_by_role("button", name="Confirmar: Cancelar Pedido").click()

        expect(
            self.page.get_by_text(
                "El Pedido fue cancelado y el Inventario fue restituido correctamente."
            )
        ).to_be_visible()
        expect(
            self.page.get_by_text("Cancelación de pedido", exact=True)
        ).to_be_visible()
        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.CANCELADO)
        self.assertEqual(producto.inventario.cantidad_disponible, 20)
        self.assertEqual(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
            ).count(),
            1,
        )
        self.assertEqual(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
            ).count(),
            1,
        )
        expect(self.page.get_by_text("Estado terminal", exact=True)).to_be_visible()
        self.assert_sin_errores_consola()

    def test_permisos_parciales_no_filtran_cliente_ni_pedido(self):
        pedido, _ = self.crear_pedido(
            session_key="e2e-admin-permisos",
            dni="80444555",
            nombre_producto="Permisos E2E",
        )
        Cliente.objects.filter(pk=pedido.cliente_id).update(
            nombre="Nombre Actual Oculto",
            telefono="+54 11 4888 8888",
        )
        solo_cliente = self.crear_usuario(
            "solo-cliente-e2e",
            "view_cliente",
        )
        solo_pedido = self.crear_usuario(
            "solo-pedido-e2e",
            "view_pedido",
        )
        url_cliente = self.url(
            reverse("admin:orders_cliente_change", args=(pedido.cliente_id,))
        )
        url_pedido = self.url(
            reverse("admin:orders_pedido_change", args=(pedido.pk,))
        )

        self.iniciar_sesion_admin(
            self.page,
            usuario=solo_cliente.username,
            password=PASSWORD_ADMIN,
        )
        self.page.goto(url_cliente)
        expect(
            self.page.get_by_text("Nombre Actual Oculto", exact=True)
        ).to_be_visible()
        self.assertEqual(
            self.page.get_by_text("Historial de Pedidos", exact=True).count(),
            0,
        )
        self.assertEqual(self.page.get_by_text(pedido.numero_pedido).count(), 0)
        respuesta_pedido = self.page.goto(url_pedido)
        self.assertEqual(respuesta_pedido.status, 403)

        contexto_pedido = self.nuevo_contexto()
        try:
            pagina_pedido = contexto_pedido.new_page()
            self.iniciar_sesion_admin(
                pagina_pedido,
                usuario=solo_pedido.username,
                password=PASSWORD_ADMIN,
            )
            pagina_pedido.goto(url_pedido)
            expect(
                pagina_pedido.get_by_role("heading", name="Comprador histórico")
            ).to_be_visible()
            expect(
                pagina_pedido.get_by_text("Ana Histórica", exact=True)
            ).to_be_visible()
            expect(
                pagina_pedido.get_by_text("Cliente asociado", exact=True)
            ).to_be_visible()
            self.assertEqual(
                pagina_pedido.get_by_text("Nombre Actual Oculto").count(),
                0,
            )
            self.assertEqual(
                pagina_pedido.locator(f'a[href="{url_cliente}"]').count(),
                0,
            )
            respuesta_cliente = pagina_pedido.goto(url_cliente)
            self.assertEqual(respuesta_cliente.status, 403)
        finally:
            contexto_pedido.close()
