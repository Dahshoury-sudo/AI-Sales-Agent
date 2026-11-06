"""Replay the saved chat and approved store wording in disposable SQLite.

Only model HTTP is allowed. Customer delivery and background tasks are disabled.
The source and before/after store fields live in artifacts/conversation1337.
"""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TARGETS = {"gender": 8113, "price_objection": 8119, "niche": 8121,
           "oils": 8123, "longevity": 8125, "projection": 8127}


def normalized(text):
    return text.lower().translate(str.maketrans("أإآىة", "ااايه"))


def check_reply(case, reply, name):
    text = normalized(reply)
    errors = []
    if not reply.strip():
        errors.append("Empty reply")
    if re.search(r"حسب\s+(?:تصنيف|المتوفر|الداتا|البيانات|معلومات)|مصن[ّ]?ف\s+عندنا", text):
        errors.append("Source qualifier")
    if re.search(r"(?:90|95)\s*[%٪]", text):
        errors.append("Old similarity percentage")
    if case in TARGETS and name.lower() in text:
        errors.append("Unnecessary product name in a clear follow-up")
    if case == "gender" and "رجالي" not in text:
        errors.append("Wrong or missing gender answer")
    if case == "projection" and "فوحانه قوي" not in text:
        errors.append("Missing strong projection wording")
    if case == "longevity" and "اكتر من 8 ساعات" not in text:
        errors.append("Wrong longevity duration")
    if case in ("price_objection", "single_size", "exclusive"):
        if "زيوت" not in text or "غالي" not in text:
            errors.append("Missing oil cost explanation")
        if "جنيه" in text or re.search(r"\d+(?:\.\d+)?\s*EGP", reply, re.I):
            errors.append("Unrequested price quotation")
        if case == "price_objection":
            if not re.search(r"اوريج|اورجين", text) or not re.search(r"بالظبط|بالضبط|نفس", text):
                errors.append("Missing approved original-scent wording")
            if not re.search(r"صغير|اصغر|50", text):
                errors.append("Missing available smaller option")
        elif case == "exclusive" and re.search(r"اوريج|اورجين", text):
            errors.append("Exclusive perfume compared to an original")
        elif case == "single_size" and re.search(r"(?:جرب|تجرب|اعرض|نعرض).*(?:صغير|اصغر|عينه)", text):
            errors.append("Invented smaller option")
    if case == "oils" and not re.search(r"خاصه|مش بنشارك|تفاصيل خاصه", text):
        errors.append("Supplier privacy answer missing")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=[*TARGETS, "single_size", "exclusive", "continuous"])
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/conversation1337/replay.json")
    args = parser.parse_args()
    folder = ROOT / "artifacts/conversation1337"
    source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    updates = json.loads((folder / "store-wording-update.json").read_text(encoding="utf-8"))
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
    os.environ["TEST_DATABASE_URL"] = "sqlite:///:memory:"
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection, transaction
    from products.models import Brand, Conversation, Product, ProductVariant, StaticFAQ, Store, StoreSettings
    from products.services.ai.client import client
    from products.services.conversation_service import build_llm_history, save_message
    from products.services.product_formatting import format_product
    from products.services.reply_sanitizer import sanitize_reply
    from products.services.router import route
    import httpx

    settings.OPENAI_SMART_MODEL = os.environ.get("OPENAI_SMART_MODEL")
    settings.OPENAI_RESOLVER_MODEL = os.environ.get("OPENAI_RESOLVER_MODEL")
    assert connection.settings_dict["NAME"] == ":memory:"
    call_command("migrate", verbosity=0)
    store = Store.objects.create(pk=1, name="Perfamix")
    config = {key: value for key, value in source["settings"][0].items() if key != "id"}
    config["business_facts"] = next(row["after"] for row in updates if row["field"] == "business_facts")
    StoreSettings.objects.create(store=store, **config)
    for row in source["brands"]:
        Brand.objects.create(store=store, **row)
    for row in source["products"]:
        fields = {key: value for key, value in row.items() if key not in ("created_at", "category_id")}
        Product.objects.create(**fields)
    for row in source["variants"]:
        ProductVariant.objects.create(**row)
    for row in source["faqs"]:
        if row["id"] == 21:
            row = {**row, "answer": next(update["after"] for update in updates if update["field"] == "answer")}
        StaticFAQ.objects.create(store=store, **row)
    product = Product.objects.get(store=store, name="Alexandria II")
    record = {"database": "isolated in-memory SQLite", "source_conversation": 1337,
              "models": {key: getattr(settings, key) for key in
                         ("OPENAI_MODEL", "OPENAI_SMART_MODEL", "OPENAI_RESOLVER_MODEL")}, "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def denied(*unused, **kwargs):
        raise AssertionError("Replay attempted customer delivery or background work")

    original_send = httpx.Client.send
    model_host = client.base_url.host

    def only_model_http(session, request, *send_args, **kwargs):
        if request.url.host != model_host:
            raise AssertionError("Replay attempted non-model HTTP")
        return original_send(session, request, *send_args, **kwargs)

    def turn(conversation, message, case, name=product.name, mode="original"):
        row = {"case": case, "mode": mode, "message": message, "errors": []}
        history = build_llm_history(conversation)
        save_message(conversation, "user", message)
        try:
            reply, context = route(message, history, store, conversation)
            reply = sanitize_reply(reply, conversation)
            save_message(conversation, "assistant", reply, internal_context=context)
            row.update(reply=reply, context=context, errors=check_reply(case, reply, name))
        except Exception as error:
            row["errors"].append(type(error).__name__ + ": " + str(error))
        record["runs"].append(row)
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: row[key] for key in ("case", "mode", "errors")}, ensure_ascii=False), flush=True)

    wanted = set(args.case or [*TARGETS, "single_size", "exclusive", "continuous"])
    with ExitStack() as guard:
        guard.enter_context(patch("requests.sessions.Session.request", side_effect=denied))
        guard.enter_context(patch("httpx.Client.send", new=only_model_http))
        guard.enter_context(patch("httpx.AsyncClient.send", side_effect=denied))
        guard.enter_context(patch("celery.app.task.Task.apply_async", side_effect=denied))
        guard.enter_context(patch("smtplib.SMTP", side_effect=denied))
        for case, message_id in TARGETS.items():
            if case not in wanted:
                continue
            with transaction.atomic():
                conversation = Conversation.objects.create(store=store)
                for row in source["messages"]:
                    if row["id"] >= message_id:
                        break
                    save_message(conversation, row["role"], row["content"],
                                 internal_context=row["internal_context"], delivery_status=row["delivery_status"])
                message = next(row["content"] for row in source["messages"] if row["id"] == message_id)
                turn(conversation, message, case)
                transaction.set_rollback(True)
        for case in ("single_size", "exclusive"):
            if case not in wanted:
                continue
            with transaction.atomic():
                chosen = product
                if case == "single_size":
                    ProductVariant.objects.filter(product=chosen).exclude(volume=90, bottle_type="normal").delete()
                else:
                    chosen = Product.objects.filter(store=store, brand__name=store.name, is_active=True).first()
                    assert chosen is not None, "Snapshot needs an exclusive perfume"
                conversation = Conversation.objects.create(store=store)
                save_message(conversation, "assistant", chosen.name + " متوفر.", internal_context=format_product(chosen))
                turn(conversation, "غالي ليه كدا؟", case, name=chosen.name, mode="boundary")
                transaction.set_rollback(True)
        if "continuous" in wanted:
            with transaction.atomic():
                conversation = Conversation.objects.create(store=store)
                steps = [("availability", 8111), ("gender", 8113), ("prices", 8117),
                         ("price_objection", 8119), ("niche", 8121), ("oils", 8123),
                         ("longevity", 8125), ("projection", 8127)]
                for case, message_id in steps:
                    message = next(row["content"] for row in source["messages"] if row["id"] == message_id)
                    turn(conversation, message, case, mode="continuous")
                transaction.set_rollback(True)
    return int(any(row["errors"] for row in record["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
