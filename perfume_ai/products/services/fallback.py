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


def suggest_alternatives(store, gender=None, max_price=None, exclude=None, limit=DEFAULT_LIMIT):
    """Products worth offering instead, ordered by how safe a suggestion each is.

    Deterministic, so the same conversation always produces the same fallback. Gender is
    honoured when known, widened to include unisex rather than treated as an exact match
    — a men's request is well served by a unisex perfume, and excluding those is what
    made a fifth of this catalogue unreachable.
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
    return candidates[:limit]
