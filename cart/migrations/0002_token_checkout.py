import uuid

from django.db import migrations, models


def asignar_tokens_checkout(apps, schema_editor):
    Carrito = apps.get_model("cart", "Carrito")
    for carrito in Carrito.objects.filter(token_checkout__isnull=True).iterator():
        carrito.token_checkout = uuid.uuid4()
        carrito.save(update_fields=("token_checkout",))


class Migration(migrations.Migration):
    dependencies = [
        ("cart", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="carrito",
            name="token_checkout",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(
            asignar_tokens_checkout,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="carrito",
            name="token_checkout",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddConstraint(
            model_name="carrito",
            constraint=models.UniqueConstraint(
                fields=("token_checkout",),
                name="cart_carrito_token_checkout_uniq",
            ),
        ),
    ]
