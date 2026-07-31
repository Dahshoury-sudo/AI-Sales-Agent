import logging

from celery import shared_task

from products.models import Store
from products.services.conversation_service import (
    get_or_create_platform_conversation,
    get_conversation_messages,
    save_message,
)
from products.services.router import route
from products.services.meta_service import send_platform_message

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=15,          # wait 15 s before retrying
    name='products.tasks.process_incoming_message',
    acks_late=True,                   # only ack after the task succeeds
)
def process_incoming_message(self, store_id, platform, sender_id, text):
    """
    Process an incoming message from a Meta platform (WhatsApp / Messenger / Instagram).
    Runs inside a Celery worker so the webhook endpoint returns 200 immediately.

    Retry behaviour:
      - Retries up to 3 times with a 15-second delay on any unhandled exception.
      - Uses acks_late so the message is never lost if the worker crashes mid-task.
    """
    try:
        store = Store.objects.get(id=store_id)
        conversation, created = get_or_create_platform_conversation(store, platform, sender_id)

        # If a human agent has taken over, just save the message and stop.
        if conversation.needs_human:
            save_message(conversation, "user", text)
            return

        past_messages = get_conversation_messages(conversation)
        history = [{"role": msg.role, "content": msg.content} for msg in past_messages]

        save_message(conversation, "user", text)

        reply, context = route(text, history, store, conversation)
        save_message(conversation, "assistant", reply, internal_context=context)
        send_platform_message(conversation, reply)

    except Exception as exc:
        logger.exception(
            f"Error processing message — store={store_id}, platform={platform}, "
            f"attempt={self.request.retries + 1}/{self.max_retries + 1}: {exc}"
        )
        # Re-raise so Celery can retry automatically
        raise self.retry(exc=exc)


def process_message_async(store_id, platform, sender_id, text):
    """
    Enqueue process_incoming_message as a Celery task.

    The calling view (views_meta.py) stays unchanged — it still calls
    process_message_async() and returns 200 to Meta immediately.
    """
    process_incoming_message.delay(store_id, platform, sender_id, text)
