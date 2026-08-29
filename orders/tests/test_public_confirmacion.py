from urllib.parse import parse_qs, urlparse
import shutil
import tempfile

from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from cart.models import Carrito
from cart.services import agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from inventory.models import MovimientoInventario
from orders.models import Cliente, ModalidadEntrega, Pedido
from orders.services import (
    DatosComprador,
    DatosDireccionEnvio,
    crear_pedido_desde_carrito,
)


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ConfirmacionPublicaTests(TestCase):
    def crear_pedido(
        self,
        *,
        envio=False,
        nombre_cliente="Ana",
        nombre_producto="Canarias",
        productos_adicionales=0,
    ):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        producto = crear_producto_con_stock(nombre=nombre_producto, stock=30)
        item = agregar_producto(
            session_key=sesion.session_key,
            producto_id=producto.pk,
            cantidad=2,
        )
        productos = [producto]
        for indice in range(productos_adicionales):
            adicional = crear_producto_con_stock(
                nombre=f"Producto adicional {indice}",
                stock=30,
            )
            agregar_producto(
                session_key=sesion.session_key,
                producto_id=adicional.pk,
                cantidad=1,
            )
            productos.append(adicional)
        item.carrito.refresh_from_db()
        modalidad = ModalidadEntrega.RETIRO
        direccion = None
        if envio:
            modalidad = ModalidadEntrega.ENVIO_DOMICILIO
            direccion = DatosDireccionEnvio(
                calle="San Martín",
                numero="123",
                localidad="Posadas",
                provincia="Misiones",
                codigo_postal="3300",
                referencias="Portón verde",
            )
        pedido = crear_pedido_desde_carrito(
            session_key=sesion.session_key,
            token_idempotencia=item.carrito.token_checkout,
            datos_comprador=DatosComprador(
                dni="12.345.678",
                nombre=nombre_cliente,
                apellido="Coronado",
                telefono="+54 (11) 4567-8901",
            ),
            modalidad_entrega=modalidad,
            direccion_envio=direccion,
            observaciones="Sin bolsa",
        ).pedido
        return pedido, productos

    def test_sesion_originaria_accede_y_dni_permanece_oculto(self):
        pedido, _ = self.crear_pedido()
        url = reverse("orders:confirmacion", args=(pedido.numero_pedido,))

        respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, pedido.numero_pedido)
        self.assertContains(respuesta, "Ana Coronado")
        self.assertContains(respuesta, pedido.telefono_cliente)
        self.assertContains(respuesta, "Canarias")
        self.assertContains(respuesta, "$ 10.000,00")
        self.assertNotContains(respuesta, pedido.dni_cliente)
        self.assertNotIn(pedido.dni_cliente, url)
        self.assertEqual(respuesta["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_pedido_inexistente_ajeno_o_sin_sesion_producen_404_equivalente(self):
        pedido, _ = self.crear_pedido()
        url = reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        otro = Client()
        sesiones_antes = Session.objects.count()

        self.assertEqual(otro.get(url).status_code, 404)
        self.assertEqual(Session.objects.count(), sesiones_antes)
        sesion_ajena = otro.session
        sesion_ajena["iniciada"] = True
        sesion_ajena.save()
        self.assertEqual(otro.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("orders:confirmacion", args=("YC-000000000000",))
            ).status_code,
            404,
        )
        self.assertEqual(Session.objects.count(), sesiones_antes + 1)

    def test_identificador_malformado_produce_404(self):
        self.crear_pedido()

        self.assertEqual(
            self.client.get("/pedidos/abc/confirmacion/").status_code,
            404,
        )

    def test_confirmacion_usa_snapshots_tras_cambiar_cliente_y_producto(self):
        pedido, productos = self.crear_pedido()
        Cliente.objects.filter(pk=pedido.cliente_id).update(
            nombre="Nombre actual del Cliente",
            telefono="999999",
        )
        productos[0].__class__.objects.filter(pk=productos[0].pk).update(
            nombre="Nombre actual del Producto",
            peso="500 g",
        )

        respuesta = self.client.get(
            reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        )

        self.assertContains(respuesta, "Ana Coronado")
        self.assertContains(respuesta, "+54 (11) 4567-8901")
        self.assertContains(respuesta, "Canarias")
        self.assertContains(respuesta, "1 kg")
        self.assertNotContains(respuesta, "Nombre actual del Cliente")
        self.assertNotContains(respuesta, "Nombre actual del Producto")
        self.assertNotContains(respuesta, "500 g")

    def test_envio_muestra_direccion_historica_y_costo_no_persistido(self):
        pedido, _ = self.crear_pedido(envio=True)

        respuesta = self.client.get(
            reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        )

        self.assertContains(respuesta, "San Martín 123")
        self.assertContains(respuesta, "Posadas, Misiones")
        self.assertContains(respuesta, "Portón verde")
        self.assertContains(respuesta, "El costo de envío se coordina posteriormente")

    @override_settings(WHATSAPP_BUSINESS_NUMBER="5491112345678")
    def test_whatsapp_codifica_snapshots_omite_dni_y_protege_referrer(self):
        pedido, _ = self.crear_pedido(envio=True)

        respuesta = self.client.get(
            reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        )

        enlace = respuesta.context["enlace_whatsapp"]
        mensaje = parse_qs(urlparse(enlace).query)["text"][0]
        self.assertTrue(enlace.startswith("https://wa.me/5491112345678?text="))
        self.assertIn(pedido.numero_pedido, mensaje)
        self.assertIn(pedido.telefono_cliente, mensaje)
        self.assertIn("Dirección: San Martín 123", mensaje)
        self.assertNotIn(pedido.dni_cliente, mensaje)
        self.assertContains(respuesta, 'referrerpolicy="no-referrer"')
        self.assertContains(respuesta, 'rel="noreferrer"')

    def test_configuracion_whatsapp_ausente_o_invalida_no_oculta_pedido(self):
        pedido, _ = self.crear_pedido()
        url = reverse("orders:confirmacion", args=(pedido.numero_pedido,))

        for numero in ("", "+5491112345678"):
            with self.subTest(numero=numero), override_settings(
                WHATSAPP_BUSINESS_NUMBER=numero
            ):
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, pedido.numero_pedido)
                self.assertContains(
                    respuesta,
                    "WhatsApp no está disponible temporalmente",
                )
                self.assertNotContains(respuesta, "https://wa.me/")

    def test_contenido_del_visitante_permanece_autoescapado(self):
        pedido, _ = self.crear_pedido(nombre_cliente="<script>alert('x')</script>")

        respuesta = self.client.get(
            reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        )

        self.assertContains(
            respuesta,
            "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;",
        )
        self.assertNotContains(respuesta, "<script>alert('x')</script>")

    def test_get_y_head_no_modifican_pedido_inventario_o_movimientos(self):
        pedido, productos = self.crear_pedido()
        url = reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        producto = productos[0]
        producto.inventario.refresh_from_db()
        estado = pedido.estado
        stock = producto.inventario.cantidad_disponible
        conteos = (
            Pedido.objects.count(),
            MovimientoInventario.objects.count(),
            pedido.detalles.count(),
        )

        self.assertEqual(self.client.get(url).status_code, 200)
        respuesta_head = self.client.head(url)

        pedido.refresh_from_db()
        producto.inventario.refresh_from_db()
        self.assertEqual(respuesta_head.status_code, 200)
        self.assertEqual(respuesta_head.content, b"")
        self.assertEqual(pedido.estado, estado)
        self.assertEqual(producto.inventario.cantidad_disponible, stock)
        self.assertEqual(
            (
                Pedido.objects.count(),
                MovimientoInventario.objects.count(),
                pedido.detalles.count(),
            ),
            conteos,
        )

    def test_confirmacion_rechaza_metodos_de_escritura(self):
        pedido, _ = self.crear_pedido()
        url = reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        for metodo in ("post", "put", "patch", "delete"):
            with self.subTest(metodo=metodo):
                self.assertEqual(getattr(self.client, metodo)(url).status_code, 405)

    def test_consultas_no_crecen_con_los_detalles(self):
        pedido_uno, _ = self.crear_pedido(nombre_producto="Pedido uno")
        with CaptureQueriesContext(connection) as una:
            self.client.get(
                reverse("orders:confirmacion", args=(pedido_uno.numero_pedido,))
            )

        pedido_tres, _ = self.crear_pedido(
            nombre_producto="Pedido tres",
            productos_adicionales=2,
        )
        with CaptureQueriesContext(connection) as tres:
            self.client.get(
                reverse("orders:confirmacion", args=(pedido_tres.numero_pedido,))
            )

        self.assertEqual(len(una), len(tres))
        self.assertLessEqual(len(tres), 4)

    def test_replay_antiguo_no_oculta_el_nuevo_carrito_del_encabezado(self):
        pedido, productos = self.crear_pedido()
        agregar_producto(
            session_key=self.client.session.session_key,
            producto_id=productos[0].pk,
            cantidad=1,
        )

        respuesta = self.client.get(
            reverse("orders:confirmacion", args=(pedido.numero_pedido,))
        )

        self.assertContains(respuesta, "Carrito, 1 unidad")
        self.assertTrue(Carrito.objects.filter(session_key=self.client.session.session_key).exists())
