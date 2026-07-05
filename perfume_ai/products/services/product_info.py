from .product_resolver import resolve_product
from .ai.client import chat
from .ai.prompts import SYSTEM_PROMPT


def get_product_info(message):

    product = resolve_product(message)

    if not product:
        return "للأسف لم أجد هذا المنتج."

    context = f"""
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
"""

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": f"""
Use ONLY the information below.

{context}

Customer Question:

{message}
"""
        }
    ]

    return chat(messages)