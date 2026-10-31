"""Keep a perfume interest separate from a customer-requested lookalike search."""

from dataclasses import dataclass
import re

from ..static_faq_service import normalize_arabic


REFERENCE_KEYS = ("similar_to", "similar_to_notes")
STATE_KEY = "similarity_request"
_SIMILAR = re.compile(
    r"(?<!\w)(?:شبه(?:ه|ها)?|زي(?:ه|ها)?|بديل|قريب(?:ه)?\s+من(?:ه|ها)?|"
    r"[يت]شبه(?:ه|ها)?|شبيه(?:ه)?|نفس\s+(?:ال)?(?:جو|ستايل|طابع|نوع|ريحه)|"
    r"similar\s+to|alternative\s+to|same\s+(?:vibe|style)|something\s+like)(?!\w)"
)
_NEGATIVE = re.compile(
    r"(?:مش\s+(?:عايز(?:ه)?|طالب|محتاج|لازم)|من\s+غير|بلاش|"
    r"don['’]?t\s+want|do\s+not\s+want|not\s+looking\s+for|no)"
    r"(?:\s+\w+){0,4}\s*$|(?:مش|not)\s*$"
)
_WITHDRAW = re.compile(
    r"(?:انسي|انسى|سيبك\s+من|الغي|بلاش)\s+(?:المقارنه|التشابه|البديل)|"
    r"(?:العطر|البرفان)\s+نفسه\s+مش\s+(?:بديل|حاجه\s+شبهه)|"
    r"(?:forget|drop)\s+(?:the\s+)?(?:comparison|similarity)"
)
_RESET = re.compile(
    r"انسي\s+(?:اللي\s+قلته|الشروط|طلبي)|سيبك\s+من\s+(?:الشروط|طلبي)|"
    r"الغي\s+(?:اللي\s+قلته|الشروط)|شروط\s+جديده|مش\s+نفس\s+الشروط|"
    r"(?:لشخص|لواحد|لحد)\s+تاني|(?:new|different)\s+recipient"
)


def _normalized(text):
    return normalize_arabic(str(text or "")).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def similarity_requested(message):
    """An affirmative comparison cue; naming, owning, or liking alone is insufficient."""
    text = _normalized(message)
    return bool(_affirmative_cues(text))


def _affirmative_cues(text):
    matches = []
    for match in _SIMILAR.finditer(text):
        if _NEGATIVE.search(text[:match.start()]):
            continue
        if match.group() == "زي" and (
            re.match(r"\s+ما\b", text[match.end():])
            or re.search(r"(?:براند|ماركه|brand|house)(?:\s+\w+){0,2}\s*$", text[:match.start()])
        ):
            continue
        matches.append(match)
    return matches


def _withdrawn(message):
    text = _normalized(message)
    return bool(_WITHDRAW.search(text)) or any(
        _NEGATIVE.search(text[:match.start()]) for match in _SIMILAR.finditer(text)
    )


def _same_name(left, right):
    return bool(left and right and _normalized(left).strip() == _normalized(right).strip())


def _mentions(text, name, store, history=None):
    """Check a legacy source against its saved target without asking a model."""
    from . import naming

    normalized, target = _normalized(text), _normalized(name)
    if target and re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", normalized):
        return True
    if not store:
        return False
    product = naming.match_product(name, store)
    if product is None:
        return False
    if re.fullmatch(r"[0-9]{2,6}", normalized):
        from .followups import numeric_reference
        found = numeric_reference(normalized, store, history=history, respect_budget=False).product
    else:
        found = naming.match_product(naming.arabic_span(normalized), store)
        if found is None:
            # In mixed-script speech the Latin span can be a shortened catalogue
            # name: "بحب ريحة Sauvage" names Dior Sauvage without its house.
            for span in re.findall(r"[a-z][a-z0-9\s'’.\-]*", normalized):
                match = naming.match_product(span.strip(), store)
                if match is not None and match.pk == product.pk:
                    return True
    if found is None:
        from ..product_resolver import confident_catalogue_match
        found = confident_catalogue_match(naming.arabic_span(normalized), store)
    return found is not None and found.pk == product.pk


