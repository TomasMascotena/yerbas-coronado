from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from cart.exceptions import (
    CantidadCarritoInvalida,
    CarritoNoPerteneceALaSesion,
    ItemCarritoNoEncontrado,
    ProductoNoDisponible,
    ProductoSinInventario,
    SesionNoDisponible,
    StockInsuficienteParaCarrito,
)
from cart.models import Carrito, ItemCarrito
from cart.pricing import calcular_resumen
from catalog.models import Producto
from inventory.models import Inventario


DURACION_CARRITO = timedelta(hours=6)


def _ahora():
    return timezone.now()


def _validar_session_key(session_key):
    if (
        not isinstance(session_key, str)
        or not session_key.strip()
        or len(session_key) > 40
    ):
        raise SesionNoDisponible("La sesión no posee una clave válida.")


def _validar_cantidad(cantidad):
    if (
        isinstance(cantidad, bool)
        or not isinstance(cantidad, int)
        or cantidad <= 0
    ):
        raise CantidadCarritoInvalida(
            "La cantidad debe ser un entero estrictamente positivo."
        )


def _esta_expirado(carrito, ahora):
    return ahora >= carrito.ultima_actividad + DURACION_CARRITO


def _obtener_carrito_vigente_bloqueado(session_key, *, ahora):
    carrito = (
        Carrito.objects.select_for_update()
        .filter(session_key=session_key)
        .first()
    )
    if carrito is None:
        return None
    if _esta_expirado(carrito, ahora):
        carrito.delete()
        return None
    return carrito


@transaction.atomic
def obtener_carrito_vigente(session_key):
    _validar_session_key(session_key)
    return _obtener_carrito_vigente_bloqueado(
        session_key,
        ahora=_ahora(),
    )


@transaction.atomic
def obtener_o_crear_carrito(session_key):
    _validar_session_key(session_key)
    ahora = _ahora()
    carrito = _obtener_carrito_vigente_bloqueado(
        session_key,
        ahora=ahora,
    )
    if carrito is not None:
        return carrito

    try:
        with transaction.atomic():
            return Carrito.objects.create(
                session_key=session_key,
                creado_en=ahora,
                ultima_actividad=ahora,
            )
    except IntegrityError:
        return Carrito.objects.select_for_update().get(
            session_key=session_key
        )


def _obtener_producto_e_inventario_bloqueados(producto_id):
    try:
        producto = Producto.objects.select_for_update().get(pk=producto_id)
    except Producto.DoesNotExist as error:
        raise ProductoNoDisponible("El Producto no existe.") from error

    if not producto.activo:
        raise ProductoNoDisponible("El Producto se encuentra inactivo.")

    try:
        inventario = Inventario.objects.select_for_update().get(
            producto_id=producto.pk
        )
    except Inventario.DoesNotExist as error:
        raise ProductoSinInventario(
            "El Producto no posee Inventario."
        ) from error
    return producto, inventario


def _validar_stock(inventario, cantidad_final):
    if cantidad_final > inventario.cantidad_disponible:
        raise StockInsuficienteParaCarrito(
            "La cantidad solicitada supera el stock observado."
        )


def _actualizar_actividad(carrito, *, ahora):
    carrito.ultima_actividad = ahora
    carrito.save(update_fields=("ultima_actividad",))


@transaction.atomic
def agregar_producto(*, session_key, producto_id, cantidad):
    _validar_session_key(session_key)
    _validar_cantidad(cantidad)
    carrito = obtener_o_crear_carrito(session_key)
    item = (
        ItemCarrito.objects.select_for_update()
        .filter(carrito=carrito, producto_id=producto_id)
        .first()
    )
    producto, inventario = _obtener_producto_e_inventario_bloqueados(
        producto_id
    )
    cantidad_final = cantidad if item is None else item.cantidad + cantidad
    _validar_stock(inventario, cantidad_final)

    if item is None:
        item = ItemCarrito(
            carrito=carrito,
            producto=producto,
            cantidad=cantidad,
            precio_unitario_snapshot=producto.precio_unitario,
            precio_desde_3_snapshot=producto.precio_desde_3,
            precio_desde_20_snapshot=producto.precio_desde_20,
        )
        item.full_clean()
        item.save()
    else:
        item.cantidad = cantidad_final
        item.full_clean()
        item.save(update_fields=("cantidad",))

    _actualizar_actividad(carrito, ahora=_ahora())
    return item


def _obtener_item_bloqueado(*, carrito, item_id):
    item = (
        ItemCarrito.objects.select_for_update()
        .filter(pk=item_id)
        .first()
    )
    if item is None:
        raise ItemCarritoNoEncontrado("El Item no existe.")
    if item.carrito_id != carrito.pk:
        raise CarritoNoPerteneceALaSesion(
            "El Item no pertenece al Carrito de la sesión."
        )
    return item


def establecer_cantidad_item(*, session_key, item_id, cantidad):
    _validar_session_key(session_key)
    _validar_cantidad(cantidad)
    carrito_no_disponible = False
    item_actualizado = None
    with transaction.atomic():
        carrito = _obtener_carrito_vigente_bloqueado(
            session_key,
            ahora=_ahora(),
        )
        if carrito is None:
            carrito_no_disponible = True
        else:
            item = _obtener_item_bloqueado(
                carrito=carrito,
                item_id=item_id,
            )
            producto, inventario = _obtener_producto_e_inventario_bloqueados(
                item.producto_id
            )
            _validar_stock(inventario, cantidad)

            if item.cantidad != cantidad:
                item.cantidad = cantidad
                item.save(update_fields=("cantidad",))
                _actualizar_actividad(carrito, ahora=_ahora())
            item_actualizado = item

    if carrito_no_disponible:
        raise ItemCarritoNoEncontrado("La sesión no posee ese Item.")
    return item_actualizado


def eliminar_item(*, session_key, item_id):
    _validar_session_key(session_key)
    carrito_no_disponible = False
    with transaction.atomic():
        carrito = _obtener_carrito_vigente_bloqueado(
            session_key,
            ahora=_ahora(),
        )
        if carrito is None:
            carrito_no_disponible = True
        else:
            item = _obtener_item_bloqueado(
                carrito=carrito,
                item_id=item_id,
            )
            item.delete()
            _actualizar_actividad(carrito, ahora=_ahora())

    if carrito_no_disponible:
        raise ItemCarritoNoEncontrado("La sesión no posee ese Item.")


@transaction.atomic
def vaciar_carrito(session_key):
    _validar_session_key(session_key)
    carrito = _obtener_carrito_vigente_bloqueado(
        session_key,
        ahora=_ahora(),
    )
    if carrito is None:
        return None

    eliminados, _ = carrito.items.all().delete()
    if eliminados:
        _actualizar_actividad(carrito, ahora=_ahora())
    return carrito


def obtener_resumen_carrito(session_key):
    _validar_session_key(session_key)
    carrito = Carrito.objects.filter(session_key=session_key).first()
    if carrito is None:
        return calcular_resumen(carrito_id=None, items=())

    if _esta_expirado(carrito, _ahora()):
        carrito = obtener_carrito_vigente(session_key)
        if carrito is None:
            return calcular_resumen(carrito_id=None, items=())

    items = list(
        ItemCarrito.objects.filter(carrito=carrito)
        .only(
            "id",
            "producto_id",
            "cantidad",
            "precio_unitario_snapshot",
            "precio_desde_3_snapshot",
            "precio_desde_20_snapshot",
        )
        .order_by("pk")
    )
    return calcular_resumen(carrito_id=carrito.pk, items=items)
