# Despliegue en Railway

## Servicios

Crear un proyecto con estos recursos:

1. un servicio de aplicación conectado al repositorio de GitHub;
2. un servicio PostgreSQL;
3. un volumen persistente conectado a la aplicación y montado en `/app/media`.

El volumen es obligatorio porque las imágenes de Producto se cargan desde la
administración y deben sobrevivir reinicios y nuevos despliegues.

## Variables de la aplicación

Configurar las siguientes variables en el servicio web:

```text
DJANGO_SECRET_KEY=<valor largo, aleatorio y secreto>
DJANGO_DEBUG=False
WHATSAPP_BUSINESS_NUMBER=5492664933059
PGDATABASE=${{Postgres.PGDATABASE}}
PGUSER=${{Postgres.PGUSER}}
PGPASSWORD=${{Postgres.PGPASSWORD}}
PGHOST=${{Postgres.PGHOST}}
PGPORT=${{Postgres.PGPORT}}
```

`RAILWAY_PUBLIC_DOMAIN` y `RAILWAY_VOLUME_MOUNT_PATH` son provistas por
Railway. Para usar un dominio propio, agregar además:

```text
DJANGO_ALLOWED_HOSTS=tienda.ejemplo.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://tienda.ejemplo.com
```

La aplicación comienza con HSTS de una hora. Después de comprobar el dominio y
HTTPS puede ampliarse con `DJANGO_SECURE_HSTS_SECONDS=31536000`. No activar
`DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` ni `DJANGO_SECURE_HSTS_PRELOAD` sin
verificar previamente todos los subdominios.

## Comandos del servicio

- Pre-deploy command: `python manage.py migrate --noinput`
- Start command: dejar vacío para que Railway utilice el `Procfile`.
- Healthcheck path: `/`

El `Procfile` ejecuta `collectstatic` dentro del contenedor que servirá la
aplicación y luego inicia Gunicorn en el puerto asignado por Railway.

## Primera puesta en marcha

1. Generar el dominio público en Railway.
2. Confirmar que el despliegue y el healthcheck finalizan correctamente.
3. Ejecutar una única vez `python manage.py createsuperuser` desde Railway.
4. Ingresar a `/admin/`, crear o verificar Productos e Inventario y cargar las
   imágenes definitivas.
5. Probar catálogo, Carrito, Checkout, confirmación y enlace de WhatsApp.
6. Reiniciar el servicio y confirmar que las imágenes siguen disponibles.

## Cambio futuro de servidor

La configuración no depende de un dominio concreto. Para migrar a otro
servidor deberán reemplazarse las variables de entorno, PostgreSQL y el destino
persistente de `MEDIA_ROOT`; las reglas del dominio y la aplicación no cambian.
