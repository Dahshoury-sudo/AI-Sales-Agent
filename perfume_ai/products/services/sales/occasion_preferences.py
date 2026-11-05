"""Normalize occasion updates and apply delegation after saved preferences merge."""

import re

from products.occasions import DAILY, normalize_occasion, normalize_text, occasion_groups


CLARIFICATION = "هتستخدم العطر في أي مناسبة: يومي وكاجوال، شغل، خروجات وسهرات، ديت، مناسبة رسمية، ولا بحر وصيف وجيم؟"
PENDING_KEY = "occasion_needs_clarification"
_DELEGATE = re.compile(
    r"(?<!\w)(?:اختار(?:لي|يلي)?(?:\s+(?:انت|انتي|بنفسك))?|انت\s+اختار|انتي\s+اختاري|"
    r"(?:علي|على|من)\s+ذوقك|ذوقك\s+انت|choose\s+(?:for\s+me|yourself)|"
    r"pick\s+(?:(?:one|something)\s+)?for\s+me|you\s+(?:choose|pick|decide)|"
    r"surprise\s+me|up\s+to\s+you|your\s+(?:choice|pick))(?!\w)"
)
_NEGATIVE = re.compile(r"(?<!\w)(?:مش|لا|بلاش|متختارش|not|don't|dont|do\s+not)\s*$")
_SELF_CHOICE = re.compile(r"(?<!\w)(?:انا|انا\s+اللي|عايز|عاوز|محتاج|خليني|i\s+want\s+to|let\s+me)\s*$")
_OCCASION_SCOPE = re.compile(r"مناسبه|مناسبات|استخدام|هتستخدم|بتستخدم|occasion|when.*wear|wear.*when|نهار.*ليل|day.*night")
_OTHER_QUESTION = re.compile(r"ريحه|ستايل|طابع|فريش|سويت|حجم|ملي|ميزانيه|سعر|انهي|اي واحد|scent|style|fresh|sweet|size|bottle|budget|price|which")
_SCOPED_CHOICE = re.compile(
    r"(?:choose|pick|select|decide)\s+(?:(?:the|a)\s+)?(?:scent|style|size|bottle|brand)|"
    r"اختار(?:لي)?\s+(?:(?:لي|انت)\s+)?(?:الريحه|الستايل|الطابع|الحجم|الزجاجه|البراند)|"
    r"(?<!\w)(?:between|بين|دول|الاتنين)(?!\w)"
)
_CONFIRMATION = re.compile(r"(?:ok|okay|yes|sure|تمام|اه|ايوه|ماشي|حاضر)[.!؟?]*", re.I)


def _previous_reply(history):
    return next((row.get("content", "") for row in reversed(history or [])
                 if row.get("role") == "assistant"), "")


def occasion_question(history):
    previous = _previous_reply(history)
    questions = re.findall(r"(?:^|[.!؟?\n])([^.!؟?\n]*[؟?])", previous)
    text = normalize_text(questions[-1]) if questions else ""
    return bool(_OCCASION_SCOPE.search(text) or (
        len(occasion_groups(text)) > 1 and not _OTHER_QUESTION.search(text)
    ))


def _other_choice(message, history):
    text = normalize_text(message)
    if _SCOPED_CHOICE.search(text) and not _OCCASION_SCOPE.search(text):
        return True
    if _OCCASION_SCOPE.search(text) or occasion_question(history):
        return False
    previous = _previous_reply(history)
    return bool(_SCOPED_CHOICE.search(text) or (
        re.search(r"[؟?]", previous) and _OTHER_QUESTION.search(normalize_text(previous))
    ))


def delegates_occasion(message, history=None):
    text = normalize_text(message)
    return bool(not _other_choice(message, history) and any(
        not _NEGATIVE.search(text[:match.start()]) and not _SELF_CHOICE.search(text[:match.start()])
        for match in _DELEGATE.finditer(text)
    ))


def _grounded_source(source, message):
    if not isinstance(source, str) or not normalize_text(source) or not message:
        return None
    text = normalize_text(source)
    if _CONFIRMATION.fullmatch(text):
        return None
    match = re.search(r"(?<!\w)" + re.escape(text) + r"(?!\w)", normalize_text(message))
    if match and not _NEGATIVE.search(normalize_text(message)[:match.start()]):
        return source.strip()
    return None


def normalize_occasion_updates(intent, message=None, history=None):
    """Canonicalize the extractor output; delegation is a transient instruction.

    The optional quoted occasion_text lets an unfamiliar situation mapped by the AI
    count as evidence from this turn, without copying an occasion from history.
    """
    result = dict(intent or {})
    raw = result.pop("occasion", None)
    source = _grounded_source(result.pop("occasion_text", None), message)
    model_delegation = result.pop("occasion_delegated", None) is True
    delegated = delegates_occasion(message, history) or bool(
        model_delegation and source and not _DELEGATE.search(normalize_text(message))
        and not _NEGATIVE.search(normalize_text(source).split(" ")[0] + " ")
        and not _other_choice(message, history)
    )
    category = normalize_occasion(raw) if isinstance(raw, str) else None
    positive = occasion_groups(message, positive_only=True)
    explicit = normalize_occasion(message, positive_only=True)
    if source:
        result["occasion_text"] = source
    if delegated:
        result["occasion_delegated"] = True
        # The AI's default must not replace an existing explicit work/date/etc.
        # occasion. The actual default is applied after the preference merge.
        if explicit:
            result["occasion"] = explicit
    elif category:
        unrelated_choice = bool(message and not positive and (
            _DELEGATE.search(normalize_text(message)) or _CONFIRMATION.fullmatch(normalize_text(message))
        ))
        if not unrelated_choice and (not message or positive or not occasion_groups(message)):
            result["occasion"] = category
    elif raw not in (None, "", "multiple") or (source and not positive):
        result[PENDING_KEY] = True
    if result.get("occasion"):
        result.pop(PENDING_KEY, None)
    if "occasion" in (result.get("clear_preferences") or []):
        result["clear_preferences"] = list(dict.fromkeys([*result["clear_preferences"], PENDING_KEY]))
    return result


def merge_occasion_preferences(merged, fresh, history=None):
    """Resolve explicit > saved > delegated default, keeping unknowns restrictive."""
    category = normalize_occasion(merged.get("occasion"))
    explicit = normalize_occasion(fresh.get("occasion"))
    delegated = merged.pop("occasion_delegated", False)
    merged.pop("occasion_text", None)
    if "occasion" in (fresh.get("clear_preferences") or []):
        category = explicit = None
        merged.pop(PENDING_KEY, None)
    if explicit:
        category = explicit
        merged.pop(PENDING_KEY, None)
    elif delegated:
        if not category and not fresh.get("reset_preferences") and "occasion" not in (fresh.get("clear_preferences") or []):
            # Stateless callers can still preserve an occasion named in history.
            category = next((occasion for row in reversed(history or []) if row.get("role") == "user"
                             if (occasion := normalize_occasion(row.get("content"), positive_only=True))), None)
        category = category or DAILY
        merged.pop(PENDING_KEY, None)
    elif "occasion" not in (fresh.get("clear_preferences") or []) and (fresh.get(PENDING_KEY) or merged.get(PENDING_KEY)):
        category = None
        merged[PENDING_KEY] = True
    elif "occasion" not in (fresh.get("clear_preferences") or []) and merged.get("occasion") and not category and merged["occasion"] != "multiple":
        merged[PENDING_KEY] = True
    if category:
        merged["occasion"] = category
    else:
        merged.pop("occasion", None)
    if not merged.get(PENDING_KEY):
        merged.pop(PENDING_KEY, None)
