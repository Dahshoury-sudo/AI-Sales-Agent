import json
import re
from decimal import Decimal

from .ai.client import chat
from .bottle_wording import bottle_label
from .ai.prompts import get_system_prompt
from .product_formatting import format_products, product_quotes, render_quotes, quote_context
from .product_info import get_product_info
from .product_resolver import resolve_products
from . import performance_policy
from .price_reply import supported_facts_question, compose_price_reply


def requested_dimensions(message, history=None):
    from .static_faq_service import normalize_arabic
    text = normalize_arabic(message)
    if "نوع السائل" in text or "مصنع" in text:
        return set()
    dimensions = {key for key, words in {
        "price": ("سعر", "بكام", "price", "اسعار"),
        "longevity": ("ثبات", "يثبت", "longevity"),
        "projection": ("فوحان", "projection"),
        "value": ("اوفَر", "اوفر", "سعر المل", "per ml"),
        "sweetness": ("حلاوه", "اقل حلو", "اقل مسكر", "sweet"),
    }.items() if any(word in text for word in words)}
    request = performance_policy.request_for(message, history)
    if request.longevity:
        dimensions.add("longevity")
    if request.projection:
        dimensions.add("projection")
    return dimensions


@performance_policy.governed_reply
def requested_facts(message, products, history=None, *, max_price=None, include_names=True):
    from .sales.value import price_per_ml
    dims = requested_dimensions(message, history)
    if dims and dims <= {"longevity", "projection"}:
        wording = performance_policy.description(
            longevity="longevity" in dims, projection="projection" in dims,
        )
        show_names = include_names or len(products) > 1
        reply = "\n\n".join(
            (f"{product.name}: " if show_names else "") + wording + "." for product in products
        )
        return reply, format_products(products, show_prices=False, show_value_pick=False)
    lines, values, quotes = [], [], []
    for product in products:
        facts = []
        if dims & {"price", "value"}:
            current_quotes = product_quotes([product], message, max_price, filter_budget=False)
            quotes.extend(current_quotes)
            by_id = {v.pk: v for v in product.variants.all()}
            variants = [by_id[q.variant_id] for q in current_quotes]
            if not variants:
                facts.append("الحجم ونوع الزجاجة المطلوبين مش متاحين حالياً")
            for v in variants:
                bottle = bottle_label(v.bottle_type)
                quote = next(q for q in current_quotes if q.variant_id == v.pk)
                facts.append(render_quotes([quote], max_price, include_name=False))
                if "value" in dims and v.volume > 0:
                    unit = price_per_ml(v).quantize(Decimal(".01"))
                    facts.append(f"سعر المل: {unit} جنيه")
                    values.append((price_per_ml(v), product.name, v.volume, bottle))
        for key, label in (("longevity", "الثبات"), ("projection", "الفوحان")):
            if key in dims:
                facts.append(f"{label}: {performance_policy.value(key)}")
        lines.append(f"{product.name}:\n" + "\n".join(facts) if facts else product.name)
    if "value" in dims and len(values) >= 2:
        best = min(values)
        position = next(index for index, product in enumerate(products, 1) if product.name == best[1])
        lines.append(f"الأوفر في سعر المل بين الأحجام دي: الخيار رقم {position} ({best[2]} مل، {best[3]}).")
    if "sweetness" in dims:
        lines.append("ماقدرش أجزم أنهي أقل حلاوة؛ وجود نوتة زي الفانيليا لوحده مش كفاية عشان نقارن درجة الحلاوة بدقة.")
    reply = "\n\n".join(lines)
    context = format_products(products, show_prices=bool(dims & {"price", "value"}), show_value_pick=False)
    return reply, quote_context(context, quotes, max_price=max_price, rendered=reply)


