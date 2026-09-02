# Configuración por entorno

Yerbas Coronado utiliza un único módulo de settings respaldado por validadores
puros en `config/environment.py`. La estrategia mantiene el desarrollo local
simple y hace que producción falle al iniciar ante valores ausentes, ambiguos
o inseguros.

## Entornos

`DJANGO_ENV` admite únicamente:

- `development`: valor predeterminado; carga `.env` local y permite HTTP;
- `test`: no carga `.env` y requiere una configuración explícita;
- `production`: no carga `.env`, activa protecciones de producción y valida
  todos los valores con criterio fail-closed.

En producción, `DJANGO_ENV=production` debe definirse en el entorno real del
proceso. Un valor desconocido impide el arranque.

## Desarrollo local

1. Copiar `.env.example` a `.env`.
2. Reemplazar todos los valores de ejemplo por valores locales propios.
3. Iniciar PostgreSQL mediante `docker compose up -d db`.
4. Ejecutar `python manage.py runserver` desde el entorno virtual.

`.env` está ignorada por Git. El entorno local usa por defecto `DEBUG=True`,
hosts loopback y no activa redirección HTTPS ni HSTS.

Para una ejecución de tests aislada se debe definir `DJANGO_ENV=test` junto
con `DJANGO_SECRET_KEY` y las cinco variables `POSTGRES_*` en el entorno del
proceso. Ese modo no carga `.env`, usa `DEBUG=False` y conserva PostgreSQL.

## Variables de producción obligatorias

- `DJANGO_ENV=production`;
- `DJANGO_SECRET_KEY`: secreto aleatorio de al menos 50 caracteres, sin
  marcadores de ejemplo;
- `DJANGO_ALLOWED_HOSTS`: lista separada por comas, sin esquemas ni `*`;
- `POSTGRES_DB`;
- `POSTGRES_USER`;
- `POSTGRES_PASSWORD`;
- `POSTGRES_HOST`;
- `POSTGRES_PORT`.

`DJANGO_DEBUG` es opcional y vale `false` por defecto en producción. Si se
declara, solo admite `true` o `false`; `true` impide el arranque de producción.

## Variables opcionales

- `DJANGO_CSRF_TRUSTED_ORIGINS`: orígenes separados por comas. En producción
  deben ser orígenes HTTPS completos, sin ruta, credenciales, query o fragmento;
- `DJANGO_LOG_LEVEL`: `INFO`, `WARNING`, `ERROR` o `CRITICAL` en producción;
- `DJANGO_SECURE_HSTS_SECONDS`: entero mayor que cero en producción; su valor
  predeterminado es 3600;
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`: `true` o `false`;
- `DJANGO_SECURE_HSTS_PRELOAD`: `true` o `false`;
- `DJANGO_TRUST_X_FORWARDED_PROTO`: `true` o `false`;
- `WHATSAPP_BUSINESS_NUMBER`: número comercial en el formato ya validado por
  la aplicación.

No se debe activar `DJANGO_TRUST_X_FORWARDED_PROTO` hasta conocer y controlar
el proxy inverso. Al activarlo, Django confiará en `X-Forwarded-Proto`; el proxy
debe eliminar cualquier valor enviado por el cliente y establecer el suyo.

## Protecciones de producción

Producción activa redirección HTTPS, cookies de sesión y CSRF seguras,
`HttpOnly` para la cookie de sesión, `SameSite=Lax`, protección contra MIME
sniffing, `Referrer-Policy: strict-origin-when-cross-origin`, bloqueo de frames
y HSTS. `DEBUG=False` evita que las páginas técnicas de Django se expongan.

El logging de producción conserva por consola los eventos `WARNING` y `ERROR`
de `django.security`, incluidos los rechazos CSRF, sin propagarlos al logger
raíz para evitar duplicados. Los eventos `DEBUG` e `INFO` de seguridad se
descartan y la configuración no incorpora cuerpos de solicitudes ni valores
sensibles a los mensajes.

HSTS puede dificultar la recuperación de una configuración HTTPS incorrecta.
Antes de aumentar su duración, habilitar subdominios o solicitar preload, se
deben verificar el certificado, HTTPS y el control de todos los subdominios.

## Estáticos y media

- `STATIC_ROOT` apunta a `staticfiles/` y permite ejecutar
  `python manage.py collectstatic --noinput`;
- `MEDIA_ROOT` permanece en `media/` y está separado de los estáticos;
- Django solo sirve media mediante las URLs de desarrollo cuando `DEBUG=True`.

La persistencia, los backups y el almacenamiento local o externo de imágenes
son decisiones pendientes de 10D. También quedan para 10D el servidor WSGI,
el proxy, el proveedor y la estrategia operativa de estáticos/media.

## Validación de producción

Con variables ficticias seguras configuradas en el proceso se debe ejecutar:

```text
python manage.py check --deploy
python manage.py collectstatic --noinput
```

Para que `check --deploy` quede sin advertencias HSTS en una simulación
controlada, también se deben definir explícitamente una duración definitiva,
`DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true` y
`DJANGO_SECURE_HSTS_PRELOAD=true`. Esos valores no son defaults universales:
solo corresponden cuando HTTPS y todos los subdominios ya fueron verificados.

La configuración no imprime variables de entorno ni valores sensibles en sus
errores. Los mensajes identifican únicamente el nombre o la categoría del
valor inválido.
