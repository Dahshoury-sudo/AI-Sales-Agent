"""Move the previously hardcoded payment block into per-store settings.

order_service.create_order_in_db used to emit one store's InstaPay account to
every store's customers. Now that the text lives on StoreSettings, the store it
actually belonged to needs it seeded, otherwise deploying this would silently
drop the payment details from its order confirmations.

Matched on the store name containing "perfamix" because the hardcoded link itself
was "ipn.eg/S/perfamix2/instapay" — that is the only store the text was ever
correct for. Every other store is left blank on purpose and falls back to "the
sales team will contact you with payment details" until its owner fills it in.
"""

from django.db import migrations


LEGACY_PAYMENT_INSTRUCTIONS = """📌 لتأكيد وشحن الأوردر برجاء تحويل جزء من المبلغ (عربون لا يقل عن ٢٥٠ج) والباقي عند الاستلام، أو تحويل المبلغ كاملاً.
⚠️ في حالة إلغاء الأوردر بعد تأكيده لا يتم استرداد العربون لأنه بيكون اتحضر وخرج لشركة الشحن.

💳 طرق التحويل:
إنستاباي: https://ipn.eg/S/perfamix2/instapay/3dFdnw
(اضغط الرابط لإرسال نقود إلى perfamix2@instapay)

برجاء إرسال سكرين شوت بالتحويل هنا فور الانتهاء لتأكيد الشحن."""


def seed_legacy_payment_instructions(apps, schema_editor):
    StoreSettings = apps.get_model('products', 'StoreSettings')
    StoreSettings.objects.filter(
        store__name__icontains='perfamix', payment_instructions='',
    ).update(payment_instructions=LEGACY_PAYMENT_INSTRUCTIONS)


def clear_legacy_payment_instructions(apps, schema_editor):
    StoreSettings = apps.get_model('products', 'StoreSettings')
    StoreSettings.objects.filter(
        payment_instructions=LEGACY_PAYMENT_INSTRUCTIONS,
    ).update(payment_instructions='')


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0022_storesettings_payment_instructions'),
    ]

    operations = [
        migrations.RunPython(
            seed_legacy_payment_instructions,
            clear_legacy_payment_instructions,
        ),
    ]
