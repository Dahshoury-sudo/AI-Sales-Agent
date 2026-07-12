import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.models import Conversation

try:
    c = Conversation.objects.get(id=119)
    with open('conv119.txt', 'w', encoding='utf-8') as f:
        f.write(f"Store: {c.store.name if c.store else 'None'}\n")
        for m in c.messages.all().order_by('created_at'):
            f.write(f"{m.role}: {m.content}\n")
except Exception as e:
    print(e)
