from decimal import Decimal, InvalidOperation

from .client import chat
from .prompts import get_system_prompt
from ..product_formatting import format_products
from ..sales.constraints import acknowledgement_hint
from ..sales.ranking import reasons_note
from ..search_service import MAX_PRODUCTS_IN_CONTEXT


def _coerce_budget(value):
    """Turn an LLM-extracted budget into a Decimal, or None if unusable.

    The intent schema asks for a float, but a model can return "500" or "500.0",
    and the old int() call raised ValueError on the latter. Decimal also matches
    ProductVariant.price, so comparisons stay exact — Decimal and float can be
    compared but not multiplied together.
    """
    if value is None:
        return None
    try:
        budget = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return budget if budget > 0 else None


def _format_products(products, max_price=None, ranked=None):
    """Thin wrapper over the shared renderer, capped for prompt size.

    search_products already applies the LIMIT; the cap here only bounds the
    prompt, so a caller passing an unsliced queryset can't blow up the request.

    When ranking ran, each product's own evidence line is appended to its block. The
    model gets the *reasons* — shared notes, which constraint matched — and never the
    score, because a number in the prompt is how "شبهه بنسبة 95%" gets invented.
    """
    context = format_products(products, max_price=max_price, limit=MAX_PRODUCTS_IN_CONTEXT)
    if not ranked:
        return context

    blocks = []
    for product in list(products)[:MAX_PRODUCTS_IN_CONTEXT]:
        block = format_products([product], max_price=max_price)
        note = reasons_note(ranked.get(product.id))
        blocks.append(f"{block}{note}\n" if note else block)
    return "".join(blocks)


def _similarity_instruction(search):
    """Tell the model how close we actually got, when similarity was asked for.

    This is the honesty path for "عايز حاجة شبه X". Presenting the nearest perfume as a
    match is exactly what produced Fahrenheit as an answer for Sauvage, so when nothing
    reaches the close band the reply has to say so.
    """
    summary = (search or {}).get("similarity")
    if not summary:
        return ""

    name = summary["reference_name"]
    if summary["has_close_match"]:
        return (
            f"\n🔎 العميل طلب حاجة شبه {name}. سطر Match في بيانات كل عطر بيقولك إيه "
            f"المشترك بينهم بالظبط — اعتمد عليه واذكر النوتات المشتركة دي بالاسم عشان "
            f"كلامك يكون مبني على حقيقة.\n"
            f"❌ ممنوع تذكر نسبة مئوية للتشابه ولا تقول \"مطابق\" أو \"نفس العطر\".\n"
        )

    return (
        f"\n🔎 العميل طلب حاجة شبه {name}، ومفيش عندنا عطر قريب منه فعلاً.\n"
        f"- 🔴 قوله كده بصراحة: مفيش حاجة قريبة من {name} بالظبط عندنا.\n"
        f"- ⚠️ ده استثناء من قاعدة \"ممنوع تقول مفيش\": القاعدة دي عن التوافر، وهنا "
        f"الكلام عن التشابه. مسموح — بل لازم — تقول إن مفيش حاجة شبهه، وبعدها تكمّل "
        f"وتعرض المتاح عادي.\n"
        f"- اعرض أقرب المتاح كـ\"مختلف بس ممكن يعجبك\"، ووضّح الفرق في جملة.\n"
        f"- ❌ ممنوع تقول عن أي عطر إنه شبهه أو بديله أو نفس ريحته.\n"
    )


def recommend(message, products, history=None, alternatives=None, store=None, intent=None, search=None):
    # Not repeating a recommendation is handled upstream, not here: ai/intent.py fills
    # intent["exclude_names"] when the customer asks for something else, and
    # search_service drops those from the queryset before this function ever sees it.
    # A prompt-level exclusion list used to live here as well, keyed on every catalogue
    # name mentioned anywhere in the conversation — which excluded the perfume the
    # customer had just asked about, contradicting the persona's own rule to stay on it.
    # Deleted rather than narrowed: a hard queryset filter beats asking the model twice.
    max_price = _coerce_budget(intent.get("max_price") if intent else None)
    budget_note = ""
    if max_price:
        budget_note = f"\n⚠️ ميزانية العميل: {int(max_price)} جنيه. لازم تذكر الأسعار والأحجام اللي داخل الميزانية. متسألوش عن الميزانية تاني. لو فيه حجم أكبر أغلى شوية بس قريب من الميزانية، ممكن تذكره كمان مع التوضيح.\n"
        budget_note += "🔴 ملاحظة هامة جداً بخصوص الميزانية والأحجام: إذا طلب العميل حجماً معيناً (مثل 90 ملي) وكان سعره يتخطى ميزانيته، أخبره بوضوح ولطف أن الحجم المطلوب غير متاح بهذه الميزانية، واعرض عليه الحجم الأصغر (مثل 50 ملي) الذي يناسب ميزانيته كبديل، ووضح له أن الحجم الأكبر متاح أيضاً إذا أمكنه زيادة الميزانية قليلاً، دون أي ضغط أو إلحاح.\n"
        budget_note += "🔴 كل حجم في البيانات اللي تحت مكتوب جانبه إذا كان داخل الميزانية (✅) أو أعلى منها شوية (⚠️) أو أعلى منها بكتير (❌). التزم بده حرفياً: ممنوع تعرض أي حجم عليه ❌.\n"
    price_instruction = "🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله." if not max_price else "🔴🔴 العميل حدد ميزانيته، فلازم تذكر الأحجام والأسعار اللي داخل ميزانيته مع الترشيح. اذكر السعر بشكل طبيعي جوه الكلام (مثال: \"الـ50ml بـ 400 جنيه، يعني داخل ميزانيتك\"). متسألوش عن الميزانية تاني."

    # What the customer already told us, so the reply can nod to it once instead of
    # answering five stated constraints as though none had registered.
    constraint_note = acknowledgement_hint(intent or {})

    # Case 1: Exact matches found
    if products.exists():
        context = _format_products(products, max_price=max_price, ranked=(search or {}).get("ranked"))
        user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}{constraint_note}{_similarity_instruction(search)}
