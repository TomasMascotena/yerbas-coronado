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
