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
    Notification.objects.get_or_create(
        dedupe_key=f"order:{order.pk}",
        defaults=dict(
            store=order.store,
            type="new_order",
            title="طلب جديد! 🛍️",
            message=f"طلب جديد من {order.customer_name} بقيمة {order.total_price} ج.م (طلب #{order.id}).",
        ),
    )


def notify_delivery_failure(conversation):
    """Flag a failed or uncertain delivery once per saved message for staff review."""
    platform_labels = {
        "whatsapp": "واتساب",
        "messenger": "ماسنجر",
        "instagram": "انستجرام",
        "facebook": "تعليقات فيسبوك",
        "web": "الموقع",
    }
    platform = platform_labels.get(conversation.platform, conversation.platform or "غير معروف")
    latest = conversation.messages.filter(role__in=["assistant", "agent"]).order_by("-id").first()
    uncertain = latest is not None and latest.delivery_status == "uncertain"
    Notification.objects.get_or_create(
        dedupe_key=f"delivery:{conversation.pk}:{latest.pk if latest else 0}",
        defaults=dict(
            store=conversation.store, type="delivery_failed",
            title="توصيل الرد مش مؤكد ⚠️" if uncertain else "رد البوت لم يوصل للعميل ⚠️",
            message=(f"محادثة #{conversation.id} على {platform}: " +
                ("المنصة ممكن تكون استلمت الرسالة. راجع المحادثة قبل إعادة الإرسال." if uncertain else
                 "المنصة رفضت توصيل الرسالة. راجع المحادثة وتواصل مع العميل.")),
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
