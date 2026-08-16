from django.db.models import Q, Sum
from products.models import Product, ProductVariant


# The AI only ever picks 1-2 perfumes out of whatever we hand it, but every
# product costs ~15 lines of prompt text. Without a cap the "no exact match"
# branch below serialises the entire filtered catalogue into a single request.
MAX_PRODUCTS_IN_CONTEXT = 12


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
    occasion = intent.get("occasion")
    longevity = intent.get("longevity")
    projection = intent.get("projection")
    exclude_names = intent.get("exclude_names") or []
    # Fallback to single exclude_name if present (backward compatibility)
    old_exclude = intent.get("exclude_name")
    if old_exclude and old_exclude not in exclude_names:
        exclude_names.append(old_exclude)

    notes = intent.get("notes") or []

    # Hard filters (gender, brand, season, notes, longevity, projection)
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

    # Soft filters: occasion, longevity, projection
    soft_filters = base
    if occasion:
        soft_filters = soft_filters.filter(occasion__icontains=occasion)
    if longevity:
        soft_filters = soft_filters.filter(longevity__icontains=longevity)
    if projection:
        soft_filters = soft_filters.filter(projection__icontains=projection)

    # Tier 1: All filters (occasion + notes + price)
    exact = soft_filters
    sweet_notes = ["vanilla", "caramel", "tonka", "praline", "honey", "chocolate", "cacao", "marshmallow", "sugar", "cherry", "plum"]
    for note in notes:
        if note.lower() in ["sweet", "gourmand", "مسكر", "سويتي"]:
            query = Q()
            for sn in sweet_notes:
                query |= Q(top_notes__icontains=sn) | Q(middle_notes__icontains=sn) | Q(base_notes__icontains=sn)
            exact = exact.filter(query)
        else:
            exact = exact.filter(
                Q(top_notes__icontains=note) |
                Q(middle_notes__icontains=note) |
                Q(base_notes__icontains=note)
            )
    if max_price:
        exact = exact.filter(variants__price__lte=max_price).distinct()

    if exact.exists():
        return {"products": _shortlist(exact), "alternatives": None}

    # No exact match — hand the AI the closest base products so it can:
    # 1. Upsell higher-price products that match the occasion
    # 2. Suggest cheaper products for different occasions
    if base.exists():
        return {"products": base.none(), "alternatives": _shortlist(base)}

    return {"products": base.none(), "alternatives": None}