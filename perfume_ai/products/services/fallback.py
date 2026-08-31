"""Pick alternatives to suggest when the thing the customer asked for is not available.

Three call sites used `order_by('?')` — product_info's not-found branch, the router's
repetition redirect, and order_service's unresolved-product branch. Random selection
made the fallback actively misleading: a customer asking about Black Orchid was offered
Libre (a women's perfume) and Dior Homme Intense (a men's) side by side, because the
choice ignored gender, budget and everything else they had said.

Random also made the failure irreproducible, which is the other reason it had to go: the
same conversation could not be replayed to see what the customer was shown.
"""

from django.db.models import Q

from .product_formatting import is_variant_available
from .sales import notes as note_tables
from .sales import ranking, similarity

# Enough to give the customer a real choice without turning the reply into a catalogue.
DEFAULT_LIMIT = 3


def _obtainable(product):
    variants = list(product.variants.all())
    if not variants:
        return False
    return any(is_variant_available(variant) for variant in variants)


def _cheapest_price(product):
    prices = [
        variant.price
        for variant in product.variants.all()
        if variant.bottle_type == "normal" and variant.volume
    ]
    return min(prices) if prices else None


def _requested_fit(product, wanted):
    """How well one product answers the notes the customer asked for, 0.0-1.0.

    Mirrors the note block of `ranking.rank` — tolerant families on the product side, one
    `ranking.note_fit` per requested term, mean across terms — so the fallback path and a
    recommendation turn agree on what "smells like عود" means.

    Deliberately not `ranking.rank` itself. A `Ranked.matched_something` is true for a budget
    reason as well as a note one, so ranking through it here would keep promoting a perfume for
    being cheap, which is the defect the `notes` argument exists to remove. The scoring
    primitive is shared; the verdict is local.
    """
    profile = similarity.note_profile(product)
    if not (profile and wanted):
        return 0.0
    return sum(ranking.note_fit(term, profile, note_tables.families(profile, tolerant=True))
               for term in wanted) / len(wanted)


def suggest_alternatives(store, gender=None, max_price=None, exclude=None,
                         limit=DEFAULT_LIMIT, notes=()):
    """Products worth offering instead, ordered by how safe a suggestion each is.

    Deterministic, so the same conversation always produces the same fallback. Gender is
    honoured when known, widened to include unisex rather than treated as an exact match
    — a men's request is well served by a unisex perfume, and excluding those is what
    made a fifth of this catalogue unreachable.

    `notes` are the accords the customer actually asked for, from `sales.notes.terms_in`.
    Supplied, they become the top sort tier; omitted, this function behaves exactly as it did
    before — which is what keeps the router's repetition redirect and order_service's
    unresolved-product branch unchanged. Only product_info opts in.
    """
    from products.models import Product

    from .search_service import SELLABLE

    queryset = (
        Product.objects.filter(store=store, is_active=True)
        .filter(SELLABLE)
        .select_related("brand")
        .prefetch_related("variants")
        .distinct()
    )

    if gender in ("male", "female"):
        queryset = queryset.filter(Q(gender=gender) | Q(gender="unisex"))
    if exclude:
        queryset = queryset.exclude(pk__in=[product.pk for product in exclude])

    candidates = [product for product in queryset if _obtainable(product)]

    def sort_key(product):
        price = _cheapest_price(product)
        # In-budget first, then cheapest, then a stable name tie-break.
        over_budget = bool(max_price is not None and price is not None and price > max_price)
        return (over_budget, price if price is not None else 0, product.name)

    candidates.sort(key=sort_key)

    if notes:
        # Note fit is a tier above price, not a replacement for it: the sort is stable, so
        # everything scoring equally — including everything scoring zero — keeps the price
        # order underneath, budget-first ordering included.
        #
        # Conversation 795: "عندكو لادور بخور صح ؟" named a bakhoor this store does not carry,
        # and a price-only sort answered with the cheapest perfume in the catalogue while two
        # real incense perfumes sat in it. Being cheap is not an answer to being asked for بخور.
        candidates.sort(key=lambda product: -_requested_fit(product, notes))

    return candidates[:limit]
