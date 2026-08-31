import json, os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()
from products.models import Conversation

c = Conversation.objects.filter(id=799).first()
if not c:
    print("NO CONVERSATION 799")
else:
    print("store:", c.store, "| platform:", c.platform, "| created:", c.created_at)
    out = [{"role": m.role, "content": m.content} for m in c.messages.order_by("created_at")]
    with open("conv_799.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("wrote conv_799.json,", len(out), "messages")
