# Matriz de variables de despliegue

Nunca copiar valores reales a este documento, GitHub, tickets o logs. En
Railway, las credenciales PostgreSQL deben enlazarse mediante referencias al
servicio de base de datos, no copiarse manualmente.

| Variable | Req. | Ejemplo ficticio | Origen / entorno | Sensible | Rotación |
|---|---:|---|---|---:|---|
| `DJANGO_ENV` | Sí | `production` | Operador / todos | No | Solo al cambiar de entorno |
| `DJANGO_SECRET_KEY` | Sí | `<cadena-aleatoria-de-50+-caracteres>` | Gestor de secretos / producción | Sí | Generar una nueva, actualizar el secreto y redesplegar; invalida firmas y sesiones |
| `DJANGO_DEBUG` | No | `false` | Operador / producción | No | No aplica; `true` está prohibido en producción |
| `DJANGO_ALLOWED_HOSTS` | Sí | `tienda.example.test,healthcheck.railway.app` | Dominio Railway, host de health check o dominio propio / producción | No | Actualizar antes de cambiar dominio |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Según dominio | `https://tienda.example.test` | Dominio Railway o propio / producción | No | Actualizar junto con hosts y certificado |
| `PGDATABASE` | Sí¹ | `${{Postgres.PGDATABASE}}` | Referencia Railway / producción | No | Gestionada por el servicio PostgreSQL |
| `PGUSER` | Sí¹ | `${{Postgres.PGUSER}}` | Referencia Railway / producción | Sí | Rotar credencial en PostgreSQL y actualizar referencia |
| `PGPASSWORD` | Sí¹ | `${{Postgres.PGPASSWORD}}` | Referencia Railway / producción | Sí | Rotar credencial en PostgreSQL y redesplegar |
| `PGHOST` | Sí¹ | `${{Postgres.PGHOST}}` | Red privada Railway / producción | No | Gestionada por Railway |
| `PGPORT` | Sí¹ | `${{Postgres.PGPORT}}` | Referencia Railway / producción | No | Gestionada por Railway |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | Alternativa¹ | Valores locales ficticios | Desarrollo, tests u otros proveedores | Contraseña: sí | Igual que las variables `PG*` equivalentes |
| `POSTGRES_CONN_MAX_AGE` | No | `60` | Operador / producción | No | Ajustar con métricas; `60` es el default productivo |
| `POSTGRES_SSLMODE` | Según conexión | `require` | Operador / producción | No | Revisar al cambiar red/proveedor; usar `verify-full` si hay CA y hostname verificables |
| `WHATSAPP_BUSINESS_NUMBER` | No | `5491100000000` | Administradora / producción | No | Cambiar al rotar el número comercial |
| `DJANGO_LOG_LEVEL` | No | `INFO` | Operador / producción | No | Ajustar temporalmente; `DEBUG` está prohibido |
| `DJANGO_SECURE_HSTS_SECONDS` | No | `3600` | Operador / producción | No | Aumentar gradualmente tras validar HTTPS |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | No | `false` | Operador / producción | No | Activar solo al controlar todos los subdominios |
| `DJANGO_SECURE_HSTS_PRELOAD` | No | `false` | Operador / producción | No | Activar únicamente tras aprobar preload |
| `DJANGO_TRUST_X_FORWARDED_PROTO` | Sí en Railway | `true` | Operador / proxy conocido | No | Desactivar si el proxy no sanea `X-Forwarded-Proto` |
| `PORT` | Automática | `8000` | Railway / producción | No | Gestionada por la plataforma |
| `GUNICORN_WORKERS` | No | `1` | Operador / producción | No | Ajustar con CPU/RAM y conexiones disponibles |
| `GUNICORN_THREADS` | No | `1` | Operador / producción | No | Ajustar con pruebas de carga |
| `GUNICORN_TIMEOUT` | No | `60` | Operador / producción | No | Ajustar solo ante evidencia |
| `GUNICORN_GRACEFUL_TIMEOUT` | No | `30` | Operador / producción | No | Coordinar con el tiempo de drenaje del proveedor |
| `RAILWAY_PUBLIC_DOMAIN` | Automática | `app.example.up.railway.app` | Railway / validación | No | Cambia al regenerar el dominio; copiar su valor a hosts/orígenes |

¹ Se requiere un juego completo: las variables históricas `POSTGRES_*` tienen
precedencia y las `PG*` son el fallback nativo de Railway. No mezclar juegos
parciales. `DATABASE_URL` no se interpreta para evitar parsing implícito y
mantener validación campo por campo.

El dominio personalizado futuro no es un secreto ni una variable consumida
directamente. Sus valores se incorporarán a `DJANGO_ALLOWED_HOSTS` y
`DJANGO_CSRF_TRUSTED_ORIGINS` cuando exista una decisión de dominio y DNS.
