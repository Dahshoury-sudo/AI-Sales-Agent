from django.db import migrations
from products.encryption import EncryptedTextField


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0017_staticfaq'),
    ]

    operations = [
        migrations.AddField(
            model_name='storesettings',
            name='messenger_access_token',
            field=EncryptedTextField(blank=True, help_text='Messenger & Instagram Page Access Token'),
        ),
    ]