@performance_policy.governed_reply
def compare_products(message, history=None, store=None, conversation=None, retry_hint=""):
    """Compare two named perfumes.

    Rendered with show_prices=False: this prompt forbids mentioning any price or size,
    while the product block it injects used to carry the 💡 Value Pick line telling the
    model to lead with exactly those numbers. Two opposite orders in one request, and an
    "أوفر" verdict about one perfume's size ladder could be read back as a verdict about
    the other.

    Resolution goes through resolve_products rather than a second extractor of its own.
    The private extractor this replaces was never given the catalogue, so it
    transliterated Arabic names blind — "اوداورا" came back as something that matched no
    row, and before the resolver was hardened it matched *Dark Aura*, a different real
    perfume the customer had never mentioned. resolve_products injects the actual product
    list into its prompt, which is the whole reason it gets these right.

    `conversation` is passed through to the resolver, which needs it to anchor a pronoun on
    the perfumes we most recently offered ("قارنلي بينهم" names neither one). Omitting it
    was its own small bug: the one branch in this file that resolves names was the only
    caller of `resolve_products` not giving it that anchor.

    Fewer than two matches hands the whole turn to `get_product_info`. What used to be here
    was a hardcoded "واحد او اكثر من العطور دي مش متوفر عندنا" — a denial with three problems
    that delegation solves at once: nothing had verified it (`products.services.absence` now
    does, and only it may deny); it denied **both** names in order to deny one, so a customer
    comparing a perfume we stock against one we do not was told we carry neither; and it
    returned `context=""`, so `checks._unbacked_denial` — which is scoped to the injected
    context — structurally could not see it. `get_product_info` denies exactly the name that
    is missing, answers about the one that is not, and writes the markers the harness and the
    owner notification both read.

    `retry_hint` is instruction text from `router._rephrased`, appended to the instruction block and
    kept out of `message` — this function resolves perfume names out of the message, so a warning
    glued onto it would go to `resolve_products` as a name to place (the shape of conversation 816,
    recorded in `get_product_info`'s docstring). It is forwarded to `get_product_info` on the
    fewer-than-two-matches path so a delegated turn keeps the guard the direct one has.

    Until this parameter existed, this was the only model-generated branch in `router` with no
    repetition check of any kind — and instruction 5 below *requires* the closing frame
    "أنا أرشحلك X أكتر لأن…", the same frame conversation 973 repeated across four replies.
    """
    from .product_info import _named_in_message, _referent_from_conversation, offered_extensions
    matches = offered_extensions(_named_in_message(message, store), message, store, conversation)
    if not matches:
        # "خلاص اختارلي انت" / "أنهي فيهم؟" names nothing: the perfumes on the table are the subject.
        # Asking the resolver first returned one row and ended in "retype the name" (S06 replay 3).
        on_table = _referent_from_conversation(message, store, conversation)
        if len(on_table) >= 2:
            matches = on_table
        elif len(on_table) == 1:
            # One referent is still a perfume to compare: the other on the table is added below
            # instead of dropping it and ending in "retype the name" (simulation S06 round 2 full 2, t7).
            matches = list(on_table)
    if len(matches) < 2:
        resolved = resolve_products(message, history, store, conversation)
        matches += [p for p in resolved if p.pk not in {m.pk for m in matches}]
    if len(matches) == 1 and conversation is not None:
        # "إيه اللي يميز Alexandria II عن التاني؟" names one perfume and points at the only other one
        # on the table; with one row it fell through to a one-perfume description (S05 final 2, t4).
        from products.models import Product
        from .sales.described import offered_in_order
        others = [name for name in offered_in_order(conversation, store) if name != matches[0].name]
        other = Product.objects.filter(store=store, name=others[0]).first() if len(others) == 1 else None
        if other is not None:
            matches.append(other)
    if not matches:
        matches = _referent_from_conversation(message, store, conversation)

    if len(matches) < 2:
        return get_product_info(message, history, store, conversation, retry_hint=retry_hint)

    from .sales.value import stated_budget
    budget = stated_budget(conversation)
    if requested_dimensions(message, history) and supported_facts_question(message, matches):
        return requested_facts(message, matches, history, max_price=budget)

    from .scent_description import occasion_preference
    preferred = occasion_preference(message, product_names=[p.name for p in matches],
                                     saved=(conversation.preferences or {}).get("occasion") if conversation else None)
    context = format_products(matches, show_prices=False, preferred_occasion=preferred)
    from .product_formatting import price_question, QUOTE_PROSE_INSTRUCTION
    quoting = price_question(message, history, conversation)
    if quoting:
        context = quote_context(context, product_quotes(matches, message, budget, filter_budget=False),
                                append=True, max_price=budget, request=message)

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

═══ بيانات العطور اللي بيقارن بينها من قاعدة البيانات ═══
{context}

═══ تعليمات المقارنة ═══
1. جاوب بجملة أو جملتين مصري طبيعي، بفارق مفيد لكل عطر حسب سؤاله، وغطي كل العطور اللي بيسأل عنها (3 عطور زي اتنين)؛ ولو سأل أنهي أنسب لمناسبة (شغل، سهرة، خروج…) قول مناسبة كل عطر منهم، حتى اللي مش مناسب؛ من غير مقدمة أو إعادة تلخيص الطلب. الوصف العام لريحة كل عطر يتبع قاعدة التاجات فقط في الخط الأحمر 6؛ السؤال عن صفة معينة يسمح تستخدم كل تاجاته، والنوتات للمكونات المطلوبة صراحة فقط. لو طلب تفاصيل أكتر، جاوب على كلها بنفس القاعدة. استخدم أسماء العطور من البيانات أعلاه فقط. الاسم اللي العميل ذكره من غير بيانات عنه لا يجوز وصف ريحته أو ترجيحه.
2. ❌ ممنوع تسرد المواصفات في شكل قائمة جامدة (الثبات: ... / الفوحان: ... / الموسم: ...). ادمج الفروقات المهمة بس في كلام طبيعي.
3. اذكر فقط الفروقات اللي بتفرق فعلاً في قرار الشراء (زي الريحة والمناسبة). الثبات ممتاز والفوحان قوي لكل اختياراتنا، ومش أساس لترجيح عطر على التاني.
4. ❌ ممنوع تماماً تذكر أي أسعار أو أحجام أو معلومات عن التوفر في المقارنة.
5. رجّح واحد فقط لو بياناته بتدعم الترجيح حسب طلب العميل. لو البيانات مش كفاية قول إنك مش قادر تجزم؛ ممنوع تخمّن الحلاوة من النوتات أو تجبر المقارنة على فائز.
6. العميل لسه بيوازن بين اختيارين ومختارش — ممنوع تقفل البيعة في الرد ده. سؤال واحد بس لو فيه تفضيل ناقص فعلاً هيغير الإجابة؛ من غير سؤال ختامي لازم ولا إعادة سؤال جاوب عليه.
7. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه. سؤال جانبي عن سياسة الستور (استبدال، شحن، دفع) جاوبه من إجابات الستور المعتمدة في التعليمات العامة بدقة.
8. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
{retry_hint}"""
    })

    if quoting:
        messages[-1]["content"] += QUOTE_PROSE_INSTRUCTION
    response = chat(messages, profile="converse")
    # Recorded after the call, so the model never sees it: the reply may name these in Arabic,
    # and the next "أنهي فيهم؟" must still find them on the table (simulation S08 final t2).
    from .sales.described import COMPARED_MARKER
    context += "\n" + COMPARED_MARKER + " " + " | ".join(p.name for p in matches)
    return compose_price_reply(response, context) if quoting else (response, context)
