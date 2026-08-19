import django.db.models.deletion
from django.db import migrations, models

import products.encryption


class Migration(migrations.Migration):
    """Encrypt customer phones and addresses at rest, with a blind index for lookup.

    customer_name deliberately stays plaintext: admin search over it is a substring
    lookup, which no blind index can serve.

    The max_length jumps to 500 because Fernet ciphertext for an 11-digit phone is
    roughly 120 characters — the previous 50 would fail on save.

    Existing rows are left as they are: decrypt_value returns a value unchanged when it
    is not valid ciphertext, so legacy plaintext keeps reading correctly. Run
    `manage.py backfill_pii_encryption` to re-save them through the encrypted fields and
    populate customer_phone_hash.
    """

    dependencies = [
        ('products', '0031_store_usage_metering'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='customer_phone_hash',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AlterField(
            model_name='order',
            name='customer_phone',
            field=products.encryption.EncryptedCharField(max_length=500),
        ),
        migrations.AlterField(
            model_name='order',
            name='secondary_phone',
            field=products.encryption.EncryptedCharField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name='order',
            name='shipping_address',
            field=products.encryption.EncryptedTextField(),
        ),
        migrations.AlterField(
            model_name='cart',
            name='customer_phone',
            field=products.encryption.EncryptedCharField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name='cart',
            name='secondary_phone',
            field=products.encryption.EncryptedCharField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name='cart',
            name='shipping_address',
            field=products.encryption.EncryptedTextField(blank=True),
        ),
    ]
