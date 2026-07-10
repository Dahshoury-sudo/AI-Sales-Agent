import openpyxl
from io import BytesIO
from decimal import Decimal, InvalidOperation
from products.models import Store, Brand, Category, Product, ProductVariant


def parse_excel(file_bytes, store):
    """
    Parse an Excel file and bulk-create products for a given store.
    Returns a dict with 'created' count, 'errors' list, and 'skipped' count.
    
    Expected Excel columns (row 1 = headers):
    A: name (required)
    B: brand (required - will be created if not found)
    C: category (optional)
    D: gender (male/female/unisex - default: unisex)
    E: season
    F: occasion
    G: longevity
    H: projection
    I: concentration
    J: top_notes
    K: middle_notes
    L: base_notes
    M: description
    N: volume_1 (ml)
    O: price_1
    P: stock_1
    Q: volume_2 (ml)
    R: price_2
    S: stock_2
    T: volume_3 (ml)
    U: price_3
    V: stock_3
    """
    
    results = {"created": 0, "errors": [], "skipped": 0}
    
    try:
        wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True)
        ws = wb.active
    except Exception as e:
        results["errors"].append(f"فشل قراءة ملف الإكسل: {e}")
        return results
    
    rows = list(ws.iter_rows(min_row=2, values_only=True))  # Skip header row
    
    if not rows:
        results["errors"].append("الملف فاضي - مفيش بيانات بعد سطر العناوين.")
        return results
    
    for row_idx, row in enumerate(rows, start=2):
        # Pad row to at least 22 columns
        row = list(row) + [None] * (22 - len(row)) if len(row) < 22 else list(row)
        
        name = str(row[0]).strip() if row[0] else ""
        brand_name = str(row[1]).strip() if row[1] else ""
        
        # Validate required fields
        if not name or not brand_name:
            results["errors"].append(f"سطر {row_idx}: اسم المنتج أو البراند ناقص - تم تخطيه.")
            results["skipped"] += 1
            continue
        
        # Check for duplicate product name in this store
        if Product.objects.filter(store=store, name__iexact=name).exists():
            results["errors"].append(f"سطر {row_idx}: المنتج '{name}' موجود بالفعل - تم تخطيه.")
            results["skipped"] += 1
            continue
        
        # Get or create Brand
        brand, _ = Brand.objects.get_or_create(
            store=store, name__iexact=brand_name,
            defaults={"name": brand_name, "store": store}
        )
        
        # Get or create Category (optional)
        category = None
        category_name = str(row[2]).strip() if row[2] else ""
        if category_name:
            category, _ = Category.objects.get_or_create(
                store=store, name__iexact=category_name,
                defaults={"name": category_name, "store": store}
            )
        
        # Gender validation
        gender = str(row[3]).strip().lower() if row[3] else "unisex"
        if gender not in ("male", "female", "unisex"):
            gender = "unisex"
        
        try:
            product = Product.objects.create(
                store=store,
                name=name,
                brand=brand,
                category=category,
                gender=gender,
                season=str(row[4]).strip() if row[4] else "",
                occasion=str(row[5]).strip() if row[5] else "",
                longevity=str(row[6]).strip() if row[6] else "",
                projection=str(row[7]).strip() if row[7] else "",
                concentration=str(row[8]).strip() if row[8] else "",
                top_notes=str(row[9]).strip() if row[9] else "",
                middle_notes=str(row[10]).strip() if row[10] else "",
                base_notes=str(row[11]).strip() if row[11] else "",
                description=str(row[12]).strip() if row[12] else "",
            )
            
            # Create variants (up to 3)
            variant_count = 0
            for i in range(3):
                vol_idx = 13 + (i * 3)   # columns N, Q, T
                price_idx = 14 + (i * 3)  # columns O, R, U
                stock_idx = 15 + (i * 3)  # columns P, S, V
                
                volume = row[vol_idx] if vol_idx < len(row) else None
                price = row[price_idx] if price_idx < len(row) else None
                stock = row[stock_idx] if stock_idx < len(row) else None
                
                if volume and price:
                    try:
                        volume_int = int(float(str(volume)))
                        price_dec = Decimal(str(price))
                        stock_int = int(float(str(stock))) if stock else 0
                        
                        ProductVariant.objects.create(
                            product=product,
                            volume=volume_int,
                            price=price_dec,
                            stock=max(0, stock_int),
                        )
                        variant_count += 1
                    except (ValueError, InvalidOperation) as e:
                        results["errors"].append(
                            f"سطر {row_idx}: خطأ في بيانات الحجم/السعر رقم {i+1} للمنتج '{name}': {e}"
                        )
            
            if variant_count == 0:
                results["errors"].append(
                    f"سطر {row_idx}: المنتج '{name}' اتضاف بس من غير أحجام! ضيف أحجام يدوي."
                )
            
            results["created"] += 1
            
        except Exception as e:
            results["errors"].append(f"سطر {row_idx}: فشل إضافة '{name}': {e}")
    
    return results
