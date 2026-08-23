from django.db.models import Case, DecimalField, Min, OuterRef, Q, Subquery, When
from products.models import Product, ProductVariant

from .product_formatting import is_variant_available
from .sales import naming, ranking, similarity
from .sales.notes import expand_request_term


# The AI only ever picks 1-2 perfumes out of whatever we hand it, but every
# product costs ~15 lines of prompt text. Without a cap the "no exact match"
# branch below serialises the entire filtered catalogue into a single request.
MAX_PRODUCTS_IN_CONTEXT = 12

# How many products the Python scorer will look at before trimming to the prompt cap.
# Ranking has to see more candidates than it returns or it cannot reorder anything, but
# it also cannot walk an unbounded catalogue on every turn.
MAX_CANDIDATES_TO_SCORE = 60

# Products that can actually be sold: a brand bottle is compounded to order, an original
# bottle is a counted unit. Both conditions of the original clause sit inside one Q() on
# purpose — split across two, they would match different joined variant rows and a product
# with any original at all would qualify regardless of its stock.
SELLABLE = Q(variants__bottle_type="normal") | Q(
    variants__bottle_type="original", variants__stock__gt=0
)

# Cheapest brand bottle, as a correlated subquery rather than
# `annotate(Min('variants__price', ...))`. The queryset already filters on the
# multi-valued `variants` relation, and an aggregate over a relation that is also
# filtered on is computed across the duplicated join rows — so the annotation would be
# quietly wrong. A subquery is evaluated independently of the join.
_CHEAPEST_BRAND_PRICE = Subquery(
    ProductVariant.objects.filter(product=OuterRef("pk"), bottle_type="normal")
    .order_by("price")
    .values("price")[:1],
    output_field=DecimalField(max_digits=10, decimal_places=2),
)


def _by_value(queryset):
    """Order candidates cheapest-brand-bottle first, then by id.

    Replaces `order_by('-oil_stock_grams', 'id')`. That ordering existed to avoid leading
    with empty shelves, a concern that disappears once brand bottles are always
    available — but *something* has to order the shortlist deterministically, because the
    prompts tell the model to stay on a perfume once the customer shows interest and a
    list that reshuffled between turns would undermine that.

    Cheapest-first is the deliberate replacement: the previous ordering was by bulk oil
    inventory, which is commercially arbitrary, and this catalogue serves a
    price-sensitive market. `id` breaks ties so the result is stable.
    """
    return queryset.annotate(_cheapest=_CHEAPEST_BRAND_PRICE).order_by("_cheapest", "id")


def _shortlist(queryset):
    """Trim a candidate queryset down to what fits comfortably in one prompt."""
    return _by_value(queryset)[:MAX_PRODUCTS_IN_CONTEXT]


def _notes_query(notes):
    """One OR query across every requested note.

    Previously each note was a separate chained `.filter()`, i.e. an AND: a perfume had
    to contain *all* of them. Three or four notes essentially never co-occur in one
    product, so a similarity request — which is expanded into exactly that many notes —
    matched nothing, and the caller silently fell through to a branch filtering on
    gender and brand alone. That is the whole mechanism behind "something like Sauvage"
    returning same-brand perfumes that smell nothing alike.

    Partial matches now survive as candidates and are separated by score instead.
    """
    query = Q()
    for note in notes:
        # Accord words ("مسكر", "فريش") name a family, not an ingredient. The expansion
        # table lives in sales.notes so the ranker scores exactly what this filters on.
        for term in expand_request_term(note):
            query |= (
                Q(top_notes__icontains=term)
                | Q(middle_notes__icontains=term)
                | Q(base_notes__icontains=term)
            )
    return query


def _resolve_reference(intent, store):
    """The perfume the customer asked to be matched against, if any.

    Prefers a catalogue hit, whose real notes are the best evidence we can have. Falls
    back to the notes the extractor supplied from general knowledge, which is weaker and
    is labelled as such so the reply phrases it more cautiously.

    Matched through sales.naming rather than `name__icontains=<whole string>`: the
    extractor returns "9pm by Afnan" for a row named "Afnan 9PM", and "Ambiro" for
    "Ambero", both of which a substring test misses entirely — so the strongest evidence
    available (the product's own recorded notes) was being discarded in favour of the
    model's guess about a perfume we actually stock.
    """
    name = intent.get("similar_to")
    if not name:
        return None

    if store is not None:
        match = naming.match_product(name, store)
        if match is not None:
            # Re-fetch with variants so downstream performance comparisons do not
            # trigger a query per candidate.
            match = (
                Product.objects.filter(pk=match.pk).prefetch_related("variants").first()
            ) or match
            return similarity.reference_from_product(match)

    reference_notes = intent.get("similar_to_notes") or []
    if reference_notes:
        return similarity.reference_from_notes(name, reference_notes)
    return None


def _ordered_by_ids(queryset, ids):
    """Re-order a queryset to match a ranked id list.

    Returns a QuerySet rather than a list on purpose: `recommend()` calls `.exists()` on
    this, and the existing tests call `len()` and iterate. Case/When keeps that contract
    intact while letting Python decide the order.
    """
    if not ids:
        return queryset.none()
    ordering = Case(*[When(id=identifier, then=position) for position, identifier in enumerate(ids)])
    return queryset.filter(id__in=ids).order_by(ordering)


