"""Run real-API browser checks on a disposable database; never use app data.

Requires PLAYWRIGHT_MODULE when Playwright is outside Node's normal search path.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
os.environ["TEST_DATABASE_URL"] = "sqlite:///:memory:"


def main():
    with tempfile.TemporaryDirectory(prefix="perfume-analytics-test-") as directory:
        from django.conf import settings
        settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(Path(directory) / "test.sqlite3")}}
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["127.0.0.1"]
        settings.SECURE_SSL_REDIRECT = False
        settings.STATIC_ROOT = str(Path(directory) / "static")
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth.models import User
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        from django.utils import timezone
        from datetime import timedelta
        from products.models import Store, StoreSettings, Brand, Product, ProductVariant, Conversation, Order, OrderItem
        call_command("migrate", verbosity=0, interactive=False)
        now = timezone.now()
        for index in (1, 2):
            user = User.objects.create_user(username=f"analytics{index}@example.test", email=f"analytics{index}@example.test", password="browser-test-only", first_name="متجر اختبار")
            store = Store.objects.create(owner=user, name=f"متجر اختبار {index}")
            StoreSettings.objects.create(store=store)
            brand = Brand.objects.create(store=store, name="Test brand")
            product = Product.objects.create(store=store, brand=brand, name="عود الاختبار" if index == 1 else "FOREIGN-STORE-PRODUCT", gender="unisex")
            variant = ProductVariant.objects.create(product=product, volume=50, price=999)
            for i, (status, amount, platform) in enumerate((("confirmed", 100, "web"), ("delivered", 200, "whatsapp"), ("pending", 900, "instagram"))):
                amount = amount if index == 1 else amount * 10
                conv = Conversation.objects.create(store=store, platform=platform)
                Conversation.objects.filter(pk=conv.pk).update(created_at=now - timedelta(days=5 - i, hours=1))
                order = Order.objects.create(store=store, status=status, total_price=amount, conversation=conv, customer_name="Test", customer_phone=f"0100000000{i}", shipping_address="Test")
                Order.objects.filter(pk=order.pk).update(created_at=now - timedelta(days=5 - i))
                OrderItem.objects.create(order=order, variant=variant, quantity=1, price_at_time_of_order=amount)
        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server("127.0.0.1", 8767, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
        def no_http(*args, **kwargs):
            raise AssertionError("Real-API smoke test attempted external HTTP")
        try:
            with patch("requests.sessions.Session.request", side_effect=no_http), patch("httpx.Client.send", side_effect=no_http), patch("httpx.AsyncClient.send", side_effect=no_http):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                result = subprocess.run(["node", str(ROOT / "scripts/dashboard_ui/check_analytics_live.cjs")], cwd=ROOT)
                return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            connections.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
