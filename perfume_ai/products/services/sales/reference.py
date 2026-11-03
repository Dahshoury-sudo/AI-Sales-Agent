"""Keep a perfume interest separate from a customer-requested lookalike search."""

from dataclasses import dataclass
import re
import unicodedata

from ..static_faq_service import normalize_arabic


REFERENCE_KEYS = ("similar_to", "similar_to_notes")
STATE_KEY = "similarity_request"
_SIMILAR = re.compile(
    r"(?<!\w)(?:شبه(?:ه|ها|و)?|زي(?:ه|ها|و)?|بديل|قريب(?:ه)?\s+من(?:ه|ها|و)?|"
    r"[يت]شبه(?:ه|ها|و)?|شبيه(?:ه)?|نفس\s+(?:ال)?(?:جو|ستايل|طابع|نوع|ريحه|نوتات)|"
    r"similar\s+to|alternative\s+to|same\s+(?:vibe|style|notes(?:\s+as)?)|something\s+like)(?!\w)"
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
    text = unicodedata.normalize("NFKD", normalize_arabic(str(text or "")))
    return "".join(c for c in text if not unicodedata.combining(c)).translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def similarity_requested(message):
    """An affirmative comparison cue; naming, owning, or liking alone is insufficient."""
    text = _normalized(message)
    return bool(_affirmative_cues(text))


def _scent_analogy(text, cue):
    return cue.group() in {"زي", "شبه", "similar to", "something like"} and bool(re.match(
        r"\s+(?:ال)?(?:حلويات|حلو[ىي]|سكريات|فانيليا|ورد|زهور|اخشاب|خشب|فواكه|desserts|sweets)\b",
        text[cue.end():],
    ))


def _affirmative_cues(text):
    matches = []
    negated = None
    for match in _SIMILAR.finditer(text):
        if _scent_analogy(text, match):
            continue
        joined_negative = negated is not None and re.search(
            r"\b(?:او|or)\s*$", text[negated.end():match.start()],
        ) and not re.search(r"[.؛;\n]|\b(?:بس|لكن|but)\b", text[negated.end():match.start()])
        if _NEGATIVE.search(text[:match.start()]) or joined_negative:
            negated = match
            continue
        negated = None
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
        if not _scent_analogy(text, match)
    )


def _same_name(left, right):
    return bool(left and right and _normalized(left).strip() == _normalized(right).strip())


def _same_spoken_name(span, name):
    """Validate a translated target against the customer's complete Arabic spelling.

    Vowel letters differ between scripts: تيري هيرمز and Terre d'Hermes share
    the consonant words ``tr hrms`` but fail the ordinary character matcher.
    This only validates the already extracted target. It never selects a
    catalogue candidate, accepts partial names, or discards flanker words.
    """
    from . import naming

    if not re.search(r"[\u0621-\u064a]", span or ""):
        return False

    def words(text):
        text = "".join(character for character in unicodedata.normalize("NFKD", text)
                       if not unicodedata.combining(character))
        keys = []
        for token in sorted(naming.tokens(text)):
            key = naming.transliterate(token).replace("z", "s")
            key = re.sub(r"[aeiouyw]", "", key)
            key = re.sub(r"(.)\1+", r"\1", key)
            if key:
                keys.append(key)
        return sorted(keys)

    source, target = words(span), words(name)
    return bool(source == target and sum(map(len, source)) >= 5)


def _mentions(text, name, store, history=None):
    """Check a legacy source against its saved target without asking a model."""
    from . import naming

    normalized, target = _normalized(text), _normalized(name)
    if target and re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", normalized):
        if store:
            from products.models import Product
            # A named edition consumes its full span; its base is not separately named.
            names = list(Product.objects.filter(store=store).values_list("name", flat=True))
            mentioned = naming.names_in(normalized, names + [name])
            if mentioned and name not in mentioned:
                return False
        return True
    span = naming.arabic_span(normalized)
    if _same_spoken_name(span, name):
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
        found = naming.match_product(span, store)
        if found is None:
            # In mixed-script speech the Latin span can be a shortened catalogue
            # name: "بحب ريحة Sauvage" names Dior Sauvage without its house.
            for span in re.findall(r"[a-z][a-z0-9\s'’.\-]*", normalized):
                match = naming.match_product(span.strip(), store)
                if match is not None and match.pk == product.pk:
                    return True
    if found is None:
        from ..product_resolver import confident_catalogue_match
        found = confident_catalogue_match(span, store)
    return found is not None and found.pk == product.pk


