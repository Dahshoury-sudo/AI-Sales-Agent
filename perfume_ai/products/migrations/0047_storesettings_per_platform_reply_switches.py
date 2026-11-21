from django.db import migrations, models

MESSAGE_FIELDS = ("whatsapp_replies_enabled", "messenger_replies_enabled", "instagram_replies_enabled")
COMMENT_FIELDS = ("facebook_comment_replies_enabled", "instagram_comment_replies_enabled")


def split_switches(apps, schema_editor):
    """Each platform starts where its old grouped switch was, so nothing that was off turns on."""
    StoreSettings = apps.get_model("products", "StoreSettings")
    for messages in (True, False):
        for comments in (True, False):
            StoreSettings.objects.filter(
                message_replies_enabled=messages, comment_replies_enabled=comments,
            ).update(**{f: messages for f in MESSAGE_FIELDS}, **{f: comments for f in COMMENT_FIELDS})


def merge_switches(apps, schema_editor):
    """A grouped switch is on only if every platform it covers is on: rollback never adds replies."""
    StoreSettings = apps.get_model("products", "StoreSettings")
    for row in StoreSettings.objects.all():
        row.message_replies_enabled = all(getattr(row, f) for f in MESSAGE_FIELDS)
        row.comment_replies_enabled = all(getattr(row, f) for f in COMMENT_FIELDS)
        row.save(update_fields=["message_replies_enabled", "comment_replies_enabled"])


class Migration(migrations.Migration):
    dependencies = [("products", "0046_storesettings_promotions")]

    operations = [
        *[migrations.AddField(model_name="storesettings", name=name, field=models.BooleanField(default=True))
          for name in MESSAGE_FIELDS + COMMENT_FIELDS],
        migrations.RunPython(split_switches, merge_switches),
        migrations.RemoveField(model_name="storesettings", name="message_replies_enabled"),
        migrations.RemoveField(model_name="storesettings", name="comment_replies_enabled"),
    ]
