from urllib.parse import parse_qs, urlparse
import shutil
import tempfile

from django.test import TestCase, override_settings

from cart.services import agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from orders.models import ModalidadEntrega
from orders.services import DatosDireccionEnvio, crear_pedido_desde_carrito
from orders.tests.helpers import crear_carrito_checkout, datos_comprador
from orders.whatsapp import construir_enlace_whatsapp, construir_mensaje_whatsapp


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class WhatsAppTests(TestCase):
    def crear_pedido(self, *, envio=False):
        carrito, _ = crear_carrito_checkout(cantidad=3)
        direccion = None
        modalidad = ModalidadEntrega.RETIRO
        if envio:
            modalidad = ModalidadEntrega.ENVIO_DOMICILIO
            direccion = DatosDireccionEnvio(
                calle="San Martín",
                numero="123",
                piso="2",
                departamento="B",
                localidad="Posadas",
                provincia="Misiones",
                codigo_postal="3300",
                referencias="Portón & timbre",
            )
        pedido = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=modalidad,
            direccion_envio=direccion,
            observaciones="Sin bolsa",
        ).pedido
        return pedido

    def test_mensaje_retiro_utiliza_snapshots_y_no_incluye_dni(self):
        pedido = self.crear_pedido()
        mensaje = construir_mensaje_whatsapp(
            pedido=pedido,
            detalles=tuple(pedido.detalles.order_by("pk")),
            direccion_envio=None,
        )

        self.assertEqual(
            mensaje,
            f"Hola, quiero continuar con el Pedido {pedido.numero_pedido}.\n"
            "\n"
            "Comprador: Ana Coronado\n"
            "Teléfono: +54 (11) 4567-8901\n"
            "\n"
            "Productos:\n"
            "- Canarias, presentación 1 kg\n"
            "  Cantidad: 3\n"
            "  Precio unitario: ARS 4.500,00\n"
            "  Subtotal: ARS 13.500,00\n"
            "\n"
            "Total de mercadería: ARS 13.500,00\n"
            "Modalidad de entrega: Retiro\n"
            "Observaciones: Sin bolsa",
        )
        self.assertIn(f"Pedido {pedido.numero_pedido}", mensaje)
        self.assertIn("Comprador: Ana Coronado", mensaje)
        self.assertIn("Teléfono: +54 (11) 4567-8901", mensaje)
        self.assertIn("- Canarias, presentación 1 kg", mensaje)
        self.assertIn("Cantidad: 3", mensaje)
        self.assertIn("Precio unitario: ARS 4.500,00", mensaje)
        self.assertIn("Subtotal: ARS 13.500,00", mensaje)
        self.assertIn("Total de mercadería: ARS 13.500,00", mensaje)
        self.assertIn("Modalidad de entrega: Retiro", mensaje)
        self.assertIn("Observaciones: Sin bolsa", mensaje)
        self.assertNotIn(pedido.dni_cliente, mensaje)
        self.assertNotIn("Dirección:", mensaje)

    def test_mensaje_envio_formatea_direccion_y_referencias(self):
        pedido = self.crear_pedido(envio=True)
        mensaje = construir_mensaje_whatsapp(
            pedido=pedido,
            detalles=tuple(pedido.detalles.order_by("pk")),
            direccion_envio=pedido.direccion_envio,
        )

        self.assertIn("Modalidad de entrega: Envío a domicilio", mensaje)
        self.assertIn(
            "Dirección: San Martín 123, Piso 2, Departamento B, "
            "Posadas, Misiones, CP 3300",
            mensaje,
        )
        self.assertIn("Referencias: Portón & timbre", mensaje)

    def test_enlace_codifica_el_mensaje_y_es_reproducible(self):
        mensaje = "Pedido #1\nTeléfono: +54 & dirección"

        primero = construir_enlace_whatsapp(
            numero_comercial="5491112345678",
            mensaje=mensaje,
        )
        segundo = construir_enlace_whatsapp(
            numero_comercial="5491112345678",
            mensaje=mensaje,
        )

        self.assertEqual(primero, segundo)
        self.assertTrue(primero.startswith("https://wa.me/5491112345678?text="))
        self.assertEqual(parse_qs(urlparse(primero).query)["text"], [mensaje])

    def test_productos_conservan_orden_estable_de_detalles(self):
        carrito, _ = crear_carrito_checkout(
            session_key="orden-whatsapp",
            nombre="Primero",
            cantidad=1,
        )
        segundo = crear_producto_con_stock(nombre="Segundo", stock=10)
        agregar_producto(
            session_key=carrito.session_key,
            producto_id=segundo.pk,
            cantidad=1,
        )
        carrito.refresh_from_db()
        pedido = crear_pedido_desde_carrito(
            session_key=carrito.session_key,
            token_idempotencia=carrito.token_checkout,
            datos_comprador=datos_comprador(),
            modalidad_entrega=ModalidadEntrega.RETIRO,
        ).pedido

        mensaje = construir_mensaje_whatsapp(
            pedido=pedido,
            detalles=tuple(pedido.detalles.order_by("pk")),
            direccion_envio=None,
        )

        self.assertLess(mensaje.index("- Primero"), mensaje.index("- Segundo"))

    def test_numero_ausente_o_no_canonico_no_genera_enlace(self):
        for numero in (
            None,
            "",
            "+5491112345678",
            "54 9 11 1234 5678",
            "05491112345678",
            "1234567",
            "1234567890123456",
            "54911ABC5678",
        ):
            with self.subTest(numero=numero):
                self.assertIsNone(
                    construir_enlace_whatsapp(
                        numero_comercial=numero,
                        mensaje="Pedido",
                    )
                )
