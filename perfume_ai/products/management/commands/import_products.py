from django.core.management.base import BaseCommand
from products.models import Store
from products.services.bulk_import import parse_excel


class Command(BaseCommand):
    help = "Bulk import products from an Excel file. Usage: python manage.py import_products <store_id> <file.xlsx>"

    def add_arguments(self, parser):
        parser.add_argument("store_id", type=int, help="The Store ID to import products into")
        parser.add_argument("file_path", type=str, help="Path to the Excel (.xlsx) file")

    def handle(self, *args, **options):
        store_id = options["store_id"]
        file_path = options["file_path"]

        # Validate store
        try:
            store = Store.objects.get(id=store_id)
        except Store.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Store with ID {store_id} not found."))
            return

        # Read file
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        self.stdout.write(f"Importing products for store: {store.name}...")

        results = parse_excel(file_bytes, store)

        # Print results
        self.stdout.write(self.style.SUCCESS(f"\n✅ تم إضافة {results['created']} منتج بنجاح!"))
        
        if results["skipped"]:
            self.stdout.write(self.style.WARNING(f"⏭️  تم تخطي {results['skipped']} سطر."))

        if results["errors"]:
            self.stdout.write(self.style.WARNING("\n⚠️  ملاحظات:"))
            for err in results["errors"]:
                self.stdout.write(f"   - {err}")
