import os, django, json
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.services.ai.client import chat
from products.services.order_service import handle_order

history = [
    {"role": "user", "content": "عندكو بلو دي شانيل او سوفاج ؟"},
    {"role": "assistant", "content": "أيوة يا فندم، عندنا الاتنين متوفرين!  \nBleu de Chanel وDior Sauvage، وكل واحد فيهم متوفر في 50ml بـ500 جنيه، 80ml بـ650 جنيه، و100ml بـ800 جنيه.  \nتحب تعرف الفرق بينهم أو محتار تختار إيه أكتر؟"}
]

message = "عايز واحد"

prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided in the history.
2. "customer_phone": The customer's phone number if provided in the history.
3. "shipping_address": The customer's full delivery address if provided in the history.
4. "products": A comprehensive list of ALL products the user wants to buy right now. You MUST read the entire history and maintain a running list of the shopping cart. 🚨 CRITICAL 🚨: If the assistant has ALREADY confirmed an order in the history (e.g. saying "تم تأكيد طلبك بنجاح"), you MUST EMPTY your cart of any products mentioned BEFORE that confirmation. Only extract NEW products requested AFTER the last order was confirmed. If no new products were requested, return an empty list [].
5. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). ALSO, if the assistant asked "ولا في حاجة حابب تعدلها؟" and the user replies with "لا", "لا شكرا", or "لا تمام" (meaning they don't want to modify), this is a confirmation to proceed, so return true. Otherwise, return false.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer}
    ],
    "is_confirmed": false
}
"""

messages = [{"role": "system", "content": prompt}]
messages.extend(history)
messages.append({"role": "user", "content": message})

response = chat(messages, response_format={"type": "json_object"})

import io
with io.open('test_output3.json', 'w', encoding='utf-8') as f:
    f.write(response)
