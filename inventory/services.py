from django.db import connection, transaction
from django.db.transaction import TransactionManagementError

from inventory.exceptions import (
    CantidadMovimientoInvalida,
    CapacidadInventarioExcedida,
    ObservacionObligatoria,
    StockInsuficiente,
)


MAX_BIGINT_POSITIVO = 9_223_372_036_854_775_807
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
        if cantidad_nueva > MAX_BIGINT_POSITIVO:
            raise CapacidadInventarioExcedida(
                "La operación excede la capacidad del Inventario."
            )
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
    if cantidad > MAX_BIGINT_POSITIVO:
        raise CapacidadInventarioExcedida(
            "La cantidad excede la capacidad de almacenamiento."
        )


def _normalizar_observacion(observacion, *, obligatoria):
    normalizada = "" if observacion is None else observacion.strip()
    if obligatoria and not normalizada:
        raise ObservacionObligatoria(
            "Los ajustes requieren una observación no vacía."
        )
    return normalizada


def _aplicar_venta_pedido_sobre_inventario_bloqueado(
    *, inventario, pedido, cantidad
):
    _exigir_transaccion_activa()
    _validar_cantidad(cantidad)
    if cantidad > inventario.cantidad_disponible:
        raise StockInsuficiente(
            "La cantidad solicitada supera el stock disponible."
        )
    inventario.cantidad_disponible -= cantidad
    inventario.full_clean()
    inventario.save(update_fields=("cantidad_disponible",))
    return _crear_movimiento_pedido(
        inventario=inventario,
        pedido=pedido,
        cantidad=cantidad,
        tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
    )


def _aplicar_cancelacion_pedido_sobre_inventario_bloqueado(
    *, inventario, pedido, cantidad
):
    _exigir_transaccion_activa()
    _validar_cantidad(cantidad)
    cantidad_nueva = inventario.cantidad_disponible + cantidad
    if cantidad_nueva > MAX_BIGINT_POSITIVO:
        raise CapacidadInventarioExcedida(
            "La restitución excede la capacidad del Inventario."
        )
    inventario.cantidad_disponible = cantidad_nueva
    inventario.full_clean()
    inventario.save(update_fields=("cantidad_disponible",))
    return _crear_movimiento_pedido(
        inventario=inventario,
        pedido=pedido,
        cantidad=cantidad,
        tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
    )


def _crear_movimiento_pedido(
    *, inventario, pedido, cantidad, tipo_movimiento
):
    movimiento = MovimientoInventario(
        inventario=inventario,
        pedido=pedido,
        tipo_movimiento=tipo_movimiento,
        cantidad=cantidad,
        observacion="",
    )
    movimiento.full_clean()
    movimiento.save()
    return movimiento


def _exigir_transaccion_activa():
    if not connection.in_atomic_block:
        raise TransactionManagementError(
            "Los movimientos de Pedido requieren una transacción exterior."
        )
