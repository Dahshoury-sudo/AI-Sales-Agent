import logging
import threading

from products.models import Store
from products.services.conversation_service import get_or_create_platform_conversation, get_conversation_messages, save_message
from products.services.router import route
from products.services.meta_service import send_platform_message

logger = logging.getLogger(__name__)


def process_incoming_message(store_id, platform, sender_id, text):
    """
    Process an incoming message from a Meta platform.
    Designed to run in a background thread so the webhook can respond immediately.
    """
    try:
        store = Store.objects.get(id=store_id)
        conversation, created = get_or_create_platform_conversation(store, platform, sender_id)

        if conversation.needs_human:
            save_message(conversation, "user", text)
            return

        past_messages = get_conversation_messages(conversation)
        history = [{"role": msg.role, "content": msg.content} for msg in past_messages]

        save_message(conversation, "user", text)

        reply, context = route(text, history, store, conversation)
        save_message(conversation, "assistant", reply, internal_context=context)
        send_platform_message(conversation, reply)

    except Exception as e:
        logger.exception(f"Error processing background message (store={store_id}, platform={platform}): {e}")


def process_message_async(store_id, platform, sender_id, text):
    """
    Launch process_incoming_message in a background thread.
    This allows the webhook to return 200 to Meta immediately.
    """
    thread = threading.Thread(
        target=process_incoming_message,
        args=(store_id, platform, sender_id, text),
        daemon=True,
    )
    thread.start()
