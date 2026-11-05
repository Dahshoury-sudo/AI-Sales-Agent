from django.db import migrations, models
import products.scent_tags


class Migration(migrations.Migration):
    dependencies = [("products", "0044_storesettings_reply_switches")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="scent_tags",
            field=models.JSONField(blank=True, default=list, validators=[products.scent_tags.validate_scent_tags]),
        ),
    ]
