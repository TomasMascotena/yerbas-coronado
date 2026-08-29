from django import forms

from orders.models import ModalidadEntrega
from orders.services import DatosComprador, DatosDireccionEnvio


CAMPOS_DIRECCION_OBLIGATORIOS = (
    "calle",
    "numero",
    "localidad",
    "provincia",
)


class CheckoutForm(forms.Form):
    token_checkout = forms.UUIDField(widget=forms.HiddenInput)
    nombre = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    apellido = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    dni = forms.CharField(
        label="DNI",
        max_length=20,
        help_text="Podés escribirlo con puntos, espacios o guiones.",
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
            }
        ),
    )
    telefono = forms.CharField(
        label="Teléfono",
        max_length=32,
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "inputmode": "tel",
                "autocomplete": "tel",
            }
        ),
    )
    modalidad_entrega = forms.ChoiceField(
        label="Modalidad de entrega",
        choices=ModalidadEntrega.choices,
        widget=forms.RadioSelect,
    )
    calle = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "address-line1"}),
    )
    numero = forms.CharField(
        label="Número",
        max_length=30,
        required=False,
    )
    piso = forms.CharField(max_length=20, required=False)
    departamento = forms.CharField(max_length=20, required=False)
    localidad = forms.CharField(
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "address-level2"}),
    )
    provincia = forms.CharField(
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "address-level1"}),
    )
    codigo_postal = forms.CharField(
        label="Código postal",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "postal-code"}),
    )
    referencias = forms.CharField(
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    observaciones = forms.CharField(
        max_length=1000,
        required=False,
        help_text="Opcional. Podés indicar información útil para coordinar el Pedido.",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean(self):
        datos = super().clean()
        if datos.get("modalidad_entrega") == ModalidadEntrega.ENVIO_DOMICILIO:
            for campo in CAMPOS_DIRECCION_OBLIGATORIOS:
                if not datos.get(campo):
                    self.add_error(
                        campo,
                        "Este dato es obligatorio para el envío a domicilio.",
                    )
        return datos

    def datos_comprador(self):
        return DatosComprador(
            dni=self.cleaned_data["dni"],
            nombre=self.cleaned_data["nombre"],
            apellido=self.cleaned_data["apellido"],
            telefono=self.cleaned_data["telefono"],
        )

    def datos_direccion_envio(self):
        if self.cleaned_data["modalidad_entrega"] == ModalidadEntrega.RETIRO:
            return None
        return DatosDireccionEnvio(
            calle=self.cleaned_data["calle"],
            numero=self.cleaned_data["numero"],
            localidad=self.cleaned_data["localidad"],
            provincia=self.cleaned_data["provincia"],
            piso=self.cleaned_data["piso"],
            departamento=self.cleaned_data["departamento"],
            codigo_postal=self.cleaned_data["codigo_postal"],
            referencias=self.cleaned_data["referencias"],
        )
