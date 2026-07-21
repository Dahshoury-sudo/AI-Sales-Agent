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
