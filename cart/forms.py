from django import forms


class EstablecerCantidadItemForm(forms.Form):
    cantidad = forms.IntegerField(
        min_value=1,
        max_value=2_147_483_647,
        widget=forms.NumberInput(
            attrs={
                "min": 1,
                "max": 2_147_483_647,
                "step": 1,
                "inputmode": "numeric",
            }
        ),
    )
