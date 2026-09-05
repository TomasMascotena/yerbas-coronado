import os
from unittest.mock import patch

from django.test import SimpleTestCase

from config.settings import env_bool, env_list


class EnvironmentSettingsTests(SimpleTestCase):
    def test_env_bool_acepta_unicamente_valores_verdaderos_explicitos(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"TEST_BOOLEAN": value}
            ):
                self.assertTrue(env_bool("TEST_BOOLEAN"))

        for value in ("0", "false", "no", "off", "valor-invalido"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"TEST_BOOLEAN": value}
            ):
                self.assertFalse(env_bool("TEST_BOOLEAN"))

    def test_env_bool_utiliza_default_si_variable_ausente(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_bool("TEST_BOOLEAN", default=True))
            self.assertFalse(env_bool("TEST_BOOLEAN"))

    def test_env_list_limpia_espacios_y_descarta_elementos_vacios(self):
        with patch.dict(
            os.environ,
            {"TEST_LIST": " tienda.example.com, ,localhost ,"},
        ):
            self.assertEqual(
                env_list("TEST_LIST"),
                ["tienda.example.com", "localhost"],
            )
