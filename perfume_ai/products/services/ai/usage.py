"""Price estimates, content-free JSON usage logs, and per-store cost records.

Every model call passes through ai.client._complete, which calls `record` (or
`record_failure`). The store is attributed by the entry point that started the turn:

    with usage.attribute(store_id, "whatsapp"):
        generate_event(...)

Calls are tallied in memory during the turn and written once when the block exits. The
turn itself runs inside a transaction that can roll back, and OpenAI bills the calls either
way, so the record is written after it, outside it. Outside any `attribute` block (scripts,
tests, simulations) calls are only logged, never written.

Recording is observability: nothing here may raise into a customer's reply.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import json
import logging

logger = logging.getLogger("ai.usage")

TOKEN_FIELDS = ("prompt_tokens", "cached_tokens", "cache_write_tokens", "completion_tokens", "reasoning_tokens")
COUNTER_FIELDS = ("calls", "failed_calls", "unpriced_calls") + TOKEN_FIELDS
PRICING_DATE = "2026-11-03"
# USD per million input / cached input / output tokens. Keep unknown models unknown.
PRICES = {name: tuple(map(Decimal, prices)) for name, prices in {
    "gpt-4.1-mini": ("0.40", "0.10", "1.60"),
    "gpt-5-mini": ("0.25", "0.025", "2.00"),
    "gpt-6-luna": ("0.10", "0.01", "0.50"),
    "gpt-6.1-terra": ("1.00", "0.05", "5.00"),
    "gpt-5.6-terra": ("2.00", "0.20", "12.00"),
}.items()}


def price_key(model):
    for name in sorted(PRICES, key=len, reverse=True):
        suffix = str(model or "")[len(name):]
        if model == name or (str(model).startswith(name + "-") and
                             len(suffix) == 11 and suffix[1:5].isdigit()):
            return name
    return None


def field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def count(obj, name):
    value = field(obj, name, 0)
    return value if type(value) is int and value >= 0 else 0


def usage_counts(usage):
    if type(field(usage, "prompt_tokens")) is not int or type(field(usage, "completion_tokens")) is not int:
        return None
    details = field(usage, "prompt_tokens_details")
    # Current name first; older Chat Completions responses call it cache_creation_tokens.
    written = "cache_write_tokens" if type(field(details, "cache_write_tokens")) is int else "cache_creation_tokens"
    return {
        "prompt_tokens": count(usage, "prompt_tokens"),
        "cached_tokens": count(details, "cached_tokens"),
        "cache_write_tokens": count(details, written),
        "completion_tokens": count(usage, "completion_tokens"),
        # Already part of completion_tokens, never billed a second time.
        "reasoning_tokens": count(field(usage, "completion_tokens_details"), "reasoning_tokens"),
    }


def new_cache_pricing(model):
    return str(model).startswith(("gpt-6", "gpt-5.6"))


def estimate_cost(model, usage, *, batch=False, retention=None):
    key = price_key(model)
    if key is None or usage is None:
        return None
    incoming, cached, outgoing = PRICES[key]
    if new_cache_pricing(model) and usage["prompt_tokens"] > 272000:
        incoming *= 2
        cached *= 2
        outgoing *= Decimal("1.5")
    read = usage["cached_tokens"]
    written = usage.get("cache_write_tokens", 0)
    if read + written > usage["prompt_tokens"]:
        return None
    write_multiplier = Decimal("2.5" if retention == "12h" else "1.25") if new_cache_pricing(model) else Decimal(1)
    total = ((usage["prompt_tokens"] - read - written) * incoming + read * cached +
             written * incoming * write_multiplier + usage["completion_tokens"] * outgoing) / 1000000
    return total / 2 if batch else total


def emit(**values):
    # Callers pass identifiers/counters only, never requests, replies or exception text.
    try:
        logger.info(json.dumps({"event": "openai_usage", "pricing_date": PRICING_DATE, **values},
                               default=str, separators=(",", ":")))
    except Exception:
        # Observability must not change customer behavior if a log sink fails.
        pass


# ── Per-store attribution ───────────────────────────────────────────────────

class _Tally:
    def __init__(self, store_id, channel):
        self.store_id = store_id
        self.channel = channel
        self.counters = dict.fromkeys(COUNTER_FIELDS, 0)
        self.cost = Decimal(0)

    def add(self, counts, cost, failed=False):
        self.counters["calls"] += 1
        if failed:
            self.counters["failed_calls"] += 1
            return
        for name in TOKEN_FIELDS:
            self.counters[name] += (counts or {}).get(name, 0)
        if cost is None:
            self.counters["unpriced_calls"] += 1
        else:
            self.cost += cost


_active = ContextVar("ai_usage_tally", default=None)


@contextmanager
def attribute(store_id, channel):
    """Attribute every model call made inside this block to one store and channel."""
    tally = _Tally(store_id, channel)
    token = _active.set(tally)
    try:
        yield tally
    finally:
        _active.reset(token)
        flush(tally)


def record(model, response):
    """Account one completed call. Never raises."""
    try:
        counts = usage_counts(field(response, "usage"))
        cost = estimate_cost(model, counts)
        _account(model, counts, cost)
    except Exception:
        logger.exception("Could not account an OpenAI call")


def record_failure(model):
    """Account a call that raised before returning a response. Never raises."""
    try:
        _account(model, None, None, failed=True)
    except Exception:
        logger.exception("Could not account a failed OpenAI call")


def _account(model, counts, cost, failed=False):
    tally = _active.get()
    emit(store_id=tally.store_id if tally else None, channel=tally.channel if tally else None,
         model=model, failed=failed, cost_usd=cost, **(counts or {}))
    if tally is not None:
        tally.add(counts, cost, failed=failed)


def flush(tally):
    """Write a tally to its store's row for today, adding to what is there. Never raises."""
    if not tally.counters["calls"]:
        return
    try:
        from django.db.models import F
        from django.utils import timezone
        from products.models import AIUsageDaily

        row, _ = AIUsageDaily.objects.get_or_create(
            store_id=tally.store_id, date=timezone.localdate(), channel=tally.channel)
        increments = {name: F(name) + value for name, value in tally.counters.items() if value}
        if tally.cost:
            increments["cost_usd"] = F("cost_usd") + tally.cost.quantize(Decimal("0.00000001"))
        increments["updated_at"] = timezone.now()
        # F() additions, not read-modify-write: gunicorn threads and Celery processes
        # add to the same row concurrently.
        AIUsageDaily.objects.filter(pk=row.pk).update(**increments)
    except Exception:
        logger.exception("Could not save AI usage for store %s channel %s: %s",
                         tally.store_id, tally.channel,
                         json.dumps({**tally.counters, "cost_usd": str(tally.cost)}))
