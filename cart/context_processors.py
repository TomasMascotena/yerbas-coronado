from django.utils.functional import SimpleLazyObject

from cart.pricing import calcular_resumen
from cart.services import obtener_resumen_carrito


RESUMEN_CACHE_ATTR = "_cart_resumen_cache"


def obtener_resumen_para_request(request):
    if hasattr(request, RESUMEN_CACHE_ATTR):
        return getattr(request, RESUMEN_CACHE_ATTR)

    session_key = request.session.session_key
    if session_key is None:
        resumen = calcular_resumen(carrito_id=None, items=())
    else:
        resumen = obtener_resumen_carrito(session_key)

    setattr(request, RESUMEN_CACHE_ATTR, resumen)
    return resumen


def indicador_carrito(request):
    return {
        "cart_resumen_global": SimpleLazyObject(
            lambda: obtener_resumen_para_request(request)
        )
    }
