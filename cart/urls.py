from django.urls import path

from cart import views


app_name = "cart"

urlpatterns = [
    path("", views.detalle, name="detalle"),
    path(
        "agregar/<int:producto_id>/",
        views.agregar_producto,
        name="agregar_producto",
    ),
    path(
        "items/<int:item_id>/cantidad/",
        views.establecer_cantidad,
        name="establecer_cantidad",
    ),
    path(
        "items/<int:item_id>/eliminar/",
        views.eliminar_item,
        name="eliminar_item",
    ),
    path("vaciar/", views.vaciar, name="vaciar"),
]
