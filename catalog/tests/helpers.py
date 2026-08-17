from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile


def imagen_de_prueba(nombre="producto.gif"):
    return SimpleUploadedFile(
        nombre,
        (
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,"
            b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        ),
        content_type="image/gif",
    )


def datos_producto(**cambios):
    datos = {
        "nombre": "Canarias",
        "descripcion": "Yerba mate tradicional",
        "peso": "1 kg",
        "imagen": imagen_de_prueba(),
        "precio_unitario": Decimal("5000.00"),
        "precio_desde_3": Decimal("4500.00"),
        "precio_desde_20": Decimal("4000.00"),
    }
    datos.update(cambios)
    return datos
