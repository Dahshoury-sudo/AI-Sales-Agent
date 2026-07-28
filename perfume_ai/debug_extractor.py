import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.models import Conversation
from products.services.ai.client import chat

c = Conversation.objects.get(id=331)

history = []
for m in c.messages.all():
    if m.content == 'عايز سوفاج 90 ملي ازازه المحل':
        break
    # We want to see how the AI parses the exact sequence up to that message
    history.append({'role': m.role, 'content': m.content})

prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided in the history.
2. "customer_phone": The customer's phone number if provided in the history.
3. "shipping_address": The customer's full delivery address if provided in the history.
4. "products": A comprehensive list of ALL products the user wants to buy in the CURRENT active order. For each product, extract "bottle_type" ("original", "normal", or null if they didn't specify). CRITICAL: If the user hasn't explicitly chosen the bottle type yet, YOU MUST return null. You MUST read the entire history and reconstruct the FULL pending shopping cart every time. Do NOT return an empty list if there is a pending unconfirmed order, even if the user's latest message doesn't explicitly mention the product (like when they just say "تمام" to confirm). 🚨 CRITICAL 🚨: If the assistant has ALREADY finalized a previous order in the history (e.g. saying "تم تأكيد طلبك بنجاح"), that order is CLOSED. You must start a NEW empty cart for any products requested AFTER that confirmation. If no NEW products have been requested yet, return an empty list [].
5. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). ALSO, if the assistant asked "ولا في حاجة حابب تعدلها؟" and the user replies with "لا", "لا شكرا", or "لا تمام" (meaning they don't want to modify), this is a confirmation to proceed, so return true. Otherwise, return false.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer, "bottle_type": null or "normal" or "original"}
    ],
    "is_confirmed": false
}
"""

messages = [{'role': 'system', 'content': prompt}]
messages.extend(history)
messages.append({'role': 'user', 'content': 'عايز سوفاج 90 ملي ازازه المحل'})

res = chat(messages, response_format={'type': 'json_object'})
with open('res.txt', 'w', encoding='utf-8') as f:
    f.write(res)
