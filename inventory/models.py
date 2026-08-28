from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from catalog.models import Producto
from inventory.exceptions import MovimientoInventarioInmutable


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
    cantidad_disponible = models.PositiveBigIntegerField(
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
    cantidad = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1)],
    )
    observacion = models.TextField(blank=True, default="")
    pedido = models.ForeignKey(
        "orders.Pedido",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movimientos_inventario",
    )

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
            models.CheckConstraint(
                condition=(
                    Q(
                        tipo_movimiento__in=(
                            TipoMovimientoInventario.VENTA_PEDIDO,
                            TipoMovimientoInventario.CANCELACION_PEDIDO,
                        ),
                        pedido__isnull=False,
                    )
                    | Q(
                        tipo_movimiento__in=(
                            TipoMovimientoInventario.INGRESO_MERCADERIA,
                            TipoMovimientoInventario.VENTA_PRESENCIAL,
                            TipoMovimientoInventario.AJUSTE_POSITIVO,
                            TipoMovimientoInventario.AJUSTE_NEGATIVO,
                        ),
                        pedido__isnull=True,
                    )
                ),
                name="inventory_movimiento_pedido_coherente",
            ),
            models.UniqueConstraint(
                fields=("pedido", "inventario", "tipo_movimiento"),
                condition=Q(
                    pedido__isnull=False,
                    tipo_movimiento__in=(
                        TipoMovimientoInventario.VENTA_PEDIDO,
                        TipoMovimientoInventario.CANCELACION_PEDIDO,
                    ),
                ),
                name="inventory_mov_pedido_inv_tipo_uniq",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise MovimientoInventarioInmutable(
                "El Movimiento de Inventario histórico no puede modificarse."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise MovimientoInventarioInmutable(
            "El Movimiento de Inventario histórico no puede eliminarse."
        )
