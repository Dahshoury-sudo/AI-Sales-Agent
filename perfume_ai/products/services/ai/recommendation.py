from .client import chat
from .prompts import get_system_prompt


def _format_products(products):
    context = ""
    for product in products:
        variants = list(product.variants.all())
        variants_str = "\n".join([f"- {v.volume}ml: {v.price} EGP" for v in variants]) if variants else "غير متوفر أسعار/أحجام حالياً"
        context += f"""
Name: {product.name}
Brand: {product.brand.name}
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

-------------------------
"""
    return context


def recommend(message, products, history=None, alternatives=None, store=None):
    # Case 1: Exact matches found
    if products.exists():
        context = _format_products(products)
        user_content = f"""
═══ طلب العميل ═══
{message}

═══ المنتجات المتاحة (هذه هي المنتجات الوحيدة الموجودة — لا تذكر أي منتج خارج هذه القائمة) ═══
{context}

═══ تعليمات الرد ═══
1. اختر أفضل 1-2 منتج من القائمة يناسب طلب العميل.
2. اشرح في جملة أو اتنين ليه المنتج ده يناسبه.
3. اذكر السعر الحقيقي كما هو مكتوب أعلاه بالظبط — ممنوع تغييره.
4. لو طلب العميل عام/غير محدد، اسأله سؤال ذكي عشان تضيّق الخيارات (مثلاً: بتحب الفريش ولا الخشبي؟).
5. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
6. ❌ ممنوع تخترع أي سعر أو معلومة.
"""

    # Case 2: No exact match, but we have alternatives (e.g. higher price)
    elif alternatives and alternatives.exists():
        context = _format_products(alternatives)
        user_content = f"""
═══ طلب العميل ═══
{message}

═══ ملحوظة مهمة ═══
لم يتم العثور على تطابق 100% مع طلب العميل، ولكن المنتجات التالية هي أفضل وأقرب البدائل المتاحة لطلبه:

{context}

═══ تعليمات الرد ═══
1. ❌ إياك أن تقول للعميل "لا يوجد" أو "مفيش عطر مطابق لطلبك" أو "للأسف". البائع الماهر يركز على بيع الموجود والمتاح.
2. ادخل في صلب الموضوع فوراً ورشح أفضل 1-2 منتج من القائمة وكأنها صنعت خصيصاً لطلبه.
3. اشرح بشكل جذاب ومقنع لماذا هذه العطور تناسب طلبه.
4. لو في القائمة شيء سعره اقتصادي ومناسب، اذكره كخيار ممتاز.
5. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
6. ❌ ممنوع تخترع أي سعر أو معلومة.
"""

    else:
        return "للأسف مش لاقي عطر يطابق طلبك بالظبط دلوقتي. ممكن تقولي أكتر عن ذوقك وأحاول أساعدك؟", ""

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