def _source_supports(message, name, store, previous):
    text = _normalized(message)
    for cue in _affirmative_cues(text):
        tail = text[cue.end():].strip(" ،,؟?.!")
        if _mentions(tail, name, store, previous):
            return True
        anaphoric = (
            cue.group().endswith(("ه", "ها")) or cue.group().startswith(("نفس ", "same "))
            or re.match(r"^(?:اللي|ده|دي|دول|it|that|the one)\b", tail)
        )
        contextual_tail = not tail or re.match(
            r"^(?:بس|لكن|و\w*|في|لل\w*|جدا|اوي|شويه|اللي|ده|دي|دول|it|that|the one)\b", tail,
        )
        if not anaphoric or not contextual_tail:
            continue
        # A customer can name the perfume and refer back to it in the same
        # message: "بحب ريحة Sauvage بس عايز حاجة شبهه ومش منتشرة".
        if _mentions(text[:cue.start()], name, store, previous):
            return True
        for row in reversed(previous[-3:]):
            if row.get("role") == "agent":
                break
            if row.get("role") not in ("user", "assistant"):
                continue
            if row.get("role") == "assistant" and row.get("internal_context"):
                from .described import supported_offered_names
                if name in supported_offered_names(row.get("content", ""), row["internal_context"], [name]):
                    return True
            elif _mentions(row.get("content", ""), name, store):
                return True
    return False


def _history_rows(conversation, history):
    if conversation is not None and conversation.pk:
        from django.db.models import Q
        rows = list(conversation.messages.filter(
            Q(role="user") | Q(delivery_status="sent"),
        ).order_by("created_at", "id").values("role", "content", "internal_context"))
        if rows:
            return rows
        if conversation.messages.exists():
            return []
    return [row for row in (history or ())
            if row.get("role") == "user" or row.get("delivery_status", "sent") == "sent"]


def _legacy_source(conversation, history, name, store):
    messages = _history_rows(conversation, history)
    source, previous = None, []
    for entry in messages:
        text = entry.get("content", "")
        if entry.get("role") == "user":
            if _RESET.search(_normalized(text)) or _withdrawn(text):
                source = None
            if similarity_requested(text):
                supported = _source_supports(text, name, store, previous)
                source = {"reference_name": name, "source_text": text} if supported else None
        previous.append(entry)
    return source


@dataclass
class PreparedSimilarity:
    intent: dict
    preferences: dict
    sales_state: dict


def prepare_similarity(conversation, intent, message=None, history=None, store=None):
    """Validate fresh/remembered references; the caller persists the returned state."""
    fresh = dict(intent or {})
    saved = dict(getattr(conversation, "preferences", None) or {})
    state = dict(getattr(conversation, "sales_state", None) or {})
    store = store or getattr(conversation, "store", None)
    reset = fresh.get("reset_preferences") is True or bool(_RESET.search(_normalized(message)))
    if reset:
        fresh["reset_preferences"] = True
        saved, state = {}, {}

    cleared = set(fresh.get("clear_preferences") or ())
    withdrawn = bool(cleared.intersection(REFERENCE_KEYS)) or _withdrawn(message)
    old_name = saved.get("similar_to")
    origin = state.get(STATE_KEY)
    if not isinstance(origin, dict) or not _same_name(origin.get("reference_name"), old_name) or not similarity_requested(origin.get("source_text")):
        origin = _legacy_source(conversation, history, old_name, store) if old_name and not withdrawn else None
    if withdrawn:
        origin = None
        fresh["clear_preferences"] = sorted(cleared.union(REFERENCE_KEYS))

    candidate = fresh.get("similar_to")
    previous = _history_rows(conversation, history) if candidate and similarity_requested(message) else []
    if previous and previous[-1].get("role") == "user" and previous[-1].get("content") == message:
        previous = previous[:-1]
    if not previous and origin and _same_name(candidate, origin["reference_name"]):
        previous = [{"role": "user", "content": origin["source_text"]}]
    supported = candidate and _source_supports(message, candidate, store, previous)
    if supported:
        # "Forget that comparison; find something like Y" establishes Y.
        withdrawn = False
        fresh["clear_preferences"] = [key for key in fresh.get("clear_preferences", ()) if key not in REFERENCE_KEYS]
        origin = {"reference_name": candidate, "source_text": message}
        # A replacement's absent profile must not be filled from the old perfume.
        if not _same_name(candidate, old_name):
            for key in REFERENCE_KEYS:
                saved.pop(key, None)
        saved["similar_to"] = candidate
    else:
        if candidate and similarity_requested(message):
            origin = None
        fresh.pop("similar_to", None)
        if not origin or (candidate and not _same_name(candidate, origin["reference_name"])):
            fresh.pop("similar_to_notes", None)

    if origin:
        state[STATE_KEY] = origin
        saved["similar_to"] = origin["reference_name"]
    else:
        state.pop(STATE_KEY, None)
        for key in REFERENCE_KEYS:
            fresh.pop(key, None)
            saved.pop(key, None)
    return PreparedSimilarity(fresh, saved, state)