═══ المنتجات المتاحة (هذه هي المنتجات الوحيدة الموجودة — لا تذكر أي منتج خارج هذه القائمة) ═══
{context}

═══ تعليمات الرد ═══
1. اختر أفضل 1-2 منتج بيطابق شروط العميل كلها مع بعض. لو مفيش منتج بيطابق كل الشروط، ماترشحوش.
2. 🔴 لو رشحت عطرين، لازم تقارن بينهم في جملة واحدة سريعة تساعده يختار، أو توضح إيه الأنسب. مثال: "Ambero أنسب لو بتحب التوابل والريحة الدافية، أما Afnan 9PM فهو أحلى ومسَكّر أكتر وفيه طابع فاكهي." ولو فيه واحد هو الأنسب بوضوح، قوله "أنا أرشحلك كذا أكتر لطلبك 👌".
3. {price_instruction}
4. 🔴 لو الطلب عام ومش واضح رجالي ولا حريمي — ممنوع ترشح. اسأله الأول. لو واضح من السياق (قال "لخطيبتي") رشّح على طول.
5. 🔴 تجاهل تماماً أي منتج Stock Status = ❌ واختار غيره من المتوفر.
6. 🔴 لو العميل محدد ميزانية ولقيت عطر ممتاز أرخص بكتير منها، رشحه كـ"قيمة مقابل سعر" — متفضلش الأغلى لمجرد إنه بيقفل الميزانية.
7. سطر "✅ ليه مناسب" في بيانات كل عطر هو الدليل اللي بنيت عليه الترشيح — اعتمد عليه في سبب الترشيح بدل كلام عام. وسطر "⚠️ مش مطابق في" لازم تحترمه: ❌ ممنوع تقول إن العطر بيطابق حاجة مكتوب جانبها إنه مش مطابق فيها.
"""

    # Case 2: No exact match, but we have alternatives (e.g. higher price)
    elif alternatives and alternatives.exists():
        context = _format_products(alternatives, max_price=max_price, ranked=(search or {}).get("ranked"))
        price_instruction_alt = "🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله." if not max_price else "🔴🔴 العميل حدد ميزانيته، فلازم تذكر الأحجام والأسعار اللي داخل أو قريبة من ميزانيته مع الترشيح. لو السعر أعلى من الميزانية، وضّح ذلك بصراحة. متسألوش عن الميزانية تاني."
        user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}{constraint_note}{_similarity_instruction(search)}
═══ ملحوظة مهمة ═══
لم يتم العثور على تطابق 100% مع طلب العميل، ولكن المنتجات التالية هي أفضل وأقرب البدائل المتاحة لطلبه:

{context}

═══ تعليمات الرد ═══
1. ❌ إياك أن تقول "لا يوجد" أو "مفيش عطر مطابق لطلبك" أو "للأسف". البائع الماهر يركز على بيع المتاح.
2. ادخل في الموضوع فوراً ورشّح أفضل 1-2 من القائمة وكأنها صنعت خصيصاً لطلبه.
3. 🔴 لو رشحت عطرين، قارن بينهم في جملة واحدة سريعة تساعده يختار. مثال: "Ambero أنسب لو بتحب التوابل والريحة الدافية، أما Afnan 9PM فهو أحلى ومسَكّر أكتر."
4. {price_instruction_alt}
5. 🔴 تجاهل أي منتج Stock Status = ❌ ورشّح المتوفر بس.
"""

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
        "content": user_content,
    })

    response = chat(messages, profile="converse")
    return response, context