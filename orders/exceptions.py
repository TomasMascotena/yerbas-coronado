class ErrorPedido(Exception):
    """Error funcional esperado durante un caso de uso de Pedidos."""


class ErrorEstructuralPedido(ErrorPedido):
    """Inconsistencia persistente que impide operar con seguridad."""


class CarritoInexistente(ErrorPedido):
    pass


class CarritoVacio(ErrorPedido):
    pass


class CarritoExpirado(ErrorPedido):
    pass


class CarritoModificado(ErrorPedido):
    pass


class TokenIdempotenciaInvalido(ErrorPedido):
    pass


class DatosCompradorInvalidos(ErrorPedido):
    pass


class ModalidadEntregaInvalida(ErrorPedido):
    pass


class DireccionEnvioInvalida(ErrorPedido):
    pass


class ProductoNoDisponible(ErrorPedido):
    pass


class ProductoSinInventario(ErrorEstructuralPedido):
    pass


class StockInsuficienteParaPedido(ErrorPedido):
    pass


class PricingCorrupto(ErrorEstructuralPedido):
    pass


class ItemCarritoCorrupto(ErrorEstructuralPedido):
    pass


class TransicionPedidoInvalida(ErrorPedido):
    pass


class HistorialMovimientosCorrupto(ErrorEstructuralPedido):
    pass


class CapacidadInventarioExcedida(ErrorPedido):
    pass


class HistorialPedidoInmutable(ErrorPedido):
    pass


class GeneracionNumeroPedidoAgotada(ErrorPedido):
    pass


class _ReconsultarTokenIdempotencia(Exception):
    """Coordina un rollback exterior antes de resolver una colisión."""
