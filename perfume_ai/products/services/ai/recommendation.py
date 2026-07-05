from .client import chat
from .prompts import SYSTEM_PROMPT


def recommend(message, products):
    if not products.exists():
        return "للأسف لم أجد أي عطر مطابق لطلبك."

    context = ""

    for product in products:
        context += f"""
Name: {product.name}
Brand: {product.brand.name}
Price: {product.price} EGP
Gender: {product.gender}
Season: {product.season}
Occasion: {product.occasion}
Longevity: {product.longevity}
Projection: {product.projection}
Top Notes: {product.top_notes}
Middle Notes: {product.middle_notes}
Base Notes: {product.base_notes}
Description: {product.description}

-------------------------
"""

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": f"""
Customer Request:

{message}

Available Products:

{context}

Recommend the best options and explain why.
Only recommend products from the list.
""",
        },
    ]

    return chat(messages)