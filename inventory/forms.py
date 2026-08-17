from django import forms


class MovimientoAdministrativoForm(forms.Form):
    cantidad = forms.IntegerField(min_value=1)
    observacion = forms.CharField(
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AjusteInventarioForm(forms.Form):
    cantidad = forms.IntegerField(min_value=1)
    observacion = forms.CharField(
        label="Justificación",
        required=True,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
