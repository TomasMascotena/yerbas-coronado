from django.conf import settings

from orders.whatsapp import construir_enlace_whatsapp


MENSAJE_CONSULTA_MAYORISTA = (
    "¡Hola! Quiero más información para ventas mayoristas."
)
MENSAJE_CONTACTO_GENERAL = "¡Hola! Quiero más información sobre Yerbas Coronado."


def contacto_publico(_request):
    numero_comercial = settings.WHATSAPP_BUSINESS_NUMBER
    return {
        "whatsapp_mayorista_url": construir_enlace_whatsapp(
            numero_comercial=numero_comercial,
            mensaje=MENSAJE_CONSULTA_MAYORISTA,
        ),
        "whatsapp_contacto_url": construir_enlace_whatsapp(
            numero_comercial=numero_comercial,
            mensaje=MENSAJE_CONTACTO_GENERAL,
        ),
    }
