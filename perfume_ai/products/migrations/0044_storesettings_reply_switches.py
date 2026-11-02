from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0043_alter_product_perfume_type")]

    operations = [
        migrations.AddField(
            model_name="storesettings",
            name="message_replies_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="storesettings",
            name="comment_replies_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
