from datetime import timedelta
from decimal import Decimal
import shutil
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from cart.models import Carrito, ItemCarrito
from cart.exceptions import ProductoNoDisponible
from cart.pricing import EstadoPrecioCarritoInvalido
from cart.services import (
    DURACION_CARRITO,
    agregar_producto,
    establecer_cantidad_item,
)
from cart.tests.helpers import crear_item_directo, crear_producto_con_stock
from catalog.models import Producto
from catalog.tests.helpers import datos_producto, imagen_de_prueba
from inventory.models import Inventario, MovimientoInventario
from inventory.services import registrar_venta_presencial


MEDIA_ROOT_PRUEBAS = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT_PRUEBAS, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class MetodosSesionYCatalogoTests(TestCase):
    def test_detalle_acepta_get_y_head_sin_crear_persistencia(self):
        url = reverse("cart:detalle")

        respuesta_get = self.client.get(url)
        respuesta_head = self.client.head(url)

        self.assertEqual(respuesta_get.status_code, 200)
        self.assertContains(respuesta_get, "Tu Carrito está vacío.")
        self.assertEqual(respuesta_head.status_code, 200)
        self.assertEqual(respuesta_head.content, b"")
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)
        self.assertFalse(Session.objects.exists())
        self.assertFalse(Carrito.objects.exists())

    def test_catalogo_get_y_head_sin_sesion_no_crean_carrito(self):
        producto = crear_producto_con_stock(stock=5)
        url = reverse("catalog:producto_list")

        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.head(url).status_code, 200)

        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)
        self.assertFalse(Session.objects.exists())
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())
        self.assertEqual(
            Inventario.objects.get(producto=producto).cantidad_disponible,
            5,
        )

    def test_detalle_rechaza_metodos_no_seguros(self):
        url = reverse("cart:detalle")

        for metodo in ("post", "put", "patch", "delete"):
            with self.subTest(metodo=metodo.upper()):
                self.assertEqual(getattr(self.client, metodo)(url).status_code, 405)

    def test_operaciones_aceptan_solo_post(self):
        producto = crear_producto_con_stock(stock=5)
        urls = (
            reverse("cart:agregar_producto", args=(producto.pk,)),
            reverse("cart:establecer_cantidad", args=(1,)),
            reverse("cart:eliminar_item", args=(1,)),
            reverse("cart:vaciar"),
        )

        for url in urls:
            for metodo in ("get", "head", "put", "patch", "delete"):
                with self.subTest(url=url, metodo=metodo.upper()):
                    self.assertEqual(
                        getattr(self.client, metodo)(url).status_code,
                        405,
                    )

    def test_ids_malformados_producen_404(self):
        for url in (
            "/carrito/agregar/abc/",
            "/carrito/items/abc/cantidad/",
            "/carrito/items/abc/eliminar/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 404)

    def test_primer_agregado_crea_sesion_carrito_e_item_y_usa_prg(self):
        producto = crear_producto_con_stock(stock=5)
        url = reverse("cart:agregar_producto", args=(producto.pk,))

        respuesta = self.client.post(url)

        self.assertRedirects(
            respuesta,
            reverse("cart:detalle"),
            fetch_redirect_response=False,
        )
        self.assertIsNotNone(self.client.session.session_key)
        self.assertEqual(Session.objects.count(), 1)
        self.assertEqual(Carrito.objects.count(), 1)
        item = ItemCarrito.objects.get()
        self.assertEqual(item.producto, producto)
        self.assertEqual(item.cantidad, 1)

        self.client.get(reverse("cart:detalle"))
        item.refresh_from_db()
        self.assertEqual(item.cantidad, 1)

    def test_fallo_funcional_del_primer_agregado_no_deja_estado_parcial(self):
        producto = crear_producto_con_stock(stock=0)

        respuesta = self.client.post(
            reverse("cart:agregar_producto", args=(producto.pk,))
        )

        self.assertEqual(respuesta.status_code, 302)
        self.assertIsNotNone(self.client.session.session_key)
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())

    def test_operaciones_sin_sesion_no_crean_sesion_ni_carrito(self):
        for url, datos in (
            (reverse("cart:establecer_cantidad", args=(999,)), {"cantidad": 2}),
            (reverse("cart:eliminar_item", args=(999,)), {}),
            (reverse("cart:vaciar"), {}),
        ):
            with self.subTest(url=url):
                respuesta = self.client.post(url, datos)
                self.assertEqual(respuesta.status_code, 302)
                self.assertNotIn(
                    settings.SESSION_COOKIE_NAME,
                    self.client.cookies,
                )
                self.assertFalse(Session.objects.exists())
                self.assertFalse(Carrito.objects.exists())

    def test_catalogo_ofrece_agregado_solo_para_producto_con_stock(self):
        disponible = crear_producto_con_stock(nombre="Disponible", stock=5)
        sin_stock = crear_producto_con_stock(nombre="Sin stock", stock=0)

        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertContains(
            respuesta,
            reverse("cart:agregar_producto", args=(disponible.pk,)),
        )
        self.assertNotContains(
            respuesta,
            reverse("cart:agregar_producto", args=(sin_stock.pk,)),
        )
        self.assertContains(
            respuesta,
            f"Agregar {disponible.nombre}, presentación {disponible.peso} al Carrito",
        )
        self.assertNotContains(respuesta, "5 unidades disponibles")

        detalle_sin_stock = self.client.get(
            reverse("catalog:producto_detail", args=(sin_stock.pk,))
        )
        self.assertNotContains(
            detalle_sin_stock,
            reverse("cart:agregar_producto", args=(sin_stock.pk,)),
        )

    def test_formulario_de_catalogo_agrega_exactamente_una_unidad(self):
        producto = crear_producto_con_stock(stock=5)

        detalle = self.client.get(
            reverse("catalog:producto_detail", args=(producto.pk,))
        )
        self.assertContains(
            detalle,
            reverse("cart:agregar_producto", args=(producto.pk,)),
        )

        self.client.post(
            reverse("cart:agregar_producto", args=(producto.pk,))
        )

        self.assertEqual(ItemCarrito.objects.get().cantidad, 1)

    def test_indicador_global_muestra_cero_unidades(self):
        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertContains(respuesta, "Carrito, 0 unidades")
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)

    def test_indicador_global_utiliza_singular_para_una_unidad(self):
        producto = crear_producto_con_stock(stock=5)

        self.client.post(
            reverse("cart:agregar_producto", args=(producto.pk,))
        )
        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertContains(respuesta, "Carrito, 1 unidad")
        self.assertNotContains(respuesta, "Carrito, 1 unidades")

    def test_indicador_global_muestra_varias_unidades_en_catalogo(self):
        producto = crear_producto_con_stock(stock=5)
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        agregar_producto(
            session_key=sesion.session_key,
            producto_id=producto.pk,
            cantidad=3,
        )

        respuesta = self.client.get(reverse("catalog:producto_list"))

        self.assertContains(respuesta, "Carrito, 3 unidades")

    def test_producto_inexistente_e_inactivo_tienen_mensaje_funcional_generico(self):
        inactivo = crear_producto_con_stock(nombre="Inactivo", stock=5)
        inactivo.activo = False
        inactivo.save(update_fields=("activo",))

        for producto_id in (inactivo.pk, 999999):
            with self.subTest(producto_id=producto_id):
                respuesta = self.client.post(
                    reverse("cart:agregar_producto", args=(producto_id,)),
                    follow=True,
                )
                self.assertContains(
                    respuesta,
                    "El Producto no está disponible.",
                )
                self.assertFalse(Carrito.objects.exists())
                self.assertFalse(ItemCarrito.objects.exists())


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class CarritoPresentacionYPricingTests(TestCase):
    def setUp(self):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        self.session_key = sesion.session_key
        self.producto = crear_producto_con_stock(stock=30)

    def agregar(self, cantidad=1):
        return agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=cantidad,
        )

    def test_muestra_nombre_peso_imagen_cantidad_y_resumen(self):
        self.agregar(cantidad=2)

        respuesta = self.client.get(reverse("cart:detalle"))

        self.assertContains(respuesta, self.producto.nombre)
        self.assertContains(respuesta, self.producto.peso)
        self.assertContains(respuesta, self.producto.imagen.url)
        self.assertContains(
            respuesta,
            f"Producto {self.producto.nombre}, presentación {self.producto.peso}",
        )
        self.assertContains(respuesta, 'value="2"')
        self.assertContains(respuesta, "Precio unitario")
        self.assertContains(respuesta, "$ 5.000,00")
        self.assertContains(respuesta, "$ 10.000,00")
        self.assertContains(respuesta, "Carrito, 2 unidades")
        self.assertEqual(respuesta.content.count(b"<h1"), 1)
        self.assertContains(respuesta, "Resumen del Carrito")
        self.assertContains(respuesta, "Actualizar cantidad de")
        self.assertContains(respuesta, "Eliminar Canarias, presentación 1 kg")

    def test_linea_muestra_nombre_peso_e_imagen_actuales(self):
        self.agregar()
        self.producto.nombre = "Canarias Edición Actual"
        self.producto.peso = "750 g"
        self.producto.imagen = imagen_de_prueba("imagen-actual.gif")
        self.producto.save(update_fields=("nombre", "peso", "imagen"))

        respuesta = self.client.get(reverse("cart:detalle"))

        self.assertContains(respuesta, "Canarias Edición Actual")
        self.assertContains(respuesta, "750 g")
        self.assertContains(respuesta, self.producto.imagen.url)

    def test_contenido_del_producto_permanece_autoescapado(self):
        self.agregar()
        Producto.objects.filter(pk=self.producto.pk).update(
            nombre="<script>alert('x')</script>"
        )

        respuesta = self.client.get(reverse("cart:detalle"))

        self.assertContains(
            respuesta,
            "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;",
        )
        self.assertNotContains(respuesta, "<script>alert('x')</script>")

    def test_umbrales_globales_1_3_y_20(self):
        item = self.agregar(cantidad=1)
        casos = (
            (1, "Precio unitario", "$ 5.000,00"),
            (3, "Precio desde 3 unidades", "$ 4.500,00"),
            (20, "Precio desde 20 unidades", "$ 4.000,00"),
        )

        for cantidad, escala, precio in casos:
            with self.subTest(cantidad=cantidad):
                establecer_cantidad_item(
                    session_key=self.session_key,
                    item_id=item.pk,
                    cantidad=cantidad,
                )
                respuesta = self.client.get(reverse("cart:detalle"))
                self.assertContains(respuesta, escala)
                self.assertContains(respuesta, precio)
                unidad = "unidad" if cantidad == 1 else "unidades"
                self.assertContains(
                    respuesta,
                    f"Carrito, {cantidad} {unidad}",
                )

    def test_cambio_de_precios_actuales_no_reemplaza_snapshots(self):
        item = self.agregar(cantidad=3)
        snapshots = (
            item.precio_unitario_snapshot,
            item.precio_desde_3_snapshot,
            item.precio_desde_20_snapshot,
        )
        Producto.objects.filter(pk=self.producto.pk).update(
            precio_unitario=Decimal("9000.00"),
            precio_desde_3=Decimal("8000.00"),
            precio_desde_20=Decimal("7000.00"),
        )

        respuesta = self.client.get(reverse("cart:detalle"))

        item.refresh_from_db()
        self.assertEqual(
            (
                item.precio_unitario_snapshot,
                item.precio_desde_3_snapshot,
                item.precio_desde_20_snapshot,
            ),
            snapshots,
        )
        self.assertContains(respuesta, "$ 4.500,00")
        self.assertNotContains(respuesta, "$ 8.000,00")

    def test_incrementos_conservan_snapshots_y_eliminar_reagregar_los_renueva(self):
        item = self.agregar()
        snapshots = (
            item.precio_unitario_snapshot,
            item.precio_desde_3_snapshot,
            item.precio_desde_20_snapshot,
        )
        Producto.objects.filter(pk=self.producto.pk).update(
            precio_unitario=Decimal("6100.00"),
            precio_desde_3=Decimal("5600.00"),
            precio_desde_20=Decimal("5100.00"),
        )

        self.client.post(
            reverse("cart:agregar_producto", args=(self.producto.pk,))
        )
        item.refresh_from_db()
        self.assertEqual(
            (
                item.precio_unitario_snapshot,
                item.precio_desde_3_snapshot,
                item.precio_desde_20_snapshot,
            ),
            snapshots,
        )

        self.client.post(reverse("cart:eliminar_item", args=(item.pk,)))
        self.client.post(
            reverse("cart:agregar_producto", args=(self.producto.pk,))
        )
        nuevo = ItemCarrito.objects.get()
        self.assertEqual(nuevo.precio_unitario_snapshot, Decimal("6100.00"))

    def test_producto_inactivo_permanece_sin_actualizacion_y_puede_eliminarse(self):
        item = self.agregar(cantidad=2)
        self.producto.activo = False
        self.producto.save(update_fields=("activo",))

        respuesta = self.client.get(reverse("cart:detalle"))

        self.assertContains(respuesta, self.producto.nombre)
        self.assertContains(respuesta, "Producto no disponible")
        self.assertContains(respuesta, "$ 5.000,00")
        self.assertContains(
            respuesta,
            "<div><dt>Cantidad actual</dt><dd>2</dd></div>",
            html=True,
        )
        self.assertNotContains(
            respuesta,
            reverse("cart:establecer_cantidad", args=(item.pk,)),
        )
        self.assertContains(
            respuesta,
            reverse("cart:eliminar_item", args=(item.pk,)),
        )

        respuesta_manipulada = self.client.post(
            reverse("cart:establecer_cantidad", args=(item.pk,)),
            {"cantidad": 1},
            follow=True,
        )
        self.assertContains(
            respuesta_manipulada,
            "El Producto no está disponible.",
        )
        item.refresh_from_db()
        self.assertEqual(item.cantidad, 2)

        self.client.post(reverse("cart:eliminar_item", args=(item.pk,)))
        self.assertFalse(ItemCarrito.objects.exists())

    def test_stock_cero_mantiene_linea_y_snapshots_sin_actualizacion(self):
        item = self.agregar(cantidad=2)
        snapshots = item.precio_unitario_snapshot
        inventario = Inventario.objects.get(producto=self.producto)
        registrar_venta_presencial(
            inventario_id=inventario.pk,
            cantidad=inventario.cantidad_disponible,
        )

        respuesta = self.client.get(reverse("cart:detalle"))

        item.refresh_from_db()
        self.assertEqual(item.precio_unitario_snapshot, snapshots)
        self.assertContains(respuesta, "Sin Stock")
        self.assertContains(
            respuesta,
            "<div><dt>Cantidad actual</dt><dd>2</dd></div>",
            html=True,
        )
        self.assertNotContains(
            respuesta,
            reverse("cart:establecer_cantidad", args=(item.pk,)),
        )

    def test_stock_reducido_muestra_aviso_sin_exponer_cantidad(self):
        self.agregar(cantidad=5)
        registrar_venta_presencial(
            inventario_id=self.producto.inventario.pk,
            cantidad=28,
        )

        respuesta = self.client.get(reverse("cart:detalle"))

        self.assertContains(respuesta, "La disponibilidad cambió")
        self.assertNotContains(respuesta, "2 unidades disponibles")


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class OperacionesPublicasTests(TestCase):
    def setUp(self):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        self.session_key = sesion.session_key
        self.producto = crear_producto_con_stock(stock=10)
        self.item = agregar_producto(
            session_key=self.session_key,
            producto_id=self.producto.pk,
            cantidad=2,
        )

    def test_establecer_cantidad_valida_y_misma_cantidad_es_neutral(self):
        actividad = self.item.carrito.ultima_actividad

        respuesta = self.client.post(
            reverse("cart:establecer_cantidad", args=(self.item.pk,)),
            {"cantidad": 2},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.item.carrito.refresh_from_db()
        self.assertEqual(self.item.carrito.ultima_actividad, actividad)

        respuesta = self.client.get(reverse("cart:detalle"))
        self.assertContains(respuesta, "Cantidad del Carrito verificada.")
        self.assertNotContains(respuesta, "Cantidad del Carrito actualizada.")

        self.client.post(
            reverse("cart:establecer_cantidad", args=(self.item.pk,)),
            {"cantidad": 4},
        )
        self.item.refresh_from_db()
        self.item.carrito.refresh_from_db()
        self.assertEqual(self.item.cantidad, 4)
        self.assertGreater(self.item.carrito.ultima_actividad, actividad)

    def test_cantidades_invalidas_no_eliminan_item(self):
        for valor in (0, -1, "texto", "1.5", "2147483648"):
            with self.subTest(valor=valor):
                respuesta = self.client.post(
                    reverse("cart:establecer_cantidad", args=(self.item.pk,)),
                    {"cantidad": valor},
                )
                self.assertEqual(respuesta.status_code, 302)
                self.item.refresh_from_db()
                self.assertEqual(self.item.cantidad, 2)
                self.assertTrue(ItemCarrito.objects.filter(pk=self.item.pk).exists())

    def test_stock_insuficiente_muestra_error_y_no_modifica(self):
        respuesta = self.client.post(
            reverse("cart:establecer_cantidad", args=(self.item.pk,)),
            {"cantidad": 11},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.cantidad, 2)
        respuesta = self.client.get(reverse("cart:detalle"))
        self.assertContains(
            respuesta,
            "La cantidad solicitada no se encuentra disponible.",
        )
        self.assertNotContains(respuesta, "11 unidades")

    def test_item_ajeno_e_inexistente_tienen_respuesta_publica_equivalente(self):
        producto_ajeno = crear_producto_con_stock(nombre="Producto ajeno", stock=5)
        otro = crear_item_directo(producto=producto_ajeno, cantidad=1)
        mensajes = []

        for item_id in (otro.pk, 999999):
            respuesta = self.client.post(
                reverse("cart:establecer_cantidad", args=(item_id,)),
                {"cantidad": 1},
                follow=True,
            )
            mensajes.append(
                "El artículo no está disponible en tu Carrito."
                in respuesta.content.decode()
            )

        self.assertEqual(mensajes, [True, True])
        otro.refresh_from_db()
        self.assertEqual(otro.cantidad, 1)

    def test_item_ajeno_no_puede_eliminarse(self):
        producto_ajeno = crear_producto_con_stock(nombre="Producto ajeno", stock=5)
        otro = crear_item_directo(producto=producto_ajeno, cantidad=1)

        respuesta = self.client.post(
            reverse("cart:eliminar_item", args=(otro.pk,)),
            follow=True,
        )

        self.assertContains(
            respuesta,
            "El artículo no está disponible en tu Carrito.",
        )
        self.assertTrue(ItemCarrito.objects.filter(pk=otro.pk).exists())

    def test_eliminar_y_vaciar_utilizan_prg_y_son_idempotentes(self):
        respuesta = self.client.post(
            reverse("cart:eliminar_item", args=(self.item.pk,))
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(ItemCarrito.objects.filter(pk=self.item.pk).exists())

        respuesta = self.client.post(reverse("cart:vaciar"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(Carrito.objects.filter(session_key=self.session_key).exists())
        self.assertFalse(ItemCarrito.objects.exists())
        self.assertEqual(self.client.post(reverse("cart:vaciar")).status_code, 302)
        self.assertFalse(ItemCarrito.objects.exists())

    def test_mensajes_se_consumen_y_permanecen_autoescapados(self):
        with patch(
            "cart.views.agregar_producto_servicio",
            side_effect=ProductoNoDisponible("<script>interno</script>"),
        ):
            self.client.post(
                reverse("cart:agregar_producto", args=(self.producto.pk,))
            )

        primera = self.client.get(reverse("cart:detalle"))
        segunda = self.client.get(reverse("cart:detalle"))
        self.assertContains(primera, "El Producto no está disponible.")
        self.assertContains(primera, 'role="alert"')
        self.assertNotContains(primera, "<script>interno</script>")
        self.assertNotContains(segunda, "El Producto no está disponible.")

    def test_operaciones_no_modifican_inventario_ni_crean_movimientos(self):
        inventario = Inventario.objects.get(producto=self.producto)
        inventario.refresh_from_db()
        stock = inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()

        self.client.post(
            reverse("cart:establecer_cantidad", args=(self.item.pk,)),
            {"cantidad": 4},
        )
        self.client.post(reverse("cart:eliminar_item", args=(self.item.pk,)))
        self.client.post(reverse("cart:vaciar"))

        inventario.refresh_from_db()
        self.assertEqual(inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS, DEBUG=False)
class ErroresEstructuralesTests(TestCase):
    def setUp(self):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        self.session_key = sesion.session_key

    def test_producto_sin_inventario_en_linea_produce_error_sanitizado(self):
        producto = Producto.objects.create(
            **datos_producto(
                nombre="Sin inventario web",
                imagen=imagen_de_prueba("sin-inventario-web.gif"),
            )
        )
        crear_item_directo(
            carrito=Carrito.objects.create(session_key=self.session_key),
            producto=producto,
        )
        cliente = Client(raise_request_exception=False)
        cliente.cookies = self.client.cookies

        respuesta = cliente.get(reverse("cart:detalle"))

        self.assertEqual(respuesta.status_code, 500)
        self.assertNotContains(
            respuesta,
            "Sin inventario web",
            status_code=500,
        )

    def test_producto_sin_inventario_al_agregar_produce_error_sanitizado(self):
        producto = Producto.objects.create(
            **datos_producto(
                nombre="Sin inventario agregar",
                imagen=imagen_de_prueba("sin-inventario-agregar.gif"),
            )
        )
        cliente = Client(raise_request_exception=False)

        respuesta = cliente.post(
            reverse("cart:agregar_producto", args=(producto.pk,))
        )

        self.assertEqual(respuesta.status_code, 500)
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(ItemCarrito.objects.exists())

    def test_producto_sin_inventario_al_establecer_produce_error_sanitizado(self):
        producto = Producto.objects.create(
            **datos_producto(
                nombre="Sin inventario establecer",
                imagen=imagen_de_prueba("sin-inventario-establecer.gif"),
            )
        )
        item = crear_item_directo(
            carrito=Carrito.objects.create(session_key=self.session_key),
            producto=producto,
        )
        cliente = Client(raise_request_exception=False)
        cliente.cookies = self.client.cookies

        respuesta = cliente.post(
            reverse("cart:establecer_cantidad", args=(item.pk,)),
            {"cantidad": 2},
        )

        item.refresh_from_db()
        self.assertEqual(respuesta.status_code, 500)
        self.assertEqual(item.cantidad, 1)

    def test_pricing_corrupto_no_se_presenta_como_carrito_valido(self):
        cliente = Client(raise_request_exception=False)
        cliente.cookies = self.client.cookies

        with patch(
            "cart.context_processors.obtener_resumen_carrito",
            side_effect=EstadoPrecioCarritoInvalido,
        ):
            respuesta = cliente.get(reverse("cart:detalle"))

        self.assertEqual(respuesta.status_code, 500)
        self.assertNotContains(respuesta, "Resumen", status_code=500)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ExpiracionYConsultasTests(TestCase):
    def crear_sesion_y_producto(self):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()
        return sesion.session_key, crear_producto_con_stock(stock=30)

    def test_get_expirado_elimina_carrito_sin_renovar_ni_afectar_stock(self):
        session_key, producto = self.crear_sesion_y_producto()
        item = agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=2,
        )
        inventario = Inventario.objects.get(producto=producto)
        stock = inventario.cantidad_disponible
        movimientos = MovimientoInventario.objects.count()
        Carrito.objects.filter(pk=item.carrito_id).update(
            ultima_actividad=timezone.now() - DURACION_CARRITO
        )

        respuesta = self.client.get(reverse("cart:detalle"))

        inventario.refresh_from_db()
        self.assertContains(respuesta, "Tu Carrito está vacío.")
        self.assertFalse(Carrito.objects.filter(pk=item.carrito_id).exists())
        self.assertFalse(ItemCarrito.objects.filter(pk=item.pk).exists())
        self.assertEqual(inventario.cantidad_disponible, stock)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_lectura_vigente_no_renueva_actividad(self):
        session_key, producto = self.crear_sesion_y_producto()
        item = agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=1,
        )
        actividad = timezone.now() - timedelta(hours=1)
        Carrito.objects.filter(pk=item.carrito_id).update(
            ultima_actividad=actividad
        )

        self.client.get(reverse("cart:detalle"))

        item.carrito.refresh_from_db()
        self.assertEqual(item.carrito.ultima_actividad, actividad)

    def test_operacion_obsoleta_no_recupera_item_expirado(self):
        session_key, producto = self.crear_sesion_y_producto()
        item = agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=1,
        )
        Carrito.objects.filter(pk=item.carrito_id).update(
            ultima_actividad=timezone.now() - DURACION_CARRITO
        )

        respuesta = self.client.post(
            reverse("cart:establecer_cantidad", args=(item.pk,)),
            {"cantidad": 2},
            follow=True,
        )

        self.assertContains(
            respuesta,
            "El artículo no está disponible en tu Carrito.",
        )
        self.assertContains(respuesta, "Tu Carrito está vacío.")
        self.assertFalse(ItemCarrito.objects.filter(pk=item.pk).exists())

    def test_agregar_despues_de_expirar_toma_snapshots_actuales(self):
        session_key, producto = self.crear_sesion_y_producto()
        anterior = agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=1,
        )
        Carrito.objects.filter(pk=anterior.carrito_id).update(
            ultima_actividad=timezone.now() - DURACION_CARRITO
        )
        Producto.objects.filter(pk=producto.pk).update(
            precio_unitario=Decimal("6200.00"),
            precio_desde_3=Decimal("5700.00"),
            precio_desde_20=Decimal("5200.00"),
        )

        self.client.post(
            reverse("cart:agregar_producto", args=(producto.pk,))
        )

        nuevo = ItemCarrito.objects.get()
        self.assertNotEqual(nuevo.pk, anterior.pk)
        self.assertEqual(nuevo.precio_unitario_snapshot, Decimal("6200.00"))

    def test_consultas_get_son_constantes(self):
        with CaptureQueriesContext(connection) as consultas_vacio:
            self.client.get(reverse("cart:detalle"))
        self.assertEqual(len(consultas_vacio), 0)

        session_key, producto = self.crear_sesion_y_producto()
        agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=2,
        )
        with CaptureQueriesContext(connection) as consultas_una:
            self.client.get(reverse("cart:detalle"))

        for nombre in ("Baldo", "Playadito"):
            otro = crear_producto_con_stock(nombre=nombre, stock=30)
            agregar_producto(
                session_key=session_key,
                producto_id=otro.pk,
                cantidad=1,
            )
        with CaptureQueriesContext(connection) as consultas_tres:
            self.client.get(reverse("cart:detalle"))

        self.assertEqual(len(consultas_una), len(consultas_tres))
        self.assertEqual(len(consultas_una), 3)

    def test_carrito_vacio_con_sesion_utiliza_una_consulta(self):
        sesion = self.client.session
        sesion["iniciada"] = True
        sesion.save()

        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(reverse("cart:detalle"))

        self.assertContains(respuesta, "Tu Carrito está vacío.")
        self.assertEqual(len(consultas), 1)

    def test_catalogo_con_carrito_mantiene_consultas_constantes(self):
        session_key, producto = self.crear_sesion_y_producto()
        agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=1,
        )
        with CaptureQueriesContext(connection) as consultas_una:
            self.client.get(reverse("catalog:producto_list"))

        for nombre in ("Baldo", "Playadito"):
            otro = crear_producto_con_stock(nombre=nombre, stock=30)
            agregar_producto(
                session_key=session_key,
                producto_id=otro.pk,
                cantidad=1,
            )
        with CaptureQueriesContext(connection) as consultas_tres:
            self.client.get(reverse("catalog:producto_list"))

        self.assertEqual(len(consultas_una), len(consultas_tres))
        self.assertEqual(len(consultas_una), 3)


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class PresupuestosPostTests(TestCase):
    def test_presupuestos_post_medidos(self):
        producto = crear_producto_con_stock(stock=30)
        agregar_url = reverse("cart:agregar_producto", args=(producto.pk,))
        conteos = {}

        with CaptureQueriesContext(connection) as consultas:
            self.client.post(agregar_url)
        conteos["primer_agregado"] = len(consultas)

        item = ItemCarrito.objects.get()
        with CaptureQueriesContext(connection) as consultas:
            self.client.post(agregar_url)
        conteos["agregado_sucesivo"] = len(consultas)

        with CaptureQueriesContext(connection) as consultas:
            self.client.post(
                reverse("cart:establecer_cantidad", args=(item.pk,)),
                {"cantidad": 3},
            )
        conteos["establecer"] = len(consultas)

        with CaptureQueriesContext(connection) as consultas:
            self.client.post(
                reverse("cart:eliminar_item", args=(item.pk,))
            )
        conteos["eliminar"] = len(consultas)

        session_key = self.client.session.session_key
        agregar_producto(
            session_key=session_key,
            producto_id=producto.pk,
            cantidad=1,
        )
        with CaptureQueriesContext(connection) as consultas:
            self.client.post(reverse("cart:vaciar"))
        conteos["vaciar"] = len(consultas)

        limites = {
            "primer_agregado": 38,
            "agregado_sucesivo": 28,
            "establecer": 10,
            "eliminar": 8,
            "vaciar": 7,
        }
        for operacion, cantidad in conteos.items():
            with self.subTest(operacion=operacion):
                self.assertLessEqual(cantidad, limites[operacion])


