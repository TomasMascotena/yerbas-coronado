from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.http import url_has_allowed_host_and_scheme
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
from cart.forms import EstablecerCantidadCatalogoForm, EstablecerCantidadItemForm
from cart.pricing import EscalaPrecio
from cart.services import (
    agregar_producto as agregar_producto_servicio,
    eliminar_item as eliminar_item_servicio,
    establecer_cantidad_item,
    obtener_resumen_carrito,
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


def _destino_despues_de_post(request):
    destino = request.POST.get("next")
    if destino and url_has_allowed_host_and_scheme(
        destino,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destino
    return "cart:detalle"


def _es_solicitud_asincrona(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _producto_id_del_item_de_sesion(request, item_id):
    session_key = _session_key_existente(request)
    if session_key is None:
        return None
    resumen = obtener_resumen_carrito(session_key)
    linea = next(
        (linea for linea in resumen.lineas if linea.item_id == item_id),
        None,
    )
    return None if linea is None else linea.producto_id


def _respuesta_asincrona(
    request,
    *,
    producto_id,
    mensaje,
    ok=True,
    status=200,
):
    session_key = _session_key_existente(request)
    resumen = (
        obtener_resumen_para_request(request)
        if session_key is None
        else obtener_resumen_carrito(session_key)
    )
    datos = {
        "ok": ok,
        "message": mensaje,
        "cart_quantity": resumen.cantidad_total_unidades,
        "cart_label": (
            f"Ver carrito. {resumen.cantidad_total_unidades} "
            f"{'unidad' if resumen.cantidad_total_unidades == 1 else 'unidades'}"
        ),
    }

    if producto_id is not None:
        producto = (
            Producto.objects.filter(pk=producto_id)
            .select_related("inventario")
            .first()
        )
        if producto is None or not producto.activo:
            datos["remove_card"] = True
        else:
            try:
                producto.inventario
            except Producto.inventario.RelatedObjectDoesNotExist as error:
                _error_estructural_producto_sin_inventario(error)
            linea = next(
                (
                    linea
                    for linea in resumen.lineas
                    if linea.producto_id == producto.pk
                ),
                None,
            )
            datos["control_html"] = render_to_string(
                "cart/_control_producto_catalogo.html",
                {
                    "producto": producto,
                    "linea": linea,
                    "retorno_catalogo": request.POST.get("next", "/#productos"),
                },
                request=request,
            )
            datos["out_of_stock"] = producto.inventario.cantidad_disponible == 0

    return JsonResponse(datos, status=status)


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
    asincrona = _es_solicitud_asincrona(request)
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
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="Este producto ya no está disponible.",
                ok=False,
                status=409,
            )
        messages.error(request, "El Producto no está disponible.")
    except StockInsuficienteParaCarrito:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="No hay más unidades disponibles.",
                ok=False,
                status=409,
            )
        messages.error(
            request,
            "La cantidad solicitada no se encuentra disponible.",
        )
    except (CantidadCarritoInvalida, SesionNoDisponible) as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    else:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="Producto agregado al carrito.",
            )
        messages.success(request, "Producto agregado al Carrito.")
    return redirect(_destino_despues_de_post(request))


@require_POST
def establecer_cantidad(request, item_id):
    asincrona = _es_solicitud_asincrona(request)
    session_key = _session_key_existente(request)
    if session_key is None:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=None,
                mensaje="El artículo no está disponible en tu carrito.",
                ok=False,
                status=404,
            )
        messages.error(request, "El artículo no está disponible en tu Carrito.")
        return redirect(_destino_despues_de_post(request))

    producto_id = _producto_id_del_item_de_sesion(request, item_id)
    desde_catalogo = request.POST.get("catalog_control") == "1"
    clase_formulario = (
        EstablecerCantidadCatalogoForm
        if desde_catalogo
        else EstablecerCantidadItemForm
    )
    formulario = clase_formulario(request.POST)
    if not formulario.is_valid():
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="La cantidad solicitada no es válida.",
                ok=False,
                status=400,
            )
        messages.error(
            request,
            (
                "Ingresá una cantidad entera igual o mayor que cero."
                if desde_catalogo
                else "Ingresá una cantidad entera mayor que cero."
            ),
        )
        return redirect(_destino_despues_de_post(request))

    try:
        cantidad = formulario.cleaned_data["cantidad"]
        if desde_catalogo and cantidad == 0:
            eliminar_item_servicio(session_key=session_key, item_id=item_id)
        else:
            establecer_cantidad_item(
                session_key=session_key,
                item_id=item_id,
                cantidad=cantidad,
            )
    except ProductoSinInventario as error:
        _error_estructural_producto_sin_inventario(error)
    except ProductoNoDisponible:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="Este producto ya no está disponible.",
                ok=False,
                status=409,
            )
        messages.error(request, "El Producto no está disponible.")
    except StockInsuficienteParaCarrito:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="No hay stock suficiente para la cantidad solicitada.",
                ok=False,
                status=409,
            )
        messages.error(
            request,
            "La cantidad solicitada no se encuentra disponible.",
        )
    except (ItemCarritoNoEncontrado, CarritoNoPerteneceALaSesion):
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="El artículo no está disponible en tu carrito.",
                ok=False,
                status=404,
            )
        messages.error(request, "El artículo no está disponible en tu Carrito.")
    except (CantidadCarritoInvalida, SesionNoDisponible) as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    else:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje=(
                    "Producto quitado del carrito."
                    if desde_catalogo and cantidad == 0
                    else "Cantidad del carrito actualizada."
                ),
            )
        messages.success(
            request,
            (
                "Producto eliminado del Carrito."
                if desde_catalogo and cantidad == 0
                else "Cantidad del Carrito verificada."
            ),
        )
    return redirect(_destino_despues_de_post(request))


@require_POST
def eliminar_item(request, item_id):
    asincrona = _es_solicitud_asincrona(request)
    session_key = _session_key_existente(request)
    if session_key is None:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=None,
                mensaje="El artículo no está disponible en tu carrito.",
                ok=False,
                status=404,
            )
        messages.error(request, "El artículo no está disponible en tu Carrito.")
        return redirect(_destino_despues_de_post(request))

    producto_id = _producto_id_del_item_de_sesion(request, item_id)
    try:
        producto_id_eliminado = eliminar_item_servicio(
            session_key=session_key,
            item_id=item_id,
        )
    except (ItemCarritoNoEncontrado, CarritoNoPerteneceALaSesion):
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id,
                mensaje="El artículo no está disponible en tu carrito.",
                ok=False,
                status=404,
            )
        messages.error(request, "El artículo no está disponible en tu Carrito.")
    except SesionNoDisponible as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    else:
        if asincrona:
            return _respuesta_asincrona(
                request,
                producto_id=producto_id_eliminado,
                mensaje="Producto quitado del carrito.",
            )
        messages.success(request, "Producto eliminado del Carrito.")
    return redirect(_destino_despues_de_post(request))


@require_POST
def vaciar(request):
    session_key = _session_key_existente(request)
    if session_key is None:
        messages.info(request, "El Carrito ya está vacío.")
        return redirect(_destino_despues_de_post(request))

    try:
        vaciar_carrito(session_key)
    except SesionNoDisponible as error:
        raise RuntimeError("No fue posible procesar el Carrito.") from error
    messages.info(request, "El Carrito quedó vacío.")
    return redirect(_destino_despues_de_post(request))
