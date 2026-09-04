FROM python:3.13.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r requirements.txt

COPY . .
RUN DJANGO_ENV=production \
    DJANGO_SECRET_KEY=build-only-K7-yQ2-vN9-xR4-pL8-cT6-mW3-zF5-hJ1-uD0-sA7-gB9 \
    DJANGO_ALLOWED_HOSTS=build.example.test \
    POSTGRES_DB=build_only \
    POSTGRES_USER=build_only \
    POSTGRES_PASSWORD=build_only \
    POSTGRES_HOST=localhost \
    POSTGRES_PORT=5432 \
    DJANGO_SECURE_HSTS_SECONDS=3600 \
    python manage.py collectstatic --noinput

RUN addgroup --system django && \
    adduser --system --ingroup django django && \
    chown -R django:django /app

USER django

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; url='http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health/ready/'; request=urllib.request.Request(url, headers={'Host': 'healthcheck.railway.app'}); urllib.request.urlopen(request, timeout=4)"

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