@override_settings(MEDIA_ROOT=MEDIA_ROOT_PRUEBAS)
class ProteccionCsrfTests(TestCase):
    def test_los_cuatro_endpoints_exigen_y_aceptan_csrf(self):
        producto = crear_producto_con_stock(stock=10)
        cliente = Client(enforce_csrf_checks=True)
        agregar_url = reverse("cart:agregar_producto", args=(producto.pk,))

        self.assertEqual(cliente.post(agregar_url).status_code, 403)
        self.assertFalse(Carrito.objects.exists())

        cliente.get(reverse("catalog:producto_list"))
        token = cliente.cookies["csrftoken"].value
        self.assertEqual(
            cliente.post(
                agregar_url,
                {"csrfmiddlewaretoken": token},
            ).status_code,
            302,
        )
        item = ItemCarrito.objects.get()

        endpoints_sin_token = (
            (reverse("cart:establecer_cantidad", args=(item.pk,)), {"cantidad": 2}),
            (reverse("cart:eliminar_item", args=(item.pk,)), {}),
            (reverse("cart:vaciar"), {}),
        )
        for url, datos in endpoints_sin_token:
            with self.subTest(url=url, csrf=False):
                self.assertEqual(cliente.post(url, datos).status_code, 403)

        self.assertEqual(
            cliente.post(
                reverse("cart:establecer_cantidad", args=(item.pk,)),
                {"cantidad": 2, "csrfmiddlewaretoken": token},
            ).status_code,
            302,
        )
        self.assertEqual(
            cliente.post(
                reverse("cart:eliminar_item", args=(item.pk,)),
                {"csrfmiddlewaretoken": token},
            ).status_code,
            302,
        )
        cliente.post(
            agregar_url,
            {"csrfmiddlewaretoken": token},
        )
        self.assertEqual(
            cliente.post(
                reverse("cart:vaciar"),
                {"csrfmiddlewaretoken": token},
            ).status_code,
            302,
        )
