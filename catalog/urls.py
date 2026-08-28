from django.urls import path

from catalog import views


app_name = "catalog"

urlpatterns = [
    path("", views.producto_list, name="producto_list"),
    path(
        "productos/<int:pk>/",
        views.producto_detail,
        name="producto_detail",
    ),
]
