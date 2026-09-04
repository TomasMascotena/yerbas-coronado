from urllib.parse import parse_qs, unquote, urlparse
import shutil
import tempfile

from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.urls import reverse

from cart.models import ItemCarrito
from catalog.context_processors import MENSAJE_CONSULTA_MAYORISTA
from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.services import registrar_ingreso_mercaderia


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class HomePublicaTests(TestCase):
    def crear_producto(
        self,
        *,
        nombre="Canarias Tradicional",
        peso="1 kg",
        descripcion="Sabor intenso y equilibrado.",
        stock=10,
        activo=True,
    ):
        producto = crear_producto_con_inventario(
            producto=Producto(
                **datos_producto(
                    nombre=nombre,
                    peso=peso,
                    descripcion=descripcion,
                    activo=activo,
                    imagen=imagen_de_prueba(f"{nombre}-{peso}.gif"),
                )
            )
        )
        if stock:
            registrar_ingreso_mercaderia(
                inventario_id=producto.inventario.pk,
                cantidad=stock,
            )
        return producto

    def test_home_presenta_las_secciones_y_textos_aprobados(self):
        self.crear_producto()

        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.content.count(b"<h1"), 1)
        for texto in (
            "El mate que buscás, en un solo lugar",
            "Descubrí nuestra selección de yerbas y elegí la que mejor va con vos.",
            "Variedad de yerbas",
            "Stock actualizado",
            "Precios por cantidad",
            "Retiro o envío",
            "Compra simple",
            "Nuestros productos",
            "Combiná distintas yerbas en tu compra y accedé a mejores precios según la cantidad total.",
            "¿Comprás para tu negocio?",
            "Tenemos opciones mayoristas para comercios, revendedores y compras en cantidad.",
        ):
            with self.subTest(texto=texto):
                self.assertContains(respuesta, texto)
        self.assertContains(respuesta, "catalog/images/hero.png")
        self.assertContains(respuesta, "catalog/images/ventas_mayoristas.png")
        self.assertContains(
            respuesta,
            'alt="Selección de paquetes de yerba mate sobre una mesa de madera"',
        )
        self.assertContains(
            respuesta,
            'alt="Selección de yerbas para ventas mayoristas"',
        )
        self.assertContains(respuesta, 'role="search"')
        self.assertContains(respuesta, 'placeholder="Buscar productos"')
        self.assertNotContains(respuesta, "carousel")

    def test_buscador_filtra_por_nombre_o_peso_sin_crear_sesion(self):
        canarias = self.crear_producto()
        medio_kilo = self.crear_producto(
            nombre="Baldo Suave",
            peso="500 g",
        )
        self.crear_producto(nombre="Oculta", activo=False)

        por_nombre = self.client.get(
            reverse("catalog:producto_list"),
            {"q": "canarias"},
        )
        por_peso = self.client.get(
            reverse("catalog:producto_list"),
            {"q": "500 g"},
        )

        self.assertEqual(list(por_nombre.context["productos"]), [canarias])
        self.assertEqual(list(por_peso.context["productos"]), [medio_kilo])
        self.assertContains(por_nombre, 'value="canarias"')
        self.assertContains(por_nombre, "Resultados para “canarias”")
        self.assertEqual(Session.objects.count(), 0)

    def test_tarjeta_respeta_orden_y_autoescape_de_descripcion(self):
        producto = self.crear_producto(
            descripcion="<script>alert('descripción')</script>",
        )

        respuesta = self.client.get(reverse("catalog:producto_list"))
        contenido = respuesta.content.decode()

        posiciones = (
            contenido.index(producto.imagen.url),
            contenido.index(producto.nombre),
            contenido.index(producto.peso),
            contenido.index("Escalas de precio"),
            contenido.index("Agregar al carrito"),
            contenido.index("&lt;script&gt;alert"),
        )
        self.assertEqual(posiciones, tuple(sorted(posiciones)))
        self.assertNotContains(respuesta, "<script>alert('descripción')</script>")
        self.assertContains(respuesta, 'class="product-card__description"')

    def test_control_de_tarjeta_reutiliza_operaciones_existentes(self):
        producto = self.crear_producto(stock=5)
        home = reverse("catalog:producto_list")
        retorno_home = f"{home}#productos"
        agregar = reverse("cart:agregar_producto", args=(producto.pk,))

        respuesta = self.client.get(home)
        self.assertContains(respuesta, f'action="{agregar}"')
        self.assertContains(respuesta, "Agregar al carrito")

        respuesta = self.client.post(agregar, {"next": retorno_home})
        self.assertRedirects(
            respuesta,
            retorno_home,
            fetch_redirect_response=False,
        )
        item = ItemCarrito.objects.get()
        self.assertEqual(item.cantidad, 1)

        respuesta = self.client.get(home)
        self.assertContains(
            respuesta,
            f'action="{reverse("cart:eliminar_item", args=(item.pk,))}"',
        )
        self.assertContains(respuesta, '<output aria-label="Cantidad actual">1</output>')

        self.client.post(agregar, {"next": retorno_home})
        item.refresh_from_db()
        self.assertEqual(item.cantidad, 2)

        respuesta = self.client.get(home)
        establecer = reverse("cart:establecer_cantidad", args=(item.pk,))
        self.assertContains(respuesta, f'action="{establecer}"')
        self.assertContains(respuesta, 'name="cantidad" value="1"')
        self.assertContains(respuesta, '<output aria-label="Cantidad actual">2</output>')

        self.client.post(
            establecer,
            {"cantidad": 1, "next": retorno_home},
        )
        item.refresh_from_db()
        self.assertEqual(item.cantidad, 1)
        self.client.post(
            reverse("cart:eliminar_item", args=(item.pk,)),
            {"next": retorno_home},
        )
        self.assertFalse(ItemCarrito.objects.exists())

    def test_retorno_externo_es_rechazado(self):
        producto = self.crear_producto()

        respuesta = self.client.post(
            reverse("cart:agregar_producto", args=(producto.pk,)),
            {"next": "https://example.invalid/engaño"},
        )

        self.assertRedirects(
            respuesta,
            reverse("cart:detalle"),
            fetch_redirect_response=False,
        )

    @override_settings(WHATSAPP_BUSINESS_NUMBER="5491112345678")
    def test_whatsapp_mayorista_reutiliza_enlace_codificado(self):
        respuesta = self.client.get(reverse("catalog:producto_list"))
        enlace = respuesta.context["whatsapp_mayorista_url"]

        self.assertTrue(enlace.startswith("https://wa.me/5491112345678?text="))
        texto = parse_qs(urlparse(enlace).query)["text"][0]
        self.assertEqual(unquote(texto), MENSAJE_CONSULTA_MAYORISTA)
        self.assertContains(respuesta, "Consultar por mayor")
        self.assertContains(respuesta, 'referrerpolicy="no-referrer"')

    def test_whatsapp_invalido_degrada_sin_enlace_falso(self):
        for numero in ("", "inválido"):
            with self.subTest(numero=numero), override_settings(
                WHATSAPP_BUSINESS_NUMBER=numero
            ):
                respuesta = self.client.get(reverse("catalog:producto_list"))
                self.assertIsNone(respuesta.context["whatsapp_mayorista_url"])
                self.assertNotContains(respuesta, "https://wa.me/")
                self.assertNotContains(respuesta, "Consultar por mayor")
                self.assertContains(
                    respuesta,
                    "Las consultas mayoristas por WhatsApp no están disponibles temporalmente.",
                )
