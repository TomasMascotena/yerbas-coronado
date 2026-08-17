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
