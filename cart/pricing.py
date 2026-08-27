from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum


CENTAVO = Decimal("0.01")
CERO_MONETARIO = Decimal("0.00")


class EstadoPrecioCarritoInvalido(ValueError):
    pass


class EscalaPrecio(Enum):
    UNITARIO = "UNITARIO"
    DESDE_3 = "DESDE_3"
    DESDE_20 = "DESDE_20"


@dataclass(frozen=True)
class LineaCalculadaCarrito:
    item_id: int
    producto_id: int
    cantidad: int
    precio_aplicado: Decimal
    subtotal: Decimal


@dataclass(frozen=True)
class ResumenCarrito:
    carrito_id: int | None
    cantidad_lineas: int
    cantidad_total_unidades: int
    escala_aplicada: EscalaPrecio | None
    lineas: tuple[LineaCalculadaCarrito, ...]
    importe_total: Decimal


def determinar_escala(cantidad_total):
    _validar_entero(cantidad_total, permite_cero=True)
    if cantidad_total == 0:
        return None
    if cantidad_total < 3:
        return EscalaPrecio.UNITARIO
    if cantidad_total < 20:
        return EscalaPrecio.DESDE_3
    return EscalaPrecio.DESDE_20


def seleccionar_precio_snapshot(item, escala):
    atributo_por_escala = {
        EscalaPrecio.UNITARIO: "precio_unitario_snapshot",
        EscalaPrecio.DESDE_3: "precio_desde_3_snapshot",
        EscalaPrecio.DESDE_20: "precio_desde_20_snapshot",
    }
    try:
        atributo = atributo_por_escala[escala]
    except (KeyError, TypeError) as error:
        raise EstadoPrecioCarritoInvalido(
            "La escala de precio no es válida."
        ) from error

    precio = getattr(item, atributo)
    _validar_precio(precio)
    return _cuantizar(precio)


def calcular_subtotal(*, precio_aplicado, cantidad):
    _validar_precio(precio_aplicado)
    _validar_entero(cantidad, permite_cero=False)
    return _cuantizar(precio_aplicado * cantidad)


def calcular_resumen(*, carrito_id, items):
    items_materializados = tuple(items)
    items_ordenados = tuple(
        sorted(items_materializados, key=lambda item: item.pk)
    )
    for item in items_ordenados:
        _validar_item(item)

    cantidad_total = sum(item.cantidad for item in items_ordenados)
    escala = determinar_escala(cantidad_total)
    if escala is None:
        return ResumenCarrito(
            carrito_id=carrito_id,
            cantidad_lineas=0,
            cantidad_total_unidades=0,
            escala_aplicada=None,
            lineas=(),
            importe_total=CERO_MONETARIO,
        )

    lineas = tuple(
        _calcular_linea(item=item, escala=escala)
        for item in items_ordenados
    )
    importe_total = _cuantizar(
        sum((linea.subtotal for linea in lineas), CERO_MONETARIO)
    )
    return ResumenCarrito(
        carrito_id=carrito_id,
        cantidad_lineas=len(lineas),
        cantidad_total_unidades=cantidad_total,
        escala_aplicada=escala,
        lineas=lineas,
        importe_total=importe_total,
    )


def _calcular_linea(*, item, escala):
    precio = seleccionar_precio_snapshot(item, escala)
    return LineaCalculadaCarrito(
        item_id=item.pk,
        producto_id=item.producto_id,
        cantidad=item.cantidad,
        precio_aplicado=precio,
        subtotal=calcular_subtotal(
            precio_aplicado=precio,
            cantidad=item.cantidad,
        ),
    )


def _validar_item(item):
    _validar_entero(item.cantidad, permite_cero=False)
    for atributo in (
        "precio_unitario_snapshot",
        "precio_desde_3_snapshot",
        "precio_desde_20_snapshot",
    ):
        _validar_precio(getattr(item, atributo))


def _validar_entero(valor, *, permite_cero):
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise EstadoPrecioCarritoInvalido(
            "La cantidad debe representarse mediante un entero."
        )
    minimo = 0 if permite_cero else 1
    if valor < minimo:
        raise EstadoPrecioCarritoInvalido("La cantidad no es válida.")


def _validar_precio(precio):
    if not isinstance(precio, Decimal):
        raise EstadoPrecioCarritoInvalido(
            "Los precios deben representarse mediante Decimal."
        )
    try:
        es_positivo = precio > 0
    except InvalidOperation as error:
        raise EstadoPrecioCarritoInvalido(
            "El precio snapshot no es válido."
        ) from error
    if not es_positivo:
        raise EstadoPrecioCarritoInvalido(
            "El precio snapshot debe ser mayor que cero."
        )


def _cuantizar(valor):
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)
