import django, os, json
os.environ['DJANGO_SETTINGS_MODULE'] = 'perfume_ai.settings'
django.setup()

from products.models import Message

msgs = Message.objects.filter(conversation_id=78).order_by('created_at')
data = []
for m in msgs:
    data.append({
        'role': m.role,
        'content': m.content,
        'created_at': str(m.created_at)
    })

with open('conv78_msgs.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Found {len(data)} messages. Saved to conv78_msgs.json")
