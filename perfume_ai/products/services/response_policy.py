"""Read live store policy by inbound source, independently of reply destination."""
from products.models import StoreSettings


def message_replies_enabled(store_id, platform):
    if platform not in ("messenger", "instagram", "whatsapp", "facebook"):
        return True
    return _enabled(store_id, "message_replies_enabled")


def comment_replies_enabled(store_id):
    return _enabled(store_id, "comment_replies_enabled")


def _enabled(store_id, field):
    # A missing settings row retains legacy behavior. Avoid cached related objects:
    # a Celery task can outlive the owner's most recent settings change.
    value = StoreSettings.objects.filter(store_id=store_id).values_list(field, flat=True).first()
    return value is not False
