import ipaddress
import re
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


ENVIRONMENTS = {"development", "test", "production"}
PRODUCTION = "production"

DEFAULT_DEVELOPMENT_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]
INSECURE_SECRET_MARKERS = (
    "change-me",
    "changeme",
    "django-insecure-",
    "example",
    "replace-with",
)
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
HOSTNAME_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def configuration_error(message):
    raise ImproperlyConfigured(message)


def parse_environment(environ):
    value = environ.get("DJANGO_ENV", "development").strip().lower()
    if value not in ENVIRONMENTS:
        allowed = ", ".join(sorted(ENVIRONMENTS))
        configuration_error(
            f"DJANGO_ENV debe ser uno de los siguientes valores: {allowed}."
        )
    return value


def parse_bool(environ, name, *, default):
    raw_value = environ.get(name)
    if raw_value is None:
        return default

    value = raw_value.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    configuration_error(f"{name} debe ser 'true' o 'false'.")


def parse_debug(environ, environment):
    default = environment == "development"
    debug = parse_bool(environ, "DJANGO_DEBUG", default=default)
    if environment == PRODUCTION and debug:
        configuration_error("DJANGO_DEBUG no puede ser true en producción.")
    return debug


def parse_secret_key(environ, environment):
    secret_key = environ.get("DJANGO_SECRET_KEY", "")
    normalized_secret = secret_key.strip()
    if not normalized_secret:
        configuration_error("DJANGO_SECRET_KEY es obligatoria y no puede estar vacía.")

    if environment == PRODUCTION:
        lowercase_secret = normalized_secret.lower()
        if (
            len(normalized_secret) < 50
            or len(set(normalized_secret)) < 5
            or any(marker in lowercase_secret for marker in INSECURE_SECRET_MARKERS)
        ):
            configuration_error(
                "DJANGO_SECRET_KEY no cumple los requisitos de seguridad de producción."
            )
    return secret_key


def _parse_csv(environ, name, *, required):
    raw_value = environ.get(name)
    if raw_value is None or not raw_value.strip():
        if required:
            configuration_error(f"{name} es obligatoria y no puede estar vacía.")
        return []

    values = raw_value.split(",")
    if any(not value.strip() for value in values):
        configuration_error(f"{name} contiene una entrada vacía.")
    return [value.strip() for value in values]


def _valid_hostname(value):
    hostname = value[1:] if value.startswith(".") else value
    if not hostname or len(hostname) > 253:
        return False

    if hostname.startswith("[") and hostname.endswith("]"):
        try:
            ipaddress.ip_address(hostname[1:-1])
        except ValueError:
            return False
        return True

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.rstrip(".").split(".")
        return all(HOSTNAME_LABEL.fullmatch(label) for label in labels)
    return True


def parse_allowed_hosts(environ, environment):
    raw_value = environ.get("DJANGO_ALLOWED_HOSTS")
    if raw_value is None and environment != PRODUCTION:
        return DEFAULT_DEVELOPMENT_HOSTS.copy()

    hosts = _parse_csv(
        environ,
        "DJANGO_ALLOWED_HOSTS",
        required=environment == PRODUCTION,
    )
    for host in hosts:
        if environment == PRODUCTION and host == "*":
            configuration_error("DJANGO_ALLOWED_HOSTS no puede contener '*' en producción.")
        if (
            host == "*"
            or "://" in host
            or "/" in host
            or "@" in host
            or any(character.isspace() for character in host)
            or not _valid_hostname(host)
        ):
            configuration_error("DJANGO_ALLOWED_HOSTS contiene una entrada inválida.")
    return hosts


def parse_csrf_trusted_origins(environ, environment):
    origins = _parse_csv(environ, "DJANGO_CSRF_TRUSTED_ORIGINS", required=False)
    normalized_origins = []
    allowed_schemes = {"https"} if environment == PRODUCTION else {"http", "https"}

    for origin in origins:
        normalized = origin.rstrip("/")
        try:
            parsed = urlsplit(normalized)
            port = parsed.port
        except ValueError:
            configuration_error(
                "DJANGO_CSRF_TRUSTED_ORIGINS contiene un origen inválido."
            )
        if (
            parsed.scheme not in allowed_schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in parsed.hostname
            or not _valid_hostname(parsed.hostname)
            or port is not None and not 1 <= port <= 65535
        ):
            configuration_error(
                "DJANGO_CSRF_TRUSTED_ORIGINS contiene un origen inválido."
            )
        normalized_origins.append(normalized)
    return normalized_origins


def _required_value(environ, name):
    value = environ.get(name, "")
    if not value.strip():
        configuration_error(f"{name} es obligatoria y no puede estar vacía.")
    return value


def build_database_configuration(environ):
    port_value = _required_value(environ, "POSTGRES_PORT")
    try:
        port = int(port_value)
    except ValueError:
        configuration_error("POSTGRES_PORT debe ser un puerto válido.")
    if not 1 <= port <= 65535:
        configuration_error("POSTGRES_PORT debe ser un puerto válido.")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _required_value(environ, "POSTGRES_DB"),
        "USER": _required_value(environ, "POSTGRES_USER"),
        "PASSWORD": _required_value(environ, "POSTGRES_PASSWORD"),
        "HOST": _required_value(environ, "POSTGRES_HOST"),
        "PORT": port,
    }


def parse_hsts_seconds(environ, environment):
    default = "3600" if environment == PRODUCTION else "0"
    raw_value = environ.get("DJANGO_SECURE_HSTS_SECONDS", default)
    try:
        seconds = int(raw_value)
    except ValueError:
        configuration_error("DJANGO_SECURE_HSTS_SECONDS debe ser un entero válido.")
    if seconds < 0 or environment == PRODUCTION and seconds == 0:
        configuration_error(
            "DJANGO_SECURE_HSTS_SECONDS debe ser mayor que cero en producción."
        )
    return seconds


def parse_log_level(environ, environment):
    level = environ.get("DJANGO_LOG_LEVEL", "INFO").strip().upper()
    if level not in VALID_LOG_LEVELS:
        configuration_error("DJANGO_LOG_LEVEL contiene un nivel inválido.")
    if environment == PRODUCTION and level == "DEBUG":
        configuration_error("DJANGO_LOG_LEVEL no puede ser DEBUG en producción.")
    return level
