from products.models import Conversation, Message


def create_conversation():
    return Conversation.objects.create()


def save_message(conversation, role, content):
    return Message.objects.create(
        conversation=conversation,
        role=role,
        content=content,
    )


def get_conversation_messages(conversation):
    return conversation.messages.order_by("created_at")