def _catalogue_targets(text, names, store=None):
    """Exact identities, including accents/Arabic, without collapsing editions."""
    from . import naming

    normalized = _normalized(text)
    by_key = {_normalized(name): [name] for name in names}
    cues = _affirmative_cues(normalized)
    spans = []
    for index, cue in enumerate(cues):
        end = cues[index + 1].start() if index + 1 < len(cues) else len(normalized)
        tail = re.sub(r"\s+(?:او|و|or|and)$", "", normalized[cue.end():end].strip())
        spans.append(re.split(r"\b(?:عشان|لان|because)\b", tail)[0])
    if not cues:
        spans.append(normalized)
    found = []
    for span in spans:
        aliases = dict(by_key)
        if store:
            # Consume a complete typed Latin name before any nested short name.
            # Preserve multiple possible editions as an explicit ambiguity.
            for match in re.finditer(r"[a-z][a-z0-9\s'’.\-]*", span):
                alias = match.group().strip()
                exact, partial = naming.candidates(alias, store)
                if matches := exact or partial:
                    aliases[alias] = [product.name for product in matches]
        found.extend(name for key in naming.names_in(span, aliases) for name in aliases[key])
    if found:
        return list(dict.fromkeys(found))
    found = [name for name in names if any(_same_spoken_name(naming.arabic_span(span), name) for span in spans)]
    if not found and cues and _anaphoric_request(text):
        prefix = normalized[:cues[0].start()]
        found = [name for key in naming.names_in(prefix, by_key) for name in by_key[key]]
    return found


def _recent_targets(previous, store, extra=(), owned=False):
    from .described import supported_offered_names
    from products.models import Product

    names = list(Product.objects.filter(store=store).values_list("name", flat=True)) if store else []
    canonical = {}
    for name in [*names, *extra]:
        canonical.setdefault(_normalized(name), name)
    names = list(canonical.values())
    rows = [row for row in previous if row.get("role") == "user"
            or row.get("delivery_status", "sent") == "sent"]
    # "Something like it, because I own it" refers to the customer's comparison
    # target, not the alternatives just proposed in response to that comparison.
    if owned:
        latest_user = next((row for row in reversed(rows) if row.get("role") in ("user", "agent")), {})
        if latest_user.get("role") == "user" and similarity_requested(latest_user.get("content")):
            targets = _catalogue_targets(latest_user["content"], names, store)
            if targets:
                return targets
    for row in reversed(rows):
        if row.get("role") == "agent":
            break
        content = row.get("content", "")
        if row.get("role") == "assistant" and row.get("internal_context"):
            # Canonicalize the evidence, not just the proposed model spelling.
            def fold(value):
                return "".join(c for c in unicodedata.normalize("NFKD", value)
                               if not unicodedata.combining(c))
            aliases = {fold(name): name for name in names}
            found = supported_offered_names(fold(content), fold(row["internal_context"]), list(aliases))
            targets = [aliases[name] for name in found]
        elif row.get("role") in ("assistant", "user"):
            targets = _catalogue_targets(content, names, store)
        else:
            continue
        if targets:
            return targets
    return []


def _anaphoric_request(message):
    text = _normalized(message)
    return any(cue.group().endswith(("ه", "ها", "و"))
               or cue.group().startswith(("نفس ", "same "))
               for cue in _affirmative_cues(text))


