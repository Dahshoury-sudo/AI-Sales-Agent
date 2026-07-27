import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.services.router import route
msg = 'قارنلي بين marino و Acqua di Gio'
res, _ = route(msg)
with open('res_compare.txt', 'w', encoding='utf-8') as f: f.write(res)
