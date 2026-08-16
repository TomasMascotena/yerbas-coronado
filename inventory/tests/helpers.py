from catalog.models import Producto
from catalog.services import crear_producto_con_inventario
from catalog.tests.helpers import datos_producto, imagen_de_prueba


def crear_inventario_de_prueba(nombre="Canarias"):
    producto = crear_producto_con_inventario(
        producto=Producto(
            **datos_producto(
                nombre=nombre,
                imagen=imagen_de_prueba(f"{nombre}.gif"),
            )
        )
    )
    return producto.inventario
