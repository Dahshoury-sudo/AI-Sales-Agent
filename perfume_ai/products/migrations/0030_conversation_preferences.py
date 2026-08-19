from django.db import migrations, models


class Migration(migrations.Migration):
    """Carry a customer's stated preferences past the 8-message history window.

    On Conversation rather than Cart: clear_cart deletes the cart row when an order
    completes, and a customer's budget and taste should survive a purchase.
    """

    dependencies = [
        ('products', '0029_alter_message_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='preferences',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
