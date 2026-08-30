from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from inventory.models import MovimientoInventario
from orders.exceptions import (
    CapacidadInventarioExcedida,
    HistorialMovimientosCorrupto,
    TransicionPedidoInvalida,
)
from orders.models import DetallePedido, DireccionEnvio, EstadoPedido, Pedido
from orders.services import cancelar_pedido, marcar_pedido_entregado


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
    change_form_template = "admin/orders/pedido/change_form.html"
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

    def change_view(self, request, object_id, form_url="", extra_context=None):
        pedido = self.get_object(request, object_id)
        puede_operar = request.user.has_perm("orders.change_pedido")
        pendiente = pedido is not None and pedido.estado == EstadoPedido.PENDIENTE
        contexto = {
            **(extra_context or {}),
            "puede_transicionar_pedido": puede_operar and pendiente,
            "pedido_estado_terminal": pedido is not None and not pendiente,
        }
        if puede_operar and pendiente:
            contexto.update(
                {
                    "url_marcar_entregado": reverse(
                        "admin:orders_pedido_marcar_entregado",
                        args=(pedido.pk,),
                    ),
                    "url_cancelar_pedido": reverse(
                        "admin:orders_pedido_cancelar",
                        args=(pedido.pk,),
                    ),
                }
            )
        return super().change_view(request, object_id, form_url, contexto)

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/marcar-entregado/",
                self.admin_site.admin_view(self.marcar_entregado_view),
                name="orders_pedido_marcar_entregado",
            ),
            path(
                "<path:object_id>/cancelar/",
                self.admin_site.admin_view(self.cancelar_view),
                name="orders_pedido_cancelar",
            ),
        ]
        return custom_urls + super().get_urls()

    def marcar_entregado_view(self, request, object_id):
        return self._transicion_view(
            request=request,
            object_id=object_id,
            servicio=marcar_pedido_entregado,
            titulo="Marcar Pedido como entregado",
            accion="Marcar como entregado",
            advertencia=(
                "El Pedido quedará ENTREGADO de forma definitiva. "
                "Esta operación no modificará el Inventario."
            ),
            mensaje_exito=(
                "El Pedido fue marcado como entregado correctamente."
            ),
            mostrar_detalles=False,
        )

    def cancelar_view(self, request, object_id):
        return self._transicion_view(
            request=request,
            object_id=object_id,
            servicio=cancelar_pedido,
            titulo="Cancelar Pedido",
            accion="Cancelar Pedido",
            advertencia=(
                "El Pedido quedará CANCELADO de forma definitiva y se "
                "restituirán al Inventario las unidades descontadas."
            ),
            mensaje_exito=(
                "El Pedido fue cancelado y el Inventario fue restituido "
                "correctamente."
            ),
            mostrar_detalles=True,
        )

    def _transicion_view(
        self,
        *,
        request,
        object_id,
        servicio,
        titulo,
        accion,
        advertencia,
        mensaje_exito,
        mostrar_detalles,
    ):
        if not request.user.has_perm("orders.change_pedido"):
            raise PermissionDenied
        if request.method not in ("GET", "HEAD", "POST"):
            return HttpResponseNotAllowed(("GET", "HEAD", "POST"))

        pedido = self.get_object(request, object_id)
        if pedido is None:
            raise Http404

        if request.method == "POST":
            try:
                servicio(pedido_id=pedido.pk)
            except TransicionPedidoInvalida:
                self.message_user(
                    request,
                    "El Pedido ya no admite esa transición.",
                    level=messages.ERROR,
                )
            except HistorialMovimientosCorrupto:
                self.message_user(
                    request,
                    (
                        "No fue posible cancelar el Pedido porque su historial "
                        "de Inventario es inconsistente."
                    ),
                    level=messages.ERROR,
                )
            except CapacidadInventarioExcedida:
                self.message_user(
                    request,
                    (
                        "No fue posible restituir el Inventario porque se "
                        "alcanzó su capacidad máxima."
                    ),
                    level=messages.ERROR,
                )
            else:
                self.message_user(request, mensaje_exito, level=messages.SUCCESS)
            return redirect(
                reverse("admin:orders_pedido_change", args=(pedido.pk,))
            )

        request.current_app = self.admin_site.name
        contexto = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "pedido": pedido,
            "detalles": pedido.detalles.order_by("pk") if mostrar_detalles else (),
            "title": titulo,
            "accion": accion,
            "advertencia": advertencia,
            "url_detalle": reverse(
                "admin:orders_pedido_change",
                args=(pedido.pk,),
            ),
        }
        return TemplateResponse(
            request,
            "admin/orders/pedido/confirmar_transicion.html",
            contexto,
        )

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
