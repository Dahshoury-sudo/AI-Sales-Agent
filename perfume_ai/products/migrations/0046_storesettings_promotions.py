from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0045_product_scent_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="storesettings",
            name="promotions",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "العروض والباندلز والخصومات الحالية: الأسماء، المحتويات، الأسعار والشروط. "
                    "لو فاضية، البوت هيطلب تأكيد التفاصيل من خدمة العملاء."
                ),
            ),
        ),
    ]
