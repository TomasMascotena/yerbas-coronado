from django.db import transaction

from catalog.models import Producto
from inventory.models import Inventario


@transaction.atomic
def crear_producto_con_inventario(
    *,
    nombre,
    peso,
    imagen,
    precio_unitario,
    precio_desde_3,
    precio_desde_20,
    descripcion="",
):
    producto = Producto(
        nombre=nombre,
        descripcion=descripcion,
        peso=peso,
        imagen=imagen,
        precio_unitario=precio_unitario,
        precio_desde_3=precio_desde_3,
        precio_desde_20=precio_desde_20,
    )
    producto.full_clean()
    producto.save()

    inventario = Inventario(producto=producto)
    inventario.full_clean()
    inventario.save()

    return producto
