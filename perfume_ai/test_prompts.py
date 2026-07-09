import os, django, json
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.services.order_service import handle_order
from products.services.ai.client import chat

history = [
    {"role": "user", "content": "عايز اطلب سوفاج"},
    {"role": "assistant", "content": "ممتاز! لتأكيد طلبك، أحتاج فقط إلى الاسم الكريم و رقم الهاتف و عنوان التوصيل بالتفصيل و الحجم المطلوب من عطر سوفاج (متاح أحجام: 50, 80, 100 مل)."},
    {"role": "user", "content": "80 ملي"},
    {"role": "assistant", "content": "ممتاز! لتأكيد طلبك، أحتاج فقط إلى الاسم الكريم و رقم الهاتف و عنوان التوصيل بالتفصيل."},
    {"role": "user", "content": "محمد 01153032052 المقطم"},
    {"role": "assistant", "content": "تفاصيل طلبك كالتالي:\n\n👤 الاسم: محمد\n📱 الهاتف: 01153032052\n📍 العنوان: المقطم\n\n🛍️ المنتجات:\n- 1 × Dior Sauvage (80ml) (السعر: 650.00 جنيه)\n\n💰 الإجمالي: 650.00 جنيه.\n\nهل تحب نأكد الطلب على كده ولا في حاجة حابب تعدلها؟"},
    {"role": "user", "content": "تمام"},
    {"role": "assistant", "content": "تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #15.\nسيقوم فريق المبيعات بالتواصل معك قريباً لتحديد موعد التسليم."},
]

message = "اوردر"

prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided.
2. "customer_phone": The customer's phone number.
3. "shipping_address": The customer's full delivery address.
4. "products": A comprehensive list of ALL products the user wants to buy right now. You MUST read the entire history and maintain a running list of the shopping cart. If the user adds a new product, or changes a quantity, update your list accordingly so it includes ALL previous items PLUS the new ones. Each object must contain "name" (exact name from history), "quantity" (an integer, or 1 if not specified), and "volume" (an integer representing the size in ml if specified by the user, e.g., 50, 80, 100, or null if not specified).
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

new_prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided.
2. "customer_phone": The customer's phone number.
3. "shipping_address": The customer's full delivery address.
4. "products": A comprehensive list of ALL products the user wants to buy right now. You MUST read the entire history and maintain a running list of the shopping cart. 🚨 CRITICAL 🚨: If the assistant has ALREADY confirmed an order in the history (e.g. saying "تم تأكيد طلبك بنجاح"), you MUST IGNORE all products mentioned BEFORE that confirmation. Only extract new products requested AFTER the last order was confirmed. If no new products were requested, return an empty list [].
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

messages2 = [{"role": "system", "content": new_prompt}]
messages2.extend(history)
messages2.append({"role": "user", "content": message})

response2 = chat(messages2, response_format={"type": "json_object"})

import io
with io.open('test_output.json', 'w', encoding='utf-8') as f:
    f.write('Original:\n' + response + '\n\n')
    f.write('New:\n' + response2 + '\n')
