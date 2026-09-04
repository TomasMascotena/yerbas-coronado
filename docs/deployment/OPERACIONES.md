# Backups, restauración y respuesta operativa

## Objetivos antes del lanzamiento

La persona responsable debe acordar RPO (máxima pérdida de datos aceptable) y
RTO (tiempo máximo de recuperación) con la clienta. Registrarlos en cada ensayo;
no prometer valores antes de medir una restauración completa.

Se requieren tres niveles complementarios:

1. backups programados de Railway, con retención confirmada en el plan vigente;
2. recuperación a un punto en el tiempo si el producto/plan la ofrece;
3. `pg_dump` periódico cifrado y almacenado fuera de Railway.

La disponibilidad y retención de backups/PITR cambia por plan: verificarla en
la [documentación oficial de PostgreSQL de Railway](https://docs.railway.com/databases/postgresql)
antes de contratar. Un backup no se considera válido hasta restaurarlo.

## Generar un dump externo

Ejecutar desde una estación controlada con cliente PostgreSQL compatible. Usar
variables de entorno o un archivo `pgpass` temporal con permisos restrictivos;
no poner contraseña ni URL completa en la línea de comandos o logs.

```powershell
$env:PGHOST="<host-autorizado>"
$env:PGPORT="5432"
$env:PGUSER="<usuario-backup>"
$env:PGDATABASE="<base-origen>"
pg_dump --format=custom --no-owner --no-acl --file="yerbas-AAAA-MM-DD.dump"
Remove-Item Env:PGHOST,Env:PGPORT,Env:PGUSER,Env:PGDATABASE
```

Cifrar el archivo, registrar fecha/tamaño/checksum y transferirlo al destino
externo elegido. No versionarlo ni adjuntarlo a tickets.

## Ensayo de restauración

1. Crear una base PostgreSQL temporal aislada, con nombre y responsable
   explícitos. No reutilizar producción.
2. Restaurar con `pg_restore --no-owner --no-acl --exit-on-error` usando
   credenciales ingresadas fuera del comando.
3. Ejecutar `python manage.py migrate --check` contra la base temporal.
4. Verificar conteos de `orders_pedido`, `orders_detallepedido`,
   `inventory_inventario`, `inventory_movimientoinventario`, `catalog_producto`
   y `orders_cliente` contra los conteos registrados en origen.
5. Muestrear Pedidos con sus Detalles y movimientos; comprobar que ningún stock
   sea negativo y que los snapshots históricos permanezcan accesibles.
6. Ejecutar smoke tests sin enviar WhatsApp ni modificar datos reales.
7. Registrar tiempos de detección, provisión y restauración para calcular RPO y
   RTO. Destruir la base temporal solo con aprobación y nombre verificado.

## Recuperación de pedidos e inventario

Restaurar siempre la base completa al mismo punto. No recuperar por separado
Pedidos, Detalles, Inventario o Movimientos: sus relaciones y atomicidad deben
preservarse. Durante recuperación, mantener el web detenido o en modo que no
admita escrituras. Tras restaurar, comparar conteos y relaciones antes de abrir
tráfico.

## Migración de datos a Render

1. Tomar backup verificado y registrar la hora de corte.
2. Detener nuevas escrituras.
3. Crear PostgreSQL privado en Render y restaurar el dump.
4. Aplicar únicamente migraciones versionadas con `migrate --noinput`.
5. Configurar secretos y ejecutar checks/smoke tests en el servicio nuevo.
6. Comparar conteos críticos y probar historial, stock y checkout.
7. Cambiar DNS; conservar Railway durante la ventana de rollback.

## Incidentes y monitoreo

Alertar por health check fallido, errores 5xx, reinicios, CPU/RAM sostenidas,
disco PostgreSQL, conexiones agotadas y crecimiento anormal de tráfico. Los
logs deben mantener niveles y contexto técnico, nunca cuerpos de solicitud,
tokens, secretos o datos personales.

Ante un incidente: registrar hora, detener el cambio causante, preservar logs,
evaluar si es de aplicación o datos, elegir rollback o restauración (no ambos
automáticamente), validar y documentar el resultado.
