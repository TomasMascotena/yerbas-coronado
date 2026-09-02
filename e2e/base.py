import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit, urlunsplit

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright


BASE_DIR = Path(__file__).resolve().parents[1]
ARTEFACTOS_DIR = BASE_DIR / ".e2e-artifacts"
VIEWPORT_ESCRITORIO = {"width": 1280, "height": 720}
VIEWPORT_MOVIL = {"width": 390, "height": 844}


def _headless_desde_entorno():
    valor = os.environ.get("E2E_HEADLESS", "true").strip().lower()
    if valor not in {"true", "false"}:
        raise RuntimeError("E2E_HEADLESS debe ser 'true' o 'false'.")
    return valor == "true"


class BrowserE2ETestCase(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        cls._django_allow_async_unsafe = os.environ.get(
            "DJANGO_ALLOW_ASYNC_UNSAFE"
        )
        # La API sync de Playwright mantiene un loop interno aunque estos tests
        # y el ORM se ejecuten secuencialmente en este hilo.
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        cls._media_root = tempfile.mkdtemp(prefix="yerbas-e2e-media-")
        cls._settings = override_settings(
            MEDIA_ROOT=cls._media_root,
            WHATSAPP_BUSINESS_NUMBER="5491112345678",
        )
        cls._settings.enable()
        super().setUpClass()
        try:
            cls._playwright = sync_playwright().start()
            cls.browser = cls._playwright.chromium.launch(
                headless=_headless_desde_entorno()
            )
        except Exception:
            super().tearDownClass()
            cls._settings.disable()
            shutil.rmtree(cls._media_root, ignore_errors=True)
            cls._restaurar_async_unsafe()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._playwright.stop()
        finally:
            super().tearDownClass()
            cls._settings.disable()
            shutil.rmtree(cls._media_root, ignore_errors=True)
            cls._restaurar_async_unsafe()

    @classmethod
    def _restaurar_async_unsafe(cls):
        if cls._django_allow_async_unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = (
                cls._django_allow_async_unsafe
            )

    def setUp(self):
        self.console_errors = []
        self.context = self.nuevo_contexto()
        self.page = self.context.new_page()
        self.page.on(
            "console",
            lambda mensaje: self.console_errors.append(mensaje.text)
            if mensaje.type == "error"
            else None,
        )

    def tearDown(self):
        try:
            if self._fallo_actual():
                self._guardar_artefactos_fallo()
        finally:
            self.context.close()

    def nuevo_contexto(self, *, viewport=None, java_script_enabled=True):
        return self.browser.new_context(
            viewport=viewport or VIEWPORT_ESCRITORIO,
            locale="es-AR",
            java_script_enabled=java_script_enabled,
        )

    def url(self, nombre_url):
        return f"{self.live_server_url}{nombre_url}"

    def assert_un_h1(self, page=None):
        page = page or self.page
        expect(page.locator("h1")).to_have_count(1)

    def assert_sin_overflow_horizontal(self, page=None):
        page = page or self.page
        sin_overflow = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        self.assertTrue(sin_overflow)

    def assert_sin_errores_consola(self):
        self.assertEqual(self.console_errors, [])

    def iniciar_sesion_admin(self, page, *, usuario, password):
        page.goto(self.url("/admin/login/"))
        page.get_by_label("Nombre de usuario").fill(usuario)
        page.get_by_label("Contraseña").fill(password)
        page.locator('#login-form input[type="submit"]').click()
        expect(page).to_have_url(f"{self.live_server_url}/admin/")

    def _fallo_actual(self):
        resultado = self._outcome.result
        incidencias = resultado.failures + resultado.errors
        return any(test is self for test, _ in incidencias)

    def _guardar_artefactos_fallo(self):
        ARTEFACTOS_DIR.mkdir(exist_ok=True)
        nombre = f"{self.__class__.__name__}.{self._testMethodName}"
        ruta_imagen = ARTEFACTOS_DIR / f"{nombre}.png"
        ruta_contexto = ARTEFACTOS_DIR / f"{nombre}.txt"
        try:
            self.page.screenshot(path=str(ruta_imagen), full_page=True)
        except Exception:
            pass
        url = urlsplit(self.page.url)
        url_sanitizada = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
        ruta_contexto.write_text(
            f"prueba={nombre}\nurl={url_sanitizada}\n",
            encoding="utf-8",
        )