def _obtainable_only(queryset):
    """Drop products no size of which can actually be sold.

    With brand bottles always available, the only thing this can now exclude is a product
    whose every variant is an original with zero stock — `SELLABLE` already covers that in
    SQL, so this is a belt-and-braces pass over the same rule expressed through
    `is_variant_available`.

    Kept rather than deleted because the two expressions can drift: `SELLABLE` is a join
    condition and this is the per-variant predicate the renderer uses to mark sizes out of
    stock. If they ever disagree, the model gets shown a size the order flow then refuses,
    which is the failure this function was originally written for.
    """
    unobtainable = [
        product.id
        for product in queryset.prefetch_related("variants")
        # Scoped to products that HAVE sizes but none of them sellable. A product with no
        # variants at all keeps whatever behaviour it had before.
        if product.variants.all()
        and not any(is_variant_available(variant) for variant in product.variants.all())
    ]
    return queryset.exclude(id__in=unobtainable) if unobtainable else queryset


def search_products(intent, store=None):
    queryset = Product.objects.filter(is_active=True).prefetch_related('variants')
    if store:
        queryset = queryset.filter(store=store)

    # Only products with something sellable in them.
    queryset = queryset.filter(SELLABLE).distinct()
    queryset = _obtainable_only(queryset)

    gender = intent.get("gender")
    perfume_type = intent.get("perfume_type")
    season = intent.get("season")
    max_price = intent.get("max_price")
    brand = intent.get("brand")
    exclude_names = intent.get("exclude_names") or []
    # Fallback to single exclude_name if present (backward compatibility)
    old_exclude = intent.get("exclude_name")
    if old_exclude and old_exclude not in exclude_names:
        exclude_names.append(old_exclude)

    notes = intent.get("notes") or []

    # Hard filters: the criteria a customer means literally. gender/brand/type/season
    # stay filters because a men's perfume is not a near-miss for a woman.
    base = queryset
    # Exclusions are resolved to catalogue spellings first. An extractor that returns
    # "9pm by Afnan" for the row "Afnan 9PM" excluded nothing at all, so the perfume the
    # customer had just asked for an *alternative* to stayed in the running.
    for name in naming.resolve_names(exclude_names, store):
        base = base.exclude(name__icontains=name)
    if gender:
        base = base.filter(Q(gender=gender.lower()) | Q(gender="unisex"))
    if perfume_type:
        base = base.filter(perfume_type=perfume_type.lower())
    if season:
        base = base.filter(Q(season__icontains=season) | Q(season__icontains="All Seasons"))
    if brand:
        if brand == "STORE_BRAND_EXCLUSIVE" and store:
            base = base.filter(brand__name__iexact=store.name)
        else:
            base = base.filter(brand__name__icontains=brand)

    # occasion, longevity and projection are deliberately NOT filtered any more. They were
    # `icontains` ANDs despite being called soft, and `icontains` against an empty column
    # matches nothing — so naming an occasion silently deleted every product whose
    # occasion field the store never filled in. They are ranking signals now.
    reference = _resolve_reference(intent, store)

    # A perfume cannot be its own lookalike. Left in the pool it scored 1.0 against
    # itself, which both handed the model the very perfume the customer already knows as
    # the top "similar to X" result and — worse — set has_close_match=True off that self
    # match, so the honesty path reported a close match that did not exist.
    if reference is not None and reference.product is not None:
        base = base.exclude(pk=reference.product.pk)

    exact = base
    if notes:
        exact = exact.filter(_notes_query(notes)).distinct()
    if max_price:
        exact = exact.filter(variants__price__lte=max_price).distinct()

    # No ranking signal means nothing can discriminate between candidates, so the legacy
    # ordering is used untouched. This is what keeps the existing shortlist tests honest
    # rather than shuffling an all-equal list through a scorer.
    if not ranking.has_signal(intent, reference):
        if exact.exists():
            return {"products": _shortlist(exact), "alternatives": None, "similarity": None}
        if base.exists():
            return {"products": base.none(), "alternatives": _shortlist(base), "similarity": None}
        return {"products": base.none(), "alternatives": None, "similarity": None}

    # The exact-versus-alternatives decision stays exactly what it was: did anything match
    # the literal criteria? Ranking only decides the *order* within whichever set wins.
    # Letting the score pick the branch was wrong — gender and stock contribute points to
    # every candidate in the pool, so any threshold on the total is really a threshold on
    # a constant offset, and it wrongly promoted a zero-note-match set to "exact".
    matched = exact.exists()
    pool = exact if matched else base
    if not pool.exists():
        return {"products": base.none(), "alternatives": None, "similarity": None}

    candidates = list(_by_value(pool)[:MAX_CANDIDATES_TO_SCORE])
    ranked = ranking.rank(candidates, intent, reference=reference)
    top = ranked[:MAX_PRODUCTS_IN_CONTEXT]
    ordered = _ordered_by_ids(pool, [entry.product.id for entry in top])

    return {
        "products": ordered if matched else pool.none(),
        "alternatives": None if matched else ordered,
        "similarity": _similarity_summary(reference, ranked),
        # Keyed by product id so the renderer can attach each product's own reasons.
        "ranked": {entry.product.id: entry for entry in top},
    }


def _similarity_summary(reference, ranked):
    """What to tell the caller about how close we actually got.

    The honesty path for "شبه Sauvage" lives here: when the best candidate is below the
    loose band there is no close match, and saying so is the correct answer rather than
    presenting the nearest perfume as though it were one.
    """
    if reference is None or not reference.is_usable:
        return None

    best = 0.0
    for entry in ranked:
        if entry.result is not None:
            best = max(best, entry.result.score)

    return {
        "reference_name": reference.name,
        "reference_source": reference.source,
        "best_band": similarity.band_for(best),
        "has_close_match": similarity.band_for(best) == "close",
    }
