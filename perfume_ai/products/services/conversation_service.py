from products.models import Conversation, Message


def create_conversation(store=None):
    return Conversation.objects.create(store=store)


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