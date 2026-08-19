from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the "agent" role for human replies sent through the dashboard handoff.

    Choices-only change, so nothing happens to the column — but the model state has
    to match or makemigrations reports a pending change on every run.
    """

    dependencies = [
        ('products', '0028_alter_notification_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='role',
            field=models.CharField(
                choices=[
                    ('user', 'User'),
                    ('assistant', 'Assistant'),
                    ('agent', 'Human Agent'),
                ],
                max_length=20,
            ),
        ),
    ]
