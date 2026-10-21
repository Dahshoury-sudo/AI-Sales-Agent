from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0037_conversation_integrity")]

    operations = [
        migrations.AddField(
            model_name="storesettings",
            name="website_ordering_enabled",
            field=models.BooleanField(
                default=False,
                help_text="هل يمكن للعميل طلب العروض والباندلز من موقع المتجر؟ فعّله فقط لو الخدمة متاحة.",
            ),
        ),
    ]
