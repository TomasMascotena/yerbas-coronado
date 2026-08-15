from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from catalog.models import Producto


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
