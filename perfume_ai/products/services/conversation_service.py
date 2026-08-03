from datetime import timedelta
from django.utils import timezone
from products.models import Conversation, Message


def create_conversation(store=None, platform="web", platform_sender_id=""):
    return Conversation.objects.create(store=store, platform=platform, platform_sender_id=platform_sender_id)

def get_or_create_platform_conversation(store, platform, sender_id):
    # Get the latest conversation for this user
    conversation = Conversation.objects.filter(
        store=store,
        platform=platform,
        platform_sender_id=sender_id
    ).order_by('-created_at').first()

    now = timezone.now()
    created = False

    if conversation:
        # Check the last message in this conversation
        last_message = conversation.messages.order_by('-created_at').first()
        
        # If there's a last message and it's older than 24 hours, create a new conversation
        if last_message and (now - last_message.created_at) > timedelta(hours=24):
            conversation = create_conversation(store, platform, sender_id)
            created = True
        # If there are no messages yet (edge case) or last message is recent, use the existing one
    else:
        # No previous conversation found, create a new one
        conversation = create_conversation(store, platform, sender_id)
        created = True

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


def get_conversation_messages(conversation, limit=8):
    messages = conversation.messages.order_by("-created_at")[:limit]
    return reversed(messages)