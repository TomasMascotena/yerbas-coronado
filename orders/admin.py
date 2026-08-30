from django.contrib import admin

from inventory.models import MovimientoInventario
from orders.models import DetallePedido, DireccionEnvio, Pedido


class _InlineHistoricoSoloLectura:
    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        opts = self.parent_model._meta
        return request.user.has_perm(
            f"{opts.app_label}.view_{opts.model_name}"
        ) or request.user.has_perm(
            f"{opts.app_label}.change_{opts.model_name}"
        )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DireccionEnvioInline(_InlineHistoricoSoloLectura, admin.StackedInline):
    model = DireccionEnvio
    fields = (
        "calle",
        "numero",
        "piso",
        "departamento",
        "localidad",
        "provincia",
        "codigo_postal",
        "referencias",
    )
    readonly_fields = fields
    verbose_name_plural = "Dirección de envío histórica"


class DetallePedidoInline(_InlineHistoricoSoloLectura, admin.TabularInline):
    model = DetallePedido
    fields = (
        "producto",
        "nombre_producto",
        "peso_producto",
        "cantidad",
        "precio_unitario_aplicado",
        "subtotal",
    )
    readonly_fields = fields
    ordering = ("pk",)
    verbose_name_plural = "Detalles históricos del Pedido"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("producto")


class MovimientoPedidoInline(_InlineHistoricoSoloLectura, admin.TabularInline):
    model = MovimientoInventario
    fk_name = "pedido"
    fields = (
        "fecha_hora",
        "inventario",
        "tipo_movimiento",
        "cantidad",
        "observacion",
    )
    readonly_fields = fields
    ordering = ("fecha_hora", "pk")
    verbose_name_plural = "Movimientos de Inventario asociados"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("inventario", "inventario__producto")
        )


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "numero_pedido",
        "fecha_hora_creacion",
        "estado",
        "nombre_cliente",
        "apellido_cliente",
        "dni_cliente",
        "modalidad_entrega",
        "cantidad_total",
        "importe_total",
    )
    list_filter = (
        "estado",
        "modalidad_entrega",
        ("fecha_hora_creacion", admin.DateFieldListFilter),
    )
    search_fields = (
        "numero_pedido",
        "dni_cliente",
        "nombre_cliente",
        "apellido_cliente",
        "telefono_cliente",
    )
    ordering = ("-fecha_hora_creacion", "-pk")
    fieldsets = (
        (
            "Identificación",
            {
                "fields": (
                    "numero_pedido",
                    "fecha_hora_creacion",
                    "estado",
                )
            },
        ),
        (
            "Comprador histórico",
            {
                "fields": (
                    "cliente",
                    "nombre_cliente",
                    "apellido_cliente",
                    "dni_cliente",
                    "telefono_cliente",
                )
            },
        ),
        (
            "Operación",
            {
                "fields": (
                    "modalidad_entrega",
                    "cantidad_total",
                    "importe_total",
                    "observaciones",
                )
            },
        ),
    )
    readonly_fields = (
        "numero_pedido",
        "fecha_hora_creacion",
        "estado",
        "cliente",
        "nombre_cliente",
        "apellido_cliente",
        "dni_cliente",
        "telefono_cliente",
        "modalidad_entrega",
        "cantidad_total",
        "importe_total",
        "observaciones",
    )
    actions = None

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("cliente", "direccion_envio")
        )

    def get_inlines(self, request, obj):
        inlines = [DetallePedidoInline, MovimientoPedidoInline]
        if obj is not None:
            try:
                obj.direccion_envio
            except Pedido.direccion_envio.RelatedObjectDoesNotExist:
                pass
            else:
                inlines.insert(0, DireccionEnvioInline)
        return inlines

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions
