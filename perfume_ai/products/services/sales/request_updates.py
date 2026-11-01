"""Current recommendation goals, independent of the classifier's route label.

Only positive, bounded requests trigger a new goal. Product questions, purchases,
and answers refining an offer retain their existing handling.
"""
import re

from ..static_faq_service import normalize_arabic


GOAL_KEYS = frozenset({"notes", "occasion", "projection", "longevity",
                       "similar_to", "similar_to_notes"})
_REQUEST = r"(?:عايز(?:ه)?|عاوز(?:ه)?|محتاج(?:ه)?|رشح(?:لي)?|ترشح(?:لي)?|عندك(?:م|و)?|recommend|suggest|want)"
_FACT = re.compile(r"^(?:(?:طيب|طب)\s+)?(?:هل|هو|هي|بكام|سعر|how|is|does)\b|\b(?:ينفع|بينفع|مناسب|سعره|ثباته كام|فوحانه ايه)\b")
_PURCHASE = re.compile(r"(?<!\w)(?:ضيف|اطلب|هاخد|هشتري|اشتري|شيل|الغ[يى]|كنسل|order|buy|add|cancel)(?!\w)")
_LINK = re.compile(r"(?<!\w)(?:نفس\s+(?:الطلب|الشروط|المواصفات|الريحه)|شبه(?:ه|ها|و)|زي(?:ه|ها)|يكون\s+كمان|same\s+(?:one|request)|also\s+suitable)(?!\w)")
_NEGATIVE = re.compile(r"(?:مش|لا|بلاش|بدون|غير|not|no)(?:\s+(?:عايز(?:ه)?|عاوز(?:ه)?|حاجه|يكون|ريحه|want|something)){0,3}\s*$")
_OCCASIONS = (
    ("daytime", r"(?:لل)?نهار(?:ي)?|بالنهار|الصبح|للصبح|صباحي|للصباح|يومي|daily|daytime|morning"),
    ("evening", r"للسهره|للسهرات|سهره|بالليل|للليل|ليلي|مسائي|evening|night"),
    ("casual", r"(?:لل|ال)?خروجات(?:\s+الخفيفه)?|كاجوال|casual"),
    ("office", r"للشغل|الشغل|للمكتب|office|work"),
    ("gym", r"للجيم|الجيم|gym|sport"),
    ("mosque", r"للمسجد|المسجد|للصلاه|mosque"),
)


def _text(message):
    text = normalize_arabic(message or "")
    # Arabic conjunctions attach to words, including the beginning of a request.
    return re.sub(r"(?<!\w)و(?=(?:لل|بال|ال|عايز|عاوز|محتاج|رشح|ترشح|نفس|فريش|منعش|خفيف|مريح|فواح|ثابت|نظافه|نهار|صباح|هادي|هادئ))", "", text)


def _positive(text, pattern):
    return [match for match in re.finditer(r"(?<!\w)(?:" + pattern + r")(?!\w)", text)
            if not _NEGATIVE.search(text[:match.start()])]


def explicit_criteria(message):
    """Canonical updates for the concrete use/scent/performance words we know."""
    text = _text(message)
    updates = {}
    # Casual + mosque is casual use with subdued projection, not 'multiple'.
    for occasion, pattern in _OCCASIONS:
        if _positive(text, pattern):
            updates["occasion"] = occasion
            break
    notes = []
    for note, pattern in (("fresh", r"فريش|منعش(?:ه)?|fresh"),
                          ("clean", r"نظافه|نضيف(?:ه)?|نظيف(?:ه)?|clean")):
        if _positive(text, pattern):
            notes.append(note)
    if notes:
        updates["notes"] = notes
    if _positive(text, r"فواح(?:ه)?|فوحان\s+قوي|strong\s+projection"):
        updates["projection"] = "strong"
    elif _positive(text, r"خفيف(?:ه)?|مريح(?:ه)?|هادي|هادئ|هادئه|subdued|quiet|light"):
        updates["projection"] = "moderate"
    if _positive(text, r"ثابت(?:ه)?|ثبات\s+(?:عالي|طويل)|long[- ]lasting"):
        updates["longevity"] = "long-lasting"
    return updates


