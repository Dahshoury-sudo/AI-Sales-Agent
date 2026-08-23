import logging
import random

from celery import shared_task

from products.models import Store
from products.services.conversation_service import (
    get_or_create_platform_conversation,
    get_conversation_messages,
    build_llm_history,
    save_message,
)
from products.services.router import route
from products.services.reply_sanitizer import sanitize_reply
from products.services.meta_service import (
    send_platform_message,
    reply_to_comment,
    reply_to_ig_comment,
    send_private_reply,
    fetch_post_content,
    conversation_platform_for,
)
from products.services.notification_service import notify_delivery_failure
from products.services import rate_limit

logger = logging.getLogger(__name__)


def _flag_undelivered_reply(conversation):
    """The bot's reply was written and saved, but the platform refused to deliver it.

    The message stays saved — it is the record of what the bot tried to say, and
    deleting it would lose that. But the conversation is flagged for a human,
    because two things are now true and neither is visible otherwise: the customer
    is still waiting, and the bot's own history contains a turn it never delivered,
    which will feed the next message's context as though it had.

    No retry: the usual cause is Meta refusing a recipient who has no role on the
    app while it is in development mode, which fails identically every time.
    """
    logger.error(
        f"Reply for conversation #{conversation.id} ({conversation.platform}) was saved "
        f"but the platform rejected delivery; flagging for a human."
    )
    if not conversation.needs_human:
        conversation.needs_human = True
        conversation.save(update_fields=["needs_human"])
    notify_delivery_failure(conversation)


# ── Cost limiting on the message path ───────────────────────────────────────
#
# This is the only real limit on the expensive path. `route()` spends two to three model
# calls per message, and before this nothing capped per store or per end customer: the old
# WebhookThrottle keyed on Meta's IP (one global bucket for every store, and a 429 that
# makes Meta disable the subscription), and usage_service counts without ever blocking.
#
# A real conversation never approaches these. Fifteen messages in a minute from one sender
# is a script or a stuck client, not a shopper — the hourly window catches the slower
# version of the same abuse.
SENDER_PER_MINUTE = 15
SENDER_PER_HOUR = 150

# Aggregate ceiling per store, replacing the global bucket. Generous: what matters is that
# one store's traffic can no longer throttle another's, which the IP-keyed version could.
STORE_PER_MINUTE = 120

# How many times a message may be pushed back before it is dropped. Unbounded requeueing
# turns a flood into an unbounded queue, which is worse than dropping — the queue is shared
# with every other store's live conversations.
MAX_DEFERRALS = 3

# Added per deferral so re-dispatched messages do not all land on the same instant and race.
# Without it a sender's queued messages can be delivered out of order, and the bot's own
# saved history then misrepresents the conversation, which poisons the next turn's context.
DEFERRAL_STAGGER_SECONDS = 5


def _rate_limit_buckets(store_id, platform, sender_id):
    sender = f"{store_id}:{platform}:{sender_id}"
    return (
        (f"sender:{sender}", SENDER_PER_MINUTE, 60),
        (f"sender-hour:{sender}", SENDER_PER_HOUR, 3600),
        (f"store:{store_id}", STORE_PER_MINUTE, 60),
    )


def _defer_or_drop(store_id, platform, sender_id, text, deferrals, retry_after, exhausted):
    """Push an over-limit message back, or drop it once it has waited long enough.

    Deferring rather than dropping is the deliberate choice: a dropped message is a real
    customer's question vanishing, and this is a sales bot. Nothing is saved to the
    conversation before this point, so a deferred message leaves no half-written turn behind.
    """
    if deferrals >= MAX_DEFERRALS:
        logger.warning(
            "Dropping message after %d deferrals — store=%s platform=%s sender=%s "
            "bucket=%s. Sustained rate above the limit.",
            deferrals, store_id, platform, sender_id, exhausted,
        )
        return

    countdown = retry_after + deferrals * DEFERRAL_STAGGER_SECONDS
    logger.info(
        "Rate limit hit (%s) — deferring message %ds, attempt %d/%d, store=%s sender=%s.",
        exhausted, countdown, deferrals + 1, MAX_DEFERRALS, store_id, sender_id,
    )
    process_incoming_message.apply_async(
        args=[store_id, platform, sender_id, text],
        kwargs={"deferrals": deferrals + 1},
        countdown=countdown,
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=15,          # wait 15 s before retrying
    name='products.tasks.process_incoming_message',
    acks_late=True,                   # only ack after the task succeeds
)
def process_incoming_message(self, store_id, platform, sender_id, text, deferrals=0):
    """
    Process an incoming message from a Meta platform (WhatsApp / Messenger / Instagram).
    Runs inside a Celery worker so the webhook endpoint returns 200 immediately.

    Retry behaviour:
      - Retries up to 3 times with a 15-second delay on any unhandled exception.
      - Uses acks_late so the message is never lost if the worker crashes mid-task.

    `deferrals` counts how many times this message has been pushed back by the rate limit.
    It is separate from Celery's retry count on purpose: `max_retries` is the budget for
    genuine failures, and a deferral is not a failure — spending a retry on one would both
    burn that budget and log an exception for normal back-pressure.
    """
    buckets = _rate_limit_buckets(store_id, platform, sender_id)
    if deferrals:
        # Already counted on first entry; only ask whether the window has rolled.
        allowed, retry_after = rate_limit.peek(buckets)
        exhausted = None if allowed else "still over limit"
    else:
        allowed, retry_after, exhausted = rate_limit.hit_all(buckets)

    if not allowed:
        _defer_or_drop(
            store_id, platform, sender_id, text, deferrals, retry_after, exhausted
        )
        return

    try:
        store = Store.objects.get(id=store_id)
        conversation, created = get_or_create_platform_conversation(store, platform, sender_id)

        # If a human agent has taken over, just save the message and stop.
        if conversation.needs_human:
            save_message(conversation, "user", text)
            return

        history = build_llm_history(conversation)

        save_message(conversation, "user", text)

        reply, context = route(text, history, store, conversation)
        reply = sanitize_reply(reply, conversation)
        save_message(conversation, "assistant", reply, internal_context=context)
        delivered = send_platform_message(conversation, reply)
        # None means nothing needed sending (web conversations); only False is a
        # delivery failure.
        if delivered is False:
            _flag_undelivered_reply(conversation)

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
        # `platform` identifies where the comment came from and drives the
        # comment-reply endpoint below. The *conversation* is filed under
        # conversation_platform_for(platform) so a Facebook comment and a later
        # Facebook DM from the same person land in one thread — they share a
        # page-scoped ID — and the bot keeps the comment context.
        conversation, _ = get_or_create_platform_conversation(
            store, conversation_platform_for(platform), commenter_id
        )
        history = build_llm_history(conversation)
        save_message(conversation, "user", comment_text)  # save original comment only

        ai_reply, context = route(enriched_text, history, store, conversation)
        ai_reply = sanitize_reply(ai_reply, conversation)
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

