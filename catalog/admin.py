from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from catalog.models import Producto
from catalog.services import crear_producto_con_inventario


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "peso",
        "precio_unitario",
        "precio_desde_3",
        "precio_desde_20",
        "cantidad_disponible_actual",
        "activo",
    )
    search_fields = ("nombre", "peso")
    list_filter = ("activo",)
    readonly_fields = (
        "cantidad_disponible_actual",
        "administrar_inventario",
    )
    actions = ("activar_productos", "inactivar_productos")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("inventario")

    @admin.display(description="Stock")
    def cantidad_disponible_actual(self, obj):
        if obj is None:
            return "-"
        return obj.inventario.cantidad_disponible

    @admin.display(description="Inventario")
    def administrar_inventario(self, obj):
        if obj is None:
            return "-"
        url = reverse(
            "admin:inventory_inventario_change",
            args=(obj.inventario.pk,),
        )
        return format_html('<a href="{}">Ver Inventario</a>', url)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        crear_producto_con_inventario(producto=obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Activar Productos seleccionados")
    def activar_productos(self, request, queryset):
        queryset.update(activo=True)

    @admin.action(description="Inactivar Productos seleccionados")
    def inactivar_productos(self, request, queryset):
        queryset.update(activo=False)
