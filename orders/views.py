from dataclasses import dataclass

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from cart.pricing import EstadoPrecioCarritoInvalido, calcular_resumen
from cart.services import obtener_carrito_vigente
from orders.exceptions import (
    CarritoExpirado,
    CarritoInexistente,
    CarritoModificado,
    CarritoVacio,
    DatosCompradorInvalidos,
    DireccionEnvioInvalida,
    GeneracionNumeroPedidoAgotada,
    ItemCarritoCorrupto,
    ModalidadEntregaInvalida,
    PricingCorrupto,
    ProductoNoDisponible,
    ProductoSinInventario,
    StockInsuficienteParaPedido,
    TokenIdempotenciaInvalido,
)
from orders.forms import CheckoutForm
from orders.models import ModalidadEntrega, Pedido
from orders.services import (
    crear_pedido_desde_carrito,
    obtener_pedido_para_confirmacion,
)
from orders.whatsapp import construir_enlace_whatsapp, construir_mensaje_whatsapp


@dataclass(frozen=True)
class ContextoCheckout:
    carrito: object
    resumen: object
    lineas: tuple


def _cargar_checkout(session_key):
    if session_key is None:
        return None
    carrito = obtener_carrito_vigente(session_key)
    if carrito is None:
        return None
    items = list(
        carrito.items.select_related("producto").order_by("pk")
    )
    if not items:
        return None
    try:
        resumen = calcular_resumen(carrito_id=carrito.pk, items=items)
    except EstadoPrecioCarritoInvalido as error:
        raise RuntimeError(
            "No puede presentarse un Carrito con precios inválidos."
        ) from error
    calculos = {linea.item_id: linea for linea in resumen.lineas}
    if set(calculos) != {item.pk for item in items}:
        raise RuntimeError("El resumen del Carrito no coincide con sus Items.")
    lineas = tuple(
        {"item": item, "calculo": calculos[item.pk]} for item in items
    )
    return ContextoCheckout(carrito=carrito, resumen=resumen, lineas=lineas)


def _marcar_primer_error(formulario):
    for nombre in formulario.errors:
        if nombre in formulario.fields:
            formulario.fields[nombre].widget.attrs["autofocus"] = True
            return


def _render_checkout(request, *, formulario, contexto):
    respuesta = render(
        request,
        "orders/checkout.html",
        {
            "form": formulario,
            "resumen": contexto.resumen,
            "lineas": contexto.lineas,
            "cart_resumen_global": contexto.resumen,
        },
    )
    respuesta["Referrer-Policy"] = "no-referrer"
    return respuesta


def _redirigir_carrito(request, mensaje=None):
    if mensaje is not None and request.session.session_key is not None:
        messages.error(request, mensaje)
    return redirect("cart:detalle")


@never_cache
@require_http_methods(("GET", "HEAD", "POST"))
def checkout(request):
    session_key = request.session.session_key
    if request.method == "POST":
        return _procesar_checkout(request, session_key)

    contexto = _cargar_checkout(session_key)
    if contexto is None:
        return _redirigir_carrito(request)
    formulario = CheckoutForm(
        initial={"token_checkout": contexto.carrito.token_checkout}
    )
    return _render_checkout(
        request,
        formulario=formulario,
        contexto=contexto,
    )


def _procesar_checkout(request, session_key):
    if session_key is None:
        return _redirigir_carrito(request)

    formulario = CheckoutForm(request.POST)
    if not formulario.is_valid():
        contexto = _cargar_checkout(session_key)
        if contexto is None:
            return _redirigir_carrito(request)
        _marcar_primer_error(formulario)
        return _render_checkout(
            request,
            formulario=formulario,
            contexto=contexto,
        )

    try:
        resultado = crear_pedido_desde_carrito(
            session_key=session_key,
            token_idempotencia=formulario.cleaned_data["token_checkout"],
            datos_comprador=formulario.datos_comprador(),
            modalidad_entrega=formulario.cleaned_data["modalidad_entrega"],
            direccion_envio=formulario.datos_direccion_envio(),
            observaciones=formulario.cleaned_data["observaciones"],
        )
    except (DatosCompradorInvalidos, DireccionEnvioInvalida, ModalidadEntregaInvalida):
        formulario.add_error(
            None,
            "Revisá los datos ingresados antes de confirmar el Pedido.",
        )
        contexto = _cargar_checkout(session_key)
        if contexto is None:
            return _redirigir_carrito(request)
        _marcar_primer_error(formulario)
        return _render_checkout(
            request,
            formulario=formulario,
            contexto=contexto,
        )
    except (CarritoInexistente, CarritoVacio, CarritoExpirado):
        return _redirigir_carrito(
            request,
            "El Carrito ya no está disponible para confirmar.",
        )
    except CarritoModificado:
        return _redirigir_carrito(
            request,
            "El contenido del Carrito cambió. Revisalo antes de confirmar.",
        )
    except ProductoNoDisponible:
        return _redirigir_carrito(
            request,
            "Uno de los Productos ya no está disponible.",
        )
    except StockInsuficienteParaPedido:
        return _redirigir_carrito(
            request,
            "La disponibilidad cambió. Revisá el Carrito antes de confirmar.",
        )
    except TokenIdempotenciaInvalido:
        return _redirigir_carrito(
            request,
            "No fue posible validar la confirmación. Revisá el Carrito.",
        )
    except (ProductoSinInventario, PricingCorrupto, ItemCarritoCorrupto) as error:
        raise RuntimeError(
            "La estructura del Carrito impide generar el Pedido."
        ) from error
    except GeneracionNumeroPedidoAgotada as error:
        raise RuntimeError("No fue posible generar el Pedido.") from error

    return redirect(
        "orders:confirmacion",
        numero_pedido=resultado.pedido.numero_pedido,
    )


def _obtener_direccion_historica(pedido):
    try:
        direccion = pedido.direccion_envio
    except Pedido.direccion_envio.RelatedObjectDoesNotExist:
        direccion = None
    if pedido.modalidad_entrega == ModalidadEntrega.ENVIO_DOMICILIO:
        if direccion is None:
            raise RuntimeError("El Pedido de envío no posee Dirección histórica.")
    elif direccion is not None:
        raise RuntimeError("El Pedido de retiro posee una Dirección inesperada.")
    return direccion


@never_cache
@require_safe
def confirmacion(request, numero_pedido):
    pedido = obtener_pedido_para_confirmacion(
        numero_pedido=numero_pedido,
        session_key=request.session.session_key,
    )
    if pedido is None:
        raise Http404

    direccion = _obtener_direccion_historica(pedido)
    detalles = tuple(pedido.detalles_confirmacion)
    if not detalles:
        raise RuntimeError("El Pedido no posee Detalles históricos.")
    mensaje_whatsapp = construir_mensaje_whatsapp(
        pedido=pedido,
        detalles=detalles,
        direccion_envio=direccion,
    )
    enlace_whatsapp = construir_enlace_whatsapp(
        numero_comercial=settings.WHATSAPP_BUSINESS_NUMBER,
        mensaje=mensaje_whatsapp,
    )
    respuesta = render(
        request,
        "orders/confirmacion.html",
        {
            "pedido": pedido,
            "detalles": detalles,
            "direccion": direccion,
            "enlace_whatsapp": enlace_whatsapp,
        },
    )
    respuesta["Referrer-Policy"] = "no-referrer"
    return respuesta
