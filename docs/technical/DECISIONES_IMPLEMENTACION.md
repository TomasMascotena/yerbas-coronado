# Decisiones de implementación

Este documento registra decisiones aprobadas durante el desarrollo que
completan aspectos no definidos explícitamente por la Especificación del
Dominio v3.2 congelada.

Estas decisiones no pueden contradecir la Especificación del Dominio.

## DI-001 — Estado inicial de Producto

**Estado:** Aprobada

Cuando se crea un nuevo Producto, su atributo `activo` tendrá valor
`True` por defecto.

La Especificación del Dominio establece que todo Producto debe encontrarse
Activo o Inactivo, pero no define cuál debe ser el estado inicial.

Decisión adoptada:

`activo = True`

## DI-002 — Relación incremental entre MovimientoInventario y Pedido

**Estado:** Aprobada

El modelo de dominio final establece que `MovimientoInventario` posee una
relación opcional con `Pedido`. Durante el Módulo 2A, la entidad `Pedido`
todavía no existe.

Decisión adoptada:

- `MovimientoInventario` se implementa temporalmente sin el campo `pedido`;
- no se almacena un `pedido_id` sin clave foránea ni se crea una entidad
  provisional;
- la clave foránea nullable se agregará mediante una migración posterior al
  implementar `Pedido`;
- `VENTA_PEDIDO` y `CANCELACION_PEDIDO` permanecen definidos en la enumeración,
  pero no están disponibles mediante los servicios públicos del Módulo 2A.

Esta omisión temporal no modifica el modelo de dominio final.

## DI-003 — Inventario de Productos inactivos

**Estado:** Aprobada

Las operaciones administrativas de Inventario están permitidas sobre
Productos activos e inactivos.

La inactivación de un Producto afecta su disponibilidad comercial, pero no
elimina ni congela automáticamente su Inventario. Los movimientos de
Inventario no modifican el atributo `activo` del Producto.

## DI-004 — Configuración regional de la administración

**Estado:** Aprobada

La administración de Yerbas Coronado utiliza la siguiente configuración
regional:

- `LANGUAGE_CODE = "es-ar"`;
- `TIME_ZONE = "America/Argentina/Buenos_Aires"`;
- `USE_I18N = True`;
- `USE_TZ = True`.

Las fechas se almacenan con soporte timezone-aware y Django realiza su
presentación en la zona horaria configurada.

## DI-005 — Persistencia e identificación del Carrito

**Estado:** Aprobada

`Carrito` e `ItemCarrito` se persisten en PostgreSQL. La sesión estándar de
Django identifica al Visitante mediante su `session_key`, pero no almacena la
información comercial del Carrito ni se representa mediante una clave foránea
obligatoria a la tabla de sesiones.

Una sesión posee como máximo un Carrito persistido y la restricción única
sobre `session_key` constituye la defensa definitiva ante creaciones
concurrentes.

## DI-006 — Expiración diferida del Carrito

**Estado:** Aprobada

Un Carrito expira cuando transcurren seis horas o más desde su
`ultima_actividad`. La expiración se aplica de forma diferida al acceder al
Carrito: se elimina junto con sus Items y no se modifica Inventario ni se crean
Movimientos de Inventario.

Las consultas de solo lectura no renuevan el plazo. `ultima_actividad` se
actualiza únicamente cuando una operación modifica efectivamente el Carrito.

## DI-007 — Cálculos derivados del Carrito

**Estado:** Aprobada

La Escala de Precio se calcula dinámicamente utilizando la cantidad total de
unidades del Carrito. Todos los Items comparten una única escala global y el
precio aplicado a cada uno proviene exclusivamente del snapshot correspondiente
almacenado en el Item.

Los subtotales, el importe total y la escala aplicada son valores derivados y
no se persisten. Todos los cálculos monetarios utilizan `Decimal`.

Un Carrito vacío no posee una escala aplicable y su importe total es
`Decimal("0.00")`.

## DI-008 — Interfaz pública del Carrito

**Estado:** Aprobada

La interfaz pública del Carrito utiliza la sesión anónima de Django únicamente
para identificar al Visitante. La `session_key` se crea ante el primer intento
explícito de agregar un Producto; las lecturas del catálogo y del Carrito no
crean sesiones ni renuevan `ultima_actividad`.

Las operaciones de agregado, actualización, eliminación y vaciado utilizan
exclusivamente POST, protección CSRF y el patrón POST/Redirect/GET. La capa web
delega las reglas comerciales a los servicios existentes de `cart`.

El encabezado público muestra la cantidad total de unidades mediante el resumen
canónico del Carrito, evaluado de forma diferida y reutilizado dentro de una
misma solicitud. Los precios aplicados, subtotales, escala e importe total
provienen exclusivamente de `cart.pricing`.

