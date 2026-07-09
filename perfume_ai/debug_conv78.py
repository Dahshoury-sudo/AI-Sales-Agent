import django, os, json
os.environ['DJANGO_SETTINGS_MODULE'] = 'perfume_ai.settings'
django.setup()

from products.models import Order

orders = Order.objects.filter(conversation_id=78).order_by('created_at')
data = []
for o in orders:
    data.append({
        'id': o.id,
        'name': o.customer_name,
        'phone': o.customer_phone,
        'total': str(o.total_price),
        'status': o.status,
        'created': str(o.created_at),
    })

with open('conv78_orders.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Found {len(data)} orders. Saved to conv78_orders.json")
