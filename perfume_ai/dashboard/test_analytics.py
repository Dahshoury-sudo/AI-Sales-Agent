import csv
import io
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from dashboard.analytics import Filters
from products.models import Brand, Conversation, Order, OrderItem, Product, ProductVariant, Store

NOW = datetime(2026, 10, 22, 12, tzinfo=dt_timezone.utc)


class AnalyticsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="analytics-owner")
        self.store = Store.objects.create(name="Store", owner=self.owner)
        self.other = Store.objects.create(name="Other")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")
        brand = Brand.objects.create(store=self.store, name="Brand")
        self.product = Product.objects.create(store=self.store, name="عود", brand=brand, gender="unisex")
        self.variant = ProductVariant.objects.create(product=self.product, volume=50, price="999.00")

    def conversation(self, when=NOW - timedelta(hours=2), store=None, platform="web"):
        conv = Conversation.objects.create(store=store or self.store, platform=platform)
        Conversation.objects.filter(pk=conv.pk).update(created_at=when)
        return conv

    def order(self, when=NOW - timedelta(hours=1), status="confirmed", total="25.50", conversation=None, store=None, phone="01000000000", item=True):
        order = Order.objects.create(store=store or self.store, status=status, total_price=total, conversation=conversation,
                                     customer_name="Test", customer_phone=phone, shipping_address="Test")
        Order.objects.filter(pk=order.pk).update(created_at=when)
        if item:
            OrderItem.objects.create(order=order, variant=self.variant, quantity=3, price_at_time_of_order="8.50")
        return order

    def get(self, params=None, endpoint="", status=200):
        with patch("dashboard.analytics.timezone.now", return_value=NOW):
            response = self.client.get("/dashboard/api/analytics/" + endpoint, {"version": "2", "preset": "today", **(params or {})})
        self.assertEqual(response.status_code, status, response.content[:1000])
        return response

    def test_metrics_use_qualifying_revenue_and_historical_prices(self):
        conv = self.conversation()
        self.order(conversation=conv)
        self.order(status="delivered", total="10.10", item=False, phone="0100 000 0000")
        self.order(status="pending", total="900")
        self.order(status="cancelled", total="800")
        data = self.get().json()
        self.assertEqual(Decimal(data["kpis"]["revenue"]), Decimal("35.60"))
        self.assertEqual(data["kpis"]["orders"], 4)
        self.assertEqual(data["kpis"]["customers"], 1)
        self.assertEqual(data["kpis"]["aov"], "17.80")
        self.assertEqual(data["kpis"]["conversion"], "100.0")
        self.assertEqual(data["products"]["rows"][0]["units"], 3)
        self.assertEqual(Decimal(data["products"]["rows"][0]["revenue"]), Decimal("25.50"))
        self.assertEqual(Decimal(data["reconciliation"]["difference"]), Decimal("10.10"))
        self.assertEqual(data["reconciliation"]["orders_without_items"], 1)

    def test_conversion_is_distinct_and_same_window(self):
        converted, pending, empty = [self.conversation() for _ in range(3)]
        old = self.conversation(NOW - timedelta(days=2))
        self.order(conversation=converted)
        self.order(conversation=converted)
        self.order(conversation=pending, status="pending")
        self.order(conversation=old)
        self.order(conversation=empty, when=NOW + timedelta(hours=1))
        data = self.get().json()
        self.assertEqual(data["kpis"]["converted_conversations"], 1)
        self.assertEqual(data["kpis"]["conversion"], "33.3")

    def test_order_before_conversation_does_not_convert(self):
        self.order(conversation=self.conversation(NOW), when=NOW - timedelta(hours=1))
        self.assertIsNone(self.get().json()["kpis"]["conversion"])
        self.order(conversation=self.conversation(NOW - timedelta(minutes=10)), when=NOW - timedelta(hours=1))
        self.assertEqual(self.get().json()["kpis"]["conversion"], "0.0")

    def test_boundaries_and_equivalent_partial_previous_period(self):
        self.order(when=NOW.replace(hour=0))
        self.order(when=NOW)
        self.order(when=NOW - timedelta(days=1, hours=1), total="12.75")
        self.order(when=NOW - timedelta(days=1) + timedelta(hours=1), total="200")
        data = self.get().json()
        self.assertEqual(data["kpis"]["orders"], 1)
        self.assertEqual(data["previous"]["orders"], 1)
        self.assertEqual(data["changes"]["revenue"], "100.0")
        self.assertTrue(data["meta"]["partial"])
        self.assertEqual(data["meta"]["previous_end_exclusive"], "2026-10-21T12:00:00+00:00")

    def test_empty_and_zero_baseline_are_not_fake_rates(self):
        data = self.get().json()
        self.assertEqual(data["kpis"]["revenue"], "0.00")
        self.assertIsNone(data["kpis"]["aov"])
        self.assertIsNone(data["kpis"]["conversion"])
        self.assertIsNone(data["changes"]["revenue"])
        self.assertEqual(data["products"]["rows"], [])
        self.assertEqual(data["trends"][0]["orders"], 0)
        self.order()
        self.assertIsNone(self.get().json()["changes"]["revenue"])

    def test_missing_customer_hash_is_excluded_without_pii(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(customer_phone_hash="")
        data = self.get().json()
        self.assertEqual(data["kpis"]["customers"], 0)
        self.assertEqual(data["kpis"]["missing_customer_orders"], 1)
        self.assertNotIn("01000000000", str(data))
        self.assertNotIn("customer_phone_hash", str(data))

    def test_channels_include_older_links_and_unknown_and_unattributed(self):
        self.order(conversation=self.conversation(NOW - timedelta(days=4), platform="whatsapp"))
        self.order(conversation=self.conversation(platform="obsolete"), status="pending")
        self.order()
        rows = {r["platform"]: r for r in self.get().json()["channels"]}
        self.assertEqual(rows["whatsapp"]["orders"], 1)
        self.assertEqual(rows["whatsapp"]["conversations"], 0)
        self.assertEqual(rows["unknown"]["conversations"], 1)
        self.assertEqual(Decimal(rows["unknown"]["revenue"]), 0)
        self.assertEqual(rows["unattributed"]["orders"], 1)

    def test_store_isolation_and_foreign_links(self):
        foreign = self.conversation(store=self.other, platform="instagram")
        self.order(store=self.other, conversation=foreign, total="9000", item=False)
        local = self.order(conversation=foreign)
        other_brand = Brand.objects.create(store=self.other, name="SECRET BRAND")
        other_product = Product.objects.create(store=self.other, brand=other_brand, name="SECRET PRODUCT", gender="unisex")
        other_variant = ProductVariant.objects.create(product=other_product, volume=50, price=9)
        OrderItem.objects.filter(order=local).update(variant=other_variant)
        data = self.get({"store_id": self.other.pk}).json()
        self.assertEqual(data["kpis"]["orders"], 1)
        self.assertEqual(data["kpis"]["conversations"], 0)
        self.assertNotIn("SECRET", str(data))
        self.assertEqual(next(r for r in data["channels"] if r["platform"] == "unattributed")["orders"], 1)
        for report in ("overview", "products", "channels", "trends", "statuses"):
            content = self.get({"report": report}, "export/").content.decode("utf-8-sig")
            self.assertNotIn("9000", content)
            self.assertNotIn("SECRET", content)

    def test_invalid_filters(self):
        for params in ({"preset": "bad"}, {"preset": "custom"}, {"start": "bad"},
                       {"preset": "custom", "start": "2026-10-23", "end": "2026-10-23"},
                       {"preset": "custom", "start": "2026-10-22", "end": "2026-10-20"},
                       {"preset": "custom", "start": "2020-01-01", "end": "2026-01-01"},
                       {"preset": "custom", "start": "0001-01-01", "end": "0001-01-01"},
                       {"aggregation": "hourly"}, {"page": "0"}, {"page_size": "101"},
                       {"sort": "customer_phone_hash"}, {"report": "profit"},
                       {"as_of": "2030-01-01T00:00:00Z"}, {"search": "x" * 201}):
            with self.subTest(params=params):
                self.get(params, status=400)

    def test_default_is_30_days_and_custom_completed_days(self):
        with patch("dashboard.analytics.timezone.now", return_value=NOW):
            data = self.client.get("/dashboard/api/analytics/", {"version": "2"}).json()
        self.assertEqual(data["meta"]["start"], "2026-09-23")
        self.assertEqual(len(data["trends"]), 30)
        data = self.get({"preset": "custom", "start": "2026-09-01", "end": "2026-09-30"}).json()
        self.assertEqual(data["meta"]["previous_start"], "2026-08-02")
        self.assertFalse(data["meta"]["partial"])

    def test_aggregation_keeps_totals_and_clips_buckets(self):
        self.order(when=NOW - timedelta(days=20))
        self.order()
        for aggregation in ("daily", "weekly", "monthly"):
            data = self.get({"preset": "30d", "aggregation": aggregation}).json()
            self.assertEqual(sum(r["orders"] for r in data["trends"]), 2)
            self.assertEqual(sum(Decimal(r["revenue"]) for r in data["trends"]), Decimal("51.00"))
            self.assertEqual(data["trends"][0]["start"], "2026-09-23")
            self.assertEqual(data["trends"][-1]["end"], "2026-10-22")

    @override_settings(TIME_ZONE="Africa/Cairo")
    def test_local_date_boundary_and_dst_windows(self):
        self.order(when=datetime(2026, 10, 21, 21, tzinfo=dt_timezone.utc))
        data = self.get().json()
        self.assertEqual(data["kpis"]["orders"], 1)
        self.assertEqual(data["meta"]["timezone"], "Africa/Cairo")
        f = Filters.parse({"preset": "custom", "start": "2026-10-29", "end": "2026-10-30"}, now=datetime(2026, 11, 2, tzinfo=dt_timezone.utc))
        self.assertEqual(f.start.utcoffset(), timedelta(hours=3))
        self.assertEqual(f.end.utcoffset(), timedelta(hours=2))
        self.assertEqual((f.end.astimezone(dt_timezone.utc) - f.start.astimezone(dt_timezone.utc)).total_seconds(), 49 * 3600)

    def test_leap_day(self):
        f = Filters.parse({"preset": "custom", "start": "2024-02-28", "end": "2024-03-01"}, now=NOW)
        self.assertEqual(f.days, 3)
        self.assertEqual(f.previous_start.date().isoformat(), "2024-02-25")

    def test_product_rename_delete_and_order_display(self):
        order = self.order()
        self.product.name = "Renamed"
        self.product.save()
        self.order()
        self.assertEqual(self.get().json()["products"]["total"], 1)
        self.product.delete()
        self.assertEqual(OrderItem.objects.count(), 2)
        data = self.get().json()
        self.assertEqual(data["products"]["total"], 1)
        self.assertEqual(data["products"]["rows"][0]["name"], "Renamed")
        self.assertEqual(data["products"]["rows"][0]["units"], 6)
        response = self.client.get("/api/orders/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders"][1]["items"][0]["product_name"], "عود")
        self.assertIn("عود", str(order.items.first()))

    def test_cancellation_after_variant_deletion_preserves_item_and_total(self):
        from products.services.order_service import change_order_status
        order = self.order(status="pending")
        OrderItem.objects.filter(order=order).update(bottle_type="original")
        self.variant.delete()
        change_order_status(order.pk, self.store, "cancelled")
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.total_price, Decimal("25.50"))

    def test_snapshot_cannot_capture_other_store_product(self):
        other_order = self.order(store=self.other, item=False)
        with self.assertRaises(ValueError):
            OrderItem.objects.create(order=other_order, variant=self.variant, quantity=1, price_at_time_of_order=1)

    def test_export_limit_rejects_instead_of_truncating(self):
        order = self.order(item=False)
        products = Product.objects.bulk_create([
            Product(store=self.store, brand=self.product.brand, name=f"Product {i}", gender="unisex") for i in range(10001)
        ])
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product_snapshot_id=p.pk, product_snapshot_name=p.name,
                      quantity=1, price_at_time_of_order=1) for p in products
        ])
        self.get({"report": "products"}, "export/", status=400)

    def test_csv_matches_json_and_has_unique_headers(self):
        self.order()
        data = self.get().json()
        response = self.get({"report": "trends", "as_of": data["meta"]["as_of"]}, "export/")
        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
        self.assertEqual(len(reader.fieldnames), len(set(reader.fieldnames)))
        row = next(reader)
        self.assertEqual(Decimal(row["revenue"]), Decimal(data["trends"][0]["revenue"]))
        self.assertEqual(row["filter_start"], data["meta"]["start"])

    def test_search_sort_pagination_and_export_all_matches(self):
        self.order()
        second = Product.objects.create(store=self.store, name="Amber", brand=self.product.brand, gender="unisex")
        variant = ProductVariant.objects.create(product=second, volume=100, price=100)
        OrderItem.objects.create(order=self.order(item=False), variant=variant, quantity=1, price_at_time_of_order=100)
        first = self.get({"page_size": 1, "sort": "name"}, "products/").json()
        second_page = self.get({"page_size": 1, "sort": "name", "page": 2}, "products/").json()
        self.assertEqual(first["rows"][0]["name"], "Amber")
        self.assertEqual(second_page["rows"][0]["name"], "عود")
        self.assertEqual(self.get({"search": "not-found"}, "products/").json()["rows"], [])
        exported = self.get({"report": "products", "page_size": 1}, "export/")
        self.assertEqual(len(list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))), 2)

    def test_csv_filters_unicode_formula_quoting_and_empty_headers(self):
        self.product.name = '=HYPERLINK("bad")،عود\nsecond'
        self.product.save()
        self.order()
        result = self.get({"report": "products", "aggregation": "weekly"}, "export/")
        self.assertTrue(result.content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(result.content.decode("utf-8-sig"))))
        self.assertTrue(rows[0]["name"].startswith("'="))
        self.assertEqual(rows[0]["currency"], "EGP")
        self.assertEqual(rows[0]["aggregation"], "weekly")
        result = self.get({"report": "products", "search": "missing"}, "export/")
        self.assertIn("revenue", result.content.decode("utf-8-sig"))
        empty = next(csv.DictReader(io.StringIO(result.content.decode("utf-8-sig"))))
        self.assertEqual(empty["search"], "missing")
        self.assertEqual(empty["name"], "")

    def test_no_authentication_and_legacy_contract(self):
        client = APIClient()
        for path in ("", "products/", "export/"):
            self.assertEqual(client.get("/dashboard/api/analytics/" + path).status_code, 401)
        legacy = self.client.get("/dashboard/api/analytics/").json()
        self.assertIn("period_comparisons", legacy)
        self.assertIn("total_revenue", legacy["kpis"])
        self.get({"version": "9"}, status=400)

    def test_queries_are_bounded_and_gets_are_read_only(self):
        self.order()
        with CaptureQueriesContext(connection) as small:
            self.get()
        for _ in range(15):
            self.order()
        with CaptureQueriesContext(connection) as large:
            self.get()
        self.assertEqual(len(small), len(large))
        self.assertLess(len(large), 30)
        for query in large:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))


