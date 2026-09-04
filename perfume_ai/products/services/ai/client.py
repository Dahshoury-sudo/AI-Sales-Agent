from openai import OpenAI
from django.conf import settings

client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
)


# Model families that take `reasoning_effort` and reject an explicit `temperature`.
# Prefix matching rather than an exact list so a point release does not silently fall
# through to the wrong branch.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning(model):
    return str(model or "").startswith(_REASONING_PREFIXES)


def _profiles():
    """Model and sampling strategy per kind of call, resolved from settings.

    Built per call rather than at import time so override_settings works.
    """
    base = settings.OPENAI_MODEL
    smart = getattr(settings, 'OPENAI_SMART_MODEL', None)
    resolver = getattr(settings, 'OPENAI_RESOLVER_MODEL', None)

    if smart:
        # Reasoning models accept only their default temperature and return a
        # 400 on any explicit value, so the parameter is omitted entirely.
        reason = {"model": smart}
    else:
        # No smart model configured. Fall back to the standard model *with*
        # temperature 0 rather than omitting it — omitting would inherit the
        # API default of 1.0 on a JSON extractor that decides whether an order
        # is created and stock decremented.
        reason = {"model": base, "temperature": 0}

    if resolver:
        resolve = {"model": resolver}
        if _is_reasoning(resolver):
            # Effort pinned low rather than left at the API default: this call sits on the
            # critical path of six call sites, and matching a name against a printed list is
            # shallow work. Temperature is omitted for the same reason as `reason` above.
            resolve["reasoning_effort"] = "low"
        else:
            # A non-reasoning override still must not inherit the API default of 1.0, and it
            # must not be sent `reasoning_effort` — that 400s, and a 400 here is *silent*:
            # product_resolver swallows it into `failed=True`, which files the turn as UNKNOWN
            # and asks the customer to retype a name we could have placed.
            resolve["temperature"] = 0
    else:
        resolve = {"model": base, "temperature": 0}

    return {
        # Structured JSON out, no prose. Deterministic.
        "extract": {"model": base, "temperature": 0},
        # Multi-step conditional reasoning where a wrong answer is expensive.
        "reason": reason,
        # The one component that reads Arabic: it turns what the customer typed into an exact
        # catalogue name, and `absence.catalogue_verdict` treats its misses as grounds for
        # telling a customer we do not stock something.
        "resolve": resolve,
        # Customer-facing prose, on the tuned temperature.
        "converse": {"model": base, "temperature": getattr(settings, 'OPENAI_TEMPERATURE', 1)},
    }


def chat(messages, profile="converse", response_format=None):
    profiles = _profiles()
    if profile not in profiles:
        raise ValueError(
            f"unknown chat profile: {profile!r} (expected one of {sorted(profiles)})"
        )

    kwargs = {"messages": messages, **profiles[profile]}

    if response_format:
        kwargs["response_format"] = response_format

    response = client.chat.completions.create(**kwargs)

    return response.choices[0].message.content


def test():
    return chat([
        {
            "role": "user",
            "content": "Say hello."
        }
    ])
