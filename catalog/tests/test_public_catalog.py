from decimal import Decimal
import shutil
import tempfile

from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from cart.models import Carrito, ItemCarrito
from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.templatetags.catalog_format import precio_ars
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.models import Inventario, MovimientoInventario
from inventory.services import registrar_ingreso_mercaderia


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class CatalogoPublicoTests(TestCase):
    def crear_producto(
        self,
        *,
        nombre="Canarias",
        peso="1 kg",
        activo=True,
        stock=0,
        **cambios,
    ):
        producto = crear_producto_con_inventario(
            producto=Producto(
                **datos_producto(
                    nombre=nombre,
                    peso=peso,
                    activo=activo,
                    imagen=imagen_de_prueba(
                        f"{nombre}-{peso}".replace(" ", "-") + ".gif"
                    ),
                    **cambios,
                )
            )
        )
        if stock:
            registrar_ingreso_mercaderia(
                inventario_id=producto.inventario.pk,
                cantidad=stock,
            )
        return producto

    def test_listado_y_detalle_permiten_get_y_head(self):
        producto = self.crear_producto()
        urls = (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        )

        for url in urls:
            with self.subTest(url=url, metodo="GET"):
                self.assertEqual(self.client.get(url).status_code, 200)
            with self.subTest(url=url, metodo="HEAD"):
                respuesta = self.client.head(url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.content, b"")

    def test_listado_y_detalle_rechazan_metodos_no_seguros_sin_efectos(self):
        producto = self.crear_producto(stock=5)
        urls = (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        )
        conteos_iniciales = self.conteos_de_estado()

        for url in urls:
            for metodo in ("post", "put", "patch", "delete"):
                with self.subTest(url=url, metodo=metodo.upper()):
                    respuesta = getattr(self.client, metodo)(url)
                    self.assertEqual(respuesta.status_code, 405)

        self.assertEqual(self.conteos_de_estado(), conteos_iniciales)

    def conteos_de_estado(self):
        return {
            "productos": Producto.objects.count(),
            "inventarios": Inventario.objects.count(),
            "movimientos": MovimientoInventario.objects.count(),
            "carritos": Carrito.objects.count(),
            "items": ItemCarrito.objects.count(),
            "sesiones": Session.objects.count(),
        }

    def test_catalogo_vacio_muestra_su_estado_sin_renderizar_grilla(self):
        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(list(respuesta.context["productos"]), [])
        self.assertContains(
            respuesta,
            "No hay Productos publicados en este momento.",
        )
        self.assertNotContains(respuesta, 'aria-label="Catálogo de productos"')

    def test_listado_muestra_solo_activos_en_orden_estable(self):
        producto_z = self.crear_producto(nombre="Zeta", peso="500 g")
        producto_a_1kg = self.crear_producto(nombre="Alfa", peso="1 kg")
        producto_a_500g = self.crear_producto(nombre="Alfa", peso="500 g")
        inactivo = self.crear_producto(nombre="Oculto", activo=False)

        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            list(respuesta.context["productos"]),
            [producto_a_1kg, producto_a_500g, producto_z],
        )
        self.assertNotContains(respuesta, inactivo.nombre)

    def test_producto_inactivo_y_producto_inexistente_devuelven_404(self):
        inactivo = self.crear_producto(activo=False)

        respuesta_inactivo = self.client.get(
            reverse("catalog:producto_detail", args=(inactivo.pk,))
        )
        respuesta_inexistente = self.client.get(
            reverse("catalog:producto_detail", args=(inactivo.pk + 999,))
        )

        self.assertEqual(respuesta_inactivo.status_code, 404)
        self.assertEqual(respuesta_inexistente.status_code, 404)
        self.assertNotContains(
            respuesta_inactivo,
            inactivo.nombre,
            status_code=404,
        )

    def test_producto_activo_sin_stock_permanece_visible(self):
        producto = self.crear_producto(stock=0)

        listado = self.client.get(reverse("catalog:producto_list"))
        detalle = self.client.get(
            reverse("catalog:producto_detail", args=(producto.pk,))
        )

        self.assertContains(listado, producto.nombre)
        self.assertContains(listado, "Sin Stock")
        self.assertContains(detalle, "Sin Stock")

    def test_tarjeta_muestra_peso_imagen_y_navegacion_accesible(self):
        producto = self.crear_producto(
            nombre="Canarias Tradicional",
            peso="1 kg",
        )
        url_detalle = reverse("catalog:producto_detail", args=(producto.pk,))

        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertContains(respuesta, ">1 kg<")
        self.assertContains(respuesta, f'src="{producto.imagen.url}"')
        self.assertContains(
            respuesta,
            'alt="Producto Canarias Tradicional, presentación 1 kg"',
        )
        self.assertContains(
            respuesta,
            'aria-label="Catálogo de productos"',
        )
        self.assertContains(respuesta, f'href="{url_detalle}"')
        self.assertContains(
            respuesta,
            (
                f'<a class="detail-link" href="{url_detalle}" '
                'aria-label="Ver Canarias Tradicional, presentación 1 kg">'
                "Ver producto</a>"
            ),
            html=True,
        )

    def test_detalle_muestra_peso_imagen_y_enlace_de_regreso(self):
        producto = self.crear_producto(
            nombre="Canarias Tradicional",
            peso="1 kg",
        )

        respuesta = self.client.get(
            reverse("catalog:producto_detail", args=(producto.pk,))
        )

        self.assertContains(respuesta, ">1 kg<")
        self.assertContains(respuesta, f'src="{producto.imagen.url}"')
        self.assertContains(
            respuesta,
            'alt="Producto Canarias Tradicional, presentación 1 kg"',
        )
        self.assertContains(
            respuesta,
            f'href="{reverse("catalog:producto_list")}"',
        )
        self.assertContains(respuesta, "Volver a Productos")

    def test_producto_con_stock_se_muestra_disponible_sin_exponer_cantidad(self):
        producto = self.crear_producto(stock=987654)

        for url in (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        ):
            with self.subTest(url=url):
                respuesta = self.client.get(url)
                self.assertContains(respuesta, "Disponible")
                self.assertNotContains(respuesta, "987654")

    def test_listado_y_detalle_muestran_las_tres_escalas_en_ars(self):
        producto = self.crear_producto(
            precio_unitario=Decimal("5000.00"),
            precio_desde_3=Decimal("4750.50"),
            precio_desde_20=Decimal("4100.25"),
        )
        importes = ("$ 5.000,00", "$ 4.750,50", "$ 4.100,25")

        for url in (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        ):
            with self.subTest(url=url):
                respuesta = self.client.get(url)
                for importe in importes:
                    self.assertContains(respuesta, importe)

    def test_detalle_muestra_descripcion_escapada_y_la_omite_si_esta_vacia(self):
        con_descripcion = self.crear_producto(
            nombre="Con descripción",
            descripcion="<script>alert('x')</script>",
        )
        sin_descripcion = self.crear_producto(
            nombre="Sin descripción",
            descripcion="",
        )

        respuesta = self.client.get(
            reverse("catalog:producto_detail", args=(con_descripcion.pk,))
        )
        self.assertContains(
            respuesta,
            "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;",
        )
        self.assertNotContains(respuesta, "<script>alert('x')</script>")

        respuesta = self.client.get(
            reverse("catalog:producto_detail", args=(sin_descripcion.pk,))
        )
        self.assertNotContains(respuesta, 'class="product-description"')

    def test_paginas_poseen_un_solo_h1(self):
        producto = self.crear_producto()

        for url in (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        ):
            with self.subTest(url=url):
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.content.count(b"<h1"), 1)

    def test_consultas_del_listado_no_crecen_con_los_productos(self):
        self.crear_producto(nombre="Producto 1")

        with CaptureQueriesContext(connection) as consultas_un_producto:
            self.client.get(reverse("catalog:producto_list"))

        for numero in range(2, 8):
            self.crear_producto(nombre=f"Producto {numero}")

        with CaptureQueriesContext(connection) as consultas_siete_productos:
            self.client.get(reverse("catalog:producto_list"))

        self.assertEqual(
            len(consultas_un_producto),
            len(consultas_siete_productos),
        )
        self.assertLessEqual(len(consultas_siete_productos), 1)

    def test_detalle_resuelve_producto_e_inventario_en_una_consulta(self):
        producto = self.crear_producto()

        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(
                reverse("catalog:producto_detail", args=(producto.pk,))
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(consultas), 1)

    def test_catalogo_no_crea_sesion_carrito_items_ni_movimientos(self):
        producto = self.crear_producto()
        conteos_iniciales = self.conteos_de_estado()

        self.client.get(reverse("catalog:producto_list"))
        self.client.get(
            reverse("catalog:producto_detail", args=(producto.pk,))
        )

        self.assertEqual(self.conteos_de_estado(), conteos_iniciales)

    def test_get_y_head_no_modifican_producto_ni_inventario(self):
        producto = self.crear_producto(stock=5)
        inventario = producto.inventario
        producto_inicial = Producto.objects.values().get(pk=producto.pk)
        inventario_inicial = Inventario.objects.values().get(pk=inventario.pk)
        conteos_iniciales = self.conteos_de_estado()
        urls = (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        )

        for metodo in ("get", "head"):
            for url in urls:
                with self.subTest(metodo=metodo.upper(), url=url):
                    respuesta = getattr(self.client, metodo)(url)
                    self.assertEqual(respuesta.status_code, 200)
                    self.assertEqual(
                        Producto.objects.values().get(pk=producto.pk),
                        producto_inicial,
                    )
                    self.assertEqual(
                        Inventario.objects.values().get(pk=inventario.pk),
                        inventario_inicial,
                    )
                    self.assertEqual(
                        self.conteos_de_estado(),
                        conteos_iniciales,
                    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS, DEBUG=False)
class InvarianteInventarioCatalogoTests(TestCase):
    def test_producto_activo_sin_inventario_produce_error_interno(self):
        producto = Producto.objects.create(
            **datos_producto(
                nombre="Estructuralmente inválido",
                imagen=imagen_de_prueba("sin-inventario.gif"),
            )
        )
        cliente = Client(raise_request_exception=False)

        for url in (
            reverse("catalog:producto_list"),
            reverse("catalog:producto_detail", args=(producto.pk,)),
        ):
            with self.subTest(url=url):
                respuesta = cliente.get(url)
                self.assertEqual(respuesta.status_code, 500)
                self.assertNotContains(
                    respuesta,
                    "Sin Stock",
                    status_code=500,
                )
                self.assertNotContains(
                    respuesta,
                    producto.nombre,
                    status_code=500,
                )


@override_settings(LANGUAGE_CODE="es-ar", USE_I18N=True)
class FormatoPrecioArsTests(TestCase):
    def test_precio_ars_formatea_decimal_con_dos_decimales_y_miles(self):
        valor = Decimal("5000.00")

        self.assertEqual(precio_ars(valor), "$ 5.000,00")
        self.assertIsInstance(valor, Decimal)
