"""Price estimates and content-free JSON usage logs, independent of DB rollback."""
from decimal import Decimal
import json
import logging

logger = logging.getLogger("ai.usage")
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
    return {
        "prompt_tokens": count(usage, "prompt_tokens"),
        "cached_tokens": count(details, "cached_tokens"),
        "cache_write_tokens": count(details, "cache_creation_tokens"),
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
