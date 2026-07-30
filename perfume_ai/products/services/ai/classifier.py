from .client import chat


def classify(message: str, history=None):
    import json
    system_prompt = """
You are a request classifier.

Read the conversation history (if any), but ONLY classify the user's LATEST message.

Return ONLY a JSON object with a single key "intent" whose value is EXACTLY one of these words that best describes the user's latest request:

recommendation: asking for suggestions or a perfume that matches some criteria.
product_info: asking for details, PRICE, ingredients, or info of a SPECIFIC perfume (e.g. "سعر سوفاج", "عامل كام", "بكام ده").
comparison: comparing between two or more perfumes.
order: wanting to buy, ordering a product, or providing their address/phone number/name to checkout. (e.g. "هاخد ده", "هاتلي واحد", "عايز اشتريه", "010...", "شارع كذا").
order_cancel: wanting to cancel the order they are currently making (e.g. "الغي الاوردر", "مش عايز").
greeting: saying hello, hi, etc.
faq: general questions not related to a specific product's info (e.g., shipping, delivery, who are you, how to make perfume, bulk buying).
promotion: asking about deals, discounts, offers, bundles, or special prices (e.g. "عندكم عروض", "في خصومات", "فيه أوفر", "أي عروض عندكم", "الأوفر إيه", "عرض خاص", "كومبو").
handoff: wanting to speak to a human, complaining, or showing frustration (e.g. "عايز اكلم حد حقيقي", "فين خدمة العملاء").
out_of_domain: questions entirely unrelated to perfumes, orders, or the store (e.g., programming, politics, medical advice, "مين رئيس امريكا", "اكتبلي كود").

CRITICAL RULE (HIGHEST PRIORITY): If the assistant's LAST message says "تم تأكيد طلبك بنجاح", the order is ALREADY finalized. Therefore, the user's next message MUST NOT be classified as "order" UNLESS they explicitly request a NEW perfume. If they object to the deposit (e.g., "مش عايز ادفع عربون", "الدفع عند الاستلام"), send a payment screenshot, ask about shipping, or complain, classify it as "faq". If they want to cancel, classify as "order_cancel". NEVER classify as "order".
CRITICAL: If the user is asking a general question (e.g. "ممكن اسأل عن حاجة", "سؤال تاني", "بقولك ايه"), or just agreeing to general info (e.g. "طيب تمام"), MUST classify it as "faq" or "recommendation" depending on context, NEVER as "order". "order" is strictly for explicitly requesting to buy, confirming a purchase, or providing shipping details.
CRITICAL: If the user is asking about the PRICE ("عامل كام", "سعره كام", "بكام") of a specific perfume, MUST classify it as "product_info".
CRITICAL: If the user is asking an opinion, detail, or question about a SPECIFIC, NAMED perfume (e.g. "هل عطر كذا حلو", "بتاع افنان ده هيعجب الناس", "ريحته عاملة ايه"), MUST classify it as "product_info".
CRITICAL: If the user is asking about a BRAND in general without specifying a perfume (e.g. "عندك حاجة من ديور", "براند شانيل"), MUST classify it as "recommendation" to suggest perfumes from that brand.
CRITICAL: If the user is asking about a CATEGORY or TYPE of perfume (e.g. "عايز عطور شرقية", "عندك الترا نيش", "عطور نيش", "عطور غربية"), MUST classify it as "recommendation".
CRITICAL: Even if the user was previously making an order, if their LATEST message is asking a question about a product's details, smell, or performance, MUST classify as "product_info".
CRITICAL: If the assistant just asked for order details (name/phone/address) AND the user provides them, classify as "order". BUT if the user ignores the question and asks about something else (e.g., price of another perfume), classify based on their NEW question.
CRITICAL: If the assistant asked if the user wants to change/modify anything in their order (e.g., "في حاجة حابب تعدلها"), and the user replies with "لا", "لا شكرا", or "لا تمام", MUST classify as "order" because they want to proceed with confirmation, NOT "order_cancel".
CRITICAL: If the user types ONLY a phone number or address or his name, MUST classify it as "order".
CRITICAL: Do NOT classify insults or bad words (e.g., "غبي", "زفت") as "handoff" UNLESS the user explicitly asks to speak to a human (e.g., "عايز اكلم حد حقيقي"). If the user is just frustrated but in the middle of an order or a conversation, classify based on the current context (e.g., "order" or "product_info"). If it's just an isolated insult, classify as "faq" so the bot can apologize gracefully and continue the sale.
CRITICAL: If the user replies with a short confirmation (e.g., "اه", "ايوة", "تمام") or denial (e.g., "لا") to a question the assistant just asked in the previous message, YOU MUST look at the assistant's LAST message context. If the assistant offered to provide product details (e.g., "تحب اقولك مناسب لايه"), classify as "product_info". If the assistant offered a recommendation, classify as "recommendation".
CRITICAL: If the user is asking about suppliers, oil manufacturers, trade secrets, or companies like "لوزي", "أرجفيل", "جيفودان", "مان", MUST classify as "faq", NOT "recommendation", even if they use words like "ترشيح" or "أحسن".
CRITICAL — PROMOTION CONTEXT RULE: If the assistant's LAST message mentioned an offer/promotion (contains words like "عرض", "خصم", "أوفر", "مندوب من فريقنا", "تحولك لمندوب"), and the user is INSISTING the bot apply/execute the offer (e.g. "لا انا عايزك انت تنفذه", "بس طبق العرض", "نفذلي العرض", "انت بقى اعمل الخصم", "حطلي الخصم", "عايز الخصم"), MUST classify as "promotion" NOT "order". The user is asking the bot to do something it cannot do (apply offers), not placing a normal product order.

🔴 HANDOFF ANTI-LOOP RULES (VERY IMPORTANT):
- If the assistant ALREADY handed off the conversation to a human (e.g. the last assistant message says "تم تحويل المحادثة" or similar), and the user sends a NEW message:
  - If the new message is asking about perfumes, prices, recommendations, or ordering: classify based on the NEW content (recommendation, product_info, order, etc.). Do NOT classify as "handoff" again.
  - If the new message is STILL only complaining or asking for a human with NO other request: classify as "faq" so the bot can handle it gracefully instead of repeating the same handoff message.
  - ONLY classify as "handoff" if the user has NOT been handed off before in this conversation.
- If the user is frustrated but ALSO asking a perfume-related question in the same message (e.g. "انتو مش فاهمين حاجة، قولي سعر سوفاج كام"), classify based on the QUESTION not the frustration. In this case, classify as "product_info".
"""

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": message
    })

    try:
        response = chat(messages, response_format={"type": "json_object"})
        data = json.loads(response)
        return data.get("intent", "").strip().lower()
    except Exception:
        # Fallback gracefully
        return "faq"