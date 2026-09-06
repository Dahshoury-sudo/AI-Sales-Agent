"""Handle an objection before selling into it.

The failing behaviour: "جبت من عندكم عطر قبل كده وكان مكتوب ثابت 8 ساعات، وبعد ساعتين مش
بحسه. خايف أطلب تاني" was answered with "الثبات بيختلف حسب نوع البشرة والجو، والـ90 ملي
معاك شهور" — a defence and an upsell, with no acknowledgement that the customer had been
let down.

The order matters and is therefore imposed in code rather than hoped for:

    acknowledge → address *this* concern → real factors → reduce risk → recommend → close

Only the last two are optional, and closing is gated on the sales stage, so an objection
turn cannot end in "تحب أساعدك في الطلب؟".

What "reduce risk" may draw on is deliberately narrow: the smaller size as a cheaper entry
point, and whatever the store itself configured in business_facts (a branch to come and
smell it, oil ratios) or payment_instructions (deposit and cancellation terms). There is no
returns-policy field, so no returns policy may be offered.

This branch never sets `needs_human`. That flag makes views.py return an empty reply, so a
bot that marked every complaint as needing a human would go silent on the customers who
most need answering. Genuine "let me talk to a person" requests still route to handoff.
"""

import re
from decimal import Decimal

from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_formatting import format_products, is_variant_available
from .product_resolver import resolve_products
from .sales import stage as sales_stage
from .sales.objection import PLAYBOOK
from .sales.value import stated_budget, value_comparison_note

# The sequence every objection reply follows. Stated once here rather than repeated per
# objection type, because the ordering is the fix — the per-type guidance in PLAYBOOK only
# decides what "address the concern" means.
_SEQUENCE = """
═══ ترتيب الرد على الاعتراض (التزم بالترتيب ده) ═══
1. 🔴 ابدأ بالاعتراف باللي قاله العميل في جملة قصيرة بأسلوبك. ❌ ممنوع تبدأ بشرح ولا بتبرير ولا بمنتج.
2. رد على الاعتراض نفسه بالتحديد — مش على اعتراض تاني ومش بكلام عام.
3. اذكر العوامل الحقيقية من البيانات المبعوتة لك بس.
4. لو ينفع تقلل المخاطرة بحاجة حقيقية (حجم أصغر كبداية، أو حقيقة مكتوبة في حقائق الستور زي وجود فرع يجرب فيه) — اعرضها. ❌ ممنوع تعرض استرجاع أو استبدال أو تعويض أو خصم.
5. رشّح بعد كده بس، ولو مناسب.
"""

_NO_CLOSING = (
    "6. 🔴 ❌ ممنوع تقفل البيعة في الرد ده. ممنوع تقول \"تحب أساعدك في الطلب؟\" ولا "
    "\"تحب تطلب؟\" — العميل لسه عنده اعتراض. سؤال واحد بالكتير، وميكونش سؤال شراء."
)

_COMPLAINT_NOTE = (
    "\n🔴 العميل ده بيتكلم عن حاجة اشتراها بالفعل واتضايق منها. ده شكوى مش اعتراض بيع:\n"
    "- لازم تعترف وتتعامل مع الشكوى الأول. ❌ ممنوع تحاول تبيعله حاجة قبل كده.\n"
    "- ❌ ممنوع تقول إن المشكلة منه أو من بشرته أو من طريقة استخدامه.\n"
    "- ❌ ممنوع توعد بتعويض أو استرجاع أو خصم — ده مش في إمكانياتك.\n"
)

_NO_GUARANTEE = (
    "\n❌ ممنوع في الرد ده: \"مضمون\"، \"100%\"، \"أضمنلك إنه هيعجبك\"، أي نسبة مئوية "
    "للتشابه أو للثبات مش مكتوبة في البيانات، وأي رقم ساعات مش مكتوب في بيانات العطر."
)


def _cheaper_alternatives(store, ceiling, exclude=None):
    """Real perfumes at or under a price the customer named."""
    from products.models import Product

    products = (
        Product.objects.filter(store=store, is_active=True)
        .filter(variants__bottle_type="normal", variants__price__lte=ceiling)
        .prefetch_related("variants")
        .distinct()
    )
    if exclude is not None:
        products = products.exclude(pk=exclude.pk)
    return [
        product
        for product in products
        if any(
            variant.bottle_type == "normal" and is_variant_available(variant)
            for variant in product.variants.all()
        )
    ][:3]


def _price_gap_context(message, history, store):
    """For "ليه أدفع 1200 بدل 500؟" — the real differences between the two.

    Nothing in the codebase compared two products numerically before; comparison
    rendered two independent text blocks and left the model to eyeball it, which is how
    invented differentiators got in.

    The single-perfume case matters just as much and used to fall through to "". A
    customer comparing one named perfume against a *price point* ("فهرنهايت بـ1200 وانا
    ممكن اجيب حاجة بـ500") got an answer about 50ml versus 90ml of the same perfume —
    which is not the question — because the playbook's size clause was the only guidance
    left standing. So when only one perfume resolves, the cheaper side is taken from the
    catalogue at the price the customer actually named.
    """
    products = resolve_products(message, history, store)

    priced = []
    for product in products[:2]:
        variants = [
            variant for variant in product.variants.all()
            if variant.bottle_type == "normal" and variant.volume
        ]
        if variants:
            priced.append((min(variant.price for variant in variants), product))

    if len(priced) < 2 and priced and store is not None:
        named_price, named = priced[0]
        # The lowest figure in the message is the budget they are comparing against.
        figures = [
            Decimal(match)
            for match in re.findall(r"\d{2,6}", message or "")
            if Decimal(match) >= 50
        ]
        ceiling = min(figures) if figures else None
        if ceiling is not None and ceiling < named_price:
            for alternative in _cheaper_alternatives(store, ceiling, exclude=named):
                cheapest = min(
                    (
                        variant.price for variant in alternative.variants.all()
                        if variant.bottle_type == "normal" and variant.volume
                    ),
                    default=None,
                )
                if cheapest is not None:
                    priced.append((cheapest, alternative))
                    break

    if len(priced) < 2:
        return ""

    priced.sort(key=lambda pair: pair[0])
    return "\n" + value_comparison_note(priced[0][1], priced[1][1]) + "\n"


