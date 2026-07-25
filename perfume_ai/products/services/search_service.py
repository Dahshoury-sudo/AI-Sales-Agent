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
    longevity = intent.get("longevity")
    projection = intent.get("projection")
    exclude_name = intent.get("exclude_name")
    notes = intent.get("notes") or []

    # Hard filters (gender, brand, season, notes, longevity, projection)
    base = queryset
    if exclude_name:
        base = base.exclude(name__icontains=exclude_name)
    if gender:
        base = base.filter(gender=gender.lower())
    if season:
        base = base.filter(Q(season__icontains=season) | Q(season__icontains="All Seasons"))
    if brand:
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