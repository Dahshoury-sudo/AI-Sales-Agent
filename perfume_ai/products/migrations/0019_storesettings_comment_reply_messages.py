from django.db import migrations
import django.db.models.fields


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0018_storesettings_messenger_access_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='storesettings',
            name='comment_reply_messages',
            field=django.db.models.fields.TextField(
                blank=True,
                default="✅ تم الرد في الخاص، راجع رسائلك!\n📩 جاوبناك في الخاص، اتفضل شوف!\nتم الرد في الإنبوكس ✅\nCheck your DM, we replied! 💬\nراجع رسائلك الخاصة، بعتنالك الرد 📬",
                help_text='رسائل الرد على التعليقات — كل سطر رسالة منفصلة، البوت يختار عشوائياً (حد أقصى 5 رسائل)',
            ),
        ),
    ]
