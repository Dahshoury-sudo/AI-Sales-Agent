from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Per-store monthly usage counting, plus the cap it is measured against.

    Nothing counted model-billed messages before this, and the Messenger/Instagram
    path bypasses the per-minute DRF throttles entirely, so the subscription tiers
    could not be enforced or invoiced.
    """

    dependencies = [
        ('products', '0030_conversation_preferences'),
    ]

    operations = [
        migrations.AddField(
            model_name='storesettings',
            name='monthly_message_cap',
            field=models.PositiveIntegerField(
                blank=True, null=True,
                help_text='الحد الشهري للرسائل اللي بتستهلك الـ AI. فاضي = بدون حد.',
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='type',
            field=models.CharField(
                choices=[
                    ('handoff', 'Human Handoff Required'),
                    ('new_order', 'New Order'),
                    ('low_stock', 'Low Stock'),
                    ('delivery_failed', 'Reply Not Delivered'),
                    ('usage_warning', 'Approaching Monthly Limit'),
                ],
                max_length=50,
            ),
        ),
        migrations.CreateModel(
            name='StoreMonthlyUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('period', models.DateField()),
                ('llm_messages', models.PositiveIntegerField(default=0)),
                ('warned_at_80', models.BooleanField(default=False)),
                ('warned_at_cap', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='usage', to='products.store')),
            ],
            options={
                'ordering': ['-period'],
            },
        ),
        migrations.AddConstraint(
            model_name='storemonthlyusage',
            constraint=models.UniqueConstraint(
                fields=('store', 'period'), name='one_usage_row_per_store_month'
            ),
        ),
    ]
