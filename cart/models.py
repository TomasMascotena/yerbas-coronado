from decimal import Decimal
import uuid

from django.core.validators import MinLengthValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from catalog.models import Producto


class Carrito(models.Model):
    session_key = models.CharField(
        max_length=40,
        unique=True,
        validators=[MinLengthValidator(1)],
    )
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    ultima_actividad = models.DateTimeField(default=timezone.now, editable=False)
    token_checkout = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("token_checkout",),
                name="cart_carrito_token_checkout_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(session_key=""),
                name="cart_carrito_session_key_no_vacia",
            ),
        ]

    @property
    def cantidad_lineas(self):
        return self.items.count()

    @property
    def cantidad_total_unidades(self):
        return self.items.aggregate(total=Sum("cantidad"))["total"] or 0

    @property
    def esta_vacio(self):
        return not self.items.exists()

    def items_con_detalle(self):
        return self.items.select_related(
            "producto",
            "producto__inventario",
        )

    def __str__(self):
        return f"Carrito de sesión {self.session_key}"


class ItemCarrito(models.Model):
    carrito = models.ForeignKey(
        Carrito,
        on_delete=models.CASCADE,
        related_name="items",
    )
    producto = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name="items_carrito",
    )
    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    precio_unitario_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    precio_desde_3_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    precio_desde_20_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("carrito", "producto"),
                name="cart_item_carrito_producto_uniq",
            ),
            models.CheckConstraint(
                condition=Q(cantidad__gt=0),
                name="cart_item_cantidad_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(precio_unitario_snapshot__gt=0),
                name="cart_item_precio_unitario_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(precio_desde_3_snapshot__gt=0),
                name="cart_item_precio_desde_3_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(precio_desde_20_snapshot__gt=0),
                name="cart_item_precio_desde_20_gt_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.producto.nombre} x {self.cantidad} "
            f"en carrito {self.carrito_id}"
        )
