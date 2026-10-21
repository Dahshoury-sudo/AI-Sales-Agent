import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0038_storesettings_website_ordering_enabled")]

    operations = [
        migrations.AddField(
            model_name="message", name="handoff_unread", field=models.BooleanField(default=False),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["conversation", "handoff_unread"], name="msg_handoff_unread_idx"),
        ),
        migrations.AddField(
            model_name="notification", name="source_message",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="notifications", to="products.message"),
        ),
        migrations.AlterField(
            model_name="notification", name="type",
            field=models.CharField(max_length=50, choices=[
                ("handoff", "Human Handoff Required"),
                ("handoff_message", "New Message During Handoff"),
                ("new_order", "New Order"), ("low_stock", "Low Stock"),
                ("delivery_failed", "Reply Not Delivered"), ("usage_warning", "Approaching Monthly Limit"),
            ]),
        ),
    ]
