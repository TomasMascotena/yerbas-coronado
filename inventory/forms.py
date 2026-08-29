from django import forms

from inventory.services import MAX_BIGINT_POSITIVO


class MovimientoAdministrativoForm(forms.Form):
    cantidad = forms.IntegerField(
        min_value=1,
        max_value=MAX_BIGINT_POSITIVO,
    )
    observacion = forms.CharField(
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AjusteInventarioForm(forms.Form):
    cantidad = forms.IntegerField(
        min_value=1,
        max_value=MAX_BIGINT_POSITIVO,
    )
    observacion = forms.CharField(
        label="Justificación",
        required=True,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
