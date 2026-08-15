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

        has_original_bottle = any(v.bottle_type == 'original' for v in variants)
        if has_original_bottle:
            original_bottle_status = "Available (see sizes below)"
        elif is_custom_blend:
            original_bottle_status = 'NOT AVAILABLE — this is a store-exclusive perfume (NOT a global brand). If asked, say EXACTLY: "ده عطر من تصميمنا وابتكارنا إحنا يا فندم، فمفيش منه زجاجة أوريجينال."'
        else:
            original_bottle_status = f'NOT AVAILABLE — this is a GLOBAL BRAND ({product.brand.name}) perfume, NOT store-exclusive. If asked, say EXACTLY: "للاسف مش متوفر منه زجاجة أوريجينال حالياً". ❌ DO NOT say it is store-exclusive or حصري.'

        context += f"""
Name (الاسم الصحيح): {product.name}
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


def recommend(message, products, history=None, alternatives=None, store=None, intent=None):
    # Get previously recommended products to avoid repetition
    previously_recommended = _get_previously_recommended(history)
    exclusion_note = ""
    if previously_recommended:
        exclusion_note = f"\n⚠️ المنتجات التالية سبق ترشيحها للعميل في المحادثة — ممنوع تذكرها تاني، اختار منتجات مختلفة تماماً من القائمة: {', '.join(previously_recommended)}\n"

    # Detect if budget was provided
    max_price = intent.get("max_price") if intent else None
    budget_note = ""
    if max_price:
        budget_note = f"\n⚠️ ميزانية العميل: {int(max_price)} جنيه. لازم تذكر الأسعار والأحجام اللي داخل الميزانية. متسألوش عن الميزانية تاني. لو فيه حجم أكبر أغلى شوية بس قريب من الميزانية، ممكن تذكره كمان مع التوضيح.\n"
    price_instruction = "🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله." if not max_price else "🔴🔴 العميل حدد ميزانيته، فلازم تذكر الأحجام والأسعار اللي داخل ميزانيته مع الترشيح. اذكر السعر بشكل طبيعي جوه الكلام (مثال: \"الـ90ml بـ1180 جنيه، يعني داخل ميزانيتك\"). متسألوش عن الميزانية تاني."

    # Case 1: Exact matches found
    if products.exists():
        context = _format_products(products)
        user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}
═══ المنتجات المتاحة (هذه هي المنتجات الوحيدة الموجودة — لا تذكر أي منتج خارج هذه القائمة) ═══
{context}

═══ تعليمات الرد ═══
1. اختر أفضل 1-2 منتج من القائمة بحيث يطابق كل شروط العميل مع بعض (السعر، الذوق، المناسبة). لو مفيش منتج بيطابق كل الشروط مع بعض، ماترشحوش.
2. 🔴 لو العميل محدد ميزانية ولقيت عطر ممتاز بسعر أرخص بكتير من الميزانية، رشحه كاختيار "قيمة مقابل سعر"، ومتفضلش الأغلى لمجرد إنه بيقفل الميزانية.
3. 🔴🔴 اشرح ليه العطر يناسبه في كلام طبيعي (جملة أو اتنين). ادمج الريحة والثبات والمناسبة في كلام سلس زي بياع بيتكلم مع عميله.
4. 🔴 لو رشحت عطرين، متسردش مواصفات كل واحد لوحده وتمشي. لازم تقارن بينهم في جملة واحدة سريعة تساعد العميل يختار، أو توضح إيه الأنسب. مثال: "Ambero أنسب لو بتحب التوابل والريحة الدافية، أما Afnan 9PM فهو أحلى ومسَكّر أكتر وفيه طابع فاكهي." لو فيه عطر هو الأنسب بوضوح، قوله "أنا أرشحلك كذا أكتر لطلبك 👌".
5. ❌ ممنوع تسرد المواصفات في شكل قائمة (الثبات: ... / الفوحان: ... / الموسم: ...). ادمج المعلومات المهمة بس في كلام طبيعي.
6. {price_instruction}
7. 🔴 لو طلب العميل عام/غير محدد (مثلاً "عايز عطر حلو" أو "عطر مناسب لفرح") ومش واضح عايز رجالي ولا حريمي — ممنوع ترشح أي عطر! اسأله الأول: "بتدور على عطر رجالي ولا حريمي؟" وبعدها رشح. لو واضح من السياق (مثلاً قال "لخطيبتي") يبقى رشح حريمي على طول.
8. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
9. ❌ ممنوع تخترع أي سعر أو معلومة.
10. 🔴 إذا كان أحد المنتجات التي تنوي ترشيحها غير متوفر (Stock Status = ❌)، تجاهله تماماً ولا تذكره أبداً للعميل، وقم باختيار منتج آخر متوفر (✅ متوفر) من القائمة بدلاً منه.
11. دائماً أعطِ الأولوية للمنتجات المتوفرة في المخزون لترشيحها.
12. 🔴 الـ CTA: لازم تختم الترشيح بسؤال يوجه العميل للخطوة اللي بعدها. مثال: "أنا أرشحلك Asad 👌 تحب الـ50 ملي؟" أو "تحبها فواحة ولا هادية؟". ❌ ممنوع أسئلة فاضية (زي "عايز حاجة تانية؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها. ❌ ممنوع تسأل عن الميزانية لو العميل حددها بالفعل.
13. 🔴 استخدم دائماً الاسم الصحيح للعطر الموجود في البيانات حتى لو أخطأ العميل في كتابته.
{exclusion_note}"""

    # Case 2: No exact match, but we have alternatives (e.g. higher price)
    elif alternatives and alternatives.exists():
        context = _format_products(alternatives)
        price_instruction_alt = "🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله." if not max_price else "🔴🔴 العميل حدد ميزانيته، فلازم تذكر الأحجام والأسعار اللي داخل أو قريبة من ميزانيته مع الترشيح. لو السعر أعلى من الميزانية، وضّح ذلك بصراحة. متسألوش عن الميزانية تاني."
        user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}
