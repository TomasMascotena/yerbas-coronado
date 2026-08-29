from datetime import timedelta
import shutil
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from cart.models import Carrito, ItemCarrito
from cart.services import DURACION_CARRITO, agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from inventory.models import MovimientoInventario
from inventory.services import registrar_venta_presencial
from orders.exceptions import DatosCompradorInvalidos, ProductoSinInventario
from orders.models import DireccionEnvio, EstadoPedido, ModalidadEntrega, Pedido


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class CheckoutPublicoTests(TestCase):
    def crear_carrito(self, *, cantidad=2, stock=20, nombre="Canarias"):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        producto = crear_producto_con_stock(nombre=nombre, stock=stock)
        item = agregar_producto(
            session_key=sesion.session_key,
            producto_id=producto.pk,
            cantidad=cantidad,
        )
        item.carrito.refresh_from_db()
        return item.carrito, producto, item

    def datos_post(self, carrito, **cambios):
        datos = {
            "token_checkout": str(carrito.token_checkout),
            "nombre": "Ana",
            "apellido": "Coronado",
            "dni": "12.345.678",
            "telefono": "+54 (11) 4567-8901",
            "modalidad_entrega": ModalidadEntrega.RETIRO,
            "observaciones": "Sin bolsa",
        }
        datos.update(cambios)
        return datos

    def test_get_muestra_resumen_token_oculto_y_fallback_sin_javascript(self):
        carrito, producto, _ = self.crear_carrito(cantidad=3)

        respuesta = self.client.get(reverse("orders:checkout"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Finalizar compra")
        self.assertContains(respuesta, producto.nombre)
        self.assertContains(respuesta, "$ 13.500,00")
        self.assertContains(
            respuesta,
            f'value="{carrito.token_checkout}"',
        )
        self.assertContains(respuesta, 'name="token_checkout"')
        self.assertContains(respuesta, 'type="hidden"')
        self.assertContains(respuesta, 'aria-describedby="id_dni_helptext"')
        self.assertContains(respuesta, "Dirección de envío")
        self.assertContains(respuesta, "orders/checkout.js")
        self.assertNotIn(str(carrito.token_checkout), respuesta.request["PATH_INFO"])
        self.assertEqual(respuesta["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_carrito_no_vacio_ofrece_checkout_y_vacio_no_lo_ofrece(self):
        self.crear_carrito()

        respuesta = self.client.get(reverse("cart:detalle"))
        self.assertContains(respuesta, reverse("orders:checkout"))
        self.assertContains(respuesta, "Finalizar compra")

        self.client.post(reverse("cart:vaciar"))
        respuesta_vacia = self.client.get(reverse("cart:detalle"))
        self.assertNotContains(respuesta_vacia, reverse("orders:checkout"))

    def test_sin_sesion_redirige_sin_crearla(self):
        respuesta = self.client.get(reverse("orders:checkout"))

        self.assertRedirects(
            respuesta,
            reverse("cart:detalle"),
            fetch_redirect_response=False,
        )
        self.assertFalse(Session.objects.exists())
        self.assertFalse(Carrito.objects.exists())

    def test_carrito_vacio_redirige_y_carrito_expirado_se_elimina(self):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        vacio = Carrito.objects.create(session_key=sesion.session_key)

        respuesta = self.client.get(reverse("orders:checkout"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(Carrito.objects.filter(pk=vacio.pk).exists())

        producto = crear_producto_con_stock(stock=5)
        item = agregar_producto(
            session_key=sesion.session_key,
            producto_id=producto.pk,
            cantidad=1,
        )
        Carrito.objects.filter(pk=item.carrito_id).update(
            ultima_actividad=timezone.now() - DURACION_CARRITO
        )

        respuesta = self.client.get(reverse("orders:checkout"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Carrito.objects.filter(pk=item.carrito_id).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=item.pk).exists())

    def test_get_y_head_vigentes_no_escriben(self):
        carrito, producto, item = self.crear_carrito()
        actividad = timezone.now() - timedelta(hours=1)
        Carrito.objects.filter(pk=carrito.pk).update(ultima_actividad=actividad)
        conteos = (
            Pedido.objects.count(),
            MovimientoInventario.objects.count(),
            ItemCarrito.objects.count(),
        )
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible

        self.assertEqual(self.client.get(reverse("orders:checkout")).status_code, 200)
        respuesta_head = self.client.head(reverse("orders:checkout"))

        carrito.refresh_from_db()
        item.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(respuesta_head.status_code, 200)
        self.assertEqual(respuesta_head.content, b"")
        self.assertEqual(carrito.ultima_actividad, actividad)
        self.assertEqual(item.cantidad, 2)
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(
            (
                Pedido.objects.count(),
                MovimientoInventario.objects.count(),
                ItemCarrito.objects.count(),
            ),
            conteos,
        )

    def test_formulario_invalido_preserva_datos_y_no_crea_pedido(self):
        carrito, _, item = self.crear_carrito()
        respuesta = self.client.post(
            reverse("orders:checkout"),
            self.datos_post(carrito, nombre="", telefono="Teléfono escrito"),
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Revisá los datos ingresados")
        self.assertContains(respuesta, 'value="Teléfono escrito"')
        self.assertContains(respuesta, "autofocus")
        self.assertContains(respuesta, 'aria-invalid="true"')
        self.assertContains(
            respuesta,
            'aria-describedby="id_nombre_error"',
        )
        self.assertFalse(Pedido.objects.exists())
        self.assertTrue(ItemCarrito.objects.filter(pk=item.pk).exists())

    def test_envio_incompleto_preserva_datos_y_marca_campos(self):
        carrito, _, _ = self.crear_carrito()
        respuesta = self.client.post(
            reverse("orders:checkout"),
            self.datos_post(
                carrito,
                modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
                calle="San Martín",
            ),
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            set(respuesta.context["form"].errors),
            {"numero", "localidad", "provincia"},
        )
        self.assertContains(respuesta, "obligatorio para el envío a domicilio")
        self.assertContains(respuesta, 'value="San Martín"')
        self.assertFalse(Pedido.objects.exists())

    def test_post_retiro_crea_pedido_y_utiliza_prg(self):
        carrito, producto, _ = self.crear_carrito(cantidad=3, stock=10)
        respuesta = self.client.post(
            reverse("orders:checkout"),
            self.datos_post(carrito),
        )

        pedido = Pedido.objects.get()
        self.assertRedirects(
            respuesta,
            reverse("orders:confirmacion", args=(pedido.numero_pedido,)),
            fetch_redirect_response=False,
        )
        self.assertEqual(pedido.estado, EstadoPedido.PENDIENTE)
        self.assertFalse(DireccionEnvio.objects.exists())
        self.assertFalse(Carrito.objects.filter(pk=carrito.pk).exists())
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 7)
        self.assertEqual(pedido.movimientos_inventario.count(), 1)
        self.assertNotIn(pedido.dni_cliente, respuesta["Location"])
        self.assertNotIn(pedido.telefono_cliente, respuesta["Location"])
        self.assertNotIn(str(carrito.token_checkout), respuesta["Location"])

    def test_post_envio_crea_direccion_normalizada(self):
        carrito, _, _ = self.crear_carrito()
        respuesta = self.client.post(
            reverse("orders:checkout"),
            self.datos_post(
                carrito,
                modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
                calle="  San Martín ",
                numero=" 123 ",
                localidad=" Posadas ",
                provincia=" Misiones ",
                referencias=" Portón verde ",
            ),
        )

        self.assertEqual(respuesta.status_code, 302)
        direccion = DireccionEnvio.objects.get()
        self.assertEqual(direccion.calle, "San Martín")
        self.assertEqual(direccion.referencias, "Portón verde")

    def test_doble_post_es_idempotente(self):
        carrito, producto, _ = self.crear_carrito(cantidad=4, stock=10)
        datos = self.datos_post(carrito)

        primera = self.client.post(reverse("orders:checkout"), datos)
        segunda = self.client.post(reverse("orders:checkout"), datos)

        self.assertEqual(primera.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(primera["Location"], segunda["Location"])
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(MovimientoInventario.objects.filter(pedido__isnull=False).count(), 1)
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, 6)

    def test_stock_o_producto_cambiado_redirige_y_conserva_carrito(self):
        for cambio in ("stock", "inactivo"):
            with self.subTest(cambio=cambio):
                self.client = Client()
                carrito, producto, item = self.crear_carrito(
                    cantidad=2,
                    stock=3,
                    nombre=f"Canarias {cambio}",
                )
                if cambio == "stock":
                    registrar_venta_presencial(
                        inventario_id=producto.inventario.pk,
                        cantidad=2,
                    )
                else:
                    producto.activo = False
                    producto.save(update_fields=("activo",))

                respuesta = self.client.post(
                    reverse("orders:checkout"),
                    self.datos_post(carrito),
                )

                self.assertEqual(respuesta.status_code, 302)
                self.assertEqual(respuesta["Location"], reverse("cart:detalle"))
                self.assertFalse(Pedido.objects.exists())
                self.assertTrue(ItemCarrito.objects.filter(pk=item.pk).exists())

    def test_token_obsoleto_no_crea_pedido(self):
        carrito, _, item = self.crear_carrito()
        datos = self.datos_post(carrito)
        Carrito.objects.filter(pk=carrito.pk).update(token_checkout=uuid.uuid4())

        respuesta = self.client.post(reverse("orders:checkout"), datos)

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta["Location"], reverse("cart:detalle"))
        self.assertFalse(Pedido.objects.exists())
        self.assertTrue(ItemCarrito.objects.filter(pk=item.pk).exists())

    def test_error_de_datos_del_servicio_no_expone_detalle_interno(self):
        carrito, _, _ = self.crear_carrito()
        with patch(
            "orders.views.crear_pedido_desde_carrito",
            side_effect=DatosCompradorInvalidos("<script>dato interno</script>"),
        ):
            respuesta = self.client.post(
                reverse("orders:checkout"),
                self.datos_post(carrito, nombre="Nombre preservado"),
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Nombre preservado")
        self.assertContains(respuesta, "Revisá los datos ingresados")
        self.assertNotContains(respuesta, "dato interno")

    @override_settings(DEBUG=False)
    def test_error_estructural_produce_500_sanitizado(self):
        carrito, _, _ = self.crear_carrito()
        cliente = Client(raise_request_exception=False)
        cliente.cookies = self.client.cookies
        with patch(
            "orders.views.crear_pedido_desde_carrito",
            side_effect=ProductoSinInventario("Producto secreto"),
        ):
            respuesta = cliente.post(
                reverse("orders:checkout"),
                self.datos_post(carrito),
            )

        self.assertEqual(respuesta.status_code, 500)
        self.assertNotContains(respuesta, "Producto secreto", status_code=500)

    def test_checkout_exige_csrf(self):
        carrito, _, _ = self.crear_carrito()
        cliente = Client(enforce_csrf_checks=True)
        cliente.cookies = self.client.cookies

        self.assertEqual(
            cliente.post(
                reverse("orders:checkout"),
                self.datos_post(carrito),
            ).status_code,
            403,
        )
        self.assertFalse(Pedido.objects.exists())

        pagina = cliente.get(reverse("orders:checkout"))
        token_csrf = cliente.cookies["csrftoken"].value
        datos = self.datos_post(carrito, csrfmiddlewaretoken=token_csrf)
        self.assertEqual(
            cliente.post(reverse("orders:checkout"), datos).status_code,
            302,
        )
        self.assertContains(pagina, "csrfmiddlewaretoken")

    def test_metodos_no_admitidos_no_tienen_efectos(self):
        carrito, producto, item = self.crear_carrito()
        producto.inventario.refresh_from_db()
        stock = producto.inventario.cantidad_disponible
        for metodo in ("put", "patch", "delete"):
            with self.subTest(metodo=metodo):
                self.assertEqual(
                    getattr(self.client, metodo)(reverse("orders:checkout")).status_code,
                    405,
                )
        producto.inventario.refresh_from_db()
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertTrue(ItemCarrito.objects.filter(pk=item.pk).exists())
        self.assertFalse(Pedido.objects.exists())

    def test_consultas_get_no_crecen_con_las_lineas(self):
        carrito, _, _ = self.crear_carrito(nombre="Primero")
        with CaptureQueriesContext(connection) as una:
            self.client.get(reverse("orders:checkout"))

        for nombre in ("Segundo", "Tercero"):
            producto = crear_producto_con_stock(nombre=nombre, stock=20)
            agregar_producto(
                session_key=carrito.session_key,
                producto_id=producto.pk,
                cantidad=1,
            )
        with CaptureQueriesContext(connection) as tres:
            self.client.get(reverse("orders:checkout"))

        self.assertEqual(len(una), len(tres))
        self.assertLessEqual(len(tres), 4)
