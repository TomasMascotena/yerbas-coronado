from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from cart.context_processors import obtener_resumen_para_request
from catalog.models import Producto


def _productos_publicos():
    return Producto.objects.filter(activo=True).select_related("inventario")


def _verificar_inventario(producto):
    try:
        producto.inventario
    except Producto.inventario.RelatedObjectDoesNotExist as error:
        raise RuntimeError(
            "Un Producto activo debe poseer exactamente un Inventario."
        ) from error


@require_safe
def producto_list(request):
    busqueda = request.GET.get("q", "").strip()
    consulta = _productos_publicos()
    if busqueda:
        consulta = consulta.filter(
            Q(nombre__icontains=busqueda) | Q(peso__icontains=busqueda)
        )
    productos = list(consulta.order_by("nombre", "peso", "pk"))
    for producto in productos:
        _verificar_inventario(producto)

    lineas_por_producto = {
        linea.producto_id: linea
        for linea in obtener_resumen_para_request(request).lineas
    }
    tarjetas = [
        {
            "producto": producto,
            "linea_carrito": lineas_por_producto.get(producto.pk),
        }
        for producto in productos
    ]

    return render(
        request,
        "catalog/producto_list.html",
        {
            "productos": productos,
            "tarjetas": tarjetas,
            "busqueda": busqueda,
            "retorno_catalogo": f"{request.get_full_path()}#productos",
        },
    )


@require_safe
def producto_detail(request, pk):
    producto = get_object_or_404(_productos_publicos(), pk=pk)
    _verificar_inventario(producto)
    return render(
        request,
        "catalog/producto_detail.html",
        {"producto": producto},
    )
