from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from orders.exceptions import HistorialPedidoInmutable


class EstadoPedido(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    ENTREGADO = "ENTREGADO", "Entregado"
    CANCELADO = "CANCELADO", "Cancelado"


class ModalidadEntrega(models.TextChoices):
    RETIRO = "RETIRO", "Retiro"
    ENVIO_DOMICILIO = "ENVIO_DOMICILIO", "Envío a domicilio"


class _ModeloHistoricoInmutable(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise HistorialPedidoInmutable(
                "El registro histórico no puede modificarse directamente."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise HistorialPedidoInmutable(
            "El registro histórico no puede eliminarse directamente."
        )


class Cliente(models.Model):
    dni = models.CharField(max_length=20)
    nombre = models.CharField(max_length=150)
    apellido = models.CharField(max_length=150)
    telefono = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("dni",),
                name="orders_cliente_dni_uniq",
            ),
            models.CheckConstraint(
                condition=Q(dni__regex=r"^[0-9]{6,8}$"),
                name="orders_cliente_dni_formato",
            ),
            models.CheckConstraint(
                condition=Q(nombre__regex=r".*\S.*"),
                name="orders_cliente_nombre_no_vacio",
            ),
            models.CheckConstraint(
                condition=Q(apellido__regex=r".*\S.*"),
                name="orders_cliente_apellido_no_vacio",
            ),
            models.CheckConstraint(
                condition=Q(telefono__regex=r".*[0-9].*"),
                name="orders_cliente_telefono_no_vacio",
            ),
        ]


class Pedido(_ModeloHistoricoInmutable):
    numero_pedido = models.CharField(max_length=15, editable=False)
    token_idempotencia = models.UUIDField(editable=False)
    huella_sesion_origen = models.CharField(max_length=64, editable=False)
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )
    fecha_hora_creacion = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(
        max_length=10,
        choices=EstadoPedido.choices,
        default=EstadoPedido.PENDIENTE,
        editable=False,
    )
    modalidad_entrega = models.CharField(
        max_length=20,
        choices=ModalidadEntrega.choices,
        editable=False,
    )
    observaciones = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        editable=False,
    )
    cantidad_total = models.PositiveBigIntegerField(
        editable=False,
        validators=[MinValueValidator(1)],
    )
    importe_total = models.DecimalField(
        max_digits=31,
        decimal_places=2,
        editable=False,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    nombre_cliente = models.CharField(max_length=150, editable=False)
    apellido_cliente = models.CharField(max_length=150, editable=False)
    dni_cliente = models.CharField(max_length=20, editable=False)
    telefono_cliente = models.CharField(max_length=32, editable=False)

    def __str__(self):
        return self.numero_pedido

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("numero_pedido",),
                name="orders_pedido_numero_uniq",
            ),
            models.UniqueConstraint(
                fields=("token_idempotencia",),
                name="orders_pedido_token_idempotencia_uniq",
            ),
            models.CheckConstraint(
                condition=Q(numero_pedido__regex=r"^YC-[0-9A-HJKMNP-TV-Z]{12}$"),
                name="orders_pedido_numero_formato",
            ),
            models.CheckConstraint(
                condition=Q(huella_sesion_origen__regex=r"^[0-9a-f]{64}$"),
                name="orders_pedido_huella_formato",
            ),
            models.CheckConstraint(
                condition=Q(estado__in=EstadoPedido.values),
                name="orders_pedido_estado_valido",
            ),
            models.CheckConstraint(
                condition=Q(modalidad_entrega__in=ModalidadEntrega.values),
                name="orders_pedido_modalidad_valida",
            ),
            models.CheckConstraint(
                condition=Q(cantidad_total__gt=0),
                name="orders_pedido_cantidad_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(importe_total__gt=0),
                name="orders_pedido_importe_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(nombre_cliente__regex=r".*\S.*"),
                name="orders_pedido_nombre_no_vacio",
            ),
            models.CheckConstraint(
                condition=Q(apellido_cliente__regex=r".*\S.*"),
                name="orders_pedido_apellido_no_vacio",
            ),
            models.CheckConstraint(
                condition=Q(dni_cliente__regex=r"^[0-9]{6,8}$"),
                name="orders_pedido_dni_formato",
            ),
            models.CheckConstraint(
                condition=Q(telefono_cliente__regex=r".*[0-9].*"),
                name="orders_pedido_telefono_no_vacio",
            ),
        ]
        indexes = [
            models.Index(
                fields=("estado", "-fecha_hora_creacion"),
                name="orders_pedido_estado_fecha_idx",
            ),
        ]


class DetallePedido(_ModeloHistoricoInmutable):
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.PROTECT,
        related_name="detalles",
    )
    producto = models.ForeignKey(
        "catalog.Producto",
        on_delete=models.PROTECT,
        related_name="detalles_pedido",
    )
    nombre_producto = models.TextField(editable=False)
    peso_producto = models.TextField(editable=False)
    cantidad = models.PositiveIntegerField(
        editable=False,
        validators=[MinValueValidator(1)],
    )
    precio_unitario_aplicado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        editable=False,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    subtotal = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        editable=False,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("pedido", "producto"),
                name="orders_detalle_pedido_producto_uniq",
            ),
            models.CheckConstraint(
                condition=Q(cantidad__gt=0),
                name="orders_detalle_cantidad_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(precio_unitario_aplicado__gt=0),
                name="orders_detalle_precio_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(subtotal__gt=0),
                name="orders_detalle_subtotal_gt_0",
            ),
            models.CheckConstraint(
                condition=~Q(nombre_producto=""),
                name="orders_detalle_nombre_no_vacio",
            ),
            models.CheckConstraint(
                condition=~Q(peso_producto=""),
                name="orders_detalle_peso_no_vacio",
            ),
            models.CheckConstraint(
                condition=Q(
                    subtotal=F("cantidad") * F("precio_unitario_aplicado")
                ),
                name="orders_detalle_subtotal_coherente",
            ),
        ]


class DireccionEnvio(_ModeloHistoricoInmutable):
    pedido = models.OneToOneField(
        Pedido,
        on_delete=models.PROTECT,
        related_name="direccion_envio",
    )
    calle = models.CharField(max_length=200, editable=False)
    numero = models.CharField(max_length=30, editable=False)
    piso = models.CharField(max_length=20, blank=True, default="", editable=False)
    departamento = models.CharField(
        max_length=20,
        blank=True,
        default="",
        editable=False,
    )
    localidad = models.CharField(max_length=120, editable=False)
    provincia = models.CharField(max_length=120, editable=False)
    codigo_postal = models.CharField(
        max_length=20,
        blank=True,
        default="",
        editable=False,
    )
    referencias = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        editable=False,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(calle__regex=r".*\S.*"),
                name="orders_direccion_calle_no_vacia",
            ),
            models.CheckConstraint(
                condition=Q(numero__regex=r".*\S.*"),
                name="orders_direccion_numero_no_vacio",
            ),
            models.CheckConstraint(
                condition=Q(localidad__regex=r".*\S.*"),
                name="orders_direccion_localidad_no_vacia",
            ),
            models.CheckConstraint(
                condition=Q(provincia__regex=r".*\S.*"),
                name="orders_direccion_provincia_no_vacia",
            ),
        ]
