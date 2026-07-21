from products.models import Conversation, Message


def create_conversation(store=None, platform="web", platform_sender_id=""):
    return Conversation.objects.create(store=store, platform=platform, platform_sender_id=platform_sender_id)

def get_or_create_platform_conversation(store, platform, sender_id):
    conversation, created = Conversation.objects.get_or_create(
        store=store,
        platform=platform,
        platform_sender_id=sender_id
    )
    return conversation, created


def get_conversation(conversation_id, store=None):
    try:
        if store:
            return Conversation.objects.get(id=conversation_id, store=store)
        return Conversation.objects.get(id=conversation_id)
    except (Conversation.DoesNotExist, ValueError, TypeError):
        return None


def save_message(conversation, role, content, internal_context=""):
    return Message.objects.create(
        conversation=conversation,
        role=role,
        content=content,
        internal_context=internal_context,
    )


def get_conversation_messages(conversation, limit=12):
    messages = conversation.messages.order_by("-created_at")[:limit]
    return reversed(messages)