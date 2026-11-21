"""Read live store policy by inbound source, independently of reply destination."""
from products.models import StoreSettings

# "facebook" is the legacy conversation label for a thread started by a Page comment;
# its direct messages arrive through Messenger, so the Messenger switch governs them.
MESSAGE_SWITCHES = {
    "whatsapp": "whatsapp_replies_enabled",
    "messenger": "messenger_replies_enabled",
    "facebook": "messenger_replies_enabled",
    "instagram": "instagram_replies_enabled",
}
COMMENT_SWITCHES = {
    "facebook": "facebook_comment_replies_enabled",
    "instagram": "instagram_comment_replies_enabled",
}


def message_replies_enabled(store_id, platform):
    field = MESSAGE_SWITCHES.get(platform)
    return True if field is None else _enabled(store_id, field)


def comment_replies_enabled(store_id, platform):
    field = COMMENT_SWITCHES.get(platform)
    return True if field is None else _enabled(store_id, field)


def _enabled(store_id, field):
    # A missing settings row retains legacy behavior. Avoid cached related objects:
    # a Celery task can outlive the owner's most recent settings change.
    value = StoreSettings.objects.filter(store_id=store_id).values_list(field, flat=True).first()
    return value is not False
