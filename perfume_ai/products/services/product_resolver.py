from django.db.models import Q

from products.models import Product


def resolve_product(message: str):
    """
    Try to resolve a product from the user's message.
    """

    message = message.lower()

    products = Product.objects.filter(is_active=True)

    # Direct product name
    product = products.filter(
        name__icontains=message
    ).first()

    if product:
        return product

    # Brand name
    product = products.filter(
        brand__name__icontains=message
    ).first()

    if product:
        return product

    # Word by word search
    words = message.split()

    query = Q()

    for word in words:
        query |= Q(name__icontains=word)
        query |= Q(brand__name__icontains=word)

    return products.filter(query).first()