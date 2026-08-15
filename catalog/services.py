from django.db import transaction

from catalog.models import Producto
from inventory.models import Inventario


@transaction.atomic
def crear_producto_con_inventario(*, producto: Producto):
    producto.full_clean()
    producto.save()

    inventario = Inventario(producto=producto)
    inventario.full_clean()
    inventario.save()

    return producto
