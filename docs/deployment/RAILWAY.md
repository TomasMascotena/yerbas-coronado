# Despliegue controlado en Railway

Esta guía prepara un despliegue futuro; no autoriza crear recursos ni generar
gastos. Se usa un contenedor estándar y componentes estándar de Django para
seguir siendo adaptable a Render. Se descartó `railway.json` porque Railway ya
no permite activarlo en servicios nuevos y anunció su retiro. Su IaC sustituto
continúa en beta y todavía no cubre de forma estable el pre-deploy requerido.

## Arquitectura y comandos

- servicio web Django, construido desde GitHub;
- servicio PostgreSQL separado y accesible por red privada;
- build: `docker build` ejecuta `python manage.py collectstatic --noinput`;
- pre-deploy: `python manage.py migrate --noinput`;
- start: `gunicorn config.wsgi:application --config gunicorn.conf.py`;
- health check de Railway: `/health/ready/`;
- diagnóstico de proceso sin base: `/health/live/`.

Las migraciones no forman parte de la imagen ni del start. En Settings, definir
el pre-deploy exactamente como `python manage.py migrate --noinput`, health
check `/health/ready/`, timeout `120` segundos, restart `On Failure` y máximo
tres reintentos. Si pre-deploy devuelve un código no cero, Railway no debe
publicar la versión. Registrar esos valores en la revisión de cada deployment.

## 1. Preparar y conectar servicios

1. Obtener aprobación de presupuesto y almacenamiento de imágenes.
2. Crear un proyecto vacío en Railway y conectar el repositorio GitHub.
3. Seleccionar la rama aprobada y confirmar que Railway detecte `Dockerfile`.
4. Agregar PostgreSQL como servicio separado. No habilitar acceso público salvo
   una operación temporal y justificada.
5. En el servicio web, crear referencias `PGDATABASE`, `PGUSER`, `PGPASSWORD`,
   `PGHOST` y `PGPORT` hacia las variables del servicio PostgreSQL.
6. Completar la matriz de [variables](VARIABLES_ENTORNO.md) en el gestor de
   secretos de Railway. Generar `DJANGO_SECRET_KEY` localmente con un generador
   criptográfico y pegarla directamente en el gestor.
7. Definir `DJANGO_ALLOWED_HOSTS` con el hostname Railway, y
   `healthcheck.railway.app`; definir `DJANGO_CSRF_TRUSTED_ORIGINS` con el
   origen HTTPS público completo. El host de health check no es un origen CSRF.
8. Configurar `DJANGO_TRUST_X_FORWARDED_PROTO=true` solo después de confirmar
   que Railway termina HTTPS y reemplaza el encabezado del cliente.

