from urllib.parse import parse_qs, urlparse

from django.urls import reverse
from playwright.sync_api import expect

from cart.models import Carrito, ItemCarrito
from cart.tests.helpers import crear_producto_con_stock
from inventory.models import MovimientoInventario, TipoMovimientoInventario
from orders.models import DireccionEnvio, EstadoPedido, ModalidadEntrega, Pedido

from e2e.base import BrowserE2ETestCase, VIEWPORT_ESCRITORIO, VIEWPORT_MOVIL


class CompraPublicaE2ETests(BrowserE2ETestCase):
    def agregar_producto(self, producto):
        self.page.goto(self.url(reverse("catalog:producto_list")))
        self.page.get_by_role(
            "button",
            name=f"Agregar {producto.nombre}, presentación {producto.peso} al Carrito",
        ).click()
        expect(self.page).to_have_url(self.url(reverse("cart:detalle")))

    def abrir_checkout(self):
        self.page.get_by_role("link", name="Finalizar compra").click()
        expect(self.page).to_have_url(self.url(reverse("orders:checkout")))

    def completar_comprador(self):
        self.page.get_by_label("Nombre").fill("Ana E2E")
        self.page.get_by_label("Apellido").fill("Prueba")
        self.page.get_by_label("DNI").fill("87.654.321")
        self.page.get_by_label("Teléfono").fill("+54 11 4000 0000")

    def confirmar_pedido(self):
        cookies = self.context.cookies()
        tiene_cookie_csrf = any(
            cookie["name"] == "csrftoken" for cookie in cookies
        )
        tiene_token_formulario = bool(
            self.page.locator('input[name="csrfmiddlewaretoken"]').input_value()
        )
        with self.page.expect_response(
            lambda respuesta: respuesta.request.method == "POST"
            and urlparse(respuesta.url).path == reverse("orders:checkout")
        ) as respuesta_info:
            self.page.get_by_role("button", name="Confirmar Pedido").click()
        respuesta = respuesta_info.value
        self.assertTrue(tiene_cookie_csrf)
        self.assertTrue(tiene_token_formulario)
        self.assertEqual(respuesta.status, 302)
        expect(
            self.page.get_by_role("heading", name="¡Gracias por tu compra!")
        ).to_be_visible()
        return respuesta

    def test_compra_con_retiro_precio_confirmacion_y_whatsapp(self):
        producto = crear_producto_con_stock(nombre="Retiro E2E", stock=10)

        self.page.goto(self.url(reverse("catalog:producto_list")))
        self.assert_un_h1()
        expect(
            self.page.get_by_alt_text(
                f"Producto {producto.nombre}, presentación {producto.peso}"
            )
        ).to_be_visible()
        self.assert_sin_overflow_horizontal()
        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)
        self.page.keyboard.press("Tab")
        expect(self.page.locator(".skip-link")).to_be_visible()
        self.agregar_producto(producto)

        cantidad = self.page.get_by_label("Cantidad", exact=True)
        cantidad.fill("3")
        self.page.get_by_role(
            "button",
            name=(
                f"Actualizar cantidad de {producto.nombre}, "
                f"presentación {producto.peso}"
            ),
        ).click()
        expect(
            self.page.get_by_text("Precio desde 3 unidades", exact=True)
        ).to_be_visible()
        expect(self.page.get_by_text("$ 4.500,00", exact=True)).to_be_visible()
        expect(
            self.page.get_by_text("$ 13.500,00", exact=True).first
        ).to_be_visible()
        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)

        self.abrir_checkout()
        self.assert_un_h1()
        self.assertEqual(self.page.locator("fieldset").count(), 3)
        expect(self.page.get_by_text("Datos personales", exact=True)).to_be_visible()
        expect(
            self.page.get_by_text("Modalidad de entrega", exact=True)
        ).to_be_visible()
        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        expect(
            self.page.get_by_role("button", name="Confirmar Pedido")
        ).to_be_visible()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)
        self.completar_comprador()
        self.page.get_by_label("Retiro", exact=True).check()
        self.confirmar_pedido()

        pedido = Pedido.objects.select_related("cliente").get()
        detalle = pedido.detalles.get()
        producto.inventario.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertEqual(detalle.cantidad, 3)
        self.assertEqual(str(detalle.precio_unitario_aplicado), "4500.00")
        self.assertEqual(str(detalle.subtotal), "13500.00")
        self.assertEqual(producto.inventario.cantidad_disponible, 7)
        self.assertEqual(
            MovimientoInventario.objects.filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
            ).count(),
            1,
        )
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())

        self.assert_un_h1()
        expect(self.page.get_by_text(pedido.numero_pedido, exact=True)).to_be_visible()
        expect(self.page.get_by_text("Ana E2E Prueba", exact=True)).to_be_visible()
        expect(self.page.get_by_text("Retiro E2E", exact=True)).to_be_visible()
        expect(
            self.page.get_by_text("$ 13.500,00", exact=True).first
        ).to_be_visible()
        html = self.page.content()
        self.assertNotIn(pedido.dni_cliente, html)
        self.assertNotIn(str(pedido.token_idempotencia), html)
        self.assertNotIn(pedido.huella_sesion_origen, html)

        enlace = self.page.get_by_role("link", name="Continuar por WhatsApp")
        href = enlace.get_attribute("href")
        mensaje = parse_qs(urlparse(href).query)["text"][0]
        self.assertIn("Retiro E2E", mensaje)
        self.assertIn("Cantidad: 3", mensaje)
        self.assertIn("ARS 4.500,00", mensaje)
        self.assertIn("ARS 13.500,00", mensaje)
        self.assertNotIn(pedido.dni_cliente, mensaje)
        self.assertNotIn(str(pedido.token_idempotencia), mensaje)
        self.assertEqual(enlace.get_attribute("referrerpolicy"), "no-referrer")
        self.assertIn("noreferrer", enlace.get_attribute("rel"))

        cookies = self.context.cookies()
        session_cookie = next(
            cookie for cookie in cookies if cookie["name"] == "sessionid"
        )
        self.assertTrue(session_cookie["httpOnly"])
        self.page.set_viewport_size(VIEWPORT_MOVIL)
        self.assert_sin_overflow_horizontal()
        self.page.set_viewport_size(VIEWPORT_ESCRITORIO)
        self.assert_sin_errores_consola()

    def test_compra_con_envio_muestra_direccion_y_mejora_progresiva(self):
        producto = crear_producto_con_stock(nombre="Envío E2E", stock=6)
        self.agregar_producto(producto)
        self.abrir_checkout()
        self.completar_comprador()

        direccion = self.page.locator("fieldset[data-direccion-envio]")
        expect(direccion).to_be_visible()
        self.page.get_by_label("Retiro", exact=True).check()
        expect(direccion).to_be_hidden()
        self.page.get_by_label("Envío a domicilio", exact=True).check()
        expect(direccion).to_be_visible()

        contexto_sin_js = self.nuevo_contexto(java_script_enabled=False)
        try:
            contexto_sin_js.add_cookies(self.context.cookies())
            pagina_sin_js = contexto_sin_js.new_page()
            pagina_sin_js.goto(self.url(reverse("orders:checkout")))
            expect(
                pagina_sin_js.locator("fieldset[data-direccion-envio]")
            ).to_be_visible()
            pagina_sin_js.get_by_label("Nombre").fill("Ana E2E")
            pagina_sin_js.get_by_label("Apellido").fill("Prueba")
            pagina_sin_js.get_by_label("DNI").fill("87.654.321")
            pagina_sin_js.get_by_label("Teléfono").fill("+54 11 4000 0000")
            pagina_sin_js.get_by_label("Envío a domicilio", exact=True).check()
            pagina_sin_js.get_by_label("Calle").fill("San Martín")
            pagina_sin_js.get_by_label("Número").fill("123")
            pagina_sin_js.get_by_label("Localidad").fill("Posadas")
            pagina_sin_js.get_by_label("Provincia").fill("Misiones")
            pagina_sin_js.get_by_label("Referencias").fill("Portón verde")
            pagina_sin_js.get_by_role("button", name="Confirmar Pedido").click()
            expect(
                pagina_sin_js.get_by_role(
                    "heading", name="¡Gracias por tu compra!"
                )
            ).to_be_visible()
            expect(
                pagina_sin_js.get_by_text("San Martín 123", exact=False)
            ).to_be_visible()
            expect(
                pagina_sin_js.get_by_text(
                    "El costo de envío se coordina posteriormente",
                    exact=False,
                )
            ).to_be_visible()
            href = pagina_sin_js.get_by_role(
                "link", name="Continuar por WhatsApp"
            ).get_attribute("href")
        finally:
            contexto_sin_js.close()

        pedido = Pedido.objects.get()
        direccion_persistida = DireccionEnvio.objects.get(pedido=pedido)
        self.assertEqual(pedido.modalidad_entrega, ModalidadEntrega.ENVIO_DOMICILIO)
        self.assertEqual(direccion_persistida.calle, "San Martín")
        mensaje = parse_qs(urlparse(href).query)["text"][0]
        self.assertIn("Dirección: San Martín 123", mensaje)
        self.assertIn("Referencias: Portón verde", mensaje)
        self.assertNotIn(pedido.dni_cliente, mensaje)
        self.assert_sin_errores_consola()

    def test_validacion_servidor_conserva_carrito_y_permite_corregir(self):
        producto = crear_producto_con_stock(nombre="Validación E2E", stock=5)
        self.agregar_producto(producto)
        carrito = Carrito.objects.get()
        self.abrir_checkout()

        self.page.get_by_label("Apellido").fill("Prueba")
        self.page.get_by_label("DNI").fill("dato-inválido")
        self.page.get_by_label("Teléfono").fill("sin números")
        self.page.get_by_label("Retiro", exact=True).check()
        self.page.get_by_role("button", name="Confirmar Pedido").click()

        alerta = self.page.get_by_role("alert")
        expect(alerta).to_be_visible()
        expect(alerta).to_contain_text("Revisá los datos ingresados")
        self.assertEqual(
            self.page.get_by_label("Nombre").get_attribute("aria-invalid"),
            "true",
        )
        self.assertIn(
            "id_nombre_error",
            self.page.get_by_label("Nombre").get_attribute("aria-describedby"),
        )
        producto.inventario.refresh_from_db()
        self.assertFalse(Pedido.objects.exists())
        self.assertEqual(producto.inventario.cantidad_disponible, 5)
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())

        self.completar_comprador()
        payload = "<script>window.e2eXss=true</script>"
        self.page.get_by_label("Nombre").fill(payload)
        self.confirmar_pedido()
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())
        expect(self.page.get_by_text(f"{payload} Prueba", exact=True)).to_be_visible()
        self.assertIsNone(self.page.evaluate("window.e2eXss"))

    def test_sesion_aislada_url_privada_y_refresh_idempotente(self):
        producto = crear_producto_con_stock(nombre="Privacidad E2E", stock=5)
        self.agregar_producto(producto)
        self.abrir_checkout()
        self.completar_comprador()
        self.page.get_by_label("Retiro", exact=True).check()
        self.confirmar_pedido()
        pedido = Pedido.objects.get()
        url_confirmacion = self.page.url
        movimientos = MovimientoInventario.objects.filter(pedido=pedido).count()
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible

        for dato in (
            pedido.dni_cliente,
            pedido.telefono_cliente,
            str(pedido.token_idempotencia),
            pedido.huella_sesion_origen,
        ):
            self.assertNotIn(dato, url_confirmacion)

        respuesta_refresh = self.page.reload()
        self.assertIn("no-store", respuesta_refresh.headers["cache-control"])
        self.assertEqual(
            respuesta_refresh.headers["referrer-policy"],
            "no-referrer",
        )
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(
            MovimientoInventario.objects.filter(pedido=pedido).count(),
            movimientos,
        )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)

        contexto_ajeno = self.nuevo_contexto()
        try:
            pagina_ajena = contexto_ajeno.new_page()
            respuesta = pagina_ajena.goto(url_confirmacion)
            self.assertEqual(respuesta.status, 404)
            self.assertNotIn(pedido.dni_cliente, pagina_ajena.content())
        finally:
            contexto_ajeno.close()

    def test_producto_inactivado_antes_de_confirmar_no_genera_sobreventa(self):
        producto = crear_producto_con_stock(nombre="Inactivo E2E", stock=5)
        self.agregar_producto(producto)
        carrito = Carrito.objects.get()
        self.abrir_checkout()
        self.completar_comprador()
        self.page.get_by_label("Retiro", exact=True).check()
        producto.__class__.objects.filter(pk=producto.pk).update(activo=False)

        self.page.get_by_role("button", name="Confirmar Pedido").click()
        expect(self.page).to_have_url(self.url(reverse("cart:detalle")))
        expect(
            self.page.get_by_text("Uno de los Productos ya no está disponible.")
        ).to_be_visible()
        expect(
            self.page.get_by_text("Producto no disponible", exact=True)
        ).to_be_visible()
        producto.inventario.refresh_from_db()
        self.assertFalse(Pedido.objects.exists())
        self.assertEqual(producto.inventario.cantidad_disponible, 5)
        self.assertTrue(Carrito.objects.filter(pk=carrito.pk).exists())
        self.assertEqual(
            MovimientoInventario.objects.filter(
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO
            ).count(),
            0,
        )
