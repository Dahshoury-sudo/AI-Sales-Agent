"""Boolean matches against explicit catalog facts, without recommendation scores."""

import re

from products.occasions import occasion_groups, occasion_matches
from ..static_faq_service import normalize_arabic
from ..reply_sanitizer import _NOTE_ALIASES
from .notes import expand_request_term, parse_notes
from .similarity import peak_hours, _ordinal, _PROJECTION_WORDS, _LONGEVITY_WORDS


def _contains(text, value):
    return bool(re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text))


def season_matches(recorded, wanted):
    if not wanted:
        return True
    text = normalize_arabic(str(recorded or ""))
    if any(term in text for term in ("all season", "all-season", "كل المواسم", "كل الفصول")):
        return True
    seasons = {"summer": ("summer", "صيف", "الصيف"), "winter": ("winter", "شتا", "شتاء", "الشتا"),
               "spring": ("spring", "ربيع"), "fall": ("fall", "autumn", "خريف")}
    request = normalize_arabic(str(wanted))
    aliases = next((terms for terms in seasons.values() if request in terms), (request,))
    return any(_contains(text, alias) for alias in aliases)


def note_matches(product, wanted):
    wanted = normalize_arabic(str(wanted))
    aliases = next((terms for terms in _NOTE_ALIASES.values() if wanted in terms), expand_request_term(wanted))
    recorded = parse_notes(" , ".join((product.top_notes or "", product.middle_notes or "", product.base_notes or "")))
    return any(_contains(note, normalize_arabic(alias)) for note in recorded for alias in aliases)


def performance_matches(product, intent):
    longevity = intent.get("longevity")
    if longevity:
        hours = peak_hours(str(longevity))
        if hours is not None:
            if (peak_hours(product.longevity) or 0) < hours:
                return False
        else:
            level = {"weak": 1, "poor": 1, "short": 1, "moderate": 2, "medium": 2, "average": 2,
                     "long": 3, "long-lasting": 3, "long lasting": 3, "longlasting": 3,
                     "eternal": 4, "very long": 4, "very long-lasting": 4}.get(str(longevity).lower())
            actual_hours = peak_hours(product.longevity)
            actual = (4 if actual_hours >= 12 else 3 if actual_hours >= 8 else 2 if actual_hours >= 4 else 1) if actual_hours is not None else _ordinal(product.longevity, _LONGEVITY_WORDS)
            if level is not None and (actual is None or actual < level):
                return False
            if level is None and not _contains(normalize_arabic(product.longevity or ""), normalize_arabic(str(longevity))):
                return False
    projection = intent.get("projection")
    if projection:
        level = {"intimate": 1, "soft": 1, "skin": 1, "close": 1, "moderate": 2, "medium": 2,
                 "strong": 3, "heavy": 3, "loud": 3, "enormous": 4, "beast": 4, "nuclear": 4}.get(str(projection).lower())
        actual = _ordinal(product.projection, _PROJECTION_WORDS)
        if level is not None:
            if actual is None or (actual > level if level <= 2 else actual < level):
                return False
        elif not _contains(normalize_arabic(product.projection or ""), normalize_arabic(str(projection))):
            return False
    return True
