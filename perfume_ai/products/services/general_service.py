from .ai.client import chat
from .ai.prompts import get_system_prompt


# This branch is the one path that runs with no product data attached, and it still
# quoted invented prices for a perfume it had never been sent (conv_651.txt line 815:
# "Dior Homme Sport، متوفر 50 ملي بـ 450 جنيه" with an empty context block). The
# persona forbids inventing prices, but that rule was competing with ~60 others, so
# the prohibition is restated here where it is the only thing that matters.
#
# It bans inventing a price, not stating one: the promotion branch (router.py) routes
# through here and asks the model to relay the store's configured offers, which carry
# real prices in the Store Custom Instructions. A blanket ban would gag that reply.
NO_PRODUCT_DATA_GUARD = """

🔴🔴 مفيش أي بيانات منتجات مبعوتة لك في الرسالة دي:
- ❌ ممنوع تذكر سعر أو اسم عطر من ذاكرتك أو من دمك. ولا رقم واحد.
- ✅ مسموح بس تنقل الأسعار أو العروض المكتوبة حرفياً فوق في تعليمات الستور أو حقائق الستور — بالنص وبدون تغيير.
- أي سعر أو اسم عطر مش مكتوب فوق = ممنوع تقوله. لو العميل سأل عن سعر عطر معين، قوله "لحظة أتأكدلك يا فندم".
"""


def _anti_repetition_context(history):
    """Show the bot its own recent replies so it stops re-sending them.

    Listing them was not enough on its own: on five consecutive "هاي" the bot
    produced four different wordings of the same move — greeting plus "تحت أمرك"
    plus an offer to help (conv_651.txt 640-676). Varying the words is not varying
    the reply, so the instruction now asks for a different *move* and names the
    options.
    """
    if not history:
        return ""

    recent_bot_msgs = []
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            recent_bot_msgs.append(msg["content"])
        if len(recent_bot_msgs) >= 4:
            break

    if not recent_bot_msgs:
        return ""

    context = "\n\n🔴 دي ردودك السابقة — ممنوع تكررها ولا تكرر فكرتها:\n"
    for i, prev in enumerate(recent_bot_msgs, 1):
        truncated = prev[:150] + "..." if len(prev) > 150 else prev
        context += f'{i}. "{truncated}"\n'

    context += (
        "🔴 مش كفاية تغير الكلمات — لازم تغير *نوع* الرد نفسه. لو ردودك السابقة كانت "
        "كلها ترحيب و\"تحت أمرك\" و\"أقدر أساعدك إزاي\"، يبقى ممنوع تعمل كده تاني. "
        "اختار حركة مختلفة خالص:\n"
        "- اسأل سؤال تضييق بيعي مباشر (بيدور لنفسه ولا هدية؟ فريش ولا دافي؟)\n"
        "- اعرض عليه يقولك عطر بيحبه وتبني عليه\n"
        "- ادعيه يجرب في الستور\n"
        "- لو العميل بيبعت رسايل فاضية أو ترحيب متكرر، اسأله سؤال محدد يخليه يتكلم "
        "عن اللي هو عايزه بالظبط بدل الترحيب.\n"
    )
    return context


def handle_general(message, history=None, store=None):
    """
    Handle general messages (greetings, FAQ, redirected handoffs, out-of-domain, etc.)
    Runs with no product data, so it carries an explicit no-prices guard.
    """
    system_prompt = (
        get_system_prompt(store)
        + NO_PRODUCT_DATA_GUARD
        + _anti_repetition_context(history)
    )

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]
    
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": message
    })

    return chat(messages, profile="converse"), ""
