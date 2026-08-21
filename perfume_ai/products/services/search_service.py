from django.db.models import Case, Q, When
from products.models import Product

from .product_formatting import _bottles_fillable
from .sales import ranking, similarity
from .sales.notes import SWEET_NOTE_EXPANSION, SWEET_REQUEST_TERMS


# The AI only ever picks 1-2 perfumes out of whatever we hand it, but every
# product costs ~15 lines of prompt text. Without a cap the "no exact match"
# branch below serialises the entire filtered catalogue into a single request.
MAX_PRODUCTS_IN_CONTEXT = 12

# How many products the Python scorer will look at before trimming to the prompt cap.
# Ranking has to see more candidates than it returns or it cannot reorder anything, but
# it also cannot walk an unbounded catalogue on every turn.
MAX_CANDIDATES_TO_SCORE = 60


def _shortlist(queryset):
    """Trim a candidate queryset down to what fits comfortably in one prompt.

    Ordered by oil stock descending so the perfumes most likely to be
    fulfillable in any size come first — the prompts tell the model to skip
    anything marked out of stock, so leading with empty shelves wastes the
    shortlist. The `id` tie-break keeps it deterministic: the prompts also tell
    the model to stay on a perfume once the customer shows interest, which a
    shortlist that reshuffled between turns would undermine.
    """
    return queryset.order_by('-oil_stock_grams', 'id')[:MAX_PRODUCTS_IN_CONTEXT]


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
        wanted = [note]
        if str(note).lower() in SWEET_REQUEST_TERMS:
            # "عايز حاجة مسكرة" names an accord, not an ingredient. Same expansion list
            # as before, moved to sales.notes.
            wanted = list(SWEET_NOTE_EXPANSION)
        for term in wanted:
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
    """
    name = intent.get("similar_to")
    if not name:
        return None

    if store is not None:
        match = (
            Product.objects.filter(store=store, is_active=True, name__icontains=name)
            .prefetch_related("variants")
            .first()
        )
        if match is not None:
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


def search_products(intent, store=None):
    queryset = Product.objects.filter(is_active=True).prefetch_related('variants')
    if store:
        queryset = queryset.filter(store=store)

    # Exclude products with no stock
    queryset = queryset.filter(Q(oil_stock_grams__gt=0) | Q(variants__stock__gt=0)).distinct()

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
    for name in exclude_names:
        base = base.exclude(name__icontains=name)
    if gender:
        base = base.filter(gender=gender.lower())
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

    candidates = list(pool.order_by('-oil_stock_grams', 'id')[:MAX_CANDIDATES_TO_SCORE])
    ranked = ranking.rank(candidates, intent, reference=reference, fillable=_bottles_fillable)
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