def request_mode(message, history=None, store=None):
    """Return new_goal/refinement for a verified search, or '' to abstain."""
    from .reference import similarity_requested

    text = _text(message)
    if not text or _PURCHASE.search(text):
        return ""
    if _FACT.search(text) and not (_positive(text, _REQUEST) or _LINK.search(text)):
        return ""
    if re.match(r"^(?:(?:طيب|طب)\s+)?(?:هل|هو|هي|is|does)\b", text):
        return ""
    similar = similarity_requested(message)
    if similar:
        # A statement that two named perfumes resemble one another, or owning
        # such a perfume, is not an instruction to find alternatives.
        bare_request = re.match(
            r"^(?:(?:طيب|طب|ممكن|please)\s+)?(?:(?:حاجه|عطر|برفان)\s+)?"
            r"(?:شبه|زي|بديل|قريب|نفس|similar\s+to|alternative\s+to|same|something\s+like)\b", text,
        )
        if not (_positive(text, _REQUEST) or bare_request):
            return ""
        return "refinement" if _LINK.search(text) and explicit_criteria(message).get("occasion") else "new_goal"
    from ..product_info import occasion_reference_requested
    if occasion_reference_requested(message):
        return ""
    criteria = explicit_criteria(message)
    if not criteria.get("occasion"):
        return ""
    # Sizes/quantities without an explicit recommendation verb may be purchases.
    if re.search(r"[0-9٠-٩]", text) and not re.search(r"رشح|ترشح|recommend|suggest", text):
        return ""
    if store:
        from products.models import Product
        from . import naming
        names = list(Product.objects.filter(store=store).values_list("name", flat=True))
        if naming.names_in(message, names):
            return ""
        if not naming.describes_rather_than_names(message):
            from ..product_resolver import confident_catalogue_match
            if confident_catalogue_match(message, store):
                return ""
    if not (_positive(text, _REQUEST) or re.match(r"^(?:لل|بال)", text)
            or criteria.get("notes") or _LINK.search(text)):
        return ""
    if _LINK.search(text):
        return "refinement"
    if history and len(text.split()) <= 4 and re.search(r"اكتر|mostly", text) and not _positive(text, _REQUEST):
        return "refinement"
    # A short answer to an actual occasion question refines the same request.
    previous = next((row.get("content", "") for row in reversed(history or [])
                     if row.get("role") == "assistant"), "")
    if (len(text.split()) <= 6 and not _positive(text, _REQUEST)
            and re.search(r"[؟?]", previous)
            and re.search(r"نهار|ليل|مناسبه|مناسبات|استخدام|occasion|day|night", normalize_arabic(previous))):
        return "refinement"
    return "new_goal"


def prepare_updates(intent, message, history=None, store=None):
    """Return fresh fields plus the old goal fields which must not gap-fill."""
    fresh = dict(intent or {})
    mode = request_mode(message, history, store)
    # Never trust a model-supplied mode without evidence in the message.
    fresh.pop("recommendation_mode", None)
    if not mode:
        if str(fresh.get("occasion") or "").lower() == "multiple":
            fresh.pop("occasion", None)
        return fresh, frozenset()
    fresh["recommendation_mode"] = mode
    if fresh.get("brand"):
        from .brand_preference import _brand_pattern
        if not re.search(_brand_pattern(fresh["brand"], store), normalize_arabic(message or "")):
            # A model sometimes copies 'from the store's choice' into a later
            # request as an own-brand preference. Genuine saved brands gap-fill.
            fresh.pop("brand", None)
            fresh.pop("brand_strict", None)
    explicit = explicit_criteria(message)
    text = _text(message)
    if mode == "new_goal":
        # Extraction can echo history. Absent positive axes must be removed from
        # its output as well as from saved preferences.
        from .reference import similarity_requested
        from .notes import terms_in
        evidence = {
            "notes": bool(explicit.get("notes") or terms_in(message)),
            "occasion": bool(explicit.get("occasion")),
            "projection": bool(explicit.get("projection") or re.search(r"فوحان|projection", text)),
            "longevity": bool(explicit.get("longevity") or re.search(r"ثبات|يدوم|longevity|lasts", text)),
            "similar_to": similarity_requested(message),
            "similar_to_notes": similarity_requested(message),
        }
        for key, supported in evidence.items():
            if not supported:
                fresh.pop(key, None)
    if explicit.get("notes"):
        # Keep other ingredients explicitly extracted from this same utterance.
        from .notes import terms_in, FRESH_REQUEST_TERMS, CLEAN_REQUEST_TERMS
        notes = list(fresh.get("notes") or [])
        if not (set(terms_in(message)) - set(FRESH_REQUEST_TERMS) - set(CLEAN_REQUEST_TERMS)):
            notes = []
        fresh["notes"] = list(dict.fromkeys(notes + explicit.pop("notes")))
    fresh.update(explicit)
    return fresh, GOAL_KEYS if mode == "new_goal" else frozenset()
