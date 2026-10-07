"""Validate the saved live conversations against a disposable local PostgreSQL DB.

python scripts/verify_bug_fixes.py --runs 3
Only the configured model provider is contacted. Customer delivery is disabled.
The source catalogue/settings are read in a read-only transaction, never modified.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cases", default=",".join(f"L{i:02}" for i in range(1, 14)))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "bug_fix_validation")
    args = parser.parse_args()
    destination = args.output.resolve()
    originals = (ROOT / "artifacts" / "bug_reproductions").resolve()
    if destination == originals or originals in destination.parents:
        raise SystemExit("Original reproduction evidence must not be overwritten.")
    import dj_database_url
    from dotenv import dotenv_values
    config = dotenv_values(ROOT / ".env")
    url = os.environ.get("TEST_DATABASE_URL") or config.get("TEST_DATABASE_URL")
    database = dj_database_url.parse(url or "")
    if database.get("HOST") not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("TEST_DATABASE_URL must select local PostgreSQL.")
    os.environ["DATABASE_URL"] = url
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings"
    import django
    django.setup()
    from django.conf import settings
    from django.db import connections, transaction
    from django.test.runner import DiscoverRunner
    from products.models import Store, StoreSettings, Brand, Category, Product, ProductVariant, StaticFAQ, Conversation, Cart, Order
    from products.services.conversation_service import save_message, build_llm_history
    from products.services.router import route
    from products.services.reply_sanitizer import sanitize_reply

    # Explicitly read-only; only catalogue and public store facts are copied.
    with transaction.atomic():
        with connections["default"].cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        source = Store.objects.get(name="Perfamix")
        public_settings = StoreSettings.objects.filter(store=source).values(
            "system_prompt", "business_facts", "payment_instructions", "bottle_image_url").first() or {}
        brands = list(Brand.objects.filter(store=source).values())
        categories = list(Category.objects.filter(store=source).values())
        products = list(Product.objects.filter(store=source).values())
        variants = list(ProductVariant.objects.filter(product__store=source).values())
        faqs = list(StaticFAQ.objects.filter(store=source).values())
    connections.close_all()
    settings.DATABASES["default"]["TEST"]["NAME"] = "test_perfume_live_" + uuid.uuid4().hex[:10]
    runner = DiscoverRunner(verbosity=0, interactive=False)
    runner.setup_test_environment()
    old = runner.setup_databases()
    destination.mkdir(parents=True, exist_ok=True)
    failures = []
    try:
        for attempt in range(1, args.runs + 1):
            for code in args.cases.split(","):
                fixture = json.loads((ROOT / "artifacts" / "bug_reproductions" / f"{code}.json").read_text(encoding="utf-8"))
                record = {"case": code, "run": attempt, "turns": [], "errors": []}
                with transaction.atomic(), ExitStack() as stack:
                    stack.enter_context(patch("requests.sessions.Session.request", side_effect=RuntimeError("Customer transport disabled during replay")))
                    store = Store.objects.create(name="Perfamix")
                    StoreSettings.objects.create(store=store, **public_settings)
                    brand_ids, category_ids, product_ids = {}, {}, {}
                    for row in brands:
                        data = {k: v for k, v in row.items() if k not in ("id", "store_id")}
                        brand_ids[row["id"]] = Brand.objects.create(store=store, **data).pk
                    for row in categories:
                        data = {k: v for k, v in row.items() if k not in ("id", "store_id")}
                        category_ids[row["id"]] = Category.objects.create(store=store, **data).pk
                    for row in products:
                        data = {k: v for k, v in row.items() if k not in ("id", "store_id", "brand_id", "category_id", "created_at", "updated_at")}
                        data.update(brand_id=brand_ids[row["brand_id"]], category_id=category_ids.get(row.get("category_id")))
                        product_ids[row["id"]] = Product.objects.create(store=store, **data).pk
                    for row in variants:
                        data = {k: v for k, v in row.items() if k not in ("id", "product_id")}
                        ProductVariant.objects.create(product_id=product_ids[row["product_id"]], **data)
                    for row in faqs:
                        data = {k: v for k, v in row.items() if k not in ("id", "store_id")}
                        StaticFAQ.objects.create(store=store, **data)
                    conversation = Conversation.objects.create(store=store)
                    for number, entry in enumerate((m for m in fixture["transcript"] if m["role"] == "user"), 1):
                        history = build_llm_history(conversation)
                        save_message(conversation, "user", entry["content"])
                        try:
                            reply, context = route(entry["content"], history, store, conversation)
                            reply = sanitize_reply(reply, conversation)
                            save_message(conversation, "assistant", reply, internal_context=context)
                            conversation.refresh_from_db()
                            cart = Cart.objects.filter(conversation=conversation).first()
                            state = {"preferences": conversation.preferences, "sales_state": conversation.sales_state,
                                "pending": cart.pending_items if cart else [],
                                "items": list(cart.items.values("variant__product__name", "variant__product_id", "quantity", "variant__volume")) if cart else [],
                                "contacts": {key: getattr(cart, key) for key in ("customer_phone", "secondary_phone")} if cart else {},
                                "orders": list(Order.objects.filter(conversation=conversation).values("total_price"))}
                            record["turns"].append({"user": entry["content"], "reply": reply, "state": state})
                            print(json.dumps({"case": code, "run": attempt, "turn": number, "reply": reply}, ensure_ascii=False), flush=True)
                        except Exception as exc:
                            record["errors"].append(type(exc).__name__ + ": " + str(exc))
                            break
                    record["errors"] += validate(code, record["turns"])
                    (destination / f"{code}_run{attempt}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                    if record["errors"]:
                        failures.append({"case": code, "run": attempt, "errors": record["errors"]})
                    transaction.set_rollback(True)
    finally:
        runner.teardown_databases(old)
        runner.teardown_test_environment()
    (destination / "live_summary.json").write_text(json.dumps({"runs": args.runs, "cases": args.cases.split(","), "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"failures": failures}, ensure_ascii=False))
    return bool(failures)


def validate(code, turns):
    errors = []
    def require(condition, description):
        if not condition:
            errors.append(description)
    if not turns:
        return ["No completed turn"]
    final = turns[-1]["state"]
    if code == "L01" and len(turns) >= 2:
        state = turns[1]["state"]
        require(any(i["quantity"] == 2 for i in state["items"]), "Selected quantity lost")
        require(any(i.get("quantity") == 3 for i in state["pending"]), "Second pending quantity lost")
    if code == "L02":
        require(any(p["name"] == "Mystery Azure" for p in final["pending"]), "Unresolved product lost")
        require(not final["orders"], "Unresolved cart confirmed")
    if code == "L03":
        require(not final["orders"], "Edited quote confirmed without renewed approval")
    if code == "L04":
        require(not final["items"], "Over-budget basket constructed")
        require(all("كان لعطر واحد" not in t["reply"] for t in turns), "Total budget reinterpreted")
        require(all(t["state"]["preferences"].get("bottle_type") == "normal" and t["state"]["preferences"].get("max_price") == 1000 for t in turns), "Original bottle choice or total budget changed")
    if code == "L05":
        require("944" not in turns[0]["reply"], "Offered a variant above the exact ceiling")
    if code == "L06":
        require("ماقدرش أجزم" in turns[-1]["reply"], "Unsupported sweetness ranking")
    if code == "L07":
        reply = turns[0]["reply"]
        require(all(x in reply for x in ("Dior Sauvage", "Bleu de Chanel", "الثبات", "الفوحان", "سعر المل")), "Incomplete comparison")
    if code == "L09":
        require(final["preferences"].get("wants_uncommon") is False, "False preference not retained")
    if code == "L10":
        require(not final["preferences"].get("max_price") and not final["preferences"].get("projection"), "Old recipient preferences retained")
    if code == "L11":
        offered = turns[0]["state"]["sales_state"].get("offered", {}).get("product_ids", [])
        require(len(offered) >= 2 and any(i.get("variant__product_id") == offered[1] for i in final["items"]), "Second offered product not selected after FAQs")
    if code == "L12" and len(turns) >= 2:
        contacts = turns[1]["state"].get("contacts", {})
        require(contacts.get("customer_phone") == "01000000000" and contacts.get("secondary_phone") == "01100000000", "Checkout contact details intercepted")
    # L08/L13 are controls. Saved replies remain available for authenticity review;
    # their successful run is not asserted as a reproduced authenticity defect.
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
