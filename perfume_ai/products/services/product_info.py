from .product_resolver import resolve_products
from .ai.client import chat
from .ai.prompts import get_system_prompt


def get_product_info(message, history=None, store=None):

    products = resolve_products(message, history, store)

    if products:
        context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        for product in products:
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
        instructions = """
═══ تعليمات صارمة ═══
1. رد على سؤال العميل باستخدام البيانات أعلاه فقط.
2. لو العميل سأل عن السعر، اذكر السعر كما هو مكتوب بالظبط — ❌ ممنوع تغييره أو تقريبه.
3. لو العميل سأل عن الحجم أو الملي، اذكر الحجم كما هو مكتوب بالظبط.
4. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة).
5. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
6. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌) أو حجم معين نفد، أخبر العميل بذلك بشكل لطيف واقترح عليه إنه يسأل عن عطور تانية متوفرة أو اعرض عليه الأحجام المتوفرة إن وجدت.
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