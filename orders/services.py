from dataclasses import dataclass
import hashlib
import secrets
import uuid

from django.db import IntegrityError, transaction

from cart.models import Carrito, ItemCarrito
from cart.pricing import EstadoPrecioCarritoInvalido, calcular_resumen
from cart.services import DURACION_CARRITO, _ahora
from catalog.models import Producto
from inventory.exceptions import (
    CapacidadInventarioExcedida as CapacidadInventarioExcedidaInventory,
    StockInsuficiente,
)
from inventory.models import (
    Inventario,
    MovimientoInventario,
    TipoMovimientoInventario,
)
from inventory.services import (
    MAX_BIGINT_POSITIVO,
    _aplicar_cancelacion_pedido_sobre_inventario_bloqueado,
    _aplicar_venta_pedido_sobre_inventario_bloqueado,
)
from orders.exceptions import (
    CapacidadInventarioExcedida,
    CarritoExpirado,
    CarritoInexistente,
    CarritoModificado,
    CarritoVacio,
    DatosCompradorInvalidos,
    DireccionEnvioInvalida,
    GeneracionNumeroPedidoAgotada,
    HistorialMovimientosCorrupto,
    ItemCarritoCorrupto,
    ModalidadEntregaInvalida,
    PricingCorrupto,
    ProductoNoDisponible,
    ProductoSinInventario,
    StockInsuficienteParaPedido,
    TokenIdempotenciaInvalido,
    TransicionPedidoInvalida,
    _ReconsultarTokenIdempotencia,
)
from orders.models import (
    Cliente,
    DetallePedido,
    DireccionEnvio,
    EstadoPedido,
    ModalidadEntrega,
    Pedido,
)


SEPARADOR_HUELLA_SESION = "yerbas-coronado:checkout-session:v1"
ALFABETO_NUMERO_PEDIDO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_INT_POSITIVO = 2_147_483_647
CONSTRAINT_DNI_CLIENTE = "orders_cliente_dni_uniq"
CONSTRAINT_NUMERO_PEDIDO = "orders_pedido_numero_uniq"
CONSTRAINT_TOKEN_IDEMPOTENCIA = "orders_pedido_token_idempotencia_uniq"
_CARRITO_EXPIRADO = object()


@dataclass(frozen=True)
class DatosComprador:
    dni: str
    nombre: str
    apellido: str
    telefono: str


@dataclass(frozen=True)
class DatosDireccionEnvio:
    calle: str
    numero: str
    localidad: str
    provincia: str
    piso: str = ""
    departamento: str = ""
    codigo_postal: str = ""
    referencias: str = ""


@dataclass(frozen=True)
class ResultadoCreacionPedido:
    pedido: Pedido
    creado: bool


def crear_pedido_desde_carrito(
    *,
    session_key,
    token_idempotencia,
    datos_comprador,
    modalidad_entrega,
    direccion_envio=None,
    observaciones=None,
):
    session_key = _validar_session_key(session_key)
    token = _normalizar_token(token_idempotencia)
    comprador = _normalizar_datos_comprador(datos_comprador)
    modalidad = _normalizar_modalidad(modalidad_entrega)
    direccion = _normalizar_direccion(modalidad, direccion_envio)
    observaciones = _normalizar_texto_opcional(
        observaciones,
        max_length=1000,
        error=DatosCompradorInvalidos,
    )
    huella = _calcular_huella_sesion(session_key)

    pedido_existente = _buscar_pedido_por_token(token)
    if pedido_existente is not None:
        return _resolver_replay(pedido_existente, huella)

    try:
        resultado = _crear_pedido_en_transaccion(
            session_key=session_key,
            token=token,
            huella=huella,
            comprador=comprador,
            modalidad=modalidad,
            direccion=direccion,
            observaciones=observaciones,
        )
    except _ReconsultarTokenIdempotencia:
        return _reconsultar_token_despues_de_rollback(token, huella)

    if resultado is _CARRITO_EXPIRADO:
        raise CarritoExpirado("El Carrito expiró.")
    return resultado


