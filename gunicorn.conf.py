import os


def _positive_integer(name, default):
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{name} debe ser un entero positivo.") from error
    if value <= 0:
        raise RuntimeError(f"{name} debe ser un entero positivo.")
    return value


bind = f"0.0.0.0:{_positive_integer('PORT', 8000)}"
workers = _positive_integer("GUNICORN_WORKERS", 1)
threads = _positive_integer("GUNICORN_THREADS", 1)
timeout = _positive_integer("GUNICORN_TIMEOUT", 60)
graceful_timeout = _positive_integer("GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
