from django.test import SimpleTestCase

from orders.forms import CheckoutForm
from orders.models import ModalidadEntrega


class CheckoutFormTests(SimpleTestCase):
    def datos_validos(self, **cambios):
        datos = {
            "token_checkout": "247209dd-96d3-4f5e-98f7-62fcdb1eae75",
            "nombre": "Ana",
            "apellido": "Coronado",
            "dni": "12.345.678",
            "telefono": "+54 11 4567-8901",
            "modalidad_entrega": ModalidadEntrega.RETIRO,
            "observaciones": "Sin bolsa",
        }
        datos.update(cambios)
        return datos

    def test_retiro_construye_comprador_y_descarta_direccion(self):
        formulario = CheckoutForm(
            self.datos_validos(calle="Dato irrelevante", numero="123")
        )

        self.assertTrue(formulario.is_valid(), formulario.errors)
        self.assertEqual(formulario.datos_comprador().dni, "12.345.678")
        self.assertIsNone(formulario.datos_direccion_envio())

    def test_envio_exige_solo_los_campos_obligatorios_del_dominio(self):
        formulario = CheckoutForm(
            self.datos_validos(modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO)
        )

        self.assertFalse(formulario.is_valid())
        self.assertEqual(
            set(formulario.errors),
            {"calle", "numero", "localidad", "provincia"},
        )

    def test_envio_construye_direccion_con_campos_opcionales(self):
        formulario = CheckoutForm(
            self.datos_validos(
                modalidad_entrega=ModalidadEntrega.ENVIO_DOMICILIO,
                calle="San Martín",
                numero="123",
                localidad="Posadas",
                provincia="Misiones",
                piso="2",
                departamento="B",
                codigo_postal="3300",
                referencias="Portón verde",
            )
        )

        self.assertTrue(formulario.is_valid(), formulario.errors)
        direccion = formulario.datos_direccion_envio()
        self.assertEqual(direccion.calle, "San Martín")
        self.assertEqual(direccion.referencias, "Portón verde")

    def test_token_es_uuid_oculto_y_widgets_poseen_autocomplete(self):
        formulario = CheckoutForm()

        self.assertEqual(formulario.fields["token_checkout"].widget.input_type, "hidden")
        self.assertEqual(
            formulario.fields["nombre"].widget.attrs["autocomplete"],
            "given-name",
        )
        self.assertEqual(
            formulario.fields["telefono"].widget.attrs["autocomplete"],
            "tel",
        )

    def test_token_invalido_es_rechazado_sin_coercion(self):
        formulario = CheckoutForm(self.datos_validos(token_checkout="no-es-uuid"))

        self.assertFalse(formulario.is_valid())
        self.assertIn("token_checkout", formulario.errors)
