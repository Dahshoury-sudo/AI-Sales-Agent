from django.db.models import Q

from products.models import Product
from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_resolver import resolve_product


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
        variants_str_list = []
        all_out_of_stock = True
        for v in variants:
            if v.bottle_type == 'normal':
                req_oil = (v.volume * product.concentration_percentage) / 100
                is_available = product.oil_stock_grams >= req_oil
                status = 'متوفر' if is_available else '❌ نفد من المخزون'
                variants_str_list.append(f"- زجاجة تعبئة {v.volume} ملي: {v.price} EGP ({status})")
                if is_available:
                    all_out_of_stock = False
            elif v.bottle_type == 'original':
                stock_num = v.stock or 0
                is_available = stock_num > 0
                if is_available:
                    status = f"متوفر ({stock_num} زجاجة فقط)" if stock_num <= 3 else "متوفر"
                else:
                    status = '❌ نفد من المخزون'
                variants_str_list.append(f"- زجاجة أوريجينال {v.volume} ملي: {v.price} EGP ({status})")
                if is_available:
                    all_out_of_stock = False
        
        variants_str = "\n".join(variants_str_list) if variants else "غير متوفر أسعار/أحجام حالياً"
        stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"
        is_custom_blend = product.brand.name.lower() == "perfamix"
        brand_display = "⭐ عطر تركيب حصري خاص بالمتجر" if is_custom_blend else product.brand.name

        context += f"""
Name: {product.name}
Brand: {brand_display}
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
4. ⭐ نظّم شكل المقارنة عشان تكون سهلة القراءة (استخدم نقاط ومسافات). افصل أسعار وأحجام كل عطر في سطر لوحده لتجنب تداخل الكلام الإنجليزي مع العربي (ممنوع تحط أسعار العطرين جنب بعض في نفس السطر).
5. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
6. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
"""
    })

    response = chat(messages)
    return response, context