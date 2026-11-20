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


# Notes a customer who refused sweetness reads as sweet. Ranking only: labels alone decide whether a
# perfume is eligible (a fresh-labelled perfume with vanilla stays a fresh perfume), but among equal
# label fits one without these comes first. "مش عايز حاجة مسكرة" was offered Fresh Vibe, whose top
# notes include Sweet Orange, ahead of perfumes with none (simulation S10 round 2 batch 2, t6-t8).
# Amber is left out: it sits in the base of most fresh perfumes.
SWEET_MATERIALS = ("vanilla", "tonka", "caramel", "honey", "praline", "sugar", "candy", "chocolate",
                   "cocoa", "toffee", "marshmallow", "sweet orange")


def has_sweet_notes(product):
    return any(note_matches(product, material) for material in SWEET_MATERIALS)


def is_uncommon(product):
    """Niche, ultra niche, or the store's own blend: what "حاجة مميزة ومش منتشرة" asks for."""
    from .value import is_store_exclusive
    return product.perfume_type in ("niche", "ultra_niche", "store_own_made") or is_store_exclusive(product)


def candidate_key(intent, *, keep=(), offered=(), tie_breaker=None):
    """The nearest-fit sort key in two parts: (preference, product.pk).

    The preference part is the fit, then what the customer and the conversation asked for: no sweet
    notes, something uncommon, the perfume under discussion, the preferred brand, not-yet-offered,
    and the caller's tie breaker (ingredient affinity). Picking perfumes that smell different
    (`diverse_pick`) slots in between the two parts, so it never outranks any of those.
    """
    from .brand_preference import matches_brand

    new_goal = intent.get("recommendation_mode") == "new_goal"
    keeping = set(keep or ()) if not new_goal else set()
    seen = set(offered or ()) if not new_goal else set()
    avoid_sweet = "sweet" in (intent.get("excluded_scent_tags") or ())
    # "عايز حاجة مميزة ومش منتشرة" was answered with a mainstream Armani beside a niche Xerjoff
    # (simulation S05 round 2 full 3, t3): among equal label fits, niche and the store's own come first.
    uncommon = bool(intent.get("wants_uncommon"))
    # "عايز برفان يخلي البنات تحبني": among equal label fits, perfumes the owner's description calls
    # attractive come first (sales.attraction). Behind the fit, so it never beats a scent match.
    from .attraction import GOAL_KEY, describes_appeal
    appeal = bool(intent.get(GOAL_KEY))

    def preference(product):
        preferred = not intent.get("brand") or matches_brand(product, intent["brand"], product.store)
        return (*fit_key(product, intent), avoid_sweet and has_sweet_notes(product),
                uncommon and not is_uncommon(product), appeal and not describes_appeal(product),
                product.name not in keeping,
                not preferred, product.name in seen, tie_breaker(product) if tie_breaker else 0)

    def tail(product):
        return (product.pk,)

    return preference, tail


def order_candidates(products, intent, *, keep=(), offered=(), tie_breaker=None):
    preference, tail = candidate_key(intent, keep=keep, offered=offered, tie_breaker=tie_breaker)
    return sorted(products, key=lambda product: (*preference(product), *tail(product)))


# Owner request (2026-11-20): "عايز برفان رجالي فريش" got two fresh perfumes that were both woody and
# spicy, while the store has dozens of fresh perfumes with other characters. Every pick keeps the
# requested labels; among equally near picks, each next one shares as few of the earlier picks'
# *other* labels as possible, so the customer is shown two different smells.

def other_tags(product, intent):
    """The described labels (the first three) that the customer did not ask for."""
    from ..scent_description import selected_tags

    wanted = set(intent.get("scent_tags") or ())
    return [tag for tag in selected_tags(product.scent_tags) if tag not in wanted]


def shared_other_tags(product, chosen, intent):
    """This perfume's other labels that an already chosen perfume also has, in saved order."""
    taken = {tag for earlier in chosen for tag in other_tags(earlier, intent)}
    return [tag for tag in other_tags(product, intent) if tag in taken]


def diverse_pick(products, intent, count, *, keep=(), offered=(), tie_breaker=None):
    """The nearest `count` perfumes, each next one smelling as different as its equals allow.

    Greedy: every slot takes the lowest (preference, shared other labels, pk). The first pick is
    the plain nearest one, and each later pick has the same preference as the plain order's, so the
    fit profile the recommendation validator checks is unchanged.
    """
    preference, tail = candidate_key(intent, keep=keep, offered=offered, tie_breaker=tie_breaker)
    keys = {product.pk: (preference(product), tail(product)) for product in products}
    remaining = list(products)
    limit = len(remaining) if count is None else count
    chosen = []
    while remaining and len(chosen) < limit:
        best = min(remaining, key=lambda product: (
            keys[product.pk][0], len(shared_other_tags(product, chosen, intent)), keys[product.pk][1]))
        chosen.append(best)
        remaining.remove(best)
    return chosen


def distinct_selection_issue(proposed, candidates, intent, *, keep=(), offered=()):
    """Why a proposed pick repeats an earlier pick's smell when an equal one would not, or None.

    The model keeps its free choice of the first perfume and of the preference tier, as before.
    Only a later pick that shares more of the earlier picks' other labels than the best perfume of
    its own tier is refused; the message names the repeated labels and that better perfume.
    """
    preference, tail = candidate_key(intent, keep=keep, offered=offered)
    tiers = {product.pk: preference(product) for product in candidates}
    for index in range(1, len(proposed)):
        product, earlier = proposed[index], proposed[:index]
        repeated = shared_other_tags(product, earlier, intent)
        if not repeated or product.pk not in tiers:
            continue
        taken = {p.pk for p in earlier}
        equals = [c for c in candidates if c.pk not in taken and tiers.get(c.pk) == tiers[product.pk]]
        better = min(equals, key=lambda c: (len(shared_other_tags(c, earlier, intent)), tail(c)))
        if len(shared_other_tags(better, earlier, intent)) < len(repeated):
            names = ", ".join(p.name for p in earlier)
            return (f"{product.name} repeats the {', '.join(repeated)} character of {names} besides the "
                    f"requested scent. Choose perfumes that smell different: replace it with an equally near "
                    f"perfume such as {better.name} (id {better.pk}).")
    return None


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
