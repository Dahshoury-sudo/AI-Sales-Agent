import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.models import Product

# Rules
# 1. Xerjoff -> ultra_niche
# 2. Creed, MFK, Tom Ford -> niche
# 3. Lattafa, Afnan -> oriental
# 4. Perfamix -> check name
# 5. Others -> western (designer)

def determine_type(brand_name, product_name):
    brand_name = brand_name.lower()
    product_name = product_name.lower()
    
    if "xerjoff" in brand_name:
        return "ultra_niche"
    elif "creed" in brand_name or "maison francis kurkdjian" in brand_name or "tom ford" in brand_name:
        return "niche"
    elif "lattafa" in brand_name or "afnan" in brand_name:
        return "oriental"
    elif "perfamix" in brand_name:
        oriental_keywords = ["oud", "amber", "safran", "musk"]
        if any(kw in product_name for kw in oriental_keywords):
            return "oriental"
        return "niche" # Treat their custom blends as niche by default
    else:
        return "western"

updated = 0
for p in Product.objects.all():
    p_type = determine_type(p.brand.name, p.name)
    p.perfume_type = p_type
    p.save()
    updated += 1
    print(f"Updated {p.name} ({p.brand.name}) -> {p_type}")

print(f"Total updated: {updated}")
