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
greeting: saying hello, hi, etc.
faq: general questions not related to a specific product's info (e.g., shipping, delivery, who are you).
handoff: wanting to speak to a human, complaining, or showing frustration (e.g. "عايز اكلم حد حقيقي", "فين خدمة العملاء", "انت بوت").
out_of_domain: questions entirely unrelated to perfumes, orders, or the store (e.g., programming, politics, medical advice, "مين رئيس امريكا", "اكتبلي كود").

CRITICAL: If the user is asking about the PRICE ("عامل كام", "سعره كام", "بكام") of a specific perfume, MUST classify it as "product_info".
CRITICAL: If the assistant just asked for order details (name/phone/address) AND the user provides them, classify as "order". BUT if the user ignores the question and asks about something else (e.g., price of another perfume), classify based on their NEW question.
CRITICAL: If the user types ONLY a phone number or address or his name, MUST classify it as "order".
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