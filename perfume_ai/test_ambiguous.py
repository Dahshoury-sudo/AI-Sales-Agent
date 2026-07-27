import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.services.router import route
res, _ = route('شسيشسيشسي')
with open('res_ambiguous.txt', 'w', encoding='utf-8') as f:
    f.write(res)
