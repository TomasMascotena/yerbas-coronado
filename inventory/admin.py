from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from inventory.exceptions import (
    CantidadMovimientoInvalida,
    ObservacionObligatoria,
    StockInsuficiente,
)
from inventory.forms import AjusteInventarioForm, MovimientoAdministrativoForm
from inventory.models import Inventario, MovimientoInventario
from inventory.services import (
    registrar_ajuste_negativo,
    registrar_ajuste_positivo,
    registrar_ingreso_mercaderia,
    registrar_venta_presencial,
)


@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    change_form_template = "admin/inventory/inventario/change_form.html"
    list_display = (
        "nombre_producto",
        "peso_producto",
        "cantidad_disponible",
        "producto_activo",
    )
    search_fields = ("producto__nombre", "producto__peso")
    list_filter = ("producto__activo",)
    fields = (
        "nombre_producto",
        "peso_producto",
        "cantidad_disponible",
        "producto_activo",
    )
    readonly_fields = fields
    actions = None

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("producto")

    @admin.display(description="Producto", ordering="producto__nombre")
    def nombre_producto(self, obj):
        return obj.producto.nombre

    @admin.display(description="Peso", ordering="producto__peso")
    def peso_producto(self, obj):
        return obj.producto.peso

    @admin.display(description="Activo", boolean=True, ordering="producto__activo")
    def producto_activo(self, obj):
        return obj.producto.activo

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

    def change_view(self, request, object_id, form_url="", extra_context=None):
        contexto = {
            **(extra_context or {}),
            "puede_operar_inventario": request.user.has_perm(
                "inventory.change_inventario"
            ),
        }
        return super().change_view(request, object_id, form_url, contexto)

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/ingreso-mercaderia/",
                self.admin_site.admin_view(self.ingreso_mercaderia_view),
                name="inventory_inventario_ingreso_mercaderia",
            ),
            path(
                "<path:object_id>/venta-presencial/",
                self.admin_site.admin_view(self.venta_presencial_view),
                name="inventory_inventario_venta_presencial",
            ),
            path(
                "<path:object_id>/ajuste-positivo/",
                self.admin_site.admin_view(self.ajuste_positivo_view),
                name="inventory_inventario_ajuste_positivo",
            ),
            path(
                "<path:object_id>/ajuste-negativo/",
                self.admin_site.admin_view(self.ajuste_negativo_view),
                name="inventory_inventario_ajuste_negativo",
            ),
        ]
        return custom_urls + super().get_urls()

    def ingreso_mercaderia_view(self, request, object_id):
        return self._operacion_view(
            request=request,
            object_id=object_id,
            form_class=MovimientoAdministrativoForm,
            servicio=registrar_ingreso_mercaderia,
            titulo="Ingresar mercadería",
            mensaje="Ingreso de mercadería registrado correctamente.",
        )

    def venta_presencial_view(self, request, object_id):
        return self._operacion_view(
            request=request,
            object_id=object_id,
            form_class=MovimientoAdministrativoForm,
            servicio=registrar_venta_presencial,
            titulo="Registrar venta presencial",
            mensaje="Venta presencial registrada correctamente.",
        )

    def ajuste_positivo_view(self, request, object_id):
        return self._operacion_view(
            request=request,
            object_id=object_id,
            form_class=AjusteInventarioForm,
            servicio=registrar_ajuste_positivo,
            titulo="Registrar ajuste positivo",
            mensaje="Ajuste positivo registrado correctamente.",
        )

    def ajuste_negativo_view(self, request, object_id):
        return self._operacion_view(
            request=request,
            object_id=object_id,
            form_class=AjusteInventarioForm,
            servicio=registrar_ajuste_negativo,
            titulo="Registrar ajuste negativo",
            mensaje="Ajuste negativo registrado correctamente.",
        )

    def _operacion_view(
        self,
        *,
        request,
        object_id,
        form_class,
        servicio,
        titulo,
        mensaje,
    ):
        if not request.user.has_perm("inventory.change_inventario"):
            raise PermissionDenied

        inventario = self.get_object(request, object_id)
        if inventario is None:
            raise Http404

        form = form_class(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                servicio(
                    inventario_id=inventario.pk,
                    cantidad=form.cleaned_data["cantidad"],
                    observacion=form.cleaned_data["observacion"],
                )
            except CantidadMovimientoInvalida:
                form.add_error(
                    "cantidad",
                    "La cantidad debe ser un entero mayor que cero.",
                )
            except ObservacionObligatoria:
                form.add_error(
                    "observacion",
                    "Debe ingresar una justificación para realizar el ajuste.",
                )
            except StockInsuficiente:
                form.add_error(
                    None,
                    "No hay stock suficiente para realizar la operación.",
                )
            else:
                self.message_user(request, mensaje, level=messages.SUCCESS)
                return redirect(
                    reverse(
                        "admin:inventory_inventario_change",
                        args=(inventario.pk,),
                    )
                )
            inventario.refresh_from_db()

        request.current_app = self.admin_site.name
        contexto = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "inventario": inventario,
            "form": form,
            "title": titulo,
        }
        return TemplateResponse(
            request,
            "admin/inventory/inventario/operacion.html",
            contexto,
        )


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = (
        "fecha_hora",
        "nombre_producto",
        "peso_producto",
        "tipo_movimiento",
        "cantidad",
        "observacion",
    )
    list_select_related = ("inventario__producto",)
    search_fields = (
        "inventario__producto__nombre",
        "inventario__producto__peso",
        "observacion",
    )
    list_filter = (
        "tipo_movimiento",
        ("fecha_hora", admin.DateFieldListFilter),
    )
    date_hierarchy = "fecha_hora"
    fields = (
        "inventario",
        "nombre_producto",
        "peso_producto",
        "fecha_hora",
        "tipo_movimiento",
        "cantidad",
        "observacion",
    )
    readonly_fields = fields
    actions = None

    @admin.display(description="Producto", ordering="inventario__producto__nombre")
    def nombre_producto(self, obj):
        return obj.inventario.producto.nombre

    @admin.display(description="Peso", ordering="inventario__producto__peso")
    def peso_producto(self, obj):
        return obj.inventario.producto.peso

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