@transaction.atomic
def _crear_pedido_en_transaccion(
    *, session_key, token, huella, comprador, modalidad, direccion, observaciones
):
    carrito = _bloquear_carrito_para_checkout(session_key)
    if carrito is None:
        pedido = _buscar_pedido_por_token(token)
        if pedido is not None:
            return _resolver_replay(pedido, huella)
        raise CarritoInexistente("No existe un Carrito vigente.")

    ahora = _ahora()
    if ahora >= carrito.ultima_actividad + DURACION_CARRITO:
        carrito.delete()
        return _CARRITO_EXPIRADO

    if not secrets.compare_digest(str(carrito.token_checkout), str(token)):
        raise CarritoModificado("El contenido del Carrito cambió.")

    pedido = _buscar_pedido_por_token(token)
    if pedido is not None:
        return _resolver_replay(pedido, huella)

    items = list(
        ItemCarrito.objects.select_for_update()
        .filter(carrito=carrito)
        .order_by("pk")
    )
    if not items:
        raise CarritoVacio("No puede generarse un Pedido vacío.")
    _validar_items(items)

    cliente, cliente_nuevo = _obtener_o_crear_cliente_bloqueado(comprador)

    productos = _obtener_productos_bloqueados(items)
    inventarios = _obtener_inventarios_bloqueados(productos)
    cantidades_por_inventario = _validar_stock_definitivo(
        items=items,
        productos=productos,
        inventarios=inventarios,
    )
    resumen = _calcular_resumen_pedido(carrito, items)

    if not cliente_nuevo:
        cliente.nombre = comprador.nombre
        cliente.apellido = comprador.apellido
        cliente.telefono = comprador.telefono
        cliente.full_clean()
        cliente.save(update_fields=("nombre", "apellido", "telefono"))

    pedido = _crear_pedido_con_reintentos(
        token=token,
        huella=huella,
        cliente=cliente,
        comprador=comprador,
        modalidad=modalidad,
        observaciones=observaciones,
        resumen=resumen,
    )
    _crear_detalles(
        pedido=pedido,
        items=items,
        productos=productos,
        resumen=resumen,
    )
    if direccion is not None:
        _crear_direccion(pedido, direccion)

    for inventario_id in sorted(cantidades_por_inventario):
        try:
            _aplicar_venta_pedido_sobre_inventario_bloqueado(
                inventario=inventarios[inventario_id],
                pedido=pedido,
                cantidad=cantidades_por_inventario[inventario_id],
            )
        except StockInsuficiente as error:
            raise StockInsuficienteParaPedido(
                "El stock disponible cambió durante la confirmación."
            ) from error

    carrito.delete()
    return ResultadoCreacionPedido(pedido=pedido, creado=True)


def marcar_pedido_entregado(*, pedido_id):
    with transaction.atomic():
        try:
            pedido = Pedido.objects.select_for_update().get(pk=pedido_id)
        except Pedido.DoesNotExist as error:
            raise TransicionPedidoInvalida(
                "El Pedido no admite la transición solicitada."
            ) from error
        if pedido.estado != EstadoPedido.PENDIENTE:
            raise TransicionPedidoInvalida(
                "El Pedido no admite la transición solicitada."
            )
        actualizados = Pedido.objects.filter(
            pk=pedido.pk,
            estado=EstadoPedido.PENDIENTE,
        ).update(estado=EstadoPedido.ENTREGADO)
        if actualizados != 1:
            raise TransicionPedidoInvalida(
                "El Pedido no admite la transición solicitada."
            )
        pedido.refresh_from_db()
        return pedido


def cancelar_pedido(*, pedido_id):
    with transaction.atomic():
        try:
            pedido = Pedido.objects.select_for_update().get(pk=pedido_id)
        except Pedido.DoesNotExist as error:
            raise TransicionPedidoInvalida(
                "El Pedido no admite la transición solicitada."
            ) from error
        if pedido.estado != EstadoPedido.PENDIENTE:
            raise TransicionPedidoInvalida(
                "El Pedido no admite la transición solicitada."
            )
        ventas = list(
            MovimientoInventario.objects.select_for_update()
            .filter(
                pedido=pedido,
                tipo_movimiento=TipoMovimientoInventario.VENTA_PEDIDO,
            )
            .select_related("inventario")
            .order_by("pk")
        )
        cancelaciones = MovimientoInventario.objects.select_for_update().filter(
            pedido=pedido,
            tipo_movimiento=TipoMovimientoInventario.CANCELACION_PEDIDO,
        )
        if cancelaciones.exists():
            raise HistorialMovimientosCorrupto(
                "El Pedido ya posee movimientos de cancelación."
            )
        detalles = list(pedido.detalles.select_related("producto").order_by("pk"))
        cantidades_por_inventario = _validar_historial_cancelacion(
            detalles=detalles,
            ventas=ventas,
        )
        inventarios = {
            inventario.pk: inventario
            for inventario in Inventario.objects.select_for_update()
            .filter(pk__in=cantidades_por_inventario)
            .order_by("pk")
        }
        if set(inventarios) != set(cantidades_por_inventario):
            raise HistorialMovimientosCorrupto(
                "El historial referencia Inventarios inexistentes."
            )
        for inventario_id, cantidad in cantidades_por_inventario.items():
            if (
                inventarios[inventario_id].cantidad_disponible + cantidad
                > MAX_BIGINT_POSITIVO
            ):
                raise CapacidadInventarioExcedida(
                    "La restitución excede la capacidad del Inventario."
                )
        for inventario_id in sorted(cantidades_por_inventario):
            try:
                _aplicar_cancelacion_pedido_sobre_inventario_bloqueado(
                    inventario=inventarios[inventario_id],
                    pedido=pedido,
                    cantidad=cantidades_por_inventario[inventario_id],
                )
            except CapacidadInventarioExcedidaInventory as error:
                raise CapacidadInventarioExcedida(
                    "La restitución excede la capacidad del Inventario."
                ) from error
        actualizados = Pedido.objects.filter(
            pk=pedido.pk,
            estado=EstadoPedido.PENDIENTE,
        ).update(estado=EstadoPedido.CANCELADO)
        if actualizados != 1:
            raise TransicionPedidoInvalida(
                "El Pedido no admite la transición solicitada."
            )
        pedido.refresh_from_db()
        return pedido


