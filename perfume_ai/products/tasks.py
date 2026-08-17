import logging
import random

from celery import shared_task

from products.models import Store
from products.services.conversation_service import (
    get_or_create_platform_conversation,
    get_conversation_messages,
    save_message,
)
from products.services.router import route
from products.services.meta_service import (
    send_platform_message,
    reply_to_comment,
    reply_to_ig_comment,
    send_private_reply,
    fetch_post_content,
)

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


# ── Comment Auto-Reply ──────────────────────────────────────────────────────

# How long to wait before replying to a comment, so the reply doesn't look
# instantaneous. Kept short deliberately: the wait used to be 20-40s held inside
# the worker, and comment tasks share the default queue and the 2-slot pool with
# customer DMs and WhatsApp messages, so a post drawing a few comments stalled
# live conversations. The delay is now scheduled rather than slept through (see
# process_comment_async), and shortened so even the reservation the delayed task
# holds is brief.
COMMENT_REPLY_DELAY_RANGE = (5, 8)


@shared_task(bind=True, max_retries=3, default_retry_delay=60 * 5)
def process_comment_task(self, store_id, platform, comment_id, commenter_id, comment_text, post_id=""):
    """
    Background task to process a Facebook or Instagram comment.
    Features:
    - Fetches context from the post (caption)
    - Asks AI to generate a reply
    - Picks a random pre-defined public reply message
    - Posts the public reply
    - Sends the AI answer as a private DM

    The humanising delay happens before this task is dispatched, not inside it —
    see process_comment_async.
    """
    try:
        # ── 1. Load store + settings ────────────────────────────────────────
        store = Store.objects.get(id=store_id)
        store_settings = store.settings
        token = store_settings.messenger_access_token or store_settings.meta_access_token

        if not token:
            logger.warning(f"No Messenger token for store {store_id}, skipping comment reply.")
            return

        # ── 2. Fetch post content for context ───────────────────────────────
        post_context = ""
        if post_id:
            post_text = fetch_post_content(post_id, token, platform)
            if post_text:
                post_context = f"[سياق البوست المُعلَّق عليه]: {post_text}\n\n"
                logger.info(f"Fetched post context for {post_id}: {post_text[:80]}...")

        # Build the enriched message: post context + customer's comment
        enriched_text = f"{post_context}[تعليق العميل]: {comment_text}"

        # ── 3. Generate AI answer ───────────────────────────────────────────
        conversation, _ = get_or_create_platform_conversation(store, platform, commenter_id)
        past_messages = get_conversation_messages(conversation)
        history = [{"role": msg.role, "content": msg.content} for msg in past_messages]
        save_message(conversation, "user", comment_text)  # save original comment only

        ai_reply, context = route(enriched_text, history, store, conversation)
        save_message(conversation, "assistant", ai_reply, internal_context=context)

        # ── 4. Pick a random public reply message ───────────────────────────
        raw_messages = store_settings.comment_reply_messages or ""
        variations = [m.strip() for m in raw_messages.split("\n") if m.strip()]
        if not variations:
            variations = ["✅ Check your DM!"]
        public_reply = random.choice(variations)

        # ── 5. Post public reply on the comment ─────────────────────────────
        if platform == "instagram":
            reply_to_ig_comment(comment_id, public_reply, token)
        else:
            reply_to_comment(comment_id, public_reply, token)

        logger.info(f"Posted public reply on {platform} comment {comment_id}: '{public_reply}'")

        # ── 6. Send private DM with the AI answer ───────────────────────────
        # Private replies always go through the Facebook Page endpoint
        send_private_reply(store_settings.facebook_page_id, comment_id, ai_reply, token)
        logger.info(f"Sent private reply for {platform} comment {comment_id}")

    except Exception as exc:
        logger.exception(
            f"Error processing comment {comment_id} — store={store_id}, "
            f"attempt={self.request.retries + 1}/{self.max_retries + 1}: {exc}"
        )
        raise self.retry(exc=exc)


def process_comment_async(store_id, platform, comment_id, commenter_id, comment_text, post_id=""):
    """Schedule process_comment_task — returns 200 to Facebook immediately.

    The humanising delay is a `countdown` rather than a sleep inside the task, so
    the worker is free for other work while the reply waits. That matters because
    comment tasks share the default queue and the 2-slot pool with customer DMs
    and WhatsApp messages: a sleeping task held a slot outright, and a couple of
    comments could stall live conversations.
    """
    countdown = random.randint(*COMMENT_REPLY_DELAY_RANGE)
    logger.info(
        f"Comment {comment_id}: replying in {countdown}s "
        f"(scheduled, not blocking a worker)."
    )
    process_comment_task.apply_async(
        args=[store_id, platform, comment_id, commenter_id, comment_text, post_id],
        countdown=countdown,
    )

