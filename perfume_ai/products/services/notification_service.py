from products.models import Notification


# One label map for every owner alert. There used to be three copies, each missing
# a different platform, so a Facebook commenter's handoff read "على facebook".
PLATFORM_LABELS = {
    "whatsapp": "واتساب",
    "messenger": "ماسنجر",
    "instagram": "انستجرام",
    "facebook": "تعليقات فيسبوك",
    "tiktok": "تيك توك",
    "web": "الموقع",
}


def platform_label(platform):
    return PLATFORM_LABELS.get(platform, platform or "غير معروف")


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
    platform = platform_label(conversation.platform)
    create_notification(
        store=conversation.store,
        notif_type="handoff",
        title="محادثة تحتاج تدخل بشري",
        message=f"عميل على {platform} (محادثة #{conversation.id}) يحتاج التحدث مع موظف بشري.",
    )


def notify_handoff_message(message):
    """One durable alert per inbound message, including attachment-only messages."""
    conversation = message.conversation
    Notification.objects.get_or_create(
        dedupe_key=f"handoff_message:{message.pk}",
        defaults=dict(
            store=conversation.store,
            source_message=message,
            type="handoff_message",
            title="رسالة جديدة في محادثة خدمة العملاء",
            message=f"محادثة #{conversation.pk} على {conversation.get_platform_display()}: "
                    f"{message.content[:200] or '📎 مرفق من العميل'}",
        ),
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
    platform = platform_label(conversation.platform)
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


def notify_integration_problem(store, title, message, dedupe_key):
    """Tell the owner a platform connection needs them, once per dedupe_key.

    Used when a TikTok connection lapses: the bot cannot answer there until the owner
    reconnects, and a repeating alert every hour would bury every other notification.
    """
    Notification.objects.get_or_create(
        dedupe_key=dedupe_key[:100],
        defaults=dict(store=store, type="integration", title=title, message=message),
    )


def notify_attachment_received(conversation):
    """Notify the store owner when a customer sends an image during a pending order.

    The image is very likely a payment receipt: the order exists and is pending,
    and the customer sent a photo instead of text. The agent should open the
    handoff dashboard and verify it manually.
    """
    platform = platform_label(conversation.platform)
    create_notification(
        store=conversation.store,
        notif_type="handoff",
        title="عميل بعت صورة (إيصال دفع؟) 💰",
        message=f"عميل على {platform} (محادثة #{conversation.id}) بعت صورة — ممكن يكون إيصال دفع. ادخل راجع المحادثة.",
    )
