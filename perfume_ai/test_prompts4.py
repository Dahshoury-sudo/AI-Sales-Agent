import os, django, json
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.services.order_service import handle_order

history = [
    {"role": "user", "content": "عندكو بلو دي شانيل او سوفاج ؟"},
    {"role": "assistant", "content": "أيوة يا فندم، عندنا الاتنين متوفرين!  \nBleu de Chanel وDior Sauvage، وكل واحد فيهم متوفر في 50ml بـ500 جنيه، 80ml بـ650 جنيه، و100ml بـ800 جنيه.  \nتحب تعرف الفرق بينهم أو محتار تختار إيه أكتر؟"}
]

message = "عايز واحد"

from products.models import Store
store = Store.objects.first()

response, context = handle_order(message, history, store, None)
print("Bot response:", response)
