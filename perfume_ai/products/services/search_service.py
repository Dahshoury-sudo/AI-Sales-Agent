from django.db.models import Q
from products.models import Product


def search_products(intent, store=None):
    queryset = Product.objects.filter(is_active=True)
    if store:
        queryset = queryset.filter(store=store)

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
        base = base.filter(season__icontains=season)
    if brand:
        base = base.filter(brand__name__icontains=brand)
    for note in notes:
        base = base.filter(
            Q(top_notes__icontains=note) |
            Q(middle_notes__icontains=note) |
            Q(base_notes__icontains=note)
        )

    # Soft filter: occasion
    if occasion:
        with_occasion = base.filter(occasion__icontains=occasion)
    else:
        with_occasion = base

    # Tier 1: All filters (occasion + price)
    if max_price:
        exact = with_occasion.filter(price__lte=max_price)
    else:
        exact = with_occasion

    if exact.exists():
        return {"products": exact, "alternatives": None}

    # No exact match — give AI ALL base products so it can:
    # 1. Upsell higher-price products that match the occasion
    # 2. Suggest cheaper products for different occasions
    if base.exists():
        return {"products": base.none(), "alternatives": base}

    return {"products": base.none(), "alternatives": None}