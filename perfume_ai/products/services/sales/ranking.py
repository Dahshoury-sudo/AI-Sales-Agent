"""Explainable ranking of candidate perfumes.

Before this there was no ranking at all. `search_products` applied a cascade of hard SQL
filters and ordered whatever survived by `-oil_stock_grams` — most bulk oil first. So no
signal could outrank another, because nothing was weighed against anything: a criterion
either deleted a product or did nothing.

That is the mechanism behind the "شبه Sauvage" failure. Similarity was expanded into notes
and AND-filtered, the filter emptied the set, and the fallback matched on gender and brand
alone — which is how "similar to Sauvage" returned Fahrenheit. Weighing the signals
instead makes the fix expressible: scent DNA is worth five times an occasion match, so a
same-occasion perfume that smells nothing alike cannot win.

Two properties worth preserving deliberately:

  * Every score carries `reasons`, in Arabic, naming the evidence. The prompt shows those
    rather than a number, so the model can justify a recommendation from data instead of
    inventing a justification — and cannot quote a similarity percentage that does not
    exist.
  * With no discriminating signal every score is equal and the tie-break falls through to
    the caller's ordering. That used to be `(-oil_stock_grams, id)`; oil tracking is gone,
    and `search_service._by_value` now hands candidates over cheapest-brand-bottle first,
    so an all-equal set comes back in that order.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import similarity
from .notes import (
    HEAVY_FAMILIES,
    HEAVY_NOTES,
    LIGHT_FAMILIES,
    expand_request_term,
    families,
)

# Signal weights. The relative sizes are the point: an explicit request to be *like*
# something specific is the strongest thing a customer can say, and an explicit exclusion
# ("مش عايز حاجة تقيلة") must be able to sink a product that scores well on everything
# else. Occasion and season sit deliberately low — matching them is not similarity.
WEIGHTS = {
    "similarity": 5.0,
    "notes": 2.0,
    "avoid": -3.0,
    "gender": 3.0,
    "budget": 1.5,
    "longevity": 1.0,
    "occasion": 1.0,
    "projection": 0.8,
    "season": 0.8,
    "uncommon": 1.2,
}

# Below this a candidate matches nothing the customer discriminated on. Reported per
# entry rather than used to pick a branch: gender and stock contribute points to every
# candidate in the pool, so a threshold on the *total* score is really a threshold on a
# constant offset. search_service keeps its original exact-versus-alternatives decision
# (did anything match the literal criteria?) and uses ranking only for ordering.
RELEVANCE_FLOOR = 1.0


@dataclass
class Ranked:
    product: object
    score: float = 0.0
    reasons: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    result: object = None

    @property
    def matched_something(self):
        """Whether any positive signal fired, as opposed to baseline points only."""
        return bool(self.reasons)


def _as_decimal(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number > 0 else None


def has_signal(intent, reference=None):
    """Whether anything in this intent can actually discriminate between products.

    When nothing can, the caller keeps the legacy ordering rather than shuffling an
    all-equal list through a scorer.
    """
    if reference is not None and reference.is_usable:
        return True
    if not intent:
        return False
    return any(
        intent.get(key)
        for key in ("notes", "avoid_notes", "avoid_traits", "occasion",
                    "longevity", "projection", "season", "wants_uncommon")
    )


def _text_hit(field_value, wanted):
    """Does a free-text catalogue field mention what the customer asked for?

    A blank field is a miss, never a match — but crucially the caller scores it as zero
    rather than excluding the product, which is the difference between this and the
    `icontains` AND-filter it replaces. That filter deleted every sparsely-filled product
    from the candidate set the moment a customer named an occasion.
    """
    if not field_value or not wanted:
        return False
    return str(wanted).strip().lower() in str(field_value).lower()


# What the extractor emits for these two slots, as the same 1-4 scale similarity._ordinal
# reads catalogue text onto.
_WANTED_LONGEVITY = {
    "weak": 1, "poor": 1, "short": 1,
    "moderate": 2, "medium": 2, "average": 2,
    "long": 3, "long-lasting": 3, "long lasting": 3, "longlasting": 3,
    "eternal": 4, "very long": 4, "very long-lasting": 4,
}
_WANTED_PROJECTION = {
    "intimate": 1, "soft": 1, "skin": 1, "close": 1,
    "moderate": 2, "medium": 2,
    "strong": 3, "heavy": 3, "loud": 3,
    "enormous": 4, "beast": 4, "nuclear": 4,
}


def _ordinal_hit(field_value, wanted, vocabulary, words):
    """Score a graded request against a graded catalogue value, 0.0-1.0.

    This replaces `_text_hit` for longevity and projection, and it exists because that
    substring test was dead on arrival for longevity: the extractor emits
    'long-lasting' / 'eternal' / 'moderate', while stores type '8 hours', '8–10 hrs',
    '12 hours'. No product in a real catalogue ever matched, so a customer whose stated
    top priority was الثبات contributed exactly zero to the ranking — and evaluation
    caught the bot recommending a 6-hour perfume while saying out loud that its
    longevity was lower.

    similarity._ordinal already parses both the words and the hour numbers onto a 1-4
    scale, so it is reused rather than reimplemented. Meeting or beating the request
    scores full; one grade short scores half, because a 7-hour perfume is a real answer
    to "ثابت" even if it is not the best one; further short scores nothing.
    """
    if not field_value or not wanted:
        return 0.0

    target = vocabulary.get(str(wanted).strip().lower())
    if target is None:
        # An unmapped request value falls back to the old substring behaviour rather
        # than silently scoring zero.
        return 1.0 if _text_hit(field_value, wanted) else 0.0

    actual = similarity._ordinal(field_value, words)
    if actual is None:
        return 0.0
    if actual >= target:
        return 1.0
    return 0.5 if target - actual == 1 else 0.0


def _affordable_and_obtainable(product, max_price):
    """Is there a bottle that is both within budget and actually sellable?

    A brand bottle is compounded to order so it always counts; an original counts only
    while stock remains. Worth checking separately from the SQL price filter, which
    qualifies a product through *any* variant under budget — so a product whose only
    affordable size is a sold-out original would otherwise score as in-budget.
    """
    for variant in product.variants.all():
        if max_price is not None and variant.price > max_price:
            continue
        if variant.bottle_type == "normal":
            return True
        elif (variant.stock or 0) > 0:
            return True
    return False


def rank(products, intent, reference=None):
    """Score and order candidates, best first."""
    intent = intent or {}
    max_price = _as_decimal(intent.get("max_price"))
    wanted_notes = [note for note in (intent.get("notes") or ()) if note]
    avoid_notes = [note for note in (intent.get("avoid_notes") or ()) if note]
    avoid_traits = {str(trait).strip().lower() for trait in (intent.get("avoid_traits") or ())}
    avoid_heavy = bool({"heavy", "suffocating", "loud", "strong"} & avoid_traits)

    ranked = []
    for product in products:
        entry = Ranked(product=product)

        if reference is not None and reference.is_usable:
            result = similarity.compare(reference, product)
            entry.result = result
            entry.score += WEIGHTS["similarity"] * result.score
            description = similarity.describe(reference, result)
            if description:
                entry.reasons.append(description)
            elif result.band == "none":
                entry.mismatches.append(f"مش قريب من {reference.name}")

        profile = similarity.note_profile(product)
        accords = families(profile)

        matched_notes = [
            note for note in wanted_notes
            # Scored through the same expansion table the SQL filter uses. Without this
            # an accord request ("مسكر") filtered to the gourmands and then scored every
            # one of them zero, because no product has a note literally named "sweet" —
            # so the ordering fell back to bulk-oil stock and the reasons line came out
            # empty, leaving the model no evidence to recommend from.
            if any(
                term in existing
                for term in expand_request_term(note)
                for existing in profile
            )
        ]
        if wanted_notes:
            # Proportional, not all-or-nothing: matching two of three requested notes is
            # a real partial match, and the AND-filter this replaces scored it as zero.
            entry.score += WEIGHTS["notes"] * (len(matched_notes) / len(wanted_notes))
            if matched_notes:
                entry.reasons.append("فيه " + "، ".join(str(n) for n in matched_notes))

        hit_avoided = [
            note for note in avoid_notes
            if any(str(note).strip().lower() in existing for existing in profile)
        ]
        if hit_avoided:
            entry.score += WEIGHTS["avoid"]
            entry.mismatches.append("فيه " + "، ".join(str(n) for n in hit_avoided))

        if avoid_heavy:
            heaviness = len(accords & HEAVY_FAMILIES) + len(set(profile) & HEAVY_NOTES)
            if heaviness >= 2:
                entry.score += WEIGHTS["avoid"]
                entry.mismatches.append("ممكن يكون تقيل على العميل")
            elif accords & LIGHT_FAMILIES:
                entry.score += abs(WEIGHTS["avoid"]) * 0.25
                entry.reasons.append("خفيف ومش خانق")

        for key, weight_name, attribute, label in (
            ("occasion", "occasion", "occasion", "مناسب للمناسبة اللي قالها"),
            ("season", "season", "season", "مناسب للموسم"),
        ):
            wanted = intent.get(key)
            if not wanted:
                continue
            if _text_hit(getattr(product, attribute, ""), wanted):
                entry.score += WEIGHTS[weight_name]
                entry.reasons.append(label)

        # Graded, not substring-matched — see _ordinal_hit. Partial credit is reported as
        # a mismatch rather than a reason so the reply cannot claim a perfume "matches"
        # a longevity request it only half meets.
        for key, attribute, vocabulary, words, label, short_label in (
            ("longevity", "longevity", _WANTED_LONGEVITY, similarity._LONGEVITY_WORDS,
             "ثباته بيطابق طلبه", "ثباته أقل من اللي طلبه شوية"),
            ("projection", "projection", _WANTED_PROJECTION, similarity._PROJECTION_WORDS,
             "فوحانه بيطابق طلبه", "فوحانه أقل من اللي طلبه شوية"),
        ):
            wanted = intent.get(key)
            if not wanted:
                continue
            hit = _ordinal_hit(getattr(product, attribute, ""), wanted, vocabulary, words)
            if hit <= 0:
                entry.mismatches.append(
                    f"{'ثباته' if key == 'longevity' else 'فوحانه'} مش بيطابق طلبه"
                )
                continue
            entry.score += WEIGHTS[key] * hit
            entry.reasons.append(label if hit >= 1.0 else short_label)

        if intent.get("gender") and product.gender == str(intent["gender"]).lower():
            entry.score += WEIGHTS["gender"]

        if intent.get("wants_uncommon"):
            from .value import is_store_exclusive

            if is_store_exclusive(product):
                entry.score += WEIGHTS["uncommon"]
                entry.reasons.append("تركيب حصري بتاعنا — مش منتشر عند حد تاني")

        if max_price is not None:
            if _affordable_and_obtainable(product, max_price):
                entry.score += WEIGHTS["budget"]
            else:
                entry.mismatches.append("مفيش حجم متاح داخل ميزانيته")

        ranked.append(entry)

    # Equal scores fall through to the order the caller supplied, which
    # search_service._by_value has already sorted cheapest-brand-bottle first. The
    # `-oil_stock_grams` term that used to sit here went with oil tracking; there is
    # deliberately no replacement, because re-sorting on a second criterion here would
    # override the caller's ordering rather than defer to it.
    ranked.sort(key=lambda entry: -entry.score)
    return ranked


def reasons_note(entry):
    """The evidence line attached to a product block in the prompt.

    Reasons only — the numeric score is deliberately never rendered. Handing a model 0.62
    is how "شبهه بنسبة 95%" gets invented.
    """
    if entry is None:
        return ""
    parts = []
    if entry.reasons:
        parts.append("✅ ليه مناسب: " + " | ".join(entry.reasons))
    if entry.mismatches:
        parts.append("⚠️ مش مطابق في: " + " | ".join(entry.mismatches))
    return "\n".join(parts)
