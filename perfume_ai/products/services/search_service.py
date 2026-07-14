from django.db.models import Q, Sum
from products.models import Product, ProductVariant


def search_products(intent, store=None):
    queryset = Product.objects.filter(is_active=True).prefetch_related('variants')
    if store:
        queryset = queryset.filter(store=store)
    
    # Exclude products with no stock
    queryset = queryset.filter(Q(oil_stock_grams__gt=0) | Q(variants__stock__gt=0)).distinct()

    gender = intent.get("gender")
    season = intent.get("season")
    max_price = intent.get("max_price")
    brand = intent.get("brand")
    occasion = intent.get("occasion")
    notes = intent.get("notes") or []

    # Hard filters (gender, brand, season, notes)
    base = queryset
    if gender:
        base = base.filter(gender=gender.lower())
    if season:
        base = base.filter(Q(season__icontains=season) | Q(season__icontains="All Seasons"))
    if brand:
        base = base.filter(brand__name__icontains=brand)


    # Soft filter: occasion
    if occasion:
        with_occasion = base.filter(occasion__icontains=occasion)
    else:
        with_occasion = base

    # Tier 1: All filters (occasion + notes + price)
    exact = with_occasion
    for note in notes:
        exact = exact.filter(
            Q(top_notes__icontains=note) |
            Q(middle_notes__icontains=note) |
            Q(base_notes__icontains=note)
        )
    if max_price:
        exact = exact.filter(variants__price__lte=max_price).distinct()

    if exact.exists():
        return {"products": exact, "alternatives": None}

    # No exact match — give AI ALL base products so it can:
    # 1. Upsell higher-price products that match the occasion
    # 2. Suggest cheaper products for different occasions
    if base.exists():
        return {"products": base.none(), "alternatives": base}

    return {"products": base.none(), "alternatives": None}