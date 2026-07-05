from products.models import Product


def search_products(intent):
    queryset = Product.objects.filter(is_active=True)

    gender = intent.get("gender")
    season = intent.get("season")
    max_price = intent.get("max_price")

    if gender:
        queryset = queryset.filter(gender=gender)

    if season:
        queryset = queryset.filter(season__icontains=season)

    if max_price:
        queryset = queryset.filter(price__lte=max_price)

    return queryset