Railway detecta el Dockerfile en repositorios GitHub según su
[documentación de servicios](https://docs.railway.com/services). El pre-deploy
se configura según su
[documentación específica](https://docs.railway.com/deployments/pre-deploy-command).
Railway documenta que sus sondeos usan el hostname `healthcheck.railway.app`;
las dos rutas técnicas están exentas únicamente de la redirección HTTPS interna
para que ese sondeo pueda obtener `200`, pero no exponen datos ni escrituras.

## 2. Primera validación

1. Revisar el log de build: la instalación y `collectstatic` deben terminar sin
   advertencias de manifiesto.
2. Revisar pre-deploy: todas las migraciones deben quedar aplicadas.
3. Generar el dominio temporal desde Networking.
4. Confirmar `/health/live/` y `/health/ready/` con HTTPS y respuesta `200`.
5. Ejecutar los smoke tests: catálogo vacío o controlado, carrito, login Admin,
   checkout de prueba y confirmación. No usar datos personales reales.
6. Revisar que los logs no contengan cuerpos, tokens, credenciales ni datos
   personales y que un rechazo CSRF quede como `WARNING` una sola vez.
7. Verificar CPU, RAM, reinicios, latencia, conexiones y espacio PostgreSQL.

## 3. Superusuario y datos iniciales

Crear el superusuario desde una consola efímera con
`python manage.py createsuperuser`, introduciendo la contraseña en el prompt.
No usar variables versionadas ni comandos que incluyan la contraseña. Antes de
crear Productos, resolver el bloqueo de media explicado abajo. Los ingresos de
stock deben usar los casos de uso administrativos existentes.

## Estáticos y media

WhiteNoise sirve únicamente el resultado versionado y comprimido de
`collectstatic`; nunca sirve `MEDIA_ROOT`. `STATIC_ROOT=staticfiles/` y
`MEDIA_ROOT=media/` permanecen separados.

No hay imágenes de Producto versionadas actualmente. Una imagen cargada desde
Admin se escribiría en el filesystem del contenedor y se perdería al reemplazar
la instancia. Por eso queda **bloqueado el lanzamiento comercial y toda carga
administrativa de imágenes** hasta elegir e integrar almacenamiento persistente
(S3 compatible, Cloudinary u otro backend externo).

Para una validación temporal sin Productos puede usarse una base vacía y no
habilitar acceso administrativo. Si fuera imprescindible validar imágenes, se
requiere una decisión explícita previa; una opción transitoria es un volumen
persistente montado en `media/`, con backup propio, una sola réplica y aceptación
documentada de sus límites. No tratar ese volumen como solución definitiva.

## Dominio y HTTPS

### Validación

Usar el dominio temporal Railway, HTTPS y cookies seguras. Mantener HSTS en el
valor conservador inicial y no habilitar subdominios/preload.

### Producción

1. Confirmar propiedad del dominio futuro y reducir su TTL con anticipación.
2. Agregar hostname y origen HTTPS a las variables Django antes del corte.
3. Configurar DNS según Railway y esperar certificado válido.
4. Probar health checks, Admin, catálogo y checkout por HTTPS.
5. Aumentar HSTS gradualmente. Subdominios y preload requieren aprobación
   separada y control total de los nombres afectados.
6. Para rollback DNS, restaurar los registros anteriores conservados y esperar
   el TTL; no borrar el servicio previo hasta completar la validación.

## Capacidad, costos y monitoreo

Punto de partida sujeto a métricas: 512 MB para web, 512 MB evaluados para
PostgreSQL, un worker y un thread. Configurar una alerta aproximada en USD 8 y
proponer un límite duro entre USD 15 y 20 solamente con autorización expresa;
no detener servicios automáticamente desde este proyecto.

Monitorear CPU, RAM, disco, tráfico, latencia, respuestas 5xx, fallos de
readiness, reinicios, conexiones PostgreSQL y crecimiento de backups. Revisar
los valores y la facturación vigentes en Railway antes de crear recursos.
El health check de Railway valida deployments, pero no es monitoreo continuo;
configurar un monitor HTTPS externo para `/health/live/` y alertas separadas
para PostgreSQL.

## Lista de seguridad previa a publicar

- confirmar `DJANGO_ENV=production`, `DEBUG=False` y `check --deploy` limpio;
- mantener secretos solo en Railway y rotarlos ante cualquier exposición;
- aceptar tráfico público solo por HTTPS, con cookies seguras y proxy validado;
- comenzar con HSTS conservador y no activar preload por anticipado;
- proteger Admin con una contraseña única y no publicar cuentas por defecto;
- mantener PostgreSQL sin endpoint público y limitar el usuario de aplicación
  a su base/esquema; usar otra credencial de privilegios elevados solo para la
  operación que realmente la requiera;
- confirmar que no existan SQLite, Debug Toolbar, shells web ni paneles de
  diagnóstico expuestos;
- revisar logs sin datos personales, tokens, cuerpos ni credenciales;
- ejecutar `python -m pip_audit -r requirements.txt` y resolver hallazgos antes
  de publicar;
- no habilitar cargas de imágenes mientras siga pendiente el backend externo.

El workflow CI verifica configuración, migraciones, suite tradicional, E2E,
estáticos, Gunicorn, build del contenedor, configuración productiva simulada,
dependencias conocidas y artefactos locales. No contacta WhatsApp ni Railway.

## Rollback de aplicación

1. Pausar nuevas operaciones administrativas si hay riesgo de inconsistencia.
2. Identificar el último deployment sano y verificar compatibilidad de esquema.
3. Usar rollback/redeploy de Railway. Nunca revertir una migración destructiva
   sin un procedimiento específico y backup verificado.
4. Confirmar health checks y smoke tests.
5. Si el incidente es de datos, seguir `OPERACIONES.md`; no mezclar rollback de
   código y restauración de base sin medir la pérdida aceptable.

Para detener la tienda sin corromper datos, impedir nuevas solicitudes en el
proxy o escalar web a cero desde Railway, conservar PostgreSQL y verificar que
no haya una migración activa. Esta acción requiere autorización operativa.

## Portabilidad a Render

Crear servicios web/PostgreSQL equivalentes, mapear la misma matriz de entorno,
ejecutar `collectstatic` en build, `migrate --noinput` antes de publicar y usar
el mismo comando Gunicorn y health check. Restaurar un dump probado, cambiar
hosts/orígenes, validar HTTPS y recién entonces cortar DNS. Ninguna regla de la
aplicación depende de APIs de Railway.
