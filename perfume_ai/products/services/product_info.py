from .product_resolver import resolve_product
from .ai.client import chat
from .ai.prompts import get_system_prompt


def get_product_info(message, history=None, store=None):

    product = resolve_product(message, store)

    if product:
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
    else:
        context = "Use the products previously discussed in the history to answer. If you still don't know the product, politely say so."

    messages = [
        {
            "role": "system",
            "content": get_system_prompt(store),
        }
    ]
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": f"""
Use ONLY the information below (or from the conversation history).

{context}

Customer Question:

{message}
"""
    })

    response = chat(messages)
    return response, context