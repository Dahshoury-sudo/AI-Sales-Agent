"""Boolean matches against explicit catalog facts, without recommendation scores."""

import re

from products.occasions import occasion_groups, occasion_matches
from ..static_faq_service import normalize_arabic
from ..reply_sanitizer import _NOTE_ALIASES
from .notes import expand_request_term, parse_notes


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
    """Performance is uniform across inventory and never restricts eligibility."""
    return True
