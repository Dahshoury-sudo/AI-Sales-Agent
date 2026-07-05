from django.db.models import Q

from products.models import Product
from .ai.client import chat
from .ai.prompts import SYSTEM_PROMPT


def compare_products(message):

    products = Product.objects.filter(is_active=True)

    words = message.split()

    query = Q()

    for word in words:
        query |= Q(name__icontains=word)
        query |= Q(brand__name__icontains=word)

    matches = products.filter(query).distinct()[:2]

    if matches.count() < 2:
        return "من فضلك اذكر اسم العطرين اللذين تريد المقارنة بينهما."

    context = ""

    for product in matches:

        context += f"""
Name: {product.name}
Brand: {product.brand.name}
Price: {product.price}
Gender: {product.gender}
Season: {product.season}
Occasion: {product.occasion}
Longevity: {product.longevity}
Projection: {product.projection}
Top Notes: {product.top_notes}
Middle Notes: {product.middle_notes}
Base Notes: {product.base_notes}
Description: {product.description}

-----------------------
"""

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": f"""
Compare ONLY these perfumes.

{context}

Customer Request:

{message}

Explain:

- Similarities
- Differences
- Which one is better for whom
- Which one offers better value
"""
        }
    ]

    return chat(messages)