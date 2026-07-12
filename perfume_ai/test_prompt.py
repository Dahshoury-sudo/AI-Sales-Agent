import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.services.product_info import get_product_info
from products.models import Conversation

try:
    c = Conversation.objects.get(id=119)
    history_msgs = list(c.messages.all().order_by('created_at'))[:6]
    history = [{"role": m.role, "content": m.content} for m in history_msgs]
    
    msg = "طيب بتحط كام جرام في الازازة. ال ١٠٠ ملي"
    
    response, context = get_product_info(msg, history=history, store=c.store)
    
    with open('test_result.txt', 'w', encoding='utf-8') as f:
        f.write("=== BOT RESPONSE ===\n")
        f.write(response + "\n")
        
except Exception as e:
    with open('test_result.txt', 'w', encoding='utf-8') as f:
        f.write(str(e))
