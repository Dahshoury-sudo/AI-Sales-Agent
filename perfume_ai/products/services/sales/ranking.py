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
    `(-oil_stock_grams, id)`, which is exactly the previous ordering. That is what lets
    this land without changing behaviour for the searches that already worked.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import similarity
from .notes import (
    HEAVY_FAMILIES,
    HEAVY_NOTES,
    LIGHT_FAMILIES,
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
    "stock": 0.5,
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


def _affordable_and_obtainable(product, max_price, fillable):
    """Is there a bottle that is both within budget and actually gettable?

    The two ORM filters that approximate this are separate joins, so a product can qualify
    on budget through one variant and on availability through another. That is only a
    wasted shortlist slot rather than a wrong price — the renderer marks unfillable sizes
    out of stock — but the slot is worth reclaiming.
    """
    for variant in product.variants.all():
        if max_price is not None and variant.price > max_price:
            continue
        if variant.bottle_type == "normal":
            if fillable(product, variant) > 0:
                return True
        elif (variant.stock or 0) > 0:
            return True
    return False


def rank(products, intent, reference=None, fillable=None):
    """Score and order candidates, best first.

    `fillable` is injected rather than imported to keep this module free of any dependency
    on the renderer (which imports the value helpers, which would close a cycle).
    """
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
            if any(str(note).strip().lower() in existing for existing in profile)
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
            ("longevity", "longevity", "longevity", "ثباته بيطابق طلبه"),
            ("projection", "projection", "projection", "فوحانه بيطابق طلبه"),
        ):
            wanted = intent.get(key)
            if not wanted:
                continue
            if _text_hit(getattr(product, attribute, ""), wanted):
                entry.score += WEIGHTS[weight_name]
                entry.reasons.append(label)

        if intent.get("gender") and product.gender == str(intent["gender"]).lower():
            entry.score += WEIGHTS["gender"]

        if intent.get("wants_uncommon"):
            from .value import is_store_exclusive

            if is_store_exclusive(product):
                entry.score += WEIGHTS["uncommon"]
                entry.reasons.append("تركيب حصري بتاعنا — مش منتشر عند حد تاني")

        if max_price is not None and fillable is not None:
            if _affordable_and_obtainable(product, max_price, fillable):
                entry.score += WEIGHTS["budget"]
            else:
                entry.mismatches.append("مفيش حجم متاح داخل ميزانيته")

        if (product.oil_stock_grams or 0) > 0:
            entry.score += WEIGHTS["stock"]

        ranked.append(entry)

    # The tie-break is the compatibility guarantee: equal scores fall through to exactly
    # the ordering search_products used before ranking existed.
    ranked.sort(
        key=lambda entry: (
            -entry.score,
            -(entry.product.oil_stock_grams or 0),
            entry.product.id,
        )
    )
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