class AnalyticsHistoryMigrationTests(TransactionTestCase):
    def test_existing_items_backfill_without_prices_changing(self):
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        old_target = [("products", "0039_handoff_attention")]
        target = [("products", "0040_analytics_history")]
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            StoreOld = apps.get_model("products", "Store")
            store = StoreOld.objects.create(name="Legacy")
            brand = apps.get_model("products", "Brand").objects.create(store=store, name="Legacy")
            product = apps.get_model("products", "Product").objects.create(store=store, brand=brand, name="Original name", gender="unisex")
            variant = apps.get_model("products", "ProductVariant").objects.create(product=product, volume=50, price=900)
            order = apps.get_model("products", "Order").objects.create(store=store, customer_name="Old", customer_phone="", shipping_address="", total_price=25)
            item = apps.get_model("products", "OrderItem").objects.create(order=order, variant=variant, quantity=2, price_at_time_of_order="12.50")
            executor = MigrationExecutor(connection)
            executor.migrate(target)
            retained = OrderItem.objects.get(pk=item.pk)
            self.assertEqual(retained.product_snapshot_id, product.pk)
            self.assertEqual(retained.display_name, "Original name")
            self.assertEqual(retained.display_volume, 50)
            self.assertEqual(retained.price_at_time_of_order, Decimal("12.50"))
            ProductVariant.objects.get(pk=variant.pk).delete()
            retained.refresh_from_db()
            self.assertIsNone(retained.variant_id)
            self.assertEqual(retained.product_snapshot_id, product.pk)
        finally:
            MigrationExecutor(connection).migrate(target)
