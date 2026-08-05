from django.db.models import Q, Sum
from products.models import Product, ProductVariant


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
        return {"products": exact, "alternatives": None}

    # No exact match — give AI ALL base products so it can:
    # 1. Upsell higher-price products that match the occasion
    # 2. Suggest cheaper products for different occasions
    if base.exists():
        return {"products": base.none(), "alternatives": base}

    return {"products": base.none(), "alternatives": None}