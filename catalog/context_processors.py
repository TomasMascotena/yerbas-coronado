from django.conf import settings

from orders.whatsapp import construir_enlace_whatsapp


MENSAJE_CONSULTA_MAYORISTA = (
    "Hola, quisiera consultar por las opciones mayoristas de Yerbas Coronado."
)


def contacto_publico(_request):
    return {
        "whatsapp_mayorista_url": construir_enlace_whatsapp(
            numero_comercial=settings.WHATSAPP_BUSINESS_NUMBER,
            mensaje=MENSAJE_CONSULTA_MAYORISTA,
        )
    }