Los Productos modificados después de incorporarse conservan sus snapshots de
precio. Un Producto inactivo o con stock cero permanece visible en el Carrito,
no puede actualizar su cantidad y puede eliminarse. Si el stock positivo
observado es inferior a la cantidad de la línea, se informa que la
disponibilidad cambió sin exponer cantidades exactas.

La ausencia de Inventario en un Producto se trata como una violación
estructural mediante `ProductoSinInventario`, compatible como subclase de
`ProductoNoDisponible`, pero diferenciable por la capa web para producir un
error interno sanitizado.

La preparación del Carrito no reserva ni modifica Inventario y no crea
Movimientos de Inventario. La disponibilidad observada se valida de forma
preventiva mediante los servicios y deberá volver a validarse al generar el
Pedido.

## DI-009 — Versión e idempotencia del Checkout

**Estado:** Aprobada

`Carrito.token_checkout` identifica una versión concreta de su contenido y
rota únicamente ante mutaciones efectivas. Al confirmar, se copia a
`Pedido.token_idempotencia`, cuya unicidad permite resolver reintentos. El
Pedido conserva además una huella SHA-256 de la sesión con separador de
dominio; nunca persiste la clave de sesión sin procesar.

## DI-010 — Expiración bajo bloqueo

**Estado:** Aprobada

La vigencia del Carrito se evalúa con una hora obtenida después de adquirir
su bloqueo de fila. Cuando un Carrito vencido debe eliminarse antes de
informar un error o iniciar una operación nueva, el borrado se confirma en un
límite transaccional independiente para impedir su resurrección por rollback.

## DI-011 — Capacidad de cantidades e importes históricos

**Estado:** Aprobada

Inventario, MovimientoInventario y la cantidad total del Pedido utilizan
enteros positivos de 64 bits. Los precios aplicados conservan precisión 12,2;
los subtotales de Detalle utilizan 22,2 y el importe total del Pedido 31,2.

## DI-012 — Identidad y datos actuales de Cliente

**Estado:** Aprobada

El DNI canónico contiene entre seis y ocho dígitos ASCII y admite en la entrada
solamente puntos, espacios ASCII y guiones como separadores. Cliente se
reutiliza por DNI y sus datos actuales corresponden al último Checkout que
confirma bajo bloqueo. Cada Pedido conserva snapshots independientes.

## DI-013 — Número público de Pedido

**Estado:** Aprobada

El número público tiene formato `YC-XXXXXXXXXXXX`, utiliza el alfabeto Crockford
`0123456789ABCDEFGHJKMNPQRSTVWXYZ` y se genera con `secrets.choice`. Su
unicidad está protegida por una constraint nombrada y se realizan hasta cinco
intentos aislados mediante savepoints.

## DI-014 — Movimientos de Inventario asociados a Pedidos

**Estado:** Aprobada

`MovimientoInventario.pedido` completa la relación opcional prevista por el
dominio. `VENTA_PEDIDO` y `CANCELACION_PEDIDO` exigen Pedido; los movimientos
administrativos exigen su ausencia. Orders adquiere los bloqueos y funciones
internas específicas de Inventory actualizan stock y crean el movimiento como
una sola operación dentro de la transacción exterior.

## DI-015 — Estados, cancelación e inmutabilidad histórica

**Estado:** Aprobada

El Pedido nace PENDIENTE y solo admite las transiciones terminales a ENTREGADO
o CANCELADO. La cancelación repone stock según los movimientos VENTA_PEDIDO,
contrastados con los Detalles, y crea movimientos compensatorios. Los modelos
históricos rechazan cambios y borrados por instancia; operaciones masivas o SQL
privilegiado pueden omitir esas guardas Python y quedan fuera de los caminos
funcionales autorizados.

## DI-016 — Checkout público, confirmación y WhatsApp

**Estado:** Aprobada

La confirmación pública de un Pedido se autoriza combinando su
`numero_pedido` con la `huella_sesion_origen` calculada a partir de la sesión
actual. El `token_checkout` se transmite únicamente como campo oculto del POST
y no forma parte de ninguna URL.

La regla general es que GET y HEAD no produzcan escrituras. Se conserva como
excepción explícita y deliberada la eliminación diferida de un Carrito vencido
durante su acceso, según DI-006.

Las URLs propias de la aplicación no incluyen DNI, teléfono ni dirección. Se
autoriza como excepción técnica que teléfono y dirección formen parte
únicamente del parámetro externo `text`, correctamente codificado, de un enlace
`wa.me`. El DNI no se muestra en la confirmación ni se incorpora al mensaje de
WhatsApp.

El número comercial de WhatsApp se obtiene de la configuración del entorno. Si
está ausente o no posee el formato canónico aprobado, el Pedido y su página de
confirmación permanecen disponibles, pero no se muestra el enlace de WhatsApp.

La interfaz puede utilizar JavaScript como mejora progresiva para mostrar u
ocultar los campos de Dirección de Envío. El formulario completo permanece
utilizable sin JavaScript y toda validación definitiva continúa en el servidor.
