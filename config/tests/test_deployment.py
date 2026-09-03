import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from config.tests.test_environment import production_environment


BASE_DIR = Path(__file__).resolve().parents[2]


class DeploymentConfigurationTests(SimpleTestCase):
    def _settings_for_environment(self, environment):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "from django.conf import settings; "
                    "print(json.dumps({"
                    "'middleware': settings.MIDDLEWARE, "
                    "'staticfiles_backend': "
                    "settings.STORAGES['staticfiles']['BACKEND']"
                    "}))"
                ),
            ],
            cwd=BASE_DIR,
            env=production_environment(DJANGO_ENV=environment),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), result.stderr

    def test_contenedor_separa_build_inicio_y_readiness(self):
        dockerfile = (BASE_DIR / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("python manage.py collectstatic --noinput", dockerfile)
        self.assertIn("DJANGO_ENV=production", dockerfile)
        self.assertIn("config.wsgi:application", dockerfile)
        self.assertIn("/health/ready/", dockerfile)
        self.assertIn("USER django", dockerfile)
        self.assertNotIn("manage.py migrate", dockerfile)

    def test_gunicorn_tiene_defaults_prudentes_y_es_configurable(self):
        variables = {
            "PORT": "9123",
            "GUNICORN_WORKERS": "2",
            "GUNICORN_THREADS": "3",
            "GUNICORN_TIMEOUT": "75",
            "GUNICORN_GRACEFUL_TIMEOUT": "25",
        }
        with patch.dict(os.environ, variables, clear=False):
            configuration = runpy.run_path(BASE_DIR / "gunicorn.conf.py")

        self.assertEqual(configuration["bind"], "0.0.0.0:9123")
        self.assertEqual(configuration["workers"], 2)
        self.assertEqual(configuration["threads"], 3)
        self.assertEqual(configuration["timeout"], 75)
        self.assertEqual(configuration["graceful_timeout"], 25)
        self.assertEqual(configuration["accesslog"], "-")
        self.assertEqual(configuration["errorlog"], "-")

    def test_gunicorn_rechaza_concurrencia_invalida(self):
        with patch.dict(os.environ, {"GUNICORN_WORKERS": "0"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "GUNICORN_WORKERS"):
                runpy.run_path(BASE_DIR / "gunicorn.conf.py")

    def test_whitenoise_esta_ausente_fuera_de_produccion(self):
        for environment in ("development", "test"):
            with self.subTest(environment=environment):
                configuration, stderr = self._settings_for_environment(environment)
                self.assertNotIn(
                    "whitenoise.middleware.WhiteNoiseMiddleware",
                    configuration["middleware"],
                )
                self.assertEqual(
                    configuration["middleware"][0],
                    "django.middleware.security.SecurityMiddleware",
                )
                self.assertEqual(
                    configuration["staticfiles_backend"],
                    "django.contrib.staticfiles.storage.StaticFilesStorage",
                )
                self.assertNotIn("No directory at", stderr)

    def test_whitenoise_sirve_estaticos_versionados_en_produccion(self):
        configuration, _stderr = self._settings_for_environment("production")

        self.assertEqual(
            configuration["middleware"][:2],
            [
                "django.middleware.security.SecurityMiddleware",
                "whitenoise.middleware.WhiteNoiseMiddleware",
            ],
        )
        self.assertEqual(
            configuration["staticfiles_backend"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
        self.assertNotEqual(settings.STATIC_ROOT, settings.MEDIA_ROOT)
