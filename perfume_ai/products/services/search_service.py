from django.db.models import Q
from products.models import Product


def search_products(message):

    queryset = Product.objects.filter(is_active=True)

    text = message.lower()

    if "male" in text or "رجالي" in text:
        queryset = queryset.filter(gender="male")

    elif "female" in text or "حريمي" in text:
        queryset = queryset.filter(gender="female")

    elif "unisex" in text:
        queryset = queryset.filter(gender="unisex")

    return queryset[:5]