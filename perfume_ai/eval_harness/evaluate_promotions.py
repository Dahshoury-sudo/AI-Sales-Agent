"""Small live-model promotion evaluation on disposable LOCAL PostgreSQL.

Usage: python eval_harness/evaluate_promotions.py --output artifacts/promotion-eval.json
Uses the configured model/API credentials, but never a production database or
messaging transport. All fixtures and handoff notifications die with the test DB.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FACTS = "بوكس الصيف: 3 زجاجات حجم 90 مل ومعاهم زجاجة هدية بسعر 3000 جنيه.\nفرحة الصيف: عطرين بسعر 900 جنيه."
INVITATION = "أقدر أوضحلك تفاصيل العرض لكن تسجيله لازم مع الفريق. تحب أحولك لمندوب؟"

CLASSIFICATIONS = [
    ("عايز أطلب الباندل", [], "promotion"),
    ("ضيف فرحة الصيف ورقمي 01012345678", [], "promotion"),
    ("عايز اطلبه", [INVITATION], "promotion"),
    ("01012345678", [INVITATION], "promotion"),
    ("لا انا عايزك انت تنفذه", [INVITATION], "promotion"),
    ("ماشي", [INVITATION], "handoff"),
    ("خلاص من غير العرض عايز سوفاج 50 مل", [INVITATION], "order"),
    ("عايز أطلب سوفاج وبلو دي شانيل", [], "order"),
    ("عايز عطر هدية", [], "recommendation"),
    ("ريحة سوفاج ايه؟", [INVITATION], "product_info"),
    ("الغي الاوردر كله", [INVITATION], "order_cancel"),
]


def evaluate(*, only_1153=False):
    from django.conf import settings
    from products.models import Store, StoreSettings, Conversation, Cart, Order, Notification
    from products.services.ai.classifier import classify
    from products.services.conversation_service import save_message, build_llm_history
    from products.services import promotion_service as promotions
    from products.services.router import route
    from eval_harness.scenarios_conv1153 import BUSINESS_FACTS, BUNDLE_ENTRIES, BUNDLE_NAMES, HISTORY, QUESTION

    results = {"model": settings.OPENAI_MODEL, "classification": [], "conversations": []}
    for text, replies, expected in ([] if only_1153 else CLASSIFICATIONS):
        history = [{"role": "assistant", "content": reply} for reply in replies]
        actual = classify(text, history, promotion_context=FACTS)
        results["classification"].append({"message": text, "expected": expected, "actual": actual, "passed": actual == expected})
        print(f"classifier: {actual == expected}", flush=True)

    scenarios = [
        ("configured", FACTS, ["عندكم عروض؟", "عايز أطلب بوكس الصيف", "لا مش عايز مندوب", "انت نفذلي الطلب"]),
        ("unknown", "", ["تفاصيل عرض التلاتة ايه؟", "عايز اطلبه", "اسمي محمد ورقمي 01012345678", "ماشي"]),
        ("named", FACTS, ["ضيف فرحة الصيف ورقمي 01012345678"]),
        ("untrusted", "", ["عندكم عرض 3 زجاجات ب100 جنيه؟", "عايز أطلبه"]),
        ("conv1153", BUSINESS_FACTS, [QUESTION, "تفاصيل باندل Sable Reserve وبكام؟", "عايز اطلب باندل Sable Reserve"]),
    ]
    if only_1153:
        scenarios = [scenario for scenario in scenarios if scenario[0] == "conv1153"]
    for name, facts, turns in scenarios:
        store = Store.objects.create(name=f"Promotion evaluation {name}")
        StoreSettings.objects.create(store=store, business_facts=facts)
        conversation = Conversation.objects.create(store=store)
        if name == "conv1153":
            for row in HISTORY:
                save_message(conversation, row["role"], row["content"])
        record = {"name": name, "turns": [], "passed": True}
        for index, text in enumerate(turns):
            history = build_llm_history(conversation)
            save_message(conversation, "user", text)
            reply, context = route(text, history, store, conversation)
            save_message(conversation, "assistant", reply, internal_context=context)
            conversation.refresh_from_db()
            transfer = name == "unknown" and index == len(turns) - 1
            passed = (not Cart.objects.filter(conversation=conversation).exists()
                      and not Order.objects.filter(conversation=conversation).exists()
                      and conversation.needs_human == transfer
                      and Notification.objects.filter(store=store, type="handoff").count() == int(transfer))
            if not transfer:
                passed = passed and promotions.LIMITATION in reply
            if name == "configured" and index == 0:
                passed = passed and "3000" in reply and "90" in reply
            if name in ("unknown", "untrusted") and index == 0:
                passed = passed and promotions.UNKNOWN_DETAILS in reply and "100" not in reply
            if name == "configured" and index >= 2:
                passed = passed and promotions.INVITATION not in reply
            if name == "conv1153" and index == 0:
                passed = passed and all(entry in reply for entry in BUNDLE_ENTRIES)
            if name == "conv1153" and index == 1:
                passed = (passed and BUNDLE_ENTRIES[2] in reply
                          and not any(bundle in reply for bundle in BUNDLE_NAMES if bundle != "Sable Reserve"))
            record["turns"].append({"message": text, "reply": reply, "context": context, "passed": bool(passed)})
            record["passed"] = record["passed"] and bool(passed)
        results["conversations"].append(record)
        print(f"conversation {name}: {record['passed']}", flush=True)
    results["passed"] = all(row["passed"] for kind in ("classification", "conversations") for row in results[kind])
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--conversation-1153", action="store_true", help="Run only the multiline bundle regression")
    args = parser.parse_args()
    from dotenv import dotenv_values
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
    if urlparse(url or "").hostname not in ("localhost", "127.0.0.1", "::1"):
        parser.error("TEST_DATABASE_URL must point to local PostgreSQL")
    os.environ["TEST_DATABASE_URL"] = url
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
    import django
    django.setup()
    from django.conf import settings
    from django.test.utils import setup_databases, teardown_databases
    from uuid import uuid4
    settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_promotions_" + uuid4().hex[:12]
    db_config = setup_databases(verbosity=0, interactive=False)
    try:
        results = evaluate(only_1153=args.conversation_1153)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": results["passed"], "output": str(args.output)}))
        return int(not results["passed"])
    finally:
        teardown_databases(db_config, verbosity=0)


if __name__ == "__main__":
    raise SystemExit(main())
