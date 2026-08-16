from django.db import transaction

from inventory.exceptions import (
    CantidadMovimientoInvalida,
    ObservacionObligatoria,
    StockInsuficiente,
)
from inventory.models import (
    Inventario,
    MovimientoInventario,
    TipoMovimientoInventario,
)


_EFECTO_POR_TIPO_ADMINISTRATIVO = {
    TipoMovimientoInventario.INGRESO_MERCADERIA: 1,
    TipoMovimientoInventario.VENTA_PRESENCIAL: -1,
    TipoMovimientoInventario.AJUSTE_POSITIVO: 1,
    TipoMovimientoInventario.AJUSTE_NEGATIVO: -1,
}


def registrar_ingreso_mercaderia(
    *, inventario_id: int, cantidad: int, observacion: str | None = None
):
    return _registrar_movimiento(
        inventario_id=inventario_id,
        cantidad=cantidad,
        tipo_movimiento=TipoMovimientoInventario.INGRESO_MERCADERIA,
        observacion=observacion,
    )


def registrar_venta_presencial(
    *, inventario_id: int, cantidad: int, observacion: str | None = None
):
    return _registrar_movimiento(
        inventario_id=inventario_id,
        cantidad=cantidad,
        tipo_movimiento=TipoMovimientoInventario.VENTA_PRESENCIAL,
        observacion=observacion,
    )


def registrar_ajuste_positivo(
    *, inventario_id: int, cantidad: int, observacion: str | None
):
    return _registrar_movimiento(
        inventario_id=inventario_id,
        cantidad=cantidad,
        tipo_movimiento=TipoMovimientoInventario.AJUSTE_POSITIVO,
        observacion=observacion,
        observacion_obligatoria=True,
    )


def registrar_ajuste_negativo(
    *, inventario_id: int, cantidad: int, observacion: str | None
):
    return _registrar_movimiento(
        inventario_id=inventario_id,
        cantidad=cantidad,
        tipo_movimiento=TipoMovimientoInventario.AJUSTE_NEGATIVO,
        observacion=observacion,
        observacion_obligatoria=True,
    )


@transaction.atomic
def _registrar_movimiento(
    *,
    inventario_id: int,
    cantidad: int,
    tipo_movimiento: TipoMovimientoInventario,
    observacion: str | None,
    observacion_obligatoria: bool = False,
):
    _validar_cantidad(cantidad)
    observacion_normalizada = _normalizar_observacion(
        observacion,
        obligatoria=observacion_obligatoria,
    )
    try:
        efecto = _EFECTO_POR_TIPO_ADMINISTRATIVO[tipo_movimiento]
    except KeyError as error:
        raise ValueError(
            "El tipo no está habilitado en los servicios administrativos."
        ) from error

    inventario = Inventario.objects.select_for_update().get(pk=inventario_id)

    if efecto == 1:
        cantidad_nueva = inventario.cantidad_disponible + cantidad
    else:
        if cantidad > inventario.cantidad_disponible:
            raise StockInsuficiente(
                "La cantidad solicitada supera el stock disponible."
            )
        cantidad_nueva = inventario.cantidad_disponible - cantidad

    inventario.cantidad_disponible = cantidad_nueva
    inventario.full_clean()
    inventario.save(update_fields=("cantidad_disponible",))

    movimiento = MovimientoInventario(
        inventario=inventario,
        tipo_movimiento=tipo_movimiento,
        cantidad=cantidad,
        observacion=observacion_normalizada,
    )
    movimiento.full_clean()
    movimiento.save()
    return movimiento


def _validar_cantidad(cantidad):
    if (
        isinstance(cantidad, bool)
        or not isinstance(cantidad, int)
        or cantidad <= 0
    ):
        raise CantidadMovimientoInvalida(
            "La cantidad debe ser un entero estrictamente positivo."
        )


def _normalizar_observacion(observacion, *, obligatoria):
    normalizada = "" if observacion is None else observacion.strip()
    if obligatoria and not normalizada:
        raise ObservacionObligatoria(
            "Los ajustes requieren una observación no vacía."
        )
    return normalizada
