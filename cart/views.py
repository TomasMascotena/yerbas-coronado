from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST, require_safe

from cart.context_processors import obtener_resumen_para_request
from cart.exceptions import (
    CantidadCarritoInvalida,
    CarritoNoPerteneceALaSesion,
    ItemCarritoNoEncontrado,
    ProductoNoDisponible,
    ProductoSinInventario,
    SesionNoDisponible,
    StockInsuficienteParaCarrito,
)
from cart.forms import EstablecerCantidadItemForm
from cart.pricing import EscalaPrecio
from cart.services import (
    agregar_producto as agregar_producto_servicio,
    eliminar_item as eliminar_item_servicio,
    establecer_cantidad_item,
    vaciar_carrito,
)
from cart.session import asegurar_session_key
from catalog.models import Producto


ETIQUETAS_ESCALA = {
    EscalaPrecio.UNITARIO: "Precio unitario",
    EscalaPrecio.DESDE_3: "Precio desde 3 unidades",
    EscalaPrecio.DESDE_20: "Precio desde 20 unidades",
}


def _error_estructural_producto_sin_inventario(error):
    raise RuntimeError(
        "Un Producto del Carrito debe poseer exactamente un Inventario."
    ) from error


def _construir_lineas_presentacion(resumen):
    productos = {
        producto.pk: producto
        for producto in Producto.objects.filter(
            pk__in=(linea.producto_id for linea in resumen.lineas)
        ).select_related("inventario")
    }
    lineas = []
    for linea in resumen.lineas:
        producto = productos.get(linea.producto_id)
        if producto is None:
            raise RuntimeError(
                "Un ItemCarrito debe conservar su Producto relacionado."
            )
        try:
            inventario = producto.inventario
        except Producto.inventario.RelatedObjectDoesNotExist as error:
            _error_estructural_producto_sin_inventario(error)

        if not producto.activo:
            estado = "Producto no disponible"
            puede_actualizar = False
        elif inventario.cantidad_disponible == 0:
            estado = "Sin Stock"
            puede_actualizar = False
        elif inventario.cantidad_disponible < linea.cantidad:
            estado = "La disponibilidad cambió"
            puede_actualizar = True
        else:
            estado = "Disponible"
            puede_actualizar = True

        lineas.append(
            {
                "calculo": linea,
                "producto": producto,
                "estado": estado,
                "puede_actualizar": puede_actualizar,
                "formulario_cantidad": EstablecerCantidadItemForm(
                    initial={"cantidad": linea.cantidad},
                    auto_id=f"id_%s_{linea.item_id}",
                ),
            }
        )
    return lineas


def _session_key_existente(request):
    return request.session.session_key


@require_safe
def detalle(request):
    resumen = obtener_resumen_para_request(request)
    return render(
        request,
        "cart/carrito_detail.html",
        {
            "resumen": resumen,
            "lineas": _construir_lineas_presentacion(resumen),
            "escala_aplicada": ETIQUETAS_ESCALA.get(
                resumen.escala_aplicada
            ),
        },
    )


@require_POST
def agregar_producto(request, producto_id):
    try:
        session_key = asegurar_session_key(request.session)
        agregar_producto_servicio(
            session_key=session_key,
            producto_id=producto_id,
            cantidad=1,
        )
    except ProductoSinInventario as error:
        _error_estructural_producto_sin_inventario(error)
    except ProductoNoDisponible:
        messages.error(request, "El Producto no está disponible.")
    except StockInsuficienteParaCarrito:
        messages.error(
            request,
            "La cantidad solicitada no se encuentra disponible.",
        )
    except (CantidadCarritoInvalida, SesionNoDisponible) as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    else:
        messages.success(request, "Producto agregado al Carrito.")
    return redirect("cart:detalle")


@require_POST
def establecer_cantidad(request, item_id):
    session_key = _session_key_existente(request)
    if session_key is None:
        messages.error(request, "El artículo no está disponible en tu Carrito.")
        return redirect("cart:detalle")

    formulario = EstablecerCantidadItemForm(request.POST)
    if not formulario.is_valid():
        messages.error(
            request,
            "Ingresá una cantidad entera mayor que cero.",
        )
        return redirect("cart:detalle")

    try:
        establecer_cantidad_item(
            session_key=session_key,
            item_id=item_id,
            cantidad=formulario.cleaned_data["cantidad"],
        )
    except ProductoSinInventario as error:
        _error_estructural_producto_sin_inventario(error)
    except ProductoNoDisponible:
        messages.error(request, "El Producto no está disponible.")
    except StockInsuficienteParaCarrito:
        messages.error(
            request,
            "La cantidad solicitada no se encuentra disponible.",
        )
    except (ItemCarritoNoEncontrado, CarritoNoPerteneceALaSesion):
        messages.error(request, "El artículo no está disponible en tu Carrito.")
    except (CantidadCarritoInvalida, SesionNoDisponible) as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    else:
        messages.success(request, "Cantidad del Carrito verificada.")
    return redirect("cart:detalle")


@require_POST
def eliminar_item(request, item_id):
    session_key = _session_key_existente(request)
    if session_key is None:
        messages.error(request, "El artículo no está disponible en tu Carrito.")
        return redirect("cart:detalle")

    try:
        eliminar_item_servicio(session_key=session_key, item_id=item_id)
    except (ItemCarritoNoEncontrado, CarritoNoPerteneceALaSesion):
        messages.error(request, "El artículo no está disponible en tu Carrito.")
    except SesionNoDisponible as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    else:
        messages.success(request, "Producto eliminado del Carrito.")
    return redirect("cart:detalle")


@require_POST
def vaciar(request):
    session_key = _session_key_existente(request)
    if session_key is None:
        messages.info(request, "El Carrito ya está vacío.")
        return redirect("cart:detalle")

    try:
        vaciar_carrito(session_key)
    except SesionNoDisponible as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    messages.info(request, "El Carrito quedó vacío.")
    return redirect("cart:detalle")