def _normalizar_dni(valor):
    if not isinstance(valor, str):
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    digitos = []
    for caracter in valor:
        if "0" <= caracter <= "9":
            digitos.append(caracter)
        elif caracter not in ". -":
            raise DatosCompradorInvalidos(
                "Los datos del comprador no son válidos."
            )
    dni = "".join(digitos)
    if not 6 <= len(dni) <= 8:
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    return dni


def _normalizar_telefono(valor):
    if not isinstance(valor, str):
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    telefono = valor.strip()
    if len(telefono) > 32 or not telefono:
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    for posicion, caracter in enumerate(telefono):
        if "0" <= caracter <= "9" or caracter in " -()":
            continue
        if caracter == "+" and posicion == 0:
            continue
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    cantidad_digitos = sum("0" <= c <= "9" for c in telefono)
    if not 6 <= cantidad_digitos <= 15:
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    return telefono


def _normalizar_datos_comprador(datos):
    if not isinstance(datos, DatosComprador):
        raise DatosCompradorInvalidos("Los datos del comprador no son válidos.")
    return DatosComprador(
        dni=_normalizar_dni(datos.dni),
        nombre=_normalizar_requerido(
            datos.nombre, max_length=150, error=DatosCompradorInvalidos
        ),
        apellido=_normalizar_requerido(
            datos.apellido, max_length=150, error=DatosCompradorInvalidos
        ),
        telefono=_normalizar_telefono(datos.telefono),
    )


def _normalizar_modalidad(valor):
    if valor not in ModalidadEntrega.values:
        raise ModalidadEntregaInvalida("La modalidad de entrega no es válida.")
    return valor


def _normalizar_direccion(modalidad, direccion):
    if modalidad == ModalidadEntrega.RETIRO:
        if direccion is not None:
            raise DireccionEnvioInvalida(
                "El retiro no admite una Dirección de Envío."
            )
        return None
    if not isinstance(direccion, DatosDireccionEnvio):
        raise DireccionEnvioInvalida("La Dirección de Envío no es válida.")
    return DatosDireccionEnvio(
        calle=_normalizar_requerido(
            direccion.calle, max_length=200, error=DireccionEnvioInvalida
        ),
        numero=_normalizar_requerido(
            direccion.numero, max_length=30, error=DireccionEnvioInvalida
        ),
        localidad=_normalizar_requerido(
            direccion.localidad, max_length=120, error=DireccionEnvioInvalida
        ),
        provincia=_normalizar_requerido(
            direccion.provincia, max_length=120, error=DireccionEnvioInvalida
        ),
        piso=_normalizar_texto_opcional(
            direccion.piso, max_length=20, error=DireccionEnvioInvalida
        ),
        departamento=_normalizar_texto_opcional(
            direccion.departamento,
            max_length=20,
            error=DireccionEnvioInvalida,
        ),
        codigo_postal=_normalizar_texto_opcional(
            direccion.codigo_postal,
            max_length=20,
            error=DireccionEnvioInvalida,
        ),
        referencias=_normalizar_texto_opcional(
            direccion.referencias,
            max_length=1000,
            error=DireccionEnvioInvalida,
        ),
    )


def _normalizar_requerido(valor, *, max_length, error):
    if not isinstance(valor, str):
        raise error("Los datos proporcionados no son válidos.")
    normalizado = valor.strip()
    if not normalizado or len(normalizado) > max_length:
        raise error("Los datos proporcionados no son válidos.")
    return normalizado


def _normalizar_texto_opcional(valor, *, max_length, error):
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise error("Los datos proporcionados no son válidos.")
    normalizado = valor.strip()
    if len(normalizado) > max_length:
        raise error("Los datos proporcionados no son válidos.")
    return normalizado


def _validar_session_key(valor):
    if not isinstance(valor, str) or not valor.strip() or len(valor) > 40:
        raise CarritoInexistente("No existe un Carrito vigente.")
    return valor


def _normalizar_token(valor):
    if isinstance(valor, uuid.UUID):
        return valor
    try:
        return uuid.UUID(str(valor))
    except (ValueError, TypeError, AttributeError) as error:
        raise TokenIdempotenciaInvalido(
            "La solicitud de confirmación no es válida."
        ) from error


def _calcular_huella_sesion(session_key):
    material = f"{SEPARADOR_HUELLA_SESION}:{session_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _resolver_replay(pedido, huella):
    if not secrets.compare_digest(pedido.huella_sesion_origen, huella):
        raise TokenIdempotenciaInvalido(
            "La solicitud de confirmación no es válida."
        )
    return ResultadoCreacionPedido(pedido=pedido, creado=False)


def _reconsultar_token_despues_de_rollback(token, huella):
    pedido = _buscar_pedido_por_token(token)
    if pedido is None:
        raise TokenIdempotenciaInvalido(
            "La solicitud de confirmación no es válida."
        )
    return _resolver_replay(pedido, huella)


def _buscar_pedido_por_token(token):
    return Pedido.objects.filter(token_idempotencia=token).first()


def _bloquear_carrito_para_checkout(session_key):
    return (
        Carrito.objects.select_for_update()
        .filter(session_key=session_key)
        .first()
    )


def _obtener_o_crear_cliente_bloqueado(comprador):
    cliente = Cliente.objects.select_for_update().filter(dni=comprador.dni).first()
    if cliente is not None:
        return cliente, False
    try:
        with transaction.atomic():
            cliente = Cliente.objects.create(
                dni=comprador.dni,
                nombre=comprador.nombre,
                apellido=comprador.apellido,
                telefono=comprador.telefono,
            )
        return cliente, True
    except IntegrityError as error:
        if _nombre_constraint(error) != CONSTRAINT_DNI_CLIENTE:
            raise
    cliente = Cliente.objects.select_for_update().get(dni=comprador.dni)
    return cliente, False


def _validar_items(items):
    productos = set()
    for item in items:
        if (
            isinstance(item.cantidad, bool)
            or not isinstance(item.cantidad, int)
            or not 1 <= item.cantidad <= MAX_INT_POSITIVO
            or item.producto_id in productos
        ):
            raise ItemCarritoCorrupto("El Carrito contiene Items inválidos.")
        productos.add(item.producto_id)


def _obtener_productos_bloqueados(items):
    ids = sorted(item.producto_id for item in items)
    productos = {
        producto.pk: producto
        for producto in Producto.objects.select_for_update()
        .filter(pk__in=ids)
        .order_by("pk")
    }
    if set(productos) != set(ids):
        raise ProductoNoDisponible("Un Producto ya no está disponible.")
    if any(not producto.activo for producto in productos.values()):
        raise ProductoNoDisponible("Un Producto ya no está disponible.")
    if any(
        not producto.nombre.strip() or not producto.peso.strip()
        for producto in productos.values()
    ):
        raise ItemCarritoCorrupto("Los snapshots comerciales no son válidos.")
    return productos


def _obtener_inventarios_bloqueados(productos):
    inventarios = {
        inventario.pk: inventario
        for inventario in Inventario.objects.select_for_update()
        .filter(producto_id__in=productos)
        .order_by("pk")
    }
    por_producto = {
        inventario.producto_id: inventario for inventario in inventarios.values()
    }
    if set(por_producto) != set(productos):
        raise ProductoSinInventario("Un Producto no posee Inventario.")
    return inventarios


def _validar_stock_definitivo(*, items, productos, inventarios):
    inventario_por_producto = {
        inventario.producto_id: inventario for inventario in inventarios.values()
    }
    cantidades = {}
    for item in items:
        inventario = inventario_por_producto[item.producto_id]
        cantidades[inventario.pk] = cantidades.get(inventario.pk, 0) + item.cantidad
    for inventario_id, cantidad in cantidades.items():
        if cantidad > inventarios[inventario_id].cantidad_disponible:
            raise StockInsuficienteParaPedido(
                "No hay stock suficiente para confirmar el Pedido."
            )
    return cantidades


