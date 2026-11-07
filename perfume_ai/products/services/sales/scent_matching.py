"""Scent eligibility and nearest-fit ordering from explicit catalog facts."""

import re

from products.occasions import occasion_groups, occasion_matches
from ..static_faq_service import normalize_arabic
from ..reply_sanitizer import _NOTE_ALIASES
from .notes import expand_request_term, parse_notes


def matching_tags(product, intent):
    wanted = set(intent.get("scent_tags") or ())
    return list(dict.fromkeys(tag for tag in (product.scent_tags or ()) if tag in wanted))


def match_facts(product, intent):
    return {
        "matched_scent_tags": matching_tags(product, intent),
        "season_match": bool(intent.get("season")) and season_matches(product.season, intent["season"]),
        "occasion_match": bool(intent.get("occasion")) and occasion_matches(product.occasion, intent["occasion"]),
    }


def fit_key(product, intent):
    """Smaller is nearer: season, extra shared tags, then occasion."""
    facts = match_facts(product, intent)
    return (-int(facts["season_match"]), -len(facts["matched_scent_tags"]), -int(facts["occasion_match"]))


def order_candidates(products, intent, *, keep=(), offered=(), tie_breaker=None):
    from .brand_preference import matches_brand

    new_goal = intent.get("recommendation_mode") == "new_goal"
    keeping = set(keep or ()) if not new_goal else set()
    seen = set(offered or ()) if not new_goal else set()

    def order(product):
        preferred = not intent.get("brand") or matches_brand(product, intent["brand"], product.store)
        return (*fit_key(product, intent), product.name not in keeping, not preferred,
                product.name in seen, tie_breaker(product) if tie_breaker else 0, product.pk)

    return sorted(products, key=order)


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