def _budget_verdict_note(budget, context):
    """The budget markers, and what a challenged over-budget claim obliges.

    Returns "" with no budget, so the caller interpolates it unconditionally.

    The first half is the same fact every other price-rendering branch now states: the ✅/⚠️/❌
    marker is the verdict and no marker carries a difference figure — a ⚠️ size is announced with
    its printed price and the fixed sentence "أعلى حاجة بسيطة من ميزانيتك", never with a delta. It
    is emitted only alongside real product data, because a rule about markers the model cannot see
    is noise.

    The second half is this branch's own, and it is the turn conversation 931 actually failed on.
    "ازاي اعلي من ميزانيتي" is a price objection, so it arrives here — and `resolve_products` found
    no perfume name in it, so `context` was empty and the model's only source was its own previous
    reply read back through `build_llm_history`. It repeated the false claim, was challenged a
    second time, and changed the subject instead of withdrawing it. So the instruction is about the
    retraction rather than about the prices: a customer disputing an over-budget claim is usually
    right, being told so plainly is the whole reply, and none of it needs a figure the model does
    not have. `strip_false_over_budget` is what stops the claim reaching this turn at all; this is
    what to do on the turn where it already did.
    """
    if budget is None:
        return ""
    note = f"\n🔴 ميزانية العميل {int(budget)} جنيه."
    if context:
        note += (
            " وكل سعر في بيانات العطور فوق جانبه علامة محسوبة (✅ داخل الميزانية / ⚠️ أعلى حاجة "
            "بسيطة / ❌ أعلى بكتير). العلامة دي هي الحكم الوحيد على الميزانية: ❌ ممنوع تحسب الفرق "
            "بنفسك، وممنوع تقول رقم فرق خالص — مفيش رقم فرق في البيانات من الأصل. لو الحجم عليه "
            "⚠️، قول سعره المكتوب وقول \"أعلى حاجة بسيطة من ميزانيتك\" بالحرف وبس."
        )
    note += (
        "\n🔴 ولو العميل بيعترض على إنك قلتله إن سعر أعلى من ميزانيته: راجع الرقم الأول. لو السعر "
        "فعلاً داخل ميزانيته، قوله كده بصراحة في أول جملة — \"معاك حق، ده داخل ميزانيتك\" — "
        "والاعتراف بالغلط هنا هو الرد الصح والوحيد. ❌ ممنوع تكرر الكلام الغلط، ❌ ممنوع تغيّر "
        "الموضوع، و❌ ممنوع تدوّر على تبرير للرقم. ❌ وممنوع تخترع سعر جديد: لو مفيش أسعار في "
        "البيانات المبعوتة لك، اتكلم عن السعر اللي اتقاله قبل كده زي ما هو.\n"
    )
    return note


def handle_objection(message, objection, history=None, store=None, conversation=None, retry_hint=""):
    """Reply to a customer objection or complaint, addressing it before selling.

    `retry_hint` is instruction text from `router._rephrased` naming sentences this draft already
    said, appended to the instruction block and kept out of `message` — `resolve_products` below
    reads the message for perfume names, which is the conversation 816 shape recorded in
    `product_info.get_product_info`.

    This branch repeats itself structurally: `_SEQUENCE` prescribes the same three-move answer every
    time, and `PLAYBOOK` hands the same guidance for the same objection kind — so a customer who says
    "غالي" twice gets two replies built to the same plan, and the reply-level guard at 0.7 does not
    see it.
    """
    guidance = PLAYBOOK.get(objection.kind, "")
    stage = sales_stage.COMPLAINT if objection.is_complaint else sales_stage.OBJECTION

    # Only the perfumes actually under discussion, so the reply stays on the customer's
    # concern instead of pivoting to a fresh recommendation.
    products = resolve_products(message, history, store)
    budget = stated_budget(conversation)
    context = format_products(products[:2], max_price=budget) if products else ""

    extra = ""
    if objection.kind == "price_gap":
        extra = _price_gap_context(message, history, store)

    sequence = _SEQUENCE
    if not sales_stage.closing_allowed(stage):
        sequence += _NO_CLOSING

    user_content = f"""
═══ العميل قال ═══
{message}

═══ نوع الاعتراض ═══
{objection.kind}{_COMPLAINT_NOTE if objection.is_complaint else ""}

═══ إزاي تتعامل مع الاعتراض ده بالتحديد ═══
{guidance}
{sequence}
{_NO_GUARANTEE}
{("═══ بيانات العطور اللي بيتكلم عنها ═══" + chr(10) + context) if context else "⚠️ مفيش بيانات منتجات مبعوتة لك — ❌ ممنوع تذكر أي سعر أو اسم عطر من دمك."}
{extra}{_budget_verdict_note(budget, context)}{retry_hint}
"""

    messages = [{"role": "system", "content": get_system_prompt(store)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    return chat(messages, profile="converse"), context
