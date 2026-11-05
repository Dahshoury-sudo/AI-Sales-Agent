"""Canonical scent requests and conversation updates; product labels are never inferred."""

import re

from django.core.exceptions import ValidationError
from products.scent_tags import normalize_scent_tags
from ..static_faq_service import normalize_arabic


SCENT_KEYS = ("scent_tags", "excluded_scent_tags", "scent_match", "scent_open")
TAG_ALIASES = {
    "fresh": ("fresh", "فريش", "منعش", "منعشه"),
    "sweet": ("sweet", "gourmand", "سويت", "مسكر", "مسكره"),
    "oud": ("oud", "عود", "عودي"),
    "woody": ("woody", "خشبي", "خشبيه"),
    "citrus": ("citrus", "citrusy", "حمضي", "حمضيات"),
    "floral": ("floral", "زهري", "زهور"),
    "fruity": ("fruity", "فاكهي", "فاكهه", "فواكه"),
    "spicy": ("spicy", "متبل", "توابل"),
    "musky": ("musky", "مسكي"),
    "clean": ("clean", "نضيف", "نظيف", "نظافه"),
    "powdery": ("powdery", "بودري", "بودره"),
    "smoky": ("smoky", "smokey", "دخاني", "دخان"),
    "leather": ("leather", "leathery", "جلدي"),
    "aquatic": ("aquatic", "مائي", "بحري"),
    "light": ("light", "خفيف", "خفيفه"),
    "heavy": ("heavy", "تقيل", "تقيله"),
}
_ALIASES = {normalize_arabic(alias): tag for tag, aliases in TAG_ALIASES.items() for alias in aliases}
_NEGATIVE = re.compile(r"(?<!\w)(?:مش|بلاش|بدون|من غير|غير|لا|not|no|without|avoid|except|don't|dont)(?:\s+(?:عايز(?:ه|ها)?|عاوز(?:ه|ها)?|حاجه|يكون|تكون|ريحه|want|something|anything|a|an|it|to|be|too|very|overly)){0,6}\s*$")
_CONNECTOR = re.compile(r"(?:و|او|ولا|and|or|nor|[,،])\s*")
_OPEN = re.compile(r"اي\s+(?:ريحه|ستايل|طابع)|(?:الريحه|الستايل|الطابع)\s+مش\s+(?:مهم|فارقه)|مش\s+فارقه\s+(?:الريحه|الستايل)|any\s+(?:scent|style)|no\s+scent\s+preference", re.I)
_STYLE_TERMS = {"fresh", "sweet", "gourmand", "clean", "woody", "floral", "fruity", "spicy", "powdery", "aquatic", "light", "heavy",
                "فريش", "منعش", "مسكر", "سويت", "نظافه", "خشبي", "زهري", "فاكهي", "متبل", "بودري", "مائي", "خفيف", "تقيل"}


def explicit_scent_updates(message):
    text = normalize_arabic(message or "")
    opening = _OPEN.search(text)
    open_scent = bool(opening and not _NEGATIVE.search(text[:opening.start()]))
    positive, negative = [], []
    spans = []
    previous_end, previous_negative, previous_literal = 0, False, False
    pattern = r"(?<!\w)(?:[وب]?ال|[وب])?(" + "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True)) + r")(?!\w)"
    for match in re.finditer(pattern, text):
        before = text[:match.start()]
        connector = re.sub(r"ال$", "", text[previous_end:match.start(1)].strip()).strip()
        # A named perfume and an explicit performance claim have their own parsers.
        if re.search(r"فوحان(?:ه)?\s*$|projection\s*$", before):
            continue
        if (re.search(r"(?:فيه|فيها|مفيهوش|مفيهاش|يحتوي\s+علي|نوت(?:ه|ات)(?:ه)?|مكونات(?:ه)?|contains|notes?\s+of|notes:)\s*$", before)
                or (previous_literal and _CONNECTOR.fullmatch(connector))):
            previous_end, previous_literal, previous_negative = match.end(), True, False
            continue
        if re.search(r"(?:بدل|instead of)\s*$", before):
            continue
        tag = _ALIASES[match[1]]
        if tag in ("light", "heavy") and re.search(r"(?:ال)?خروجات\s*$|(?:workouts?|outings?)\s*$", before):
            continue
        is_negative = bool(_NEGATIVE.search(before) or (
            previous_negative and _CONNECTOR.fullmatch(connector)
        ))
        if is_negative:
            negative.append(tag)
        else:
            positive.append(tag)
            spans.append(match.span())
        previous_end, previous_negative, previous_literal = match.end(), is_negative, False
    result = {}
    if open_scent:
        result.update(scent_open=True, scent_tags=[])
    if positive:
        result["scent_tags"] = list(dict.fromkeys(positive))
        result["scent_open"] = False
        result["scent_match"] = "any" if any(re.search(r"(?<!\w)(?:او|ولا|or)(?!\w)", text[left[1]:right[0]])
                                                   for left, right in zip(spans, spans[1:])) else "all"
    if negative:
        result["excluded_scent_tags"] = list(dict.fromkeys(negative))
    return result


