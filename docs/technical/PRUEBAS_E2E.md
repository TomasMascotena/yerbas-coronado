# Pruebas end-to-end en navegador

La suite E2E utiliza Playwright 1.62.0, Chromium y el servidor real de Django
contra PostgreSQL. Se mantiene separada de `python manage.py test` para que la
suite tradicional no dependa de un navegador instalado.

## Instalación

Desde PowerShell, con el entorno virtual activo:

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

El segundo comando descarga el navegador en la caché local de Playwright, no
dentro del repositorio. No debe utilizarse `PLAYWRIGHT_BROWSERS_PATH=0` en este
proyecto porque eso colocaría binarios junto al código.

PostgreSQL debe estar disponible y las variables locales deben configurarse de
la misma forma que para la suite tradicional.

## Ejecución

La ejecución headless es la predeterminada:

```powershell
python manage.py test e2e.public_purchase e2e.admin_workflows
```

Para depurar con una ventana visible:

```powershell
$env:E2E_HEADLESS="false"
python manage.py test e2e.public_purchase e2e.admin_workflows
Remove-Item Env:E2E_HEADLESS
```

Puede ejecutarse un escenario pasando su clase y método completos como label
de `manage.py test`. No se requiere Internet durante las pruebas y el enlace de
WhatsApp se inspecciona sin navegar al servicio externo.

## Aislamiento y diagnóstico

Cada prueba usa un contexto de navegador limpio. Django crea y destruye una
base PostgreSQL de prueba; las imágenes se escriben en un directorio temporal
que se elimina al finalizar cada clase.

La API síncrona de Playwright mantiene internamente un loop de eventos. La
clase base habilita `DJANGO_ALLOW_ASYNC_UNSAFE` solamente durante cada clase
E2E para que el ORM síncrono y el `flush` de `TransactionTestCase` puedan
ejecutarse en el hilo de pruebas; restaura siempre el valor anterior al cerrar
el navegador. Los accesos al ORM siguen siendo secuenciales y el servidor usa
su propia conexión PostgreSQL.

Ante un fallo se guardan una captura y un archivo con el nombre de la prueba y
la URL sin query string en `.e2e-artifacts/`. La carpeta está ignorada por Git.
Los artefactos exitosos no se conservan. Para limpiar fallos anteriores:

```powershell
Remove-Item -Recurse -Force -LiteralPath .e2e-artifacts
```

Si Chromium no inicia, repetir `python -m playwright install chromium` y
comprobar que la versión de `playwright` coincida con `requirements-dev.txt`.
