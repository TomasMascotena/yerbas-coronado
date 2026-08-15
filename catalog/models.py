from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Producto(models.Model):
    nombre = models.TextField()
    descripcion = models.TextField(blank=True)
    peso = models.TextField()
    imagen = models.ImageField(upload_to="productos/")
    precio_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    precio_desde_3 = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    precio_desde_20 = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    activo = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("nombre", "peso"),
                name="catalog_producto_nombre_peso_uniq",
            ),
            models.CheckConstraint(
                condition=Q(precio_unitario__gt=0),
                name="catalog_producto_precio_unitario_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(precio_desde_3__gt=0),
                name="catalog_producto_precio_desde_3_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(precio_desde_20__gt=0),
                name="catalog_producto_precio_desde_20_gt_0",
            ),
        ]
