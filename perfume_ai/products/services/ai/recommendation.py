from .client import chat
from .prompts import get_system_prompt


def _format_products(products):
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
    return context


def recommend(message, products, history=None, alternatives=None, store=None):
    # Case 1: Exact matches found
    if products.exists():
        context = _format_products(products)
        user_content = f"""
Customer Request:

{message}

Available Products:

{context}

Recommend the best options and explain why.
Only recommend products from the list.
"""

    # Case 2: No exact match, but we have alternatives (e.g. higher price)
    elif alternatives and alternatives.exists():
        context = _format_products(alternatives)
        user_content = f"""
Customer Request:

{message}

IMPORTANT: No products matched the customer's exact criteria (e.g. budget).
However, these similar products are available:

{context}

Your job is to be a smart salesperson:
1. Acknowledge that nothing matched their exact budget.
2. Present these alternatives and highlight their value and why they're worth the price.
3. If there are cheaper options in the list, mention them too.
4. Be persuasive but honest. Never pressure the customer.
"""

    else:
        return "للأسف لم أجد أي عطر مطابق لطلبك. جرب تغيير بعض المعايير وهحاول أساعدك!", ""

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
        "content": user_content,
    })

    response = chat(messages)
    return response, context