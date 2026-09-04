import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.environment import (
    DEFAULT_DEVELOPMENT_HOSTS,
    build_database_configuration,
    parse_allowed_hosts,
    parse_bool,
    parse_csrf_trusted_origins,
    parse_debug,
    parse_environment,
    parse_hsts_seconds,
    parse_log_level,
    parse_secret_key,
)


BASE_DIR = Path(__file__).resolve().parents[2]
CONFIGURATION_VARIABLES = {
    "DJANGO_ENV",
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "DJANGO_LOG_LEVEL",
    "DJANGO_SECURE_HSTS_SECONDS",
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    "DJANGO_SECURE_HSTS_PRELOAD",
    "DJANGO_TRUST_X_FORWARDED_PROTO",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_CONN_MAX_AGE",
    "POSTGRES_SSLMODE",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGHOST",
    "PGPORT",
}
SAFE_PRODUCTION_SECRET = (
    "test-only-secret-K7!yQ2#vN9@xR4-pL8_cT6*mW3-zF5_hJ1+uD0=sA7%gB9"
)


def production_environment(**overrides):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in CONFIGURATION_VARIABLES
    }
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_ENV": "production",
            "DJANGO_SECRET_KEY": SAFE_PRODUCTION_SECRET,
            "DJANGO_ALLOWED_HOSTS": "shop.example.test",
            "POSTGRES_DB": "production_check",
            "POSTGRES_USER": "production_check",
            "POSTGRES_PASSWORD": "not-a-real-password",
            "POSTGRES_HOST": "database.example.test",
            "POSTGRES_PORT": "5432",
        }
    )
    environment.update(overrides)
    return environment


class EnvironmentParserTests(SimpleTestCase):
    def test_desarrollo_conserva_configuracion_local_utilizable(self):
        environment = {}

        self.assertEqual(parse_environment(environment), "development")
        self.assertTrue(parse_debug(environment, "development"))
        self.assertFalse(parse_debug(environment, "test"))
        self.assertEqual(
            parse_allowed_hosts(environment, "development"),
            DEFAULT_DEVELOPMENT_HOSTS,
        )
        self.assertEqual(parse_hsts_seconds(environment, "development"), 0)

    def test_produccion_fuerza_debug_false_y_rechaza_true(self):
        self.assertFalse(parse_debug({}, "production"))
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "DJANGO_DEBUG no puede ser true en producción.",
        ):
            parse_debug({"DJANGO_DEBUG": "true"}, "production")

    def test_parser_booleano_es_estricto(self):
        self.assertTrue(parse_bool({"FLAG": " true "}, "FLAG", default=False))
        self.assertFalse(parse_bool({"FLAG": "FALSE"}, "FLAG", default=True))
        for invalid_value in ("", "1", "0", "yes", "on"):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(ImproperlyConfigured):
                    parse_bool({"FLAG": invalid_value}, "FLAG", default=False)

    def test_entorno_desconocido_es_rechazado(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "DJANGO_ENV debe ser uno de los siguientes valores",
        ):
            parse_environment({"DJANGO_ENV": "staging"})

    def test_secret_key_ausente_vacia_o_insegura_es_rechazada(self):
        for value in (
            None,
            "",
            "   ",
            "django-insecure-" + "x" * 60,
            "short",
            "x" * 60,
        ):
            with self.subTest(value=value):
                environment = {} if value is None else {"DJANGO_SECRET_KEY": value}
                with self.assertRaises(ImproperlyConfigured):
                    parse_secret_key(environment, "production")

    def test_secret_key_segura_de_produccion_es_aceptada(self):
        self.assertEqual(
            parse_secret_key(
                {"DJANGO_SECRET_KEY": SAFE_PRODUCTION_SECRET}, "production"
            ),
            SAFE_PRODUCTION_SECRET,
        )

    def test_allowed_hosts_se_normaliza(self):
        self.assertEqual(
            parse_allowed_hosts(
                {"DJANGO_ALLOWED_HOSTS": " shop.example.test,api.example.test "},
                "production",
            ),
            ["shop.example.test", "api.example.test"],
        )

    def test_allowed_hosts_rechaza_vacio_wildcard_y_entradas_invalidas(self):
        for value in (
            None,
            "",
            "*",
            "example.test,,api.example.test",
            "https://x.test",
        ):
            with self.subTest(value=value):
                environment = {} if value is None else {"DJANGO_ALLOWED_HOSTS": value}
                with self.assertRaises(ImproperlyConfigured):
                    parse_allowed_hosts(environment, "production")

    def test_origenes_csrf_validos_se_normalizan(self):
        self.assertEqual(
            parse_csrf_trusted_origins(
                {
                    "DJANGO_CSRF_TRUSTED_ORIGINS": (
                        " https://shop.example.test/,https://admin.example.test:8443 "
                    )
                },
                "production",
            ),
            ["https://shop.example.test", "https://admin.example.test:8443"],
        )

    def test_origenes_csrf_invalidos_son_rechazados(self):
        for value in (
            "http://shop.example.test",
            "shop.example.test",
            "https://shop.example.test/path",
            "https://user:password@shop.example.test",
            "https://shop.example.test?query=value",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    parse_csrf_trusted_origins(
                        {"DJANGO_CSRF_TRUSTED_ORIGINS": value}, "production"
                    )

    def test_postgresql_es_obligatorio_y_los_errores_no_exponen_credenciales(self):
        sensitive_password = "sensitive-database-password"
        environment = {
            "POSTGRES_USER": "user",
            "POSTGRES_PASSWORD": sensitive_password,
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
        }

        with self.assertRaises(ImproperlyConfigured) as context:
            build_database_configuration(environment)

        self.assertIn("POSTGRES_DB", str(context.exception))
        self.assertNotIn(sensitive_password, str(context.exception))

    def test_postgresql_es_el_unico_backend_configurado(self):
        configuration = build_database_configuration(
            {
                "POSTGRES_DB": "database",
                "POSTGRES_USER": "user",
                "POSTGRES_PASSWORD": "password",
                "POSTGRES_HOST": "localhost",
                "POSTGRES_PORT": "5432",
            }
        )

        self.assertEqual(configuration["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(configuration["PORT"], 5432)

    def test_postgresql_acepta_variables_nativas_de_railway(self):
        configuration = build_database_configuration(
            {
                "PGDATABASE": "railway",
                "PGUSER": "postgres",
                "PGPASSWORD": "password",
                "PGHOST": "postgres.railway.internal",
                "PGPORT": "5432",
                "POSTGRES_SSLMODE": "require",
            },
            environment="production",
        )

        self.assertEqual(configuration["NAME"], "railway")
        self.assertEqual(configuration["HOST"], "postgres.railway.internal")
        self.assertEqual(configuration["CONN_MAX_AGE"], 60)
        self.assertTrue(configuration["CONN_HEALTH_CHECKS"])
        self.assertEqual(configuration["OPTIONS"], {"sslmode": "require"})

    def test_conexiones_persistentes_y_ssl_rechazan_valores_invalidos(self):
        base_environment = {
            "POSTGRES_DB": "database",
            "POSTGRES_USER": "user",
            "POSTGRES_PASSWORD": "password",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
        }
        for name, value in (
            ("POSTGRES_CONN_MAX_AGE", "-1"),
            ("POSTGRES_CONN_MAX_AGE", "sesenta"),
            ("POSTGRES_SSLMODE", "inseguro"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ImproperlyConfigured):
                    build_database_configuration(
                        {**base_environment, name: value},
                        environment="production",
                    )

    def test_postgresql_no_mezcla_juegos_de_variables_parciales(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "POSTGRES_PORT"):
            build_database_configuration(
                {
                    "POSTGRES_USER": "user",
                    "PGDATABASE": "railway",
                    "PGPASSWORD": "password",
                    "PGHOST": "postgres.railway.internal",
                    "PGPORT": "5432",
                }
            )

    def test_produccion_rechaza_logging_debug(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "DJANGO_LOG_LEVEL no puede ser DEBUG en producción.",
        ):
            parse_log_level({"DJANGO_LOG_LEVEL": "DEBUG"}, "production")


class SettingsIntegrationTests(SimpleTestCase):
    def run_python(self, code, *, environment=None):
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_settings_de_produccion_activan_protecciones(self):
        result = self.run_python(
            """
import json
from django.conf import settings
print(json.dumps({
    "environment": settings.ENVIRONMENT,
    "debug": settings.DEBUG,
    "ssl_redirect": settings.SECURE_SSL_REDIRECT,
    "session_secure": settings.SESSION_COOKIE_SECURE,
    "csrf_secure": settings.CSRF_COOKIE_SECURE,
    "session_httponly": settings.SESSION_COOKIE_HTTPONLY,
    "session_samesite": settings.SESSION_COOKIE_SAMESITE,
    "csrf_samesite": settings.CSRF_COOKIE_SAMESITE,
    "session_age": settings.SESSION_COOKIE_AGE,
    "content_nosniff": settings.SECURE_CONTENT_TYPE_NOSNIFF,
    "referrer_policy": settings.SECURE_REFERRER_POLICY,
    "x_frame_options": settings.X_FRAME_OPTIONS,
    "hsts_seconds": settings.SECURE_HSTS_SECONDS,
    "hsts_subdomains": settings.SECURE_HSTS_INCLUDE_SUBDOMAINS,
    "hsts_preload": settings.SECURE_HSTS_PRELOAD,
    "log_level": settings.LOGGING["root"]["level"],
}))
""",
            environment=production_environment(),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        configuration = json.loads(result.stdout)
        self.assertEqual(configuration["environment"], "production")
        self.assertFalse(configuration["debug"])
        self.assertTrue(configuration["ssl_redirect"])
        self.assertTrue(configuration["session_secure"])
        self.assertTrue(configuration["csrf_secure"])
        self.assertTrue(configuration["session_httponly"])
        self.assertEqual(configuration["session_samesite"], "Lax")
        self.assertEqual(configuration["csrf_samesite"], "Lax")
        self.assertEqual(configuration["session_age"], 60 * 60 * 24 * 14)
        self.assertTrue(configuration["content_nosniff"])
        self.assertEqual(
            configuration["referrer_policy"], "strict-origin-when-cross-origin"
        )
        self.assertEqual(configuration["x_frame_options"], "DENY")
        self.assertGreater(configuration["hsts_seconds"], 0)
        self.assertFalse(configuration["hsts_subdomains"])
        self.assertFalse(configuration["hsts_preload"])
        self.assertNotEqual(configuration["log_level"], "DEBUG")

    def test_desarrollo_no_activa_https_ni_hsts_accidentalmente(self):
        self.assertFalse(settings.SECURE_SSL_REDIRECT)
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)
        self.assertEqual(settings.SECURE_HSTS_SECONDS, 0)

    def test_proxy_ssl_solo_se_confia_por_configuracion_explicita(self):
        result = self.run_python(
            """
from django.conf import settings
print(settings.SECURE_PROXY_SSL_HEADER)
""",
            environment=production_environment(
                DJANGO_TRUST_X_FORWARDED_PROTO="true"
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HTTP_X_FORWARDED_PROTO", result.stdout)
        self.assertIn("https", result.stdout)

    def test_logging_de_seguridad_conserva_warnings_sin_duplicar_ni_filtrar_secretos(self):
        result = self.run_python(
            """
import logging
import logging.config
from django.conf import settings

logging.config.dictConfig(settings.LOGGING)
logger = logging.getLogger("django.security.csrf")
logger.warning("security-warning-marker")
logger.error("security-error-marker")
logger.info("security-info-marker")
""",
            environment=production_environment(),
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(output.count("security-warning-marker"), 1)
        self.assertEqual(output.count("security-error-marker"), 1)
        self.assertNotIn("security-info-marker", output)
        self.assertNotIn(SAFE_PRODUCTION_SECRET, output)
        self.assertNotIn("not-a-real-password", output)

    def test_static_root_esta_definido_y_separado_de_media(self):
        self.assertEqual(settings.STATIC_ROOT, BASE_DIR / "staticfiles")
        self.assertEqual(settings.MEDIA_ROOT, BASE_DIR / "media")
        self.assertNotEqual(settings.STATIC_ROOT, settings.MEDIA_ROOT)

    def test_env_real_esta_ignorada_y_env_example_es_segura(self):
        gitignore = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
        example = (BASE_DIR / ".env.example").read_text(encoding="utf-8")

        self.assertIn(".env", gitignore.splitlines())
        self.assertIn("!.env.example", gitignore.splitlines())
        self.assertIn(
            "DJANGO_SECRET_KEY=replace-with-at-least-50-random-characters",
            example,
        )
        self.assertNotIn(SAFE_PRODUCTION_SECRET, example)

    def test_check_deploy_pasa_con_produccion_segura_simulada(self):
        result = subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy"],
            cwd=BASE_DIR,
            env=production_environment(
                DJANGO_SECURE_HSTS_SECONDS="31536000",
                DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS="true",
                DJANGO_SECURE_HSTS_PRELOAD="true",
            ),
            capture_output=True,
            text=True,
            check=False,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("System check identified no issues", output)

    def test_configuracion_incompleta_falla_sin_exponer_secretos(self):
        environment = production_environment()
        sensitive_password = environment["POSTGRES_PASSWORD"]
        del environment["POSTGRES_DB"]

        result = self.run_python(
            "from django.conf import settings; print(settings.DATABASES)",
            environment=environment,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("POSTGRES_DB", output)
        self.assertNotIn(sensitive_password, output)
        self.assertNotIn(SAFE_PRODUCTION_SECRET, output)
