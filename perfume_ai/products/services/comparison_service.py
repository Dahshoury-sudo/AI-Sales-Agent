import json
import re
from decimal import Decimal

from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_formatting import format_products
from .product_info import get_product_info
from .product_resolver import resolve_products


def requested_dimensions(message):
    from .static_faq_service import normalize_arabic
    text = normalize_arabic(message)
    if "نوع السائل" in text or "مصنع" in text:
        return set()
    return {key for key, words in {
        "price": ("سعر", "بكام", "price", "اسعار"),
        "longevity": ("ثبات", "يثبت", "longevity"),
        "projection": ("فوحان", "projection"),
        "value": ("اوفَر", "اوفر", "سعر المل", "per ml"),
        "sweetness": ("حلاوه", "اقل حلو", "اقل مسكر", "sweet"),
    }.items() if any(word in text for word in words)}


def requested_facts(message, products):
    from .sales.constraints import explicit_updates
    from .sales.value import price_per_ml
    dims = requested_dimensions(message)
    wanted = explicit_updates(message)
    lines, values = [], []
    for product in products:
        facts = []
        if dims & {"price", "value"}:
            variants = [v for v in product.variants.all()
                        if (not wanted.get("requested_volume") or v.volume == wanted["requested_volume"])
                        and (not wanted.get("bottle_type") or v.bottle_type == wanted["bottle_type"])]
            if not variants:
                facts.append("الحجم ونوع الزجاجة المطلوبين مش متاحين حالياً")
            for v in variants:
                bottle = "زجاجة البراند" if v.bottle_type == "normal" else "زجاجة أوريجينال"
                facts.append(f"{bottle} {v.volume} مل بـ{v.price:g} جنيه")
                if v.bottle_type == "original" and not (v.stock or 0):
                    facts.append("الحجم ده نفد حالياً")
                if "value" in dims and v.volume > 0:
                    unit = price_per_ml(v).quantize(Decimal(".01"))
                    facts.append(f"سعر المل: {unit} جنيه")
                    values.append((price_per_ml(v), product.name, v.volume, bottle))
        for key, label in (("longevity", "الثبات"), ("projection", "الفوحان")):
            if key in dims:
                facts.append(f"{label}: {getattr(product, key) or 'مش متأكد منه حالياً'}")
        lines.append(f"{product.name}: " + "؛ ".join(facts) if facts else product.name)
    if "value" in dims and len(values) >= 2:
        best = min(values)
        lines.append(f"الأوفر في سعر المل بين الأحجام دي: {best[1]} ({best[2]} مل، {best[3]}).")
    if "sweetness" in dims:
        lines.append("ماقدرش أجزم أنهي أقل حلاوة؛ وجود نوتة زي الفانيليا لوحده مش كفاية عشان نقارن درجة الحلاوة بدقة.")
    return "\n\n".join(lines), format_products(products, show_prices=bool(dims & {"price", "value"}), show_value_pick=False)


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
    from .product_info import _named_in_message, _referent_from_conversation
    matches = _named_in_message(message, store)
    if len(matches) < 2:
        resolved = resolve_products(message, history, store, conversation)
        matches += [p for p in resolved if p.pk not in {m.pk for m in matches}]
    if not matches:
        matches = _referent_from_conversation(message, store, conversation)

    if len(matches) < 2:
        return get_product_info(message, history, store, conversation, retry_hint=retry_hint)

    if requested_dimensions(message):
        return requested_facts(message, matches)

    context = format_products(matches, show_prices=False)

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
1. 🔴🔴 لخّص قرار الشراء في فقرة قصيرة طبيعية بدل جدول مواصفات. قول للعميل: "لو بتحب كذا اختار ده، ولو بتحب كذا اختار ده." مثال: "لو بتحب التوابل والريحة الدافية، Ambero أنسب ليك، أما لو بتحب الفانيليا والروم بشكل أوضح فـ Absolutely هيكون اختيار أحسن."
2. ❌ ممنوع تسرد المواصفات في شكل قائمة جامدة (الثبات: ... / الفوحان: ... / الموسم: ...). ادمج الفروقات المهمة بس في كلام طبيعي.
3. اذكر فقط الفروقات اللي بتفرق فعلاً في قرار الشراء (زي الريحة، الثبات، المناسبة). متسردش كل حاجة.
4. ❌ ممنوع تماماً تذكر أي أسعار أو أحجام أو معلومات عن التوفر في المقارنة.
5. رجّح واحد فقط لو بياناته بتدعم الترجيح حسب طلب العميل. لو البيانات مش كفاية قول إنك مش قادر تجزم؛ ممنوع تخمّن الحلاوة من النوتات أو تجبر المقارنة على فائز.
6. 🔴 العميل لسه بيوازن بين اختيارين ومختارش — ❌ ممنوع تقفل البيعة في الرد ده. ممنوع "تحب أساعدك في الطلب؟" ولا "تحب تطلب واحد فيهم؟". لو حابب تختم بسؤال، اسأله سؤال تضييق بيساعده يقرر (زي "بتستخدمه بالنهار ولا بالليل؟").
7. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
8. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
{retry_hint}"""
    })

    response = chat(messages, profile="converse")
    return response, context
