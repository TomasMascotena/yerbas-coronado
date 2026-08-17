from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from catalog.models import Producto


class TipoMovimientoInventario(models.TextChoices):
    INGRESO_MERCADERIA = "INGRESO_MERCADERIA", "Ingreso de mercadería"
    VENTA_PEDIDO = "VENTA_PEDIDO", "Venta por pedido"
    VENTA_PRESENCIAL = "VENTA_PRESENCIAL", "Venta presencial"
    CANCELACION_PEDIDO = "CANCELACION_PEDIDO", "Cancelación de pedido"
    AJUSTE_POSITIVO = "AJUSTE_POSITIVO", "Ajuste positivo"
    AJUSTE_NEGATIVO = "AJUSTE_NEGATIVO", "Ajuste negativo"


class Inventario(models.Model):
    producto = models.OneToOneField(
        Producto,
        on_delete=models.PROTECT,
        related_name="inventario",
    )
    cantidad_disponible = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(cantidad_disponible__gte=0),
                name="inventory_cantidad_disponible_gte_0",
            ),
        ]


class MovimientoInventario(models.Model):
    inventario = models.ForeignKey(
        Inventario,
        on_delete=models.PROTECT,
        related_name="movimientos",
    )
    fecha_hora = models.DateTimeField(auto_now_add=True)
    tipo_movimiento = models.CharField(
        max_length=20,
        choices=TipoMovimientoInventario.choices,
    )
    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    observacion = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(cantidad__gt=0),
                name="inventory_movimiento_cantidad_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(
                    tipo_movimiento__in=TipoMovimientoInventario.values
                ),
                name="inventory_movimiento_tipo_valido",
            ),
        ]
