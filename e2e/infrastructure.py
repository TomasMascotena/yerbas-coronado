import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase

from e2e.base import BrowserE2ETestCase


class BrowserE2ELifecycleTests(SimpleTestCase):
    def _nueva_clase_e2e(self):
        return type("CasoE2EParcial", (BrowserE2ETestCase,), {})

    def _media_root_temporal(self):
        ruta = tempfile.mkdtemp(prefix="yerbas-e2e-lifecycle-test-")
        self.addCleanup(shutil.rmtree, ruta, ignore_errors=True)
        return ruta

    def test_fallo_de_launch_libera_recursos_y_propaga_error_original(self):
        caso_e2e = self._nueva_clase_e2e()
        media_root_anterior = settings.MEDIA_ROOT
        media_root_temporal = self._media_root_temporal()
        error_original = RuntimeError("falló chromium.launch")
        playwright = Mock()
        playwright.chromium.launch.side_effect = error_original
        administrador_playwright = Mock()
        administrador_playwright.start.return_value = playwright

        with (
            patch.dict(
                os.environ,
                {"DJANGO_ALLOW_ASYNC_UNSAFE": "valor-anterior"},
            ),
            patch("e2e.base.tempfile.mkdtemp", return_value=media_root_temporal),
            patch(
                "e2e.base.sync_playwright",
                return_value=administrador_playwright,
            ),
            patch.object(
                StaticLiveServerTestCase, "setUpClass"
            ) as django_setup,
            patch.object(
                StaticLiveServerTestCase, "tearDownClass"
            ) as django_teardown,
        ):
            with self.assertRaises(RuntimeError) as captura:
                caso_e2e.setUpClass()

            self.assertIs(captura.exception, error_original)
            self.assertEqual(
                os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"], "valor-anterior"
            )
            self.assertEqual(settings.MEDIA_ROOT, media_root_anterior)

        django_setup.assert_called_once_with()
        django_teardown.assert_called_once_with()
        playwright.stop.assert_called_once_with()
        self.assertFalse(Path(media_root_temporal).exists())
        self.assertIsNone(caso_e2e.browser)
        self.assertIsNone(caso_e2e._playwright)

    def test_fallo_de_browser_close_no_interrumpe_limpieza(self):
        caso_e2e = self._nueva_clase_e2e()
        media_root_anterior = settings.MEDIA_ROOT
        media_root_temporal = self._media_root_temporal()
        error_original = RuntimeError("falló browser.close")
        browser = Mock()
        browser.close.side_effect = error_original
        playwright = Mock()
        playwright.chromium.launch.return_value = browser
        administrador_playwright = Mock()
        administrador_playwright.start.return_value = playwright

        with (
            patch.dict(
                os.environ,
                {"DJANGO_ALLOW_ASYNC_UNSAFE": "valor-anterior"},
            ),
            patch("e2e.base.tempfile.mkdtemp", return_value=media_root_temporal),
            patch(
                "e2e.base.sync_playwright",
                return_value=administrador_playwright,
            ),
            patch.object(StaticLiveServerTestCase, "setUpClass"),
            patch.object(
                StaticLiveServerTestCase, "tearDownClass"
            ) as django_teardown,
        ):
            caso_e2e.setUpClass()
            self.assertEqual(
                os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"], "true"
            )
            self.assertEqual(settings.MEDIA_ROOT, media_root_temporal)

            with self.assertRaises(RuntimeError) as captura:
                caso_e2e.tearDownClass()

            self.assertIs(captura.exception, error_original)
            self.assertEqual(
                os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"], "valor-anterior"
            )
            self.assertEqual(settings.MEDIA_ROOT, media_root_anterior)

        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()
        django_teardown.assert_called_once_with()
        self.assertFalse(Path(media_root_temporal).exists())
        self.assertIsNone(caso_e2e.browser)
        self.assertIsNone(caso_e2e._playwright)

    def test_limpieza_parcial_es_idempotente(self):
        caso_e2e = self._nueva_clase_e2e()
        playwright = Mock()
        caso_e2e._playwright = playwright

        caso_e2e._liberar_recursos()
        caso_e2e._liberar_recursos()

        playwright.stop.assert_called_once_with()
