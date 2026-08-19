from openai import OpenAI
from django.conf import settings

client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
)


def _profiles():
    """Model and sampling strategy per kind of call, resolved from settings.

    Built per call rather than at import time so override_settings works.
    """
    base = settings.OPENAI_MODEL
    smart = getattr(settings, 'OPENAI_SMART_MODEL', None)

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

    return {
        # Structured JSON out, no prose. Deterministic.
        "extract": {"model": base, "temperature": 0},
        # Multi-step conditional reasoning where a wrong answer is expensive.
        "reason": reason,
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