def _source_supports(message, name, store, previous):
    text = _normalized(message)
    cues = _affirmative_cues(text)
    named_target = False
    for index, cue in enumerate(cues):
        end = cues[index + 1].start() if index + 1 < len(cues) else len(text)
        # In "نفس نوتات X او قريب منه", the second comparison refers to X;
        # its cue and connecting particle are not part of the perfume's name.
        tail = text[cue.end():end].strip(" ،,؟?.!")
        tail = re.sub(r"\s+(?:او|و|or|and)$", "", tail).strip()
        if _mentions(tail, name, store, previous):
            return True
        anaphoric = (
            cue.group().endswith(("ه", "ها", "و")) or cue.group().startswith(("نفس ", "same "))
            or re.match(r"^(?:اللي|ده|دي|دول|it|that|the one)\b", tail)
        )
        contextual_tail = not tail or re.match(
            r"^(?:عشان|لان|because|بس|لكن|و\w*|في|لل\w*|جدا|اوي|شويه|اللي|ده|دي|دول|it|that|the one)\b", tail,
        )
        if not contextual_tail:
            named_target = True
        if not anaphoric or not contextual_tail:
            continue
        # A customer can name the perfume and refer back to it in the same
        # message: "بحب ريحة Sauvage بس عايز حاجة شبهه ومش منتشرة".
        if _mentions(text[:cue.start()], name, store, previous):
            return True
        if named_target:
            continue
        targets = _recent_targets(previous, store, [name], owned=bool(re.search(r"عندي|اشتريت|own", text)))
        return len(targets) == 1 and _same_name(targets[0], name)
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


def _confirmed_spoken_reference(message, candidate, store):
    """Verify an Arabic name when deterministic phonetic matching is inconclusive."""
    if not candidate or store is None:
        return None
    from . import naming
    text = _normalized(message)
    cues = _affirmative_cues(text)
    if len(cues) != 1:
        return None
    tail = text[cues[0].end():].strip(" ،,؟?.!")
    # Multiple alternatives need clarification rather than a one-name confirmer.
    if re.search(r"[،,]|\s(?:و|او|and|or)\s", tail):
        return None
    span = naming.arabic_span(tail)
    if not span and _anaphoric_request(message):
        span = naming.arabic_span(text[:cues[0].start()])
    if not span or not re.search(r"[\u0621-\u064a]", span):
        return None
    product = naming.match_product(candidate, store)
    if product is None or not product.is_active:
        return None
    from ..product_resolver import confirm_unplaced
    try:
        confirmed = confirm_unplaced(span, store)
    except Exception:
        return None
    return product if getattr(confirmed, "pk", None) == product.pk else None


def prepare_similarity(conversation, intent, message=None, history=None, store=None):
    """Validate fresh/remembered references; the caller persists the returned state."""
    fresh = dict(intent or {})
    saved = dict(getattr(conversation, "preferences", None) or {})
    state = dict(getattr(conversation, "sales_state", None) or {})
    store = store or getattr(conversation, "store", None)
    if conversation is not None and store is not None and conversation.store_id != store.pk:
        conversation, history = None, []
        for key in REFERENCE_KEYS:
            saved.pop(key, None)
        state.pop(STATE_KEY, None)
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
    previous = _history_rows(conversation, history) if similarity_requested(message) else []
    if previous and previous[-1].get("role") == "user" and previous[-1].get("content") == message:
        previous = previous[:-1]
    if not previous and origin and _same_name(candidate, origin["reference_name"]):
        previous = [{"role": "user", "content": origin["source_text"]}]
    targets = []
    if similarity_requested(message):
        from products.models import Product
        names = list(Product.objects.filter(store=store).values_list("name", flat=True)) if store else []
        targets = _catalogue_targets(message, names, store)
        if not targets and _anaphoric_request(message):
            targets = _recent_targets(previous, store, [candidate] if candidate else [],
                                      owned=bool(re.search(r"عندي|اشتريت|own", _normalized(message))))
        if len(targets) == 1 and _source_supports(message, targets[0], store, previous):
            candidate = fresh["similar_to"] = targets[0]
        elif len(targets) > 1:
            candidate = None
            fresh.pop("similar_to", None)
            fresh["similarity_candidates"] = targets
            origin = None
    supported = candidate and _source_supports(message, candidate, store, previous)
    if not supported and not targets:
        confirmed = _confirmed_spoken_reference(message, candidate, store)
        if confirmed is not None:
            candidate = fresh["similar_to"] = confirmed.name
            supported = True
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
        if similarity_requested(message):
            fresh["similarity_needs_reference"] = True
    return PreparedSimilarity(fresh, saved, state)