def normalize_scent_intent(intent):
    """Validate model fields and migrate old broad note terms into explicit labels."""
    result = dict(intent or {})
    for key in ("scent_tags", "excluded_scent_tags"):
        if key not in result or result[key] is None:
            result.pop(key, None)
            continue
        try:
            result[key] = normalize_scent_tags(result[key])
        except ValidationError:
            result.pop(key, None)
    for note_key, tag_key in (("notes", "scent_tags"), ("avoid_notes", "excluded_scent_tags")):
        literal, tags = [], list(result.get(tag_key) or [])
        for term in result.get(note_key) or ():
            normalized = normalize_arabic(str(term))
            if normalized in _STYLE_TERMS:
                tag = _ALIASES.get(normalized)
                if tag and tag not in tags:
                    tags.append(tag)
            else:
                literal.append(term)
        if note_key in result:
            result[note_key] = literal
        if tags:
            result[tag_key] = tags
    for trait in result.get("avoid_traits") or ():
        if trait in ("heavy", "sweet"):
            result["excluded_scent_tags"] = list(dict.fromkeys([*result.get("excluded_scent_tags", []), trait]))
    if result.get("scent_match") not in ("all", "any"):
        result.pop("scent_match", None)
    if type(result.get("scent_open")) is not bool:
        result.pop("scent_open", None)
    return result


def merge_scent_preferences(merged, fresh, saved, message):
    """Apply a stated replacement, addition, exclusion or withdrawal to durable labels."""
    fresh = normalize_scent_intent(fresh)
    # Do not read words inside a reference perfume's name as extra scent requests.
    explicit = explicit_scent_updates(message) if not fresh.get("similar_to") else {}
    fresh.update(explicit)
    cleared = set(merged.get("clear_preferences") or ())
    resetting = merged.get("reset_preferences") is True
    old = {} if resetting else normalize_scent_intent(saved)
    if fresh.get("scent_open") is True:
        excluded = [] if "excluded_scent_tags" in cleared else old.get("excluded_scent_tags", [])
        merged.update(scent_open=True, scent_tags=[], notes=[],
                      excluded_scent_tags=list(dict.fromkeys([*excluded, *fresh.get("excluded_scent_tags", [])])))
        merged.pop("similar_to", None)
        merged.pop("similar_to_notes", None)
        return merged
    positive = list(fresh.get("scent_tags") or ([] if "scent_tags" in cleared else old.get("scent_tags")) or [])
    if fresh.get("scent_tags") and re.search(r"كمان|بالاضافه|\balso\b|\bas well\b", normalize_arabic(message or "")):
        positive = list(dict.fromkeys([*old.get("scent_tags", []), *positive]))
    negative = list(dict.fromkeys([*([] if "excluded_scent_tags" in cleared else old.get("excluded_scent_tags", [])),
                                   *fresh.get("excluded_scent_tags", [])]))
    negative = [tag for tag in negative if tag not in (fresh.get("scent_tags") or [])]
    positive = [tag for tag in positive if tag not in negative]
    if positive or "scent_tags" in merged or old.get("scent_tags"):
        merged["scent_tags"] = positive
    if negative or "excluded_scent_tags" in merged or old.get("excluded_scent_tags"):
        merged["excluded_scent_tags"] = negative
    if positive:
        merged["scent_match"] = fresh.get("scent_match") or old.get("scent_match") or "all"
    else:
        merged.pop("scent_match", None)
    if positive or negative or fresh.get("scent_open") is False:
        merged["scent_open"] = False
    return merged


def has_scent_preference(intent):
    return bool(intent.get("scent_tags") or intent.get("excluded_scent_tags") or intent.get("notes")
                or intent.get("avoid_notes") or intent.get("similar_to") or intent.get("scent_open"))
