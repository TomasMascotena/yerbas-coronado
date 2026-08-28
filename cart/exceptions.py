class SesionNoDisponible(Exception):
    pass


class CantidadCarritoInvalida(Exception):
    pass


class ProductoNoDisponible(Exception):
    pass


class ProductoSinInventario(ProductoNoDisponible):
    pass


class StockInsuficienteParaCarrito(Exception):
    pass


class ItemCarritoNoEncontrado(Exception):
    pass


class CarritoNoPerteneceALaSesion(Exception):
    pass
