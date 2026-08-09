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
