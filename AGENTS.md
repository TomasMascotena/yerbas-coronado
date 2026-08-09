# AGENTS.md

## Proyecto

Yerbas Coronado es una aplicación ecommerce desarrollada con Django para un pequeño emprendimiento de yerba mate.

El proyecto se desarrolla de forma incremental, módulo por módulo.

No implementar toda la aplicación de una vez.

---

## Fuente de verdad

La Especificación del Dominio v3.2 congelada es la fuente de verdad para todas las reglas de negocio.

Las decisiones de implementación nunca deben contradecir la Especificación del Dominio.

No inventar, simplificar, reinterpretar ni modificar silenciosamente reglas de negocio.

Si una implementación solicitada entra en conflicto con la Especificación del Dominio, detenerse e informar el conflicto antes de modificar código.

Si la Especificación del Dominio no define el comportamiento necesario para completar una tarea, detenerse y solicitar una decisión de dominio en lugar de asumir una respuesta.

---

## Flujo de desarrollo

Trabajar únicamente sobre el módulo o tarea solicitada explícitamente.

Antes de realizar cambios:

1. inspeccionar el código existente relacionado;
2. identificar las reglas del dominio afectadas;
3. describir el plan de implementación cuando la tarea no sea trivial;
4. identificar ambigüedades antes de escribir código.

No implementar módulos futuros de forma anticipada.

No introducir abstracciones únicamente para necesidades hipotéticas futuras.

Preferir la implementación más simple que respete completamente el dominio actual y mantenga el código legible y mantenible.

---

## Tecnologías

Stack actual:

- Python 3.13
- Django 5.2 LTS
- PostgreSQL 17
- psycopg
- Docker Compose para la base de datos de desarrollo
- Django Templates
- HTML
- CSS
- JavaScript
- entorno virtual de Python mediante `.venv`

No introducir nuevas dependencias de producción sin aprobación explícita.

No reemplazar las tecnologías elegidas salvo indicación explícita.

---

## Arquitectura

La aplicación utiliza una arquitectura de monolito modular con Django.

Organizar el código por capacidades del negocio y no crear automáticamente una aplicación Django por cada entidad de base de datos.

Mantener la lógica de negocio fuera de los templates.

Evitar colocar flujos de negocio complejos directamente dentro de las views de Django.

Las views deben ocuparse principalmente de cuestiones HTTP y delegar el comportamiento de negocio al código de aplicación o dominio correspondiente.

No crear un frontend separado ni una API REST salvo solicitud explícita.

---

## Integridad del dominio

Las invariantes del negocio deben garantizarse mediante lógica de aplicación y, cuando corresponda, reforzarse mediante restricciones de base de datos.

Las restricciones de base de datos no reemplazan las validaciones del dominio.

Las operaciones definidas por el dominio como atómicas deben ejecutarse dentro de transacciones de base de datos.

Las operaciones sensibles a concurrencia deben mantener la consistencia ante solicitudes simultáneas.

No modificar directamente las cantidades de Inventario fuera del flujo de negocio correspondiente.

No modificar información histórica de un Pedido después de su generación salvo donde la Especificación del Dominio lo permita explícitamente.

No eliminar físicamente entidades cuando el dominio requiera preservación histórica o inactivación.

---

## Base de datos y migraciones

PostgreSQL es la base de datos relacional utilizada para desarrollo y orientada a producción.

No introducir comportamiento dependiente de SQLite.

Todos los cambios de esquema deben representarse mediante migraciones de Django.

No modificar una migración ya aplicada salvo indicación explícita.

Preferir restricciones de base de datos para invariantes estructurales como:

- unicidad;
- nulabilidad;
- valores numéricos positivos;
- relaciones uno a uno;

siempre que sean compatibles con la Especificación del Dominio.

Las reglas condicionales y transaccionales deben permanecer en la lógica de aplicación o dominio cuando no puedan expresarse de forma segura mediante restricciones simples de base de datos.

---

## Pruebas

Toda regla de negocio agregada o modificada debe contar con pruebas automatizadas.

Las correcciones de errores deben incluir una prueba de regresión siempre que sea razonablemente posible.

Las pruebas deben cubrir:

- comportamiento exitoso;
- operaciones inválidas;
- invariantes importantes del dominio;
- casos borde relevantes;
- rollback de transacciones cuando corresponda;
- concurrencia cuando el dominio lo requiera.

Ejecutar las pruebas relevantes después de cada cambio de implementación.

Antes de considerar una tarea terminada, ejecutar:

```powershell
python manage.py check
python manage.py test
```
Si una tarea afecta el comportamiento de base de datos, asegurarse de que PostgreSQL esté funcionando antes de ejecutar las pruebas correspondientes.

No informar una tarea como completada si existen pruebas fallando.

## Seguridad y configuración
Nunca versionar secretos ni credenciales.
Los valores específicos de cada entorno deben almacenarse en .env o variables de entorno.
.env debe permanecer ignorado por Git.
No incluir directamente en el código:
contraseñas de base de datos;
claves secretas;
hosts de producción;
credenciales de servicios externos.
No exponer información sensible en logs.


## Disciplina Git
Realizar cambios enfocados únicamente en la tarea solicitada.
No crear commits automáticamente salvo solicitud explícita.
No realizar push automáticamente salvo solicitud explícita.
No reescribir el historial de Git.
No modificar archivos no relacionados únicamente por formato o limpieza.
Antes de finalizar una tarea, revisar el diff para detectar cambios accidentales.

## Calidad del código
Preferir Python legible y explícito antes que soluciones excesivamente compactas o complejas.
Utilizar nombres significativos que coincidan con el lenguaje ubicuo definido en la Especificación del Dominio.
Preservar la terminología del dominio.
No renombrar conceptos del dominio arbitrariamente.
Evitar duplicar lógica de negocio.
Mantener funciones y clases enfocadas en una responsabilidad clara.
Agregar comentarios únicamente cuando expliquen una regla de negocio o decisión técnica que no resulte evidente.
No agregar comentarios que simplemente repitan lo que ya expresa claramente el código.

## Definición de tarea terminada
Una tarea se considera terminada únicamente cuando:
el comportamiento solicitado está implementado;
la implementación respeta la Especificación del Dominio congelada;
existen las pruebas automatizadas relevantes y todas pasan;
los chequeos de Django pasan correctamente;
se incluyen las migraciones necesarias cuando corresponda;
no se incorporaron secretos;
no se modificó comportamiento no relacionado;
se revisó el diff final;
se informaron todas las ambigüedades o riesgos pendientes.
Al finalizar una tarea, resumir:
qué se modificó;
qué reglas del dominio se implementaron;
qué pruebas se ejecutaron y sus resultados;
qué migraciones se crearon, si corresponde;
qué riesgos o decisiones quedan pendientes.

## Fuente de verdad

La Especificación del Dominio v3.2 congelada ubicada en:

`docs/domain/Especificacion_Dominio_Yerbas_Coronado_v3.2_CONGELADA.docx`

es la fuente de verdad para todas las reglas de negocio.

Antes de implementar una funcionalidad de dominio, consultar las secciones relevantes de ese documento.

Las decisiones aprobadas que completen aspectos no definidos explícitamente
por la especificación se registran en:

`docs/technical/DECISIONES_IMPLEMENTACION.md`

Estas decisiones pueden completar huecos de implementación, pero nunca
contradecir la Especificación del Dominio.