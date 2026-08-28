from decimal import Decimal

from django import template
from django.utils.formats import number_format


register = template.Library()


@register.filter
def precio_ars(valor: Decimal) -> str:
    importe = number_format(
        valor,
        decimal_pos=2,
        use_l10n=True,
        force_grouping=True,
    )
    return f"$ {importe}"
