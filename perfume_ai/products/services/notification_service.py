from products.models import Notification


def create_notification(store, notif_type, title, message):
    """Create a notification for a store owner."""
    Notification.objects.create(
        store=store,
        type=notif_type,
        title=title,
        message=message,
    )


def notify_handoff(conversation):
    """Trigger a notification when a conversation is handed off to a human."""
    platform_labels = {
        "whatsapp": "واتساب",
        "messenger": "ماسنجر",
        "instagram": "انستجرام",
        "web": "الموقع",
    }
    platform = platform_labels.get(conversation.platform, conversation.platform or "غير معروف")
    create_notification(
        store=conversation.store,
        notif_type="handoff",
        title="محادثة تحتاج تدخل بشري",
        message=f"عميل على {platform} (محادثة #{conversation.id}) يحتاج التحدث مع موظف بشري.",
    )


def notify_new_order(order):
    """Trigger a notification when a new order is placed."""
    create_notification(
        store=order.store,
        notif_type="new_order",
        title="طلب جديد! 🛍️",
        message=f"طلب جديد من {order.customer_name} بقيمة {order.total_price} ج.م (طلب #{order.id}).",
    )


def notify_delivery_failure(conversation):
    """Tell the owner the bot's reply never reached the customer.

    Without this the failure lived only in the worker log: the reply is saved
    before it is sent, so the thread looks answered, and Celery reports success.
    The customer is left waiting, and the bot's own history now contains a turn it
    never actually delivered.
    """
    platform_labels = {
        "whatsapp": "واتساب",
        "messenger": "ماسنجر",
        "instagram": "انستجرام",
        "facebook": "تعليقات فيسبوك",
        "web": "الموقع",
    }
    platform = platform_labels.get(conversation.platform, conversation.platform or "غير معروف")
    create_notification(
        store=conversation.store,
        notif_type="delivery_failed",
        title="رد البوت لم يوصل للعميل ⚠️",
        message=(
            f"البوت رد على عميل من {platform} (محادثة #{conversation.id}) بس المنصة "
            f"رفضت توصيل الرسالة. العميل لسه مستني — راجع المحادثة وتواصل معاه."
        ),
    )


def notify_attachment_received(conversation):
    """Notify the store owner when a customer sends an image during a pending order.

    The image is very likely a payment receipt: the order exists and is pending,
    and the customer sent a photo instead of text. The agent should open the
    handoff dashboard and verify it manually.
    """
    platform_labels = {
        "whatsapp": "واتساب",
        "messenger": "ماسنجر",
        "instagram": "انستجرام",
        "web": "الموقع",
    }
    platform = platform_labels.get(conversation.platform, conversation.platform or "غير معروف")
    create_notification(
        store=conversation.store,
        notif_type="handoff",
        title="عميل بعت صورة (إيصال دفع؟) 💰",
        message=f"عميل على {platform} (محادثة #{conversation.id}) بعت صورة — ممكن يكون إيصال دفع. ادخل راجع المحادثة.",
    )
