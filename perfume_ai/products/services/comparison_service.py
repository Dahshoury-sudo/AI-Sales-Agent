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
        return "واحد او اكثر من العطور دي مش متوفر عندنا للاسف ممكن تقولي اسماء عطور تانية؟", ""

    context = ""

    for product in matches:
        variants = list(product.variants.all())
        available_variants = []
        out_of_stock_variants = []
        all_out_of_stock = True
        for v in variants:
            if v.bottle_type == 'normal':
                req_oil = (v.volume * product.concentration_percentage) / 100
                is_available = product.oil_stock_grams >= req_oil
                if is_available:
                    available_variants.append(f"- الـ {v.volume} ملي: {v.price} EGP")
                    all_out_of_stock = False
                else:
                    out_of_stock_variants.append(f"الـ {v.volume} ملي")
            elif v.bottle_type == 'original':
                stock_num = v.stock or 0
                is_available = stock_num > 0
                if is_available:
                    status = f" ({stock_num} زجاجة فقط)" if stock_num <= 3 else ""
                    available_variants.append(f"- زجاجة أوريجينال {v.volume} ملي: {v.price} EGP{status}")
                    all_out_of_stock = False
                else:
                    out_of_stock_variants.append(f"زجاجة أوريجينال {v.volume} ملي")
        
        avail_str = "\n".join(available_variants) if available_variants else "لا توجد أحجام متوفرة حالياً"
        oos_str = "، ".join(out_of_stock_variants) if out_of_stock_variants else "لا يوجد"
        stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"
        is_custom_blend = bool(product.store and product.brand.name.lower() == product.store.name.lower())
        brand_display = "⭐ عطر تركيب حصري خاص بالمتجر" if is_custom_blend else product.brand.name

        has_original_bottle = any(v.bottle_type == 'original' for v in variants)
        if has_original_bottle:
            original_bottle_status = "Available (see sizes below)"
        elif is_custom_blend:
            original_bottle_status = 'NOT AVAILABLE — this is a store-exclusive perfume (NOT a global brand). If asked, say EXACTLY: "ده عطر من تصميمنا وابتكارنا إحنا يا فندم، فمفيش منه زجاجة أوريجينال."'
        else:
            original_bottle_status = f'NOT AVAILABLE — this is a GLOBAL BRAND ({product.brand.name}) perfume, NOT store-exclusive. If asked, say EXACTLY: "للاسف مش متوفر منه زجاجة أوريجينال حالياً". ❌ DO NOT say it is store-exclusive or حصري.'

        context += f"""
Name: {product.name}
Brand: {brand_display}
Stock Status: {stock_status}
Original Bottle: {original_bottle_status}
Available Sizes & Prices:
{avail_str}
Out of Stock Sizes (DO NOT OFFER unless explicitly asked):
{oos_str}
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
1. قارن بين العطرين بشكل مختصر وواضح من حيث **المواصفات فقط** (الثبات، الفوحان/Projection، الموسم، المناسبة، ووصف مختصر للريحة في جملة أو اتنين بدون سرد النوتات بالتفصيل).
2. ❌ ممنوع تماماً تذكر أي أسعار أو أحجام أو معلومات عن التوفر في المقارنة.
3. انصح العميل أي واحد يناسبه أكتر بناءً على ذوقه أو سؤاله.
4. ⭐ في نهاية المقارنة، اسأل العميل: "تحب تعرف الأسعار والأحجام المتاحة من كل عطر فيهم؟"
5. ⭐ نظّم شكل المقارنة عشان تكون سهلة القراءة (استخدم نقاط ومسافات).
6. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
7. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
"""
    })

    response = chat(messages)
    return response, context