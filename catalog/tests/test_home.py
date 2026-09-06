from urllib.parse import parse_qs, unquote, urlparse
import shutil
import tempfile
from types import SimpleNamespace

from django.contrib.sessions.models import Session
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from cart.models import ItemCarrito
from catalog.context_processors import (
    MENSAJE_CONSULTA_MAYORISTA,
    MENSAJE_CONTACTO_GENERAL,
    contacto_publico,
)
from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.services import registrar_ingreso_mercaderia


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


class EncabezadoPublicoTests(SimpleTestCase):
    def test_encabezado_muestra_logo_enlazado_sin_marca_textual(self):
        contenido = render_to_string(
            "base.html",
            {
                "cart_resumen_global": SimpleNamespace(
                    cantidad_total_unidades=0
                )
            },
        )
        encabezado = contenido.split('<header class="site-header">', 1)[1].split(
            "</header>", 1
        )[0]

        self.assertIn(
            f'<a class="brand" href="{reverse("catalog:producto_list")}"',
            encabezado,
        )
        self.assertIn('src="/static/catalog/images/logo.png"', encabezado)
        self.assertIn('alt="Logo de Yerbas Coronado"', encabezado)
        self.assertIn('placeholder="Buscar productos o marcas"', encabezado)
        self.assertIn('class="site-search-toggle"', encabezado)
        self.assertIn('aria-controls="site-search-form"', encabezado)
        self.assertIn('aria-expanded="false"', encabezado)
        self.assertIn('aria-label="Ver carrito. 0 unidades"', encabezado)
        self.assertIn('class="cart-count" aria-hidden="true">0</span>', encabezado)
        self.assertNotIn('class="cart-link__label"', encabezado)
        self.assertNotIn("brand__mark", encabezado)
        self.assertNotIn("brand__name", encabezado)


class HeroPublicoTests(SimpleTestCase):
    def test_hero_superpone_contenido_aprobado_sin_llamadas_a_la_accion(self):
        contenido = render_to_string(
            "catalog/producto_list.html",
            {
                "busqueda": "",
                "productos": [],
                "tarjetas": [],
                "cart_resumen_global": SimpleNamespace(
                    cantidad_total_unidades=0
                ),
                "whatsapp_mayorista_url": None,
            },
        )
        hero = contenido.split('<section class="home-hero"', 1)[1].split(
            "</section>", 1
        )[0]

        self.assertIn('src="/static/catalog/images/hero.png"', hero)
        self.assertIn(
            'alt="Selección de paquetes de yerba mate sobre una mesa de madera"',
            hero,
        )
        self.assertIn('<h1 id="hero-title">', hero)
        self.assertIn("<span>El mate que buscás,</span>", hero)
        self.assertIn("<span>en un solo lugar</span>", hero)
        self.assertIn(
            "Descubrí nuestra selección de yerbas y elegí la que mejor va con vos.",
            hero,
        )
        self.assertNotIn("Yerbas para cada mate", contenido)
        self.assertNotIn("<a ", hero)
        self.assertNotIn("<button", hero)


class VentajasPublicasTests(SimpleTestCase):
    def test_bloque_muestra_cinco_ventajas_con_iconos_svg(self):
        contenido = render_to_string(
            "catalog/producto_list.html",
            {
                "busqueda": "",
                "productos": [],
                "tarjetas": [],
                "cart_resumen_global": SimpleNamespace(
                    cantidad_total_unidades=0
                ),
                "whatsapp_mayorista_url": None,
            },
        )
        ventajas = contenido.split('<ul class="benefits">', 1)[1].split(
            "</ul>", 1
        )[0]

        textos = (
            "Variedad de yerbas",
            "Encontrá distintas marcas, estilos y presentaciones.",
            "Stock actualizado",
            "Consultá la disponibilidad real de cada producto.",
            "Precios por cantidad",
            "Accedé a mejores precios según el volumen de tu compra.",
            "Retiro o envío",
            "Elegí la modalidad que mejor se adapte a tu compra.",
            "Compra simple",
            "Armá tu pedido online de forma rápida y sencilla.",
        )

        self.assertEqual(ventajas.count('<li class="benefit">'), 5)
        self.assertEqual(ventajas.count('<svg class="benefit__icon"'), 5)
        self.assertEqual(ventajas.count('aria-hidden="true"'), 5)
        for texto in textos:
            with self.subTest(texto=texto):
                self.assertIn(texto, ventajas)
        self.assertNotIn("Ventajas de comprar en Yerbas Coronado", contenido)
        self.assertNotIn("<a ", ventajas)
        self.assertNotIn("<button", ventajas)


class ContextoWhatsAppTests(SimpleTestCase):
    @override_settings(WHATSAPP_BUSINESS_NUMBER="5492664933059")
    def test_genera_enlaces_diferenciados_sin_consultar_base_de_datos(self):
        contexto = contacto_publico(None)

        self.assertEqual(
            set(contexto),
            {"whatsapp_mayorista_url", "whatsapp_contacto_url"},
        )
        self.assertTrue(
            contexto["whatsapp_mayorista_url"].startswith(
                "https://wa.me/5492664933059?text="
            )
        )
        self.assertTrue(
            contexto["whatsapp_contacto_url"].startswith(
                "https://wa.me/5492664933059?text="
            )
        )
        self.assertNotEqual(
            contexto["whatsapp_mayorista_url"],
            contexto["whatsapp_contacto_url"],
        )


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
        self.assertContains(respuesta, "<span>El mate que buscás,</span>", html=True)
        self.assertContains(respuesta, "<span>en un solo lugar</span>", html=True)
        self.assertNotContains(respuesta, "Yerbas para cada mate")
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
        self.assertContains(respuesta, 'placeholder="Buscar productos o marcas"')
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

    def test_busqueda_sin_resultados_se_distingue_del_catalogo_vacio(self):
        self.crear_producto()

        sin_resultados = self.client.get(
            reverse("catalog:producto_list"),
            {"q": "marca inexistente"},
        )

        self.assertContains(
            sin_resultados,
            "No encontramos productos para tu búsqueda.",
        )
        self.assertNotContains(
            sin_resultados,
            "No hay productos disponibles en este momento.",
        )
        self.assertContains(sin_resultados, 'value="marca inexistente"')

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
            contenido.index("&lt;script&gt;alert"),
            contenido.index("Escalas de precio"),
            contenido.index(">Agregar</button>"),
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
        self.assertContains(respuesta, ">Agregar</button>")

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
        self.assertContains(respuesta, 'class="quantity-stepper__input"')
        self.assertContains(respuesta, 'value="1" data-confirmed-value="1"')

        self.client.post(agregar, {"next": retorno_home})
        item.refresh_from_db()
        self.assertEqual(item.cantidad, 2)

        respuesta = self.client.get(home)
        establecer = reverse("cart:establecer_cantidad", args=(item.pk,))
        self.assertContains(respuesta, f'action="{establecer}"')
        self.assertContains(respuesta, 'name="cantidad" value="1"')
        self.assertContains(respuesta, 'value="2" data-confirmed-value="2"')

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

    @override_settings(WHATSAPP_BUSINESS_NUMBER="5492664933059")
    def test_whatsapp_mayorista_reutiliza_enlace_codificado(self):
        respuesta = self.client.get(reverse("catalog:producto_list"))
        enlace = respuesta.context["whatsapp_mayorista_url"]
        enlace_contacto = respuesta.context["whatsapp_contacto_url"]

        self.assertTrue(enlace.startswith("https://wa.me/5492664933059?text="))
        texto = parse_qs(urlparse(enlace).query)["text"][0]
        self.assertEqual(unquote(texto), MENSAJE_CONSULTA_MAYORISTA)
        self.assertEqual(
            MENSAJE_CONSULTA_MAYORISTA,
            "¡Hola! Quiero más información para ventas mayoristas.",
        )
        self.assertContains(respuesta, "Consultar por mayor")
        self.assertContains(respuesta, 'target="_blank"')
        self.assertContains(respuesta, 'rel="noopener noreferrer"')
        self.assertContains(respuesta, 'referrerpolicy="no-referrer"')
        self.assertContains(
            respuesta,
            'aria-label="Consultar por ventas mayoristas mediante WhatsApp"',
        )
        self.assertNotIn("DNI", enlace)
        self.assertNotIn("carrito", enlace.lower())
        self.assertTrue(
            enlace_contacto.startswith("https://wa.me/5492664933059?text=")
        )
        texto_contacto = parse_qs(urlparse(enlace_contacto).query)["text"][0]
        self.assertEqual(unquote(texto_contacto), MENSAJE_CONTACTO_GENERAL)
        self.assertEqual(
            MENSAJE_CONTACTO_GENERAL,
            "¡Hola! Quiero más información sobre Yerbas Coronado.",
        )

    def test_whatsapp_invalido_muestra_boton_deshabilitado_sin_enlace_falso(self):
        for numero in ("", "inválido", "+5492664933059"):
            with self.subTest(numero=numero), override_settings(
                WHATSAPP_BUSINESS_NUMBER=numero
            ):
                respuesta = self.client.get(reverse("catalog:producto_list"))
                self.assertIsNone(respuesta.context["whatsapp_mayorista_url"])
                self.assertIsNone(respuesta.context["whatsapp_contacto_url"])
                self.assertNotContains(respuesta, "https://wa.me/")
                self.assertContains(respuesta, "Consultar por mayor")
                self.assertContains(respuesta, 'aria-disabled="true"')
                self.assertContains(respuesta, 'role="link"')
                self.assertNotContains(
                    respuesta,
                    "Las consultas mayoristas por WhatsApp no están disponibles temporalmente.",
                )

    def test_footer_muestra_logo_frase_redes_y_derechos(self):
        respuesta = self.client.get(reverse("catalog:producto_list"))
        contenido = respuesta.content.decode()
        footer = contenido.split('<footer class="site-footer">', 1)[1].split(
            "</footer>", 1
        )[0]

        self.assertIn('src="/static/catalog/images/logo.png"', footer)
        self.assertIn('alt="Logo de Yerbas Coronado"', footer)
        self.assertIn("Yerbas seleccionadas para cada mate.", footer)
        self.assertIn("https://www.instagram.com/yerba.coronado/", footer)
        self.assertIn("https://www.tiktok.com/@yerbacoronado", footer)
        self.assertIn("Visitar Instagram de Yerbas Coronado", footer)
        self.assertIn("Visitar TikTok de Yerbas Coronado", footer)
        self.assertIn("Todos los derechos reservados.", footer)
        self.assertNotIn("footer-brand__mark", footer)
        self.assertNotIn(">YC<", footer)
        self.assertNotIn(">Instagram<", footer)
        self.assertNotIn(">TikTok<", footer)
