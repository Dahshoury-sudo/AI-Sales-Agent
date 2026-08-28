import json

from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_formatting import format_products
from .product_resolver import resolve_products


def compare_products(message, history=None, store=None):
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
    """
    matches = resolve_products(message, history, store)[:2]

    if len(matches) < 2:
        return "واحد او اكثر من العطور دي مش متوفر عندنا للاسف ممكن تقولي اسماء عطور تانية؟", ""

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
5. 🔴🔴 لازم تختار واحد وتقول إنه الأنسب، وتقول ليه في نص جملة مربوطة بذوق العميل أو بسؤاله. ❌ ممنوع تسيبه بين اختيارين من غير ترجيح — العميل سأل عشان يقرر، ولو رجّعتله الكرة تاني يبقى مساعدتوش. مثال: "الاتنين حلوين، بس أنا أرشحلك Ambero أكتر لأنك قلت بتحب الدافي والتوابل."
6. 🔴 العميل لسه بيوازن بين اختيارين ومختارش — ❌ ممنوع تقفل البيعة في الرد ده. ممنوع "تحب أساعدك في الطلب؟" ولا "تحب تطلب واحد فيهم؟". لو حابب تختم بسؤال، اسأله سؤال تضييق بيساعده يقرر (زي "بتستخدمه بالنهار ولا بالليل؟").
7. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
8. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
"""
    })

    response = chat(messages, profile="converse")
    return response, context