import os, django, json
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.models import Message

msgs = Message.objects.filter(conversation_id=83).order_by('created_at')
data = []
for m in msgs:
    data.append({
        'role': m.role,
        'content': m.content
    })

with open('conv83_msgs.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Dumped {len(data)} messages from conv 83")
