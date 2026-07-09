from django.db.models import Q

from products.models import Product
from .ai.client import chat
from .ai.prompts import get_system_prompt


import json

def compare_products(message, history=None, store=None):

    prompt = """
Extract the names of the two perfumes the user wants to compare from their message or conversation history.
Fix any spelling mistakes in the perfume names. Translate Arabic names to English.
Return ONLY valid JSON in this format:
{
  "perfume_1": "Name 1",
  "perfume_2": "Name 2"
}
"""
    messages_for_extract = [{"role": "system", "content": prompt}]
    if history:
        messages_for_extract.extend(history)
    messages_for_extract.append({"role": "user", "content": message})

    try:
        response = chat(messages_for_extract, response_format={"type": "json_object"})
        
        data = json.loads(response)
        p1_name = data.get("perfume_1", "")
        p2_name = data.get("perfume_2", "")
    except Exception:
        p1_name = ""
        p2_name = ""

    from .product_resolver import resolve_product
    
    prod1 = resolve_product(p1_name, history, store)
    prod2 = resolve_product(p2_name, history, store)

    matches = []
    if prod1: matches.append(prod1)
    if prod2 and prod2 not in matches: matches.append(prod2)

    if len(matches) < 2:
        return "مش واضحلي العطرين اللي عايز تقارن بينهم يا فندم. ممكن تقولي أساميهم تاني؟", ""

    context = ""

    for product in matches:
        variants = list(product.variants.all())
        variants_str = "\n".join([f"- {v.volume}ml: {v.price} EGP ({'متوفر - المخزون: ' + str(v.stock) if v.stock > 0 else '❌ نفد من المخزون'})" for v in variants]) if variants else "غير متوفر أسعار/أحجام حالياً"
        all_out_of_stock = all(v.stock == 0 for v in variants) if variants else True
        stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"

        context += f"""
Name: {product.name}
Brand: {product.brand.name}
Stock Status: {stock_status}
Available Sizes & Prices:
{variants_str}
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
═══ طلب العميل ═══
{message}

═══ بيانات العطرين من قاعدة البيانات ═══
{context}

═══ تعليمات المقارنة ═══
1. قارن بين العطرين بشكل مختصر وواضح.
2. اذكر الفروقات الرئيسية (السعر، الثبات، المناسبة، النوع).
3. انصح العميل أي واحد يناسبه أكتر بناءً على ذوقه أو سؤاله.
4. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
5. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
"""
    })

    response = chat(messages)
    return response, context