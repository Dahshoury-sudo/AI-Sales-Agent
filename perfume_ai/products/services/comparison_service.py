import json

from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_formatting import format_products
from .product_resolver import resolve_product


def compare_products(message, history=None, store=None):
    """Compare two named perfumes.

    Rendered with show_prices=False: this prompt forbids mentioning any price or size,
    while the product block it injects used to carry the 💡 Value Pick line telling the
    model to lead with exactly those numbers. Two opposite orders in one request, and an
    "أوفر" verdict about one perfume's size ladder could be read back as a verdict about
    the other.
    """
    prompt = """
Extract the names of the two perfumes the user wants to compare from their message or conversation history.
Fix any spelling mistakes in the perfume names. Translate Arabic names to English.
Return ONLY valid JSON in this format:
{
  "perfume_1": "Name 1",
  "perfume_2": "Name 2"
}
"""
    messages_for_extract = [{"role": "system", "content": prompt}]
    if history:
        messages_for_extract.extend(history)
    messages_for_extract.append({"role": "user", "content": message})

    try:
        response = chat(messages_for_extract, profile="extract", response_format={"type": "json_object"})
        
        data = json.loads(response)
        p1_name = data.get("perfume_1", "")
        p2_name = data.get("perfume_2", "")
    except Exception:
        p1_name = ""
        p2_name = ""

    
    prod1 = resolve_product(p1_name, history, store)
    prod2 = resolve_product(p2_name, history, store)

    matches = []
    if prod1: matches.append(prod1)
    if prod2 and prod2 not in matches: matches.append(prod2)

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
5. انصح العميل أي واحد يناسبه أكتر بناءً على ذوقه أو سؤاله.
6. لو حابب تختم بسؤال، اسأل سؤال تضييق بيعي (زي "تحب تطلب واحد فيهم؟") — بس مش لازم في كل رد.
7. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
8. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
"""
    })

    response = chat(messages, profile="converse")
    return response, context