def _calcular_resumen_pedido(carrito, items):
    try:
        resumen = calcular_resumen(carrito_id=carrito.pk, items=items)
    except EstadoPrecioCarritoInvalido as error:
        raise PricingCorrupto("No puede calcularse el importe del Pedido.") from error
    if (
        resumen.cantidad_total_unidades <= 0
        or resumen.cantidad_total_unidades > MAX_BIGINT_POSITIVO
        or resumen.importe_total <= 0
    ):
        raise PricingCorrupto("No puede calcularse el importe del Pedido.")
    return resumen


def _crear_pedido_con_reintentos(
    *, token, huella, cliente, comprador, modalidad, observaciones, resumen
):
    for _ in range(5):
        pedido = Pedido(
            numero_pedido=_generar_numero_pedido(),
            token_idempotencia=token,
            huella_sesion_origen=huella,
            cliente=cliente,
            estado=EstadoPedido.PENDIENTE,
            modalidad_entrega=modalidad,
            observaciones=observaciones,
            cantidad_total=resumen.cantidad_total_unidades,
            importe_total=resumen.importe_total,
            nombre_cliente=comprador.nombre,
            apellido_cliente=comprador.apellido,
            dni_cliente=comprador.dni,
            telefono_cliente=comprador.telefono,
        )
        try:
            with transaction.atomic():
                pedido.save(force_insert=True)
            return pedido
        except IntegrityError as error:
            constraint = _nombre_constraint(error)
            if constraint == CONSTRAINT_NUMERO_PEDIDO:
                continue
            if constraint == CONSTRAINT_TOKEN_IDEMPOTENCIA:
                raise _ReconsultarTokenIdempotencia from error
            raise
    raise GeneracionNumeroPedidoAgotada(
        "No pudo generarse un número de Pedido único."
    )


def _generar_numero_pedido():
    cuerpo = "".join(secrets.choice(ALFABETO_NUMERO_PEDIDO) for _ in range(12))
    return f"YC-{cuerpo}"


def _crear_detalles(*, pedido, items, productos, resumen):
    lineas = {linea.item_id: linea for linea in resumen.lineas}
    if set(lineas) != {item.pk for item in items}:
        raise PricingCorrupto("El resumen no coincide con los Items del Carrito.")
    for item in items:
        producto = productos[item.producto_id]
        linea = lineas[item.pk]
        detalle = DetallePedido(
            pedido=pedido,
            producto=producto,
            nombre_producto=producto.nombre,
            peso_producto=producto.peso,
            cantidad=item.cantidad,
            precio_unitario_aplicado=linea.precio_aplicado,
            subtotal=linea.subtotal,
        )
        detalle.full_clean()
        detalle.save()


def _crear_direccion(pedido, direccion):
    instancia = DireccionEnvio(
        pedido=pedido,
        calle=direccion.calle,
        numero=direccion.numero,
        piso=direccion.piso,
        departamento=direccion.departamento,
        localidad=direccion.localidad,
        provincia=direccion.provincia,
        codigo_postal=direccion.codigo_postal,
        referencias=direccion.referencias,
    )
    instancia.full_clean()
    instancia.save()


def _validar_historial_cancelacion(*, detalles, ventas):
    if not detalles or not ventas:
        raise HistorialMovimientosCorrupto(
            "El Pedido no posee un historial completo."
        )
    detalle_por_producto = {}
    for detalle in detalles:
        if detalle.producto_id in detalle_por_producto:
            raise HistorialMovimientosCorrupto(
                "El Pedido posee Detalles duplicados."
            )
        detalle_por_producto[detalle.producto_id] = detalle.cantidad
    venta_por_producto = {}
    cantidades_por_inventario = {}
    for venta in ventas:
        producto_id = venta.inventario.producto_id
        if producto_id in venta_por_producto:
            raise HistorialMovimientosCorrupto(
                "El Pedido posee movimientos de venta duplicados."
            )
        venta_por_producto[producto_id] = venta.cantidad
        cantidades_por_inventario[venta.inventario_id] = venta.cantidad
    if detalle_por_producto != venta_por_producto:
        raise HistorialMovimientosCorrupto(
            "Los Detalles y movimientos de venta no coinciden."
        )
    return cantidades_por_inventario


def _nombre_constraint(error):
    causa = getattr(error, "__cause__", None)
    diagnostico = getattr(causa, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
