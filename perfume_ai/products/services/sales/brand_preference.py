"""A preferred perfume house is soft unless the customer explicitly limits it."""
import re

from ..static_faq_service import normalize_arabic

STORE_BRAND_EXCLUSIVE = "STORE_BRAND_EXCLUSIVE"
_OTHER_BRANDS_OK = re.compile(
    r"(?:عادي|ممكن|ينفع|مفيش مانع|ماعنديش مانع)[^.!؟?\n]{0,35}(?:براند|ماركه)\s+(?:تاني|مختلف)|"
    r"(?:براند|ماركه)\s+(?:تاني|مختلف)[^.!؟?\n]{0,20}(?:عادي|ممكن|ينفع)|"
    r"\bother brands? (?:are )?(?:fine|okay|ok|welcome)\b"
)


def allows_other_brands(message):
    """Opening the choice does not itself withdraw the preferred house."""
    text = normalize_arabic(message or "")
    return any(
        not re.search(r"(?:مش|لا|not|cannot|can't)\s*$", text[:match.start()])
        and not re.search(r"(?:مش|لا|not)\s+(?:عادي|ممكن|ينفع|fine|okay|ok)", match.group())
        for match in _OTHER_BRANDS_OK.finditer(text)
    )


def rejects_other_brands(message):
    text = normalize_arabic(message or "")
    return bool(re.search(
        r"(?:مش\s+(?:عايز|عاوز|محتاج|ممكن)|لا\s+اريد)\s+(?:حاجه\s+|من\s+)?(?:براند|ماركه)\s+(?:تاني|مختلف)|"
        r"\bother brands? (?:are )?not (?:fine|okay|ok)\b", text,
    ))


def preferred_brand_name(brand, store=None):
    if str(brand or "").upper() == STORE_BRAND_EXCLUSIVE:
        return str(getattr(store, "name", "") or "")
    return str(brand or "").strip()


def matches_brand(product, brand, store=None):
    store = store or getattr(product, "store", None)
    preferred = preferred_brand_name(brand, store).casefold()
    actual = str(getattr(getattr(product, "brand", None), "name", "") or "").casefold()
    if str(brand or "").upper() == STORE_BRAND_EXCLUSIVE:
        return bool(preferred and actual == preferred)
    return bool(preferred and preferred in actual)


def _brand_pattern(brand, store):
    from .naming import _HOUSE_ALIASES

    label = normalize_arabic(preferred_brand_name(brand, store))
    aliases = {label} if label else set()
    aliases.update(alias for alias, name in _HOUSE_ALIASES.items()
                   if label and (label in name.casefold() or name.casefold() in label))
    if str(brand or "").upper() == STORE_BRAND_EXCLUSIVE or (
        store and label == normalize_arabic(store.name)
    ):
        aliases.update(("براندكم", "البراند بتاعكم", "البراند بتاعكو", "عطوركم الخاصه", "عطوركم",
                        "تركيبكم", "براند الستور", "براند المتجر", "تصميمكم", "من تصميمكم",
                        "your brand", "store brand"))
    return "(?<!\\w)(?:" + "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + ")(?!\\w)" if aliases else r"(?!)"


def strictness_update(message, brand=None, store=None, *, allow_reference=True):
    """True/False for an explicit restriction/opening; None for no update.

    Keep 'Dior بس يكون فريش' distinct from 'Dior بس': the first 'بس' means
    'but'. A request about price/packaging cannot accidentally restrict a house.
    """
    text = normalize_arabic(message or "")
    named = _brand_pattern(brand, store)
    if allow_reference and rejects_other_brands(text):
        return True
    if allows_other_brands(text) or re.search(
        r"(?:اي|كل)\s+(?:براند|ماركه)|(?:البراند|الماركه)\s+مش\s+مهم|"
        r"مش\s+(?:مهم|فارقه)\s+(?:البراند|الماركه)|"
        r"\bany brand\b|"
        r"brand (?:does not|doesn't) matter", text,
    ):
        return False
    if re.search(r"(?:مش\s+(?:لازم|شرط)|not\s+only)\s+" + named, text):
        return False
    target = named
    if allow_reference:
        target = "(?:" + named + r"|نفس البراند|نفس الماركه|البراند ده|الماركه دي|that brand|same brand)"
    if any(re.search(pattern, text) for pattern in (
        r"(?<!\w)(?:فقط|حصريا|only|exclusively)\s+(?:(?:عطور|perfumes|fragrances)\s+)?(?:(?:من|from)\s+)?" + target + r"(?!\w)",
        r"\bonly\s+(?:want|recommend|show me)\s+(?:perfumes?\s+from\s+)?" + target,
        target + r"\s+(?:فقط|حصريا|only|exclusively)(?!\w)",
        target + r"\s+بس(?=\s*(?:[.،,!?؟]|$)|\s+(?:لو سمحت|من فضلك)\s*$)",
        r"(?:عايز|عاوز|محتاج)\s+بس\s+(?:من\s+)?" + target + r"(?!\w)",
        r"(?:مش عايز|مش محتاج|لا اريد)\s+(?:حاجه\s+)?(?:غير|الا)\s+" + target + r"(?!\w)",
    )):
        return True
    return None


def sanitize_brand_intent(intent, message, store=None):
    """Remove common inferences that would silently narrow a brand request."""
    fresh = dict(intent or {})
    text = normalize_arabic(message or "")
    if re.search(r"(?:من\s+)?اختيار\s+(?:الستور|المتجر)|store'?s?\s+(?:choice|recommendation)", text):
        if fresh.get("brand") == STORE_BRAND_EXCLUSIVE and not re.search(
            r"تصميم|صنع|عطوركم|براندكم|البراند بتاع|تركيبكم|حصري", text,
        ):
            fresh.pop("brand", None)
            fresh.pop("brand_strict", None)
    if message and fresh.get("perfume_type") == "store_own_made" and not re.search(r"من\s+(?:صنع|تصميم)\s+(?:الستور|المتجر)|تصميمكم", text):
        fresh.pop("perfume_type", None)
    if fresh.get("brand_strict") is not None and type(fresh["brand_strict"]) is not bool:
        fresh.pop("brand_strict")
    if message:
        update = strictness_update(message, fresh.get("brand"), store)
        if update is not None:
            fresh["brand_strict"] = update
        elif type(fresh.get("brand_strict")) is bool:
            fresh.pop("brand_strict")
    return fresh


def _legacy_strictness(conversation, history, brand, store):
    if conversation is not None:
        if store is not None and conversation.store_id != store.pk:
            return False
        rows = conversation.messages.filter(role="user").order_by("created_at", "pk").values_list("content", flat=True)
    else:
        rows = (row.get("content", "") for row in history or () if row.get("role") == "user")
    current = _brand_pattern(brand, store)
    other_patterns = []
    if store:
        from products.models import Brand
        preferred = preferred_brand_name(brand, store).casefold()
        other_patterns = [_brand_pattern(name, store)
                          for name in Brand.objects.filter(store=store).values_list("name", flat=True)
                          if preferred not in name.casefold() and name.casefold() not in preferred]
    request_prefix = r"(?:^|\b(?:من|براند|ماركه|عايز|عاوز|want|from)\s+)"
    strict, anchored = False, False
    for text in rows:
        text = normalize_arabic(text)
        if re.search(request_prefix + current, text):
            anchored = True
        elif any(re.search(request_prefix + pattern, text) for pattern in other_patterns):
            strict, anchored = False, False
        # A customer may first name the house, then insist on 'same brand only'.
        # Without that anchor an old pronoun is not evidence about this house.
        update = strictness_update(text, brand, store, allow_reference=anchored)
        if update is not None:
            strict = update
    return strict


def apply_strictness(merged, saved, fresh, message, conversation=None, history=None, store=None):
    """Bind strictness to the final brand, after normal preference merging."""
    brand = merged.get("brand")
    if not brand:
        merged.pop("brand_strict", None)
        return
    store = store or getattr(conversation, "store", None)
    update = strictness_update(message, brand, store)
    if update is None and not message and type(fresh.get("brand_strict")) is bool:
        update = fresh["brand_strict"]
    if "brand_strict" in (fresh.get("clear_preferences") or ()):
        update = False
    previous = preferred_brand_name(saved.get("brand"), store).casefold()
    current = preferred_brand_name(brand, store).casefold()
    changed = current != previous
    if update is not None:
        merged["brand_strict"] = update
    elif changed:
        merged["brand_strict"] = False
    elif type(saved.get("brand_strict")) is bool:
        merged["brand_strict"] = saved["brand_strict"]
    else:
        merged["brand_strict"] = _legacy_strictness(conversation, history, brand, store)
