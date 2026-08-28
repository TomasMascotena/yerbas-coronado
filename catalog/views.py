from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

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
    productos = list(
        _productos_publicos().order_by("nombre", "peso", "pk")
    )
    for producto in productos:
        _verificar_inventario(producto)

    return render(
        request,
        "catalog/producto_list.html",
        {"productos": productos},
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
