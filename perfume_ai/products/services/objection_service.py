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

from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_formatting import format_products
from .product_resolver import resolve_products
from .sales import stage as sales_stage
from .sales.objection import PLAYBOOK
from .sales.value import value_comparison_note

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


def _price_gap_context(message, history, store):
    """For "ليه أدفع 1200 بدل 500؟" — the real differences between the two, if we can
    resolve both perfumes. Nothing in the codebase compared two products numerically
    before; comparison rendered two independent text blocks and left the model to eyeball
    it, which is how invented differentiators got in.
    """
    products = resolve_products(message, history, store)
    if len(products) < 2:
        return ""

    priced = []
    for product in products[:2]:
        variants = [
            variant for variant in product.variants.all()
            if variant.bottle_type == "normal" and variant.volume
        ]
        if variants:
            priced.append((min(variant.price for variant in variants), product))

    if len(priced) < 2:
        return ""

    priced.sort(key=lambda pair: pair[0])
    return "\n" + value_comparison_note(priced[0][1], priced[1][1]) + "\n"


def handle_objection(message, objection, history=None, store=None, conversation=None):
    """Reply to a customer objection or complaint, addressing it before selling."""
    guidance = PLAYBOOK.get(objection.kind, "")
    stage = sales_stage.COMPLAINT if objection.is_complaint else sales_stage.OBJECTION

    # Only the perfumes actually under discussion, so the reply stays on the customer's
    # concern instead of pivoting to a fresh recommendation.
    products = resolve_products(message, history, store)
    context = format_products(products[:2]) if products else ""

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
{extra}
"""

    messages = [{"role": "system", "content": get_system_prompt(store)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    return chat(messages, profile="converse"), context
