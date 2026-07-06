import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.services.ai.classifier import classify
from products.services.product_resolver import resolve_product
from products.services.router import route

messages = [
    "بكام برفان اكلير لطافه"
]

print("Starting Detailed Diagnostics...")
print("================================")

for m in messages:
    print(f"\n[1] Message: '{m}'")
    
    # 1. Classification
    cls = classify(m)
    print(f"[2] Classification Result: {cls}")
    
    # 2. Extracting Product Name via Resolver
    product = resolve_product(m)
    
    if product:
        print(f"\n[3] Product Resolver Result:")
        print(f"    -> Found exactly: {product.name} (Price: {product.price})")
    else:
        print("\n[3] Product Resolver failed to find any matching product.")

    # 3. Final Route Result
    print(f"\n[4] Final AI Reply:")
    reply = route(m)
    print(reply)
    print("\n--------------------------------")
