from cart.models import Carrito, ItemCarrito
from inventory.services import registrar_ingreso_mercaderia
from inventory.tests.helpers import crear_inventario_de_prueba


def crear_producto_con_stock(*, nombre="Canarias", stock=30):
    inventario = crear_inventario_de_prueba(nombre=nombre)
    if stock:
        registrar_ingreso_mercaderia(
            inventario_id=inventario.pk,
            cantidad=stock,
        )
    return inventario.producto


def crear_item_directo(
    *,
    carrito=None,
    producto=None,
    cantidad=1,
    **cambios,
):
    carrito = carrito or Carrito.objects.create(session_key="sesion-modelo")
    producto = producto or crear_producto_con_stock()
    datos = {
        "carrito": carrito,
        "producto": producto,
        "cantidad": cantidad,
        "precio_unitario_snapshot": producto.precio_unitario,
        "precio_desde_3_snapshot": producto.precio_desde_3,
        "precio_desde_20_snapshot": producto.precio_desde_20,
    }
    datos.update(cambios)
    item = ItemCarrito(**datos)
    item.full_clean()
    item.save()
    return item
