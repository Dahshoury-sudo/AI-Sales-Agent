"""Verify label editing and Excel round trips with real APIs on a disposable database."""
from contextlib import ExitStack
from io import BytesIO
import json
import os
from pathlib import Path
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
    import django
    import openpyxl
    from django.conf import settings
    from playwright.sync_api import sync_playwright, expect

    artifacts = ROOT / "artifacts" / "scent_labels" / "browser"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="perfume-scent-ui-") as directory:
        settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(Path(directory) / "test.sqlite3")}}
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["127.0.0.1"]
        settings.SECURE_SSL_REDIRECT = False
        settings.STATIC_ROOT = str(Path(directory) / "static")
        django.setup()
        from django.contrib.auth.models import User
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        from rest_framework_simplejwt.tokens import RefreshToken
        from products.models import Store, StoreSettings, Brand, Product, ProductVariant

        call_command("migrate", verbosity=0, interactive=False)
        owner = User.objects.create_user(username="scent-browser@example.test")
        store = Store.objects.create(owner=owner, name="Scent browser test")
        StoreSettings.objects.create(store=store)
        brand = Brand.objects.create(store=store, name="Test house")
        first = Product.objects.create(store=store, brand=brand, name="UI Fresh", gender="unisex", scent_tags=["fresh"])
        second = Product.objects.create(store=store, brand=brand, name="UI Second", gender="unisex")
        for product in (first, second):
            ProductVariant.objects.create(product=product, volume=50, price=500)
        other = Store.objects.create(name="Other store")
        Product.objects.create(store=other, brand=brand, name="Foreign product", scent_tags=["oud"])
        token = str(RefreshToken.for_user(owner).access_token)

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        def denied(*args, **kwargs):
            raise AssertionError("Browser workflow attempted server-side external HTTP")

        server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
        base = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with ExitStack() as stack:
                for target in ("requests.sessions.Session.request", "httpx.Client.send", "httpx.AsyncClient.send"):
                    stack.enter_context(patch(target, side_effect=denied))
                with sync_playwright() as playwright:
                    executable = os.environ.get("SCENT_BROWSER_EXECUTABLE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
                    browser = playwright.chromium.launch(executable_path=executable, headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.add_init_script("localStorage.setItem('access_token', " + json.dumps(token) + ");")
                    page.goto(base + "/dashboard/", wait_until="domcontentloaded")
                    page.locator("#nav-products").click()
                    row = page.locator("#products-tbody tr").filter(has_text="UI Fresh")
                    row.locator("button").first.click()
                    labels = page.locator('[name="edit-scent-tag"]')
                    expect(labels).to_have_count(16)
                    expect(page.locator('[name="edit-scent-tag"][value="fresh"]')).to_be_checked()
                    page.locator('[name="edit-scent-tag"][value="sweet"]').check()
                    page.locator('[name="edit-scent-tag"][value="woody"]').check()
                    page.locator("#edit-product-modal").screenshot(path=str(artifacts / "desktop-editor.png"))
                    page.locator('[onclick="saveProductEdit()"]').click()
                    expect(page.locator("#edit-product-modal")).to_be_hidden()
                    page.reload(wait_until="domcontentloaded")
                    page.locator("#nav-products").click()
                    row.locator("button").first.click()
                    expect(page.locator('[name="edit-scent-tag"][value="sweet"]')).to_be_checked()
                    expect(page.locator('[name="edit-scent-tag"][value="woody"]')).to_be_checked()

                    page.goto(base + "/api/import/", wait_until="domcontentloaded")
                    with page.expect_download() as event:
                        page.locator("#scent-export").click()
                    downloaded = artifacts / "export.xlsx"
                    event.value.save_as(str(downloaded))
                    workbook = openpyxl.load_workbook(downloaded)
                    sheet = workbook["Scent labels"]
                    assert {row[0] for row in list(sheet.values)[1:]} == {first.pk, second.pk}
                    assert sheet["D2"].value == "fresh, sweet, woody"
                    sheet["D2"] = "oud, heavy"
                    sheet["D3"] = "typo"
                    workbook.active = 1  # Saving after viewing the vocabulary must work.

                    def upload():
                        buffer = BytesIO()
                        workbook.save(buffer)
                        page.locator("#scent-file").set_input_files({"name": "labels.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": buffer.getvalue()})
                        with page.expect_response(lambda response: response.url.endswith("/api/import/scent-tags/") and response.request.method == "POST") as event:
                            page.locator("#scent-upload").click()
                        return event.value

                    assert upload().status == 400
                    expect(page.locator("#scent-status")).to_contain_text("typo")
                    with page.expect_download() as event:
                        page.locator("#scent-export").click()
                    unchanged = openpyxl.load_workbook(BytesIO(Path(event.value.path()).read_bytes()))
                    assert unchanged["Scent labels"]["D2"].value == "fresh, sweet, woody"
                    unchanged.close()
                    sheet["D3"] = "clean"
                    assert upload().json()["updated"] == 2
                    expect(page.locator("#scent-status")).to_contain_text("تم تحديث 2 عطر")
                    page.screenshot(path=str(artifacts / "excel-import.png"), full_page=True)
                    workbook.close()

                    page.goto(base + "/dashboard/", wait_until="domcontentloaded")
                    page.locator("#nav-products").click()
                    row.locator("button").first.click()
                    expect(page.locator('[name="edit-scent-tag"][value="oud"]')).to_be_checked()
                    expect(page.locator('[name="edit-scent-tag"][value="heavy"]')).to_be_checked()
                    expect(page.locator('[name="edit-scent-tag"][value="fresh"]')).not_to_be_checked()
                    page.set_viewport_size({"width": 375, "height": 900})
                    page.locator("#edit-product-modal fieldset").scroll_into_view_if_needed()
                    box = page.locator("#edit-product-modal fieldset").bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 375
                    page.screenshot(path=str(artifacts / "mobile-editor.png"))
                    for checkbox in labels.all():
                        checkbox.uncheck()
                    page.locator('[onclick="saveProductEdit()"]').click()
                    expect(page.locator("#edit-product-modal")).to_be_hidden()
                    browser.close()
            first.refresh_from_db()
            second.refresh_from_db()
            assert first.scent_tags == []
            assert second.scent_tags == ["clean"]
            assert first.variants.get().price == 500
            assert not errors, errors
            report = {"passed": True, "checks": ["desktop edit/save/reload", "store-scoped Excel export", "atomic invalid upload", "upload from vocabulary tab", "successful bulk update", "mobile layout", "clear labels"], "browser_errors": errors}
            (artifacts / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            connections.close_all()


if __name__ == "__main__":
    main()
