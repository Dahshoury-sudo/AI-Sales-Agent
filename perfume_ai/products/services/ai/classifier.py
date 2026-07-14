from .client import chat


def classify(message: str, history=None):
    system_prompt = """
You are a request classifier.

Read the conversation history (if any), but ONLY classify the user's LATEST message.

Return ONLY one of these words that best describes the user's latest request:

recommendation: asking for suggestions or a perfume that matches some criteria.
product_info: asking for details, PRICE, ingredients, or info of a SPECIFIC perfume (e.g. "سعر سوفاج", "عامل كام", "بكام ده").
comparison: comparing between two or more perfumes.
order: wanting to buy, ordering a product, or providing their address/phone number/name to checkout. (e.g. "هاخد ده", "هاتلي واحد", "عايز اشتريه", "010...", "شارع كذا").
order_cancel: wanting to cancel the order they are currently making (e.g. "الغي الاوردر", "مش عايز").
greeting: saying hello, hi, etc.
faq: general questions not related to a specific product's info (e.g., shipping, delivery, who are you, how to make perfume, bulk buying).
handoff: wanting to speak to a human, complaining, or showing frustration (e.g. "عايز اكلم حد حقيقي", "فين خدمة العملاء", "انت بوت").
out_of_domain: questions entirely unrelated to perfumes, orders, or the store (e.g., programming, politics, medical advice, "مين رئيس امريكا", "اكتبلي كود").

CRITICAL: If the user is asking about the PRICE ("عامل كام", "سعره كام", "بكام") of a specific perfume, MUST classify it as "product_info".
CRITICAL: If the user is asking an opinion, detail, or question about a SPECIFIC, NAMED perfume (e.g. "هل عطر كذا حلو", "بتاع افنان ده هيعجب الناس", "ريحته عاملة ايه"), MUST classify it as "product_info".
CRITICAL: Even if the user was previously making an order, if their LATEST message is asking a question about a product's details, smell, or performance, MUST classify as "product_info".
CRITICAL: If the assistant just asked for order details (name/phone/address) AND the user provides them, classify as "order". BUT if the user ignores the question and asks about something else (e.g., price of another perfume), classify based on their NEW question.
CRITICAL: If the assistant asked if the user wants to change/modify anything in their order (e.g., "في حاجة حابب تعدلها"), and the user replies with "لا", "لا شكرا", or "لا تمام", MUST classify as "order" because they want to proceed with confirmation, NOT "order_cancel".
CRITICAL: If the user types ONLY a phone number or address or his name, MUST classify it as "order".
CRITICAL: If the user insults the bot or uses bad words (e.g., "غبي", "زفت"), classify as "handoff" so a human can handle the frustrated customer.

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

    response = chat(messages)

    return response.strip().lower()