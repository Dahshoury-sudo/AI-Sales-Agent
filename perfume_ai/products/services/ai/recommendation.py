from .client import chat
from .prompts import get_system_prompt


def _format_products(products):
    context = ""
    for product in products:
        variants = list(product.variants.all())
        variants_str_list = []
        all_out_of_stock = True
        for v in variants:
            if v.bottle_type == 'normal':
                req_oil = (v.volume * product.concentration_percentage) / 100
                is_available = product.oil_stock_grams >= req_oil
                status = 'متوفر' if is_available else '❌ نفد من المخزون'
                variants_str_list.append(f"- زجاجة تعبئة {v.volume}ml: {v.price} EGP ({status})")
                if is_available:
                    all_out_of_stock = False
            elif v.bottle_type == 'original':
                is_available = (v.stock or 0) > 0
                status = f"متوفر ({v.stock} زجاجة)" if is_available else '❌ نفد من المخزون'
                variants_str_list.append(f"- زجاجة أوريجينال {v.volume}ml: {v.price} EGP ({status})")
                if is_available:
                    all_out_of_stock = False
        variants_str = "\n".join(variants_str_list) if variants else "غير متوفر أسعار/أحجام حالياً"
        stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"
        is_custom_blend = product.brand.name.lower() == "self"
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
3. ⭐ لكل منتج تذكره، لازم تذكر **كل الأحجام والأسعار المتاحة** — ممنوع تختار حجم واحد وتسيب الباقي. اكتبهم بالشكل ده:
   "متوفر بأحجام: الـ 40ml بـ 400 جنيه، والـ 70ml بـ 600 جنيه، والـ 130ml بـ 900 جنيه"
   (يعني في سطر واحد مفصولين بفاصلة، مش قائمة تحت بعض).
4. لو طلب العميل عام/غير محدد، اسأله سؤال ذكي عشان تضيّق الخيارات (مثلاً: بتحب الفريش ولا الخشبي؟).
5. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
6. ❌ ممنوع تخترع أي سعر أو معلومة.
7. 🔴 لو المنتج اللي يناسب العميل نفد من المخزون (Stock Status = ❌)، قوله إن المنتج ده نفد حالياً وارشحله بديل متوفر من نفس القائمة يكون قريب منه في الخصائص.
8. دائماً أعطِ الأولوية للمنتجات المتوفرة في المخزون.
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
4. ⭐ لكل منتج تذكره، لازم تذكر **كل الأحجام والأسعار المتاحة** في سطر واحد مفصولين بفاصلة (مثال: "الـ 40ml بـ 400 جنيه، والـ 70ml بـ 600 جنيه") — ممنوع تختار حجم واحد وتسيب الباقي.
5. لو في القائمة شيء سعره اقتصادي ومناسب، اذكره كخيار ممتاز.
6. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
7. ❌ ممنوع تخترع أي سعر أو معلومة.
8. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌)، تجاهله تماماً ورشح المنتجات المتوفرة فقط.
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