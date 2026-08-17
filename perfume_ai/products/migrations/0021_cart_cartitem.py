from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0020_alter_storesettings_meta_access_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='Cart',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('customer_name', models.CharField(blank=True, max_length=200)),
                ('customer_phone', models.CharField(blank=True, max_length=50)),
                ('secondary_phone', models.CharField(blank=True, max_length=50)),
                ('shipping_address', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('conversation', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='cart', to='products.conversation')),
            ],
        ),
        migrations.CreateModel(
            name='CartItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('bottle_type', models.CharField(choices=[('normal', 'عادية'), ('original', 'أوريجينال')], default='normal', max_length=20)),
                ('cart', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='products.cart')),
                ('variant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='products.productvariant')),
            ],
            options={
                'unique_together': {('cart', 'variant', 'bottle_type')},
            },
        ),
    ]
