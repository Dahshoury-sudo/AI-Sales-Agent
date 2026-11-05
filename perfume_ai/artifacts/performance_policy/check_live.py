"""Live model checks against disposable local data; nothing is delivered to customers."""
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from unittest.mock import patch
from urllib.parse import urlparse
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import dotenv_values

url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
if urlparse(url or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("A local TEST_DATABASE_URL is required")
os.environ["TEST_DATABASE_URL"] = url
os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
import django
django.setup()
from django.conf import settings
from django.db import connections
from django.test.runner import DiscoverRunner
from perfume_ai import settings as configured

settings.OPENAI_SMART_MODEL = configured.OPENAI_SMART_MODEL
settings.OPENAI_RESOLVER_MODEL = configured.OPENAI_RESOLVER_MODEL
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_performance_live_" + uuid.uuid4().hex[:10]

from products.models import Brand, Conversation, Product, ProductVariant, StaticFAQ, Store, StoreSettings
from products.services.ai import client as ai_client
from products.services.conversation_service import build_llm_history, save_message
from products.services.product_formatting import format_products
from products.services.router import route


CASES = [
    {"id": "general_quality", "message": "ثبات عطوركم وفوحانها عاملين إيه؟", "excellent": True},
    {"id": "general_hours", "message": "عطوركم بتثبت كام ساعة؟", "hours": True},
    {"id": "named_quality", "message": "ثبات Cedar Day وفوحانه إيه؟", "excellent": True},
    {"id": "named_hours", "message": "Cedar Day بيثبت كام ساعة؟", "hours": True},
    {"id": "blank_record", "message": "ثبات Blank Fragrance وفوحانه إيه؟", "excellent": True},
    {"id": "duration_followup", "message": "طيب كام ساعة؟", "previous": "Cedar Day ثباته وفوحانه ممتازين.", "hours": True},
    {"id": "number_followup", "message": "قولي رقم", "previous": "Cedar Day ثباته ممتاز.", "hours": True},
    {"id": "projection_hours", "message": "فوحان Cedar Day بيفضل كام ساعة؟", "excellent": True},
    {"id": "projection_followup", "message": "كام ساعة بقى؟", "previous": "Cedar Day فوحانه ممتاز.", "excellent": True},
    {"id": "projection_distance", "message": "فوحان Cedar Day بيوصل كام متر؟", "excellent": True},
    {"id": "comparison_quality", "message": "قارن ثبات وفوحان Cedar Day وAmber Night", "excellent": True},
    {"id": "comparative_quality", "message": "مين أثبت Cedar Day ولا Amber Night؟", "excellent": True},
    {"id": "comparison_hours", "message": "Cedar Day وAmber Night كل واحد ثابت كام ساعة؟", "hours": True},
    {"id": "unsupported_threshold", "message": "عايز عطر فريش يثبت 48 ساعة", "hours": True},
    {"id": "saved_preferences", "message": "رشحلي عطر فريش للشغل", "preferences": {"longevity": "48 hours", "projection": "intimate"}},
    {"id": "price_size_hours", "message": "Cedar Day حجم 50 مل بكام وثباته كام ساعة؟", "hours": True, "price": True},
    {"id": "price_number_only", "message": "ثبات Cedar Day كويس؟ وسعر حجم 50 مل كام؟", "excellent": True, "price": True},
    {"id": "size_number_only", "message": "ثبات Cedar Day كويس وحجمه كام؟", "excellent": True},
    {"id": "light_scent", "message": "عايز عطر فريش وخفيف وفواح للشغل", "avoid": "Amber Night"},
    {"id": "static_faq", "message": "الثبات كام ساعة؟", "hours": True, "faq": True},
    {"id": "english_hours", "message": "How many hours does Cedar Day last?", "hours": True},
    {"id": "external_unknown", "message": "ثبات Imaginary Outside كام ساعة؟", "external": True},
    {"id": "past_experience", "message": "جربت Cedar Day وثباته كان ساعتين ومش راضي عنه"},
    {"id": "later_scent_question", "message": "حلو، ريحته عاملة إيه؟", "previous": "Cedar Day ثباته أكتر من 8 ساعات."},
]


def normalize(text):
    return text.lower().translate(str.maketrans("أإآىة٠١٢٣٤٥٦٧٨٩", "ااايه0123456789"))


def unsupported_performance_number(reply, message):
    """The customer's reported experience is not a new claim about store stock."""
    for clause in re.split(r"[.!?؟\n؛;،,]", normalize(reply)):
        for measured in re.finditer(r"\d+\s*(?:ساعه|ساعات|hours?|متر|meters?)|ساعتين|يومين", clause):
            reported = re.search(r"تجربتك|بتقول|انت قلت|حضرتك قلت|your experience|you said", clause)
            if not reported or measured.group() not in normalize(message):
                return True
    return False


def recheck_report():
    path = Path(__file__).with_name("live-results.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    for row in report["results"]:
        remaining = normalize(row["reply"])
        if row.get("hours"):
            remaining = re.sub(r"(?:اكتر|اكثر) من 8 ساعات", "", remaining)
        errors = [error for error in row["errors"] if error != "unsupported_performance_number"]
        if unsupported_performance_number(remaining, row["message"]):
            errors.append("unsupported_performance_number")
        row["original_check_errors"] = row["errors"]
        row["errors"] = errors
        row["passed"] = not errors
    report["passed"] = sum(row["passed"] for row in report["results"])
    report["review"] = "Rechecked the same captured live outputs with customer reports distinguished from new store claims."
    path.with_name("reviewed-live-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"completed": report["completed"], "total": report["total"], "passed": report["passed"]}))
    return int(report["passed"] != report["total"])


def main():
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_db = runner.setup_databases()
    rows = []
    started = monotonic()
    try:
        store = Store.objects.create(name="Policy Test Store")
        StoreSettings.objects.create(store=store)
        brand = Brand.objects.create(store=store, name="Test House")
        products = []
        for name, longevity, projection, tags, price in (
            ("Cedar Day", "4 hours", "Weak", ["fresh", "woody", "light"], 500),
            ("Amber Night", "12-24 hours", "Enormous", ["fresh", "woody", "heavy"], 650),
            ("Blank Fragrance", "", "", ["fresh", "clean"], 450),
        ):
            product = Product.objects.create(store=store, brand=brand, name=name, gender="male",
                                             occasion="Office, Daily", season="All Seasons", scent_tags=tags,
                                             top_notes="Bergamot", base_notes="Cedar", longevity=longevity,
                                             projection=projection)
            ProductVariant.objects.create(product=product, volume=50, price=price, bottle_type="normal")
            products.append(product)
        limited = ai_client.client.with_options(timeout=35, max_retries=0)
        create = limited.chat.completions.create
        with patch.object(ai_client, "client", limited), patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected outbound delivery")):
            for case in CASES:
                drafts, errors = [], []

                def record(*args, **kwargs):
                    try:
                        response = create(*args, **kwargs)
                        drafts.append(response.choices[0].message.content)
                        return response
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                        raise

                conversation = Conversation.objects.create(store=store, preferences={
                    "gender": "male", **case.get("preferences", {}),
                })
                if case.get("previous"):
                    save_message(conversation, "assistant", case["previous"], internal_context=format_products([products[0]]))
                faq = StaticFAQ.objects.create(store=store, question="Performance", keywords="الثبات",
                                               answer="كل عطورنا ثباتها 24 ساعة وفوحانها أول 6 ساعات قوي.") if case.get("faq") else None
                begun = monotonic()
                reply = context = ""
                try:
                    with patch.object(limited.chat.completions, "create", side_effect=record):
                        reply, context = route(case["message"], build_llm_history(conversation), store, conversation)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    if faq:
                        faq.delete()
                text = normalize(reply)
                allowed = bool(re.search(r"(?:اكتر|اكثر) من 8 ساعات", text))
                remaining = re.sub(r"(?:اكتر|اكثر) من 8 ساعات", "", text) if case.get("hours") else text
                if not reply.strip():
                    errors.append("empty_reply")
                if case.get("hours") and not allowed:
                    errors.append("missing_more_than_eight")
                if unsupported_performance_number(remaining, case["message"]):
                    errors.append("unsupported_performance_number")
                if case.get("excellent") and "ممتاز" not in text:
                    errors.append("missing_excellent")
                if case.get("external") and (allowed or "ممتاز" in text):
                    errors.append("claim_about_external_perfume")
                if case.get("price") and ("500" not in text or "50" not in text):
                    errors.append("lost_price_or_size")
                if case.get("avoid") and case["avoid"] in reply:
                    errors.append("wrong_scent_character")
                row = {**case, "reply": reply, "model_outputs": drafts, "errors": errors,
                       "passed": not errors, "seconds": round(monotonic() - begun, 2)}
                rows.append(row)
                print(json.dumps({"id": case["id"], "reply": reply, "errors": errors}, ensure_ascii=False), flush=True)
                Path(__file__).with_name("live-results.json").write_text(json.dumps({
                    "model": settings.OPENAI_MODEL, "completed": len(rows), "total": len(CASES),
                    "passed": sum(row["passed"] for row in rows), "results": rows,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if any(error.endswith("Error") and error != "AssertionError" for error in errors):
                    break
    finally:
        connections.close_all()
        runner.teardown_databases(old_db)
    summary = {"completed": len(rows), "total": len(CASES), "passed": sum(row["passed"] for row in rows),
               "seconds": round(monotonic() - started, 2)}
    print(json.dumps(summary), flush=True)
    return int(summary["passed"] != summary["total"])


if __name__ == "__main__":
    raise SystemExit(recheck_report() if "--recheck" in sys.argv else main())
