from django.db.models import Q
from products.models import Product


def search_products(intent):
    queryset = Product.objects.filter(is_active=True)

    if intent.gender:
        queryset = queryset.filter(gender=intent.gender)

    if intent.season:
        queryset = queryset.filter(
            season__icontains=intent.season
        )

    if intent.max_price:
        queryset = queryset.filter(
            price__lte=intent.max_price
        )

    return queryset[:5]