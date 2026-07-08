from .product_resolver import resolve_products
from .ai.client import chat
from .ai.prompts import get_system_prompt


def get_product_info(message, history=None, store=None):

    products = resolve_products(message, history, store)

    if products:
        context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        for product in products:
            context += f"""
Name: {product.name}
Brand: {product.brand.name}
Price: {product.price} EGP
Volume: {product.volume} ml
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
        instructions = """
═══ تعليمات صارمة ═══
1. رد على سؤال العميل باستخدام البيانات أعلاه فقط.
2. لو العميل سأل عن السعر، اذكر السعر كما هو مكتوب بالظبط — ❌ ممنوع تغييره أو تقريبه.
3. لو العميل سأل عن الحجم أو الملي، اذكر الحجم كما هو مكتوب بالظبط.
4. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة).
5. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
"""
    else:
        context = ""
        instructions = """
═══ تعليمات ═══
لم يتم العثور على المنتج في قاعدة البيانات.
1. ارجع لسجل المحادثة أعلاه — لو العميل بيسأل عن منتج اتذكر قبل كده (مثلاً "كام ملي"، "حجمه اي"، "بكام ده")، رد من المعلومات اللي اتقالت في المحادثة.
2. لو مش قادر تحدد أي منتج بيسأل عنه حتى من المحادثة، اسأله: "تقصد أي عطر بالظبط يا فندم؟"
3. ❌ ممنوع تخترع أي سعر أو حجم أو معلومة من عندك.
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
═══ سؤال العميل ═══
{message}

{context}
{instructions}
"""
    })

    response = chat(messages)
    return response, context