═══ ملحوظة مهمة ═══
لم يتم العثور على تطابق 100% مع طلب العميل، ولكن المنتجات التالية هي أفضل وأقرب البدائل المتاحة لطلبه:

{context}

═══ تعليمات الرد ═══
1. 🔴 استخدم دائماً الاسم الصحيح للعطر الموجود في البيانات حتى لو أخطأ العميل في كتابته.
2. ❌ إياك أن تقول للعميل "لا يوجد" أو "مفيش عطر مطابق لطلبك" أو "للأسف". البائع الماهر يركز على بيع الموجود والمتاح.
3. ادخل في صلب الموضوع فوراً ورشح أفضل 1-2 منتج من القائمة وكأنها صنعت خصيصاً لطلبه.
4. 🔴🔴 اشرح بشكل طبيعي وجذاب لماذا العطر يناسب طلبه. ادمج الريحة والثبات والمناسبة في كلام سلس (جملة أو اتنين). ❌ ممنوع سرد المواصفات في شكل قائمة جامدة.
5. 🔴 لو رشحت عطرين، لازم تقارن بينهم في جملة واحدة سريعة تساعد العميل يختار، أو توضح إيه الأنسب. مثال: "Ambero أنسب لو بتحب التوابل والريحة الدافية، أما Afnan 9PM فهو أحلى ومسَكّر أكتر وفيه طابع فاكهي." لو فيه عطر هو الأنسب بوضوح، قوله "أنا أرشحلك كذا أكتر لطلبك 👌".
6. {price_instruction_alt}
7. ❌ ممنوع تذكر أي منتج مش موجود في القائمة أعلاه.
8. ❌ ممنوع تخترع أي سعر أو معلومة.
9. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌)، تجاهله تماماً ورشح المنتجات المتوفرة فقط.
10. 🔴 الـ CTA: لازم تختم الترشيح بسؤال يوجه العميل للخطوة اللي بعدها. مثال: "أنا أرشحلك Asad 👌 تحب الـ50 ملي؟" أو "تحبها فواحة ولا هادية؟". ❌ ممنوع أسئلة فاضية. ❌ ممنوع توعد بحاجة مش تقدر تعملها. ❌ ممنوع تسأل عن الميزانية لو العميل حددها بالفعل.
{exclusion_note}"""

    else:
        context = ""
        user_content = f"""
═══ طلب العميل ═══
{message}

═══ ملحوظة مهمة ═══
لم يتم العثور على أي منتجات تطابق طلب العميل في المتجر حالياً، أو تم ترشيح كل الخيارات المتاحة بالفعل.

═══ تعليمات الرد ═══
1. ⭐ إذا كان العميل يطلب المزيد من الخيارات (مثل "إيه تاني؟"، "غيره"، "في حاجة تانية")، اعتذر بلطف وقوله إن دي كل الخيارات المتاحة حالياً اللي بتطابق طلبه بالظبط، واعرض عليه يغير المواصفات بشكل عام عشان يظهرله عطور تانية. ❌ ممنوع تقترح عليه روائح محددة (زي "تحب حاجة خشبية؟" أو "فريش") لأنك لا تعرف ما هو متوفر في المخزون حالياً.
2. ⭐ إذا كانت رسالة العميل غامضة أو غير مفهومة، لا تعتذر عن عدم توفر العطر، بل قل له بوضوح: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر عشان أقدر أساعدك؟".
3. أما إذا كان يطلب عطراً أو طلباً واضحاً ولكنه غير متوفر، فاعتذر بلطف للعميل وأخبره أن العطر غير متوفر حالياً، واسأله لو عايز ترشحله بديل.
4. ❌ ممنوع ترشيح أو ذكر أي منتج غير موجود أو اختراع أسماء منتجات.
5. رد بشكل قصير ومباشر (1-4 جمل).
6. 🔴🔴 متسألش أسئلة كتير. سؤال واحد بس لو محتاج توضيح، ومتسألش سؤال متابعة لو الموقف مش محتاج.
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