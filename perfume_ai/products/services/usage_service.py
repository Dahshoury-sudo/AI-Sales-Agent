"""Per-store monthly counting of model-billed messages.

Nothing counted anything before this. `throttles.py` limits requests per *minute* and
only on the DRF views; the Messenger and Instagram path runs views_meta → Celery and
never touches them, so the traffic that generates the OpenAI bill had no per-store
control at all and the subscription tiers were unenforceable.

Deliberately advisory. Exceeding a cap notifies the store owner and shows on the
invoice; it never stops the bot answering. A bot that goes silent mid-sale costs the
store more than the overage costs us, and a hard cutoff on a Friday night is a support
call either way.
"""

import logging

from django.db.models import F
from django.utils import timezone

from products.models import StoreMonthlyUsage
from .notification_service import create_notification

logger = logging.getLogger(__name__)

# Warn here first, then again on reaching the cap. Each fires at most once a month.
WARN_RATIO = 0.8


def current_period(today=None):
    """The first of the month — one usage row per store per calendar month."""
    return (today or timezone.localdate()).replace(day=1)


def monthly_cap(store):
    """The store's allowance, or None for unlimited."""
    try:
        return store.settings.monthly_message_cap
    except Exception:
        # A store with no StoreSettings row yet is uncapped rather than blocked.
        return None


def usage_for(store, today=None):
    """This month's row for a store, created on first use.

    Writes, so it belongs on the message path only. Read-only callers — the analytics
    dashboard, for one — want messages_used_this_month instead.
    """
    usage, _ = StoreMonthlyUsage.objects.get_or_create(
        store=store, period=current_period(today)
    )
    return usage


def messages_used_this_month(store, today=None):
    """How many billed messages this store has used, without creating anything.

    Separate from usage_for because the analytics endpoint is a GET: calling
    get_or_create there wrote a row on every dashboard page load, including for months
    the store never sent a message.
    """
    if store is None:
        return 0
    row = StoreMonthlyUsage.objects.filter(
        store=store, period=current_period(today)
    ).first()
    return row.llm_messages if row else 0


def record_llm_message(store, today=None):
    """Count one message that is about to cost model calls.

    Called from router.route at the point where spending begins — after the StaticFAQ
    match and the goodbye shortcut have had their chance to return for free, and
    immediately before classify(), the first model call on every remaining path. So the
    free routes are never billed to the store.
    """
    if store is None:
        return None

    usage = usage_for(store, today)
    # F() rather than read-modify-write: the worker pool runs messages concurrently.
    StoreMonthlyUsage.objects.filter(pk=usage.pk).update(
        llm_messages=F("llm_messages") + 1
    )
    usage.refresh_from_db()

    _warn_if_needed(store, usage)
    return usage


def _warn_if_needed(store, usage):
    cap = monthly_cap(store)
    if not cap:
        return

    if usage.llm_messages >= cap:
        _claim_and_notify(
            usage,
            "warned_at_cap",
            title="وصلت للحد الشهري ⚠️",
            message=(
                f"استهلكت {usage.llm_messages} رسالة من حد {cap} رسالة الشهر ده. "
                f"البوت شغال عادي ومش هيتوقف — الزيادة هتتحاسب في الفاتورة."
            ),
        )
    elif usage.llm_messages >= cap * WARN_RATIO:
        _claim_and_notify(
            usage,
            "warned_at_80",
            title="قربت على الحد الشهري",
            message=(
                f"استهلكت {usage.llm_messages} رسالة من حد {cap} رسالة الشهر ده "
                f"({int(usage.llm_messages / cap * 100)}%)."
            ),
        )


def _claim_and_notify(usage, flag, title, message):
    """Send one notification per flag per period, safely under concurrency.

    The flag is claimed with a conditional UPDATE rather than a read-then-save, so two
    workers crossing the threshold on the same message cannot both notify.
    """
    claimed = StoreMonthlyUsage.objects.filter(pk=usage.pk, **{flag: False}).update(
        **{flag: True}
    )
    if not claimed:
        return

    setattr(usage, flag, True)
    create_notification(
        store=usage.store,
        notif_type="usage_warning",
        title=title,
        message=message,
    )
    logger.info(
        "Store '%s' hit the %s usage threshold (%d messages).",
        usage.store.name, flag, usage.llm_messages,
    )
