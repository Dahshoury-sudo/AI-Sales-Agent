"""Export and update classifications for existing store products."""

from io import BytesIO

import openpyxl
from django.core.exceptions import ValidationError
from django.db import transaction

from products.models import Product
from products.scent_tags import SCENT_TAG_CHOICES, parse_scent_tags_cell


def export_scent_tags(store):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Scent labels"
    sheet.append(["product_id", "name", "brand", "scent_tags"])
    for product in Product.objects.filter(store=store).select_related("brand").order_by("pk"):
        sheet.append([product.pk, product.name, product.brand.name if product.brand else "",
                      ", ".join(product.scent_tags)])
        for cell in sheet[sheet.max_row][1:]:
            cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for key, width in (("A", 15), ("B", 45), ("C", 28), ("D", 65)):
        sheet.column_dimensions[key].width = width
    labels = workbook.create_sheet("Allowed labels")
    labels.append(["label", "meaning"])
    for tag, label in SCENT_TAG_CHOICES:
        labels.append([tag, label])
    labels.column_dimensions["A"].width = 22
    labels.column_dimensions["B"].width = 22
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def import_scent_tags(file_bytes, store):
    result = {"updated": 0, "unchanged": 0, "skipped": 0, "errors": []}

    def error(row, message):
        result["errors"].append({"row": row, "message": message})

    try:
        workbook = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=False)
    except Exception:
        error(1, "تعذر قراءة ملف Excel.")
        return result
    try:
        sheet = workbook["Scent labels"] if "Scent labels" in workbook.sheetnames else workbook.active
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, ())
        columns = {str(value).strip().lower(): index for index, value in enumerate(header) if value}
        if not {"product_id", "scent_tags"} <= columns.keys():
            error(1, "الملف لازم يحتوي على product_id و scent_tags.")
            return result
        for name in ("product_id", "scent_tags"):
            if sum(str(value).strip().lower() == name for value in header) > 1:
                error(1, f"العمود {name} مكرر.")
        if result["errors"]:
            return result
        seen, changes = set(), {}
        for row_number, row in enumerate(rows, start=2):
            if not any(value is not None and str(value).strip() for value in row):
                continue
            raw_id = row[columns["product_id"]] if columns["product_id"] < len(row) else None
            try:
                product_id = int(str(raw_id).strip())
                if product_id < 1:
                    raise ValueError
            except (ValueError, TypeError):
                error(row_number, "رقم المنتج غير صحيح.")
                continue
            if product_id in seen:
                error(row_number, f"رقم المنتج {product_id} مكرر.")
                continue
            seen.add(product_id)
            cell = row[columns["scent_tags"]] if columns["scent_tags"] < len(row) else None
            try:
                tags = parse_scent_tags_cell(cell)
            except ValidationError as exc:
                error(row_number, " ".join(exc.messages))
                continue
            changes[product_id] = (row_number, tags)
    finally:
        workbook.close()

    with transaction.atomic():
        products = {p.pk: p for p in Product.objects.select_for_update().filter(store=store, pk__in=changes)}
        for product_id, (row_number, _) in changes.items():
            if product_id not in products:
                error(row_number, f"المنتج {product_id} غير موجود في متجرك.")
        if result["errors"]:
            return result
        updated = []
        for product_id, (_, tags) in changes.items():
            product = products[product_id]
            if not tags:
                result["skipped"] += 1
            elif tags == product.scent_tags:
                result["unchanged"] += 1
            else:
                product.scent_tags = tags
                updated.append(product)
        if updated:
            Product.objects.bulk_update(updated, ["scent_tags"])
        result["updated"] = len(updated)
    return result
