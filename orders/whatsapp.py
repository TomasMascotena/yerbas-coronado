import re
from urllib.parse import quote

from django.utils.formats import number_format

from orders.models import ModalidadEntrega


PATRON_NUMERO_WHATSAPP = re.compile(r"[1-9][0-9]{7,14}\Z", re.ASCII)


def formatear_importe_ars(importe):
    return "ARS " + number_format(
        importe,
        decimal_pos=2,
        use_l10n=True,
        force_grouping=True,
    )


def construir_mensaje_whatsapp(*, pedido, detalles, direccion_envio):
    lineas = [
        f"Hola, quiero continuar con el Pedido {pedido.numero_pedido}.",
        "",
        f"Comprador: {pedido.nombre_cliente} {pedido.apellido_cliente}",
        f"Teléfono: {pedido.telefono_cliente}",
        "",
        "Productos:",
    ]
    for detalle in detalles:
        lineas.extend(
            (
                f"- {detalle.nombre_producto}, presentación {detalle.peso_producto}",
                f"  Cantidad: {detalle.cantidad}",
                "  Precio unitario: "
                f"{formatear_importe_ars(detalle.precio_unitario_aplicado)}",
                f"  Subtotal: {formatear_importe_ars(detalle.subtotal)}",
            )
        )

    lineas.extend(
        (
            "",
            f"Total de mercadería: {formatear_importe_ars(pedido.importe_total)}",
            f"Modalidad de entrega: {pedido.get_modalidad_entrega_display()}",
        )
    )
    if pedido.modalidad_entrega == ModalidadEntrega.ENVIO_DOMICILIO:
        lineas.append(f"Dirección: {_formatear_direccion(direccion_envio)}")
        if direccion_envio.referencias:
            lineas.append(f"Referencias: {direccion_envio.referencias}")
    if pedido.observaciones:
        lineas.append(f"Observaciones: {pedido.observaciones}")
    return "\n".join(lineas)


def construir_enlace_whatsapp(*, numero_comercial, mensaje):
    if (
        not isinstance(numero_comercial, str)
        or PATRON_NUMERO_WHATSAPP.fullmatch(numero_comercial) is None
    ):
        return None
    return f"https://wa.me/{numero_comercial}?text={quote(mensaje, safe='')}"


def _formatear_direccion(direccion):
    partes = [f"{direccion.calle} {direccion.numero}"]
    if direccion.piso:
        partes.append(f"Piso {direccion.piso}")
    if direccion.departamento:
        partes.append(f"Departamento {direccion.departamento}")
    partes.extend((direccion.localidad, direccion.provincia))
    if direccion.codigo_postal:
        partes.append(f"CP {direccion.codigo_postal}")
    return ", ".join(partes)
