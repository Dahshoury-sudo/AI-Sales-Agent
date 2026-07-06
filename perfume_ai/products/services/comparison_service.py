from django.db.models import Q

from products.models import Product
from .ai.client import chat
from .ai.prompts import get_system_prompt


import json

def compare_products(message, history=None, store=None):

    prompt = """
Extract the names of the two perfumes the user wants to compare from their message.
Fix any spelling mistakes in the perfume names.
Return ONLY valid JSON in this format:
{
  "perfume_1": "Name 1",
  "perfume_2": "Name 2"
}
"""
    try:
        response = chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": message}
        ], response_format={"type": "json_object"})
        
        data = json.loads(response)
        p1_name = data.get("perfume_1", "")
        p2_name = data.get("perfume_2", "")
    except Exception:
        p1_name = ""
        p2_name = ""

    products = Product.objects.filter(is_active=True)
    if store:
        products = products.filter(store=store)
    matches = []

    def find_product(name_str):
        if not name_str: return None
        query = Q()
        for word in name_str.split():
            if len(word) > 2:
                query &= (Q(name__icontains=word) | Q(brand__name__icontains=word))
        
        if not query: return None
        return products.filter(query).first()

    prod1 = find_product(p1_name)
    prod2 = find_product(p2_name)

    if prod1: matches.append(prod1)
    if prod2 and prod2 not in matches: matches.append(prod2)

    if len(matches) < 2:
        return "من فضلك اذكر اسم العطرين اللذين تريد المقارنة بينهما بوضوح، أو تأكد من توفرهما لدينا."

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
            "content": get_system_prompt(store),
        }
    ]
    if history:
        messages.extend(history)
        
    messages.append({
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
    })

    response = chat(messages)
    return response, context