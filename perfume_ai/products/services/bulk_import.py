import openpyxl
from io import BytesIO
from decimal import Decimal, InvalidOperation
from products.models import Store, Brand, Category, Product, ProductVariant


# Valid choices (must match model field choices exactly)
VALID_GENDERS = {"male", "female", "unisex"}
VALID_PERFUME_TYPES = {"oriental", "western", "niche", "ultra_niche", "store_own_made"}

# Normal variant column pairs: (volume_col_name, price_col_name)
NORM_VARIANT_COLS = [
    ("norm_vol_1", "norm_price_1"),
    ("norm_vol_2", "norm_price_2"),
    ("norm_vol_3", "norm_price_3"),
]

# Original variant column triplets: (volume, price, stock)
ORIG_VARIANT_COLS = [
    ("orig_vol_1", "orig_price_1", "orig_stock_1"),
    ("orig_vol_2", "orig_price_2", "orig_stock_2"),
]


def _build_col_map(header_row):
    """Return a dict mapping lowercase column name -> 0-based index."""
    return {
        str(cell).strip().lower(): idx
        for idx, cell in enumerate(header_row)
        if cell is not None
    }


def _get(row, col_map, col_name, default=None):
    """Safely fetch a cell value by column name."""
    idx = col_map.get(col_name.lower())
    if idx is None or idx >= len(row):
        return default
    return row[idx]


def parse_excel(file_bytes, store):
    """
    Parse an Excel file and bulk-create products for a given store.
    Returns a dict with 'created' count, 'errors' list, and 'skipped' count.

    Columns are resolved by **header name** (row 1), so the order/count of
    variant columns does not need to be fixed — the file can have 2 or 3
    normal-size columns and the importer adapts automatically.

    Required headers:  name, brand
    Optional headers:  category, gender, perfume_type, season, occasion,
                       longevity, projection, concentration,
                       top_notes, middle_notes, base_notes, description,
                       norm_vol_1, norm_price_1,
                       norm_vol_2, norm_price_2,
                       norm_vol_3, norm_price_3,   (optional — skipped if absent)
                       orig_vol_1, orig_price_1, orig_stock_1,
                       orig_vol_2, orig_price_2, orig_stock_2

    Valid values:
        gender       -> male / female / unisex          (default: unisex)
        perfume_type -> oriental / western / niche / ultra_niche
    """

    results = {"created": 0, "errors": [], "skipped": 0}

    try:
        wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True)
        ws = wb.active
    except Exception as e:
        results["errors"].append(f"فشل قراءة ملف الإكسل: {e}")
        return results

    all_rows = list(ws.iter_rows(min_row=1, values_only=True))

    if not all_rows:
        results["errors"].append("الملف فاضي تمامًا.")
        return results

    # Build header -> index map from row 1
    col_map = _build_col_map(all_rows[0])

    if "name" not in col_map or "brand" not in col_map:
        results["errors"].append(
            "مفيش عمود 'name' أو 'brand' في سطر العناوين - تأكد من الهيدر."
        )
        return results

    data_rows = all_rows[1:]

    if not data_rows:
        results["errors"].append("الملف فاضي - مفيش بيانات بعد سطر العناوين.")
        return results

    for row_idx, row in enumerate(data_rows, start=2):
        row = list(row)  # make mutable

        name = str(_get(row, col_map, "name", "")).strip()
        brand_name = str(_get(row, col_map, "brand", "")).strip()

        # -- Required fields --------------------------------------------------
        if not name or not brand_name:
            results["errors"].append(
                f"سطر {row_idx}: اسم المنتج أو البراند ناقص - تم تخطيه."
            )
            results["skipped"] += 1
            continue

        # -- Duplicate check --------------------------------------------------
        if Product.objects.filter(store=store, name__iexact=name).exists():
            results["errors"].append(
                f"سطر {row_idx}: المنتج '{name}' موجود بالفعل - تم تخطيه."
            )
            results["skipped"] += 1
            continue

        # -- Brand ------------------------------------------------------------
        brand, _ = Brand.objects.get_or_create(
            store=store,
            name__iexact=brand_name,
            defaults={"name": brand_name, "store": store},
        )

        # -- Category (optional) ----------------------------------------------
        category = None
        category_name = str(_get(row, col_map, "category", "")).strip()
        if category_name:
            category, _ = Category.objects.get_or_create(
                store=store,
                name__iexact=category_name,
                defaults={"name": category_name, "store": store},
            )

        # -- Gender -----------------------------------------------------------
        raw_gender = str(_get(row, col_map, "gender", "")).strip().lower()
        gender = raw_gender if raw_gender in VALID_GENDERS else "unisex"

        # -- Perfume type -----------------------------------------------------
        raw_type = str(_get(row, col_map, "perfume_type", "")).strip().lower()
        if not raw_type:
            perfume_type = None
        elif raw_type in VALID_PERFUME_TYPES:
            perfume_type = raw_type
        else:
            perfume_type = None
            results["errors"].append(
                f"سطر {row_idx}: نوع العطر '{raw_type}' مش متعرف - "
                f"القيم المقبولة: oriental / western / niche / ultra_niche. "
                f"اتضاف المنتج '{name}' من غير نوع."
            )

        # -- Create product ---------------------------------------------------
        try:
            product = Product.objects.create(
                store=store,
                name=name,
                brand=brand,
                category=category,
                gender=gender,
                perfume_type=perfume_type,
                season=str(_get(row, col_map, "season", "")).strip(),
                occasion=str(_get(row, col_map, "occasion", "")).strip(),
                longevity=str(_get(row, col_map, "longevity", "")).strip(),
                projection=str(_get(row, col_map, "projection", "")).strip(),
                concentration=str(_get(row, col_map, "concentration", "")).strip(),
                top_notes=str(_get(row, col_map, "top_notes", "")).strip(),
                middle_notes=str(_get(row, col_map, "middle_notes", "")).strip(),
                base_notes=str(_get(row, col_map, "base_notes", "")).strip(),
                description=str(_get(row, col_map, "description", "")).strip(),
            )

            variant_count = 0

            # -- Normal variants (2 or 3 — depends on headers present) --------
            for vol_col, price_col in NORM_VARIANT_COLS:
                volume = _get(row, col_map, vol_col)
                price = _get(row, col_map, price_col)
                # Column absent in this file — skip silently
                if volume is None or price is None:
                    continue
                # Cell is empty — skip
                if not volume or not price:
                    continue
                try:
                    ProductVariant.objects.create(
                        product=product,
                        volume=int(float(str(volume))),
                        price=Decimal(str(price)),
                        bottle_type="normal",
                    )
                    variant_count += 1
                except (ValueError, InvalidOperation) as e:
                    results["errors"].append(
                        f"سطر {row_idx}: خطأ في بيانات '{vol_col}'/'{price_col}' "
                        f"للمنتج '{name}': {e}"
                    )

            # -- Original variants (up to 2) ----------------------------------
            for vol_col, price_col, stock_col in ORIG_VARIANT_COLS:
                volume = _get(row, col_map, vol_col)
                price = _get(row, col_map, price_col)
                stock = _get(row, col_map, stock_col)
                # Column absent — skip
                if volume is None or price is None:
                    continue
                # Cell empty — skip
                if not volume or not price:
                    continue
                try:
                    ProductVariant.objects.create(
                        product=product,
                        volume=int(float(str(volume))),
                        price=Decimal(str(price)),
                        stock=int(float(str(stock))) if stock else 0,
                        bottle_type="original",
                    )
                    variant_count += 1
                except (ValueError, InvalidOperation) as e:
                    results["errors"].append(
                        f"سطر {row_idx}: خطأ في بيانات '{vol_col}'/'{price_col}' "
                        f"للمنتج '{name}': {e}"
                    )

            if variant_count == 0:
                results["errors"].append(
                    f"سطر {row_idx}: المنتج '{name}' اتضاف بس من غير أي أحجام! "
                    f"ضيف أحجام يدوي."
                )

            results["created"] += 1

        except Exception as e:
            results["errors"].append(f"سطر {row_idx}: فشل إضافة '{name}': {e}")

    return results
