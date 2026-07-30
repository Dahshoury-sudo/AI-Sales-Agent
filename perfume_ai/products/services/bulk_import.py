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
    E: perfume_type (oriental/western/niche/ultra_niche)
    F: season
    G: occasion
    H: longevity
    I: projection
    J: concentration
    K: top_notes
    L: middle_notes
    M: base_notes
    N: description
    O: oil_stock_grams
    P: concentration_percentage (default: 30)
    Q: norm_vol_1 (ml)
    R: norm_price_1
    S: norm_vol_2 (ml)
    T: norm_price_2
    U: norm_vol_3 (ml)
    V: norm_price_3
    W: orig_vol_1 (ml)
    X: orig_price_1
    Y: orig_stock_1
    Z: orig_vol_2 (ml)
    AA: orig_price_2
    AB: orig_stock_2
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
        # Pad row to at least 28 columns
        row = list(row) + [None] * (28 - len(row)) if len(row) < 28 else list(row)
        
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
        
        # Perfume Type validation
        perfume_type = str(row[4]).strip().lower() if row[4] else None
        if perfume_type and perfume_type not in ("oriental", "western", "niche", "ultra_niche"):
            perfume_type = None

        try:
            product = Product.objects.create(
                store=store,
                name=name,
                brand=brand,
                category=category,
                gender=gender,
                perfume_type=perfume_type,
                season=str(row[5]).strip() if row[5] else "",
                occasion=str(row[6]).strip() if row[6] else "",
                longevity=str(row[7]).strip() if row[7] else "",
                projection=str(row[8]).strip() if row[8] else "",
                concentration=str(row[9]).strip() if row[9] else "",
                top_notes=str(row[10]).strip() if row[10] else "",
                middle_notes=str(row[11]).strip() if row[11] else "",
                base_notes=str(row[12]).strip() if row[12] else "",
                description=str(row[13]).strip() if row[13] else "",
                oil_stock_grams=int(float(str(row[14]))) if len(row) > 14 and row[14] else 0,
                concentration_percentage=int(float(str(row[15]))) if len(row) > 15 and row[15] else 30,
            )
            
            # Create normal variants (up to 3)
            variant_count = 0
            for i in range(3):
                vol_idx = 16 + (i * 2)   # columns Q, S, U (16, 18, 20)
                price_idx = 17 + (i * 2)  # columns R, T, V (17, 19, 21)
                
                volume = row[vol_idx] if vol_idx < len(row) else None
                price = row[price_idx] if price_idx < len(row) else None
                
                if volume and price:
                    try:
                        ProductVariant.objects.create(
                            product=product,
                            volume=int(float(str(volume))),
                            price=Decimal(str(price)),
                            bottle_type='normal'
                        )
                        variant_count += 1
                    except (ValueError, InvalidOperation) as e:
                        results["errors"].append(f"سطر {row_idx}: خطأ في بيانات التركيب {i+1} للمنتج '{name}': {e}")
            
            # Create original variants (up to 2)
            for i in range(2):
                vol_idx = 22 + (i * 3)   # columns W, Z (22, 25)
                price_idx = 23 + (i * 3)  # columns X, AA (23, 26)
                stock_idx = 24 + (i * 3)  # columns Y, AB (24, 27)
                
                volume = row[vol_idx] if vol_idx < len(row) else None
                price = row[price_idx] if price_idx < len(row) else None
                stock = row[stock_idx] if stock_idx < len(row) else None
                
                if volume and price:
                    try:
                        ProductVariant.objects.create(
                            product=product,
                            volume=int(float(str(volume))),
                            price=Decimal(str(price)),
                            stock=int(float(str(stock))) if stock else 0,
                            bottle_type='original'
                        )
                        variant_count += 1
                    except (ValueError, InvalidOperation) as e:
                        results["errors"].append(f"سطر {row_idx}: خطأ في بيانات الأوريجينال {i+1} للمنتج '{name}': {e}")
            
            if variant_count == 0:
                results["errors"].append(
                    f"سطر {row_idx}: المنتج '{name}' اتضاف بس من غير أي أحجام! ضيف أحجام يدوي."
                )
            
            results["created"] += 1
            
        except Exception as e:
            results["errors"].append(f"سطر {row_idx}: فشل إضافة '{name}': {e}")
    
    return results
