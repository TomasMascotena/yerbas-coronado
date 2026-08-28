from cart.services import agregar_producto
from cart.tests.helpers import crear_producto_con_stock
from orders.models import ModalidadEntrega
from orders.services import DatosComprador, crear_pedido_desde_carrito


def datos_comprador(**cambios):
    datos = {
        "dni": "12.345.678",
        "nombre": "Ana",
        "apellido": "Coronado",
        "telefono": "+54 (11) 4567-8901",
    }
    datos.update(cambios)
    return DatosComprador(**datos)


def crear_carrito_checkout(
    *, session_key="sesion-checkout", cantidad=2, stock=20, nombre="Canarias"
):
    producto = crear_producto_con_stock(nombre=nombre, stock=stock)
    item = agregar_producto(
        session_key=session_key,
        producto_id=producto.pk,
        cantidad=cantidad,
    )
    item.carrito.refresh_from_db()
    return item.carrito, producto


def crear_pedido_de_prueba(*, session_key="sesion-pedido", cantidad=2, stock=20):
    carrito, producto = crear_carrito_checkout(
        session_key=session_key,
        cantidad=cantidad,
        stock=stock,
    )
    resultado = crear_pedido_desde_carrito(
        session_key=session_key,
        token_idempotencia=carrito.token_checkout,
        datos_comprador=datos_comprador(),
        modalidad_entrega=ModalidadEntrega.RETIRO,
    )
    return resultado.pedido, producto
