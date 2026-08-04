import re

from .client import chat
from .prompts import get_system_prompt


def _format_products(products):
    context = ""
    for product in products:
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

        context += f"""
Name: {product.name}
Brand: {brand_display}
Stock Status: {stock_status}
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

-------------------------
"""
    return context


def _get_previously_recommended(history):
    """Extract product names already recommended in the conversation (from **bold** mentions)."""
    if not history:
        return []

    recommended = []
    for msg in history:
        if msg.get("role") == "assistant":
            names = re.findall(r'\*\*(.+?)\*\*', msg.get("content", ""))
            recommended.extend(names)

    return list(set(recommended))


def recommend(message, products, history=None, alternatives=None, store=None):
    # Get previously recommended products to avoid repetition
    previously_recommended = _get_previously_recommended(history)
    exclusion_note = ""
    if previously_recommended:
        exclusion_note = f"\n⚠️ المنتجات التالية سبق ترشيحها للعميل في المحادثة — ممنوع تذكرها تاني، اختار منتجات مختلفة تماماً من القائمة: {', '.join(previously_recommended)}\n"

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
2. اذكر اسم العطر واشرح في جملة أو اتنين ليه يناسبه (ريحته إيه، مناسب لإيه، ثباته إيه، فوحانه اي).
3. 🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله.
4. 🔴 لو طلب العميل عام/غير محدد (مثلاً "عايز عطر حلو" أو "عطر مناسب لفرح") ومش واضح عايز رجالي ولا حريمي — ممنوع ترشح أي عطر! اسأله الأول: "بتدور على عطر رجالي ولا حريمي؟" وبعدها رشح. لو واضح من السياق (مثلاً قال "لخطيبتي") يبقى رشح حريمي على طول.
5. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
6. ❌ ممنوع تخترع أي سعر أو معلومة.
7. 🔴 لو المنتج اللي يناسب العميل نفد من المخزون (Stock Status = ❌)، قوله إن المنتج ده نفد حالياً وارشحله بديل متوفر من نفس القائمة يكون قريب منه في الخصائص.
8. دائماً أعطِ الأولوية للمنتجات المتوفرة في المخزون.
9. 🔴 ممنوع أسئلة فاضية (زي "عايز حاجة تانية؟"). مسموح بـ CTA بيعي بس مش كل مرة (زي "تحب تعرف أسعارهم والأحجام؟" أو "تنورنا في الستور تجربهم؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
{exclusion_note}"""

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
3. اشرح بشكل جذاب ومقنع لماذا هذه العطور تناسب طلبه (ريحتها، ثباتها، مناسبة لإيه).
4. 🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله.
5. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
6. ❌ ممنوع تخترع أي سعر أو معلومة.
7. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌)، تجاهله تماماً ورشح المنتجات المتوفرة فقط.
8. 🔴 ممنوع أسئلة فاضية. مسموح بـ CTA بيعي بس مش كل مرة (زي "تحب تعرف أسعارهم والأحجام؟" أو "تنورنا تجربهم؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
{exclusion_note}"""

    else:
        context = ""
        user_content = f"""
═══ طلب العميل ═══
{message}

═══ ملحوظة مهمة ═══
لم يتم العثور على أي منتجات تطابق طلب العميل في المتجر حالياً.

═══ تعليمات الرد ═══
1. ⭐ إذا كانت رسالة العميل غامضة أو غير مفهومة، لا تعتذر عن عدم توفر العطر، بل قل له بوضوح: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر عشان أقدر أساعدك؟".
2. أما إذا كان يطلب عطراً أو طلباً واضحاً ولكنه غير متوفر، فاعتذر بلطف للعميل وأخبره أن العطر غير متوفر حالياً، واسأله لو عايز ترشحله بديل.
3. ❌ ممنوع ترشيح أو ذكر أي منتج غير موجود أو اختراع أسماء منتجات.
4. رد بشكل قصير ومباشر.
5. 🔴🔴 متسألش أسئلة كتير. سؤال واحد بس لو محتاج توضيح، ومتسألش سؤال متابعة لو الموقف مش محتاج.
{exclusion_note}"""

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