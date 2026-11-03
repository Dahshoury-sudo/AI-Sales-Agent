"""Reproduce the migration checks using the shared, isolated USD-budgeted runner.

All contact details below are synthetic. The runner copies only public catalogue
facts from local PostgreSQL, rolls every conversation back, and disables delivery.
Pass --resume-from to retain the entire migration's cumulative API spending.
"""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def scenarios():
    def cart(quantity, **extra):
        return {
            "items": [{"name": "Eros", "volume": 50, "bottle_type": "normal",
                       "quantity": quantity, "price": "750.00"}],
            "pending_count": 0,
            "order_count": 0,
            **extra,
        }

    phones = ["customer_name", "customer_phone", "secondary_phone"]
    details = phones + ["shipping_address"]
    return [
        {
            "id": "CHECKOUT",
            "category": "purchase",
            "persona": "ready_buyer",
            "turns": [
                "عايز أطلب إيروس فرزاتشي 50 ملي زجاجة الستور",
                "خليها اتنين بدل واحدة",
                "اسمي عميل اختبار، رقمي 01000000000 ورقم بديل 01100000000",
                "العنوان القاهرة مدينة نصر شارع الاختبار عمارة 1 شقة 1",
                "خليها واحدة بس وأكد الطلب",
                "تمام أكد الطلب",
            ],
            "probe": (
                "Resolve the Arabic name to Eros, 50 ml, normal/store bottle, at 750 EGP. "
                "Save the quantity change to two without duplicating the line. Collect "
                "only missing contact details, then show a 1500 EGP product subtotal. "
                "The fifth turn changes the quantity and asks to confirm together: "
                "show the revised 750 EGP subtotal and do not create an order yet. "
                "Create exactly one order only after the sixth turn's fresh confirmation."
            ),
            "assert_cart_names": ["Eros"],
            "assert_cart_by_turn": {
                1: cart(1, contact_fields=[]),
                2: cart(2, contact_fields=[]),
                3: cart(2, contact_fields=phones),
                4: cart(2, contact_fields=details),
                5: cart(1, contact_fields=details),
                6: {"items": [], "pending_count": 0, "order_count": 1},
            },
        },
        {
            "id": "ORIGINAL_BOTTLE",
            "category": "product_info",
            "persona": "bottle_buyer",
            "turns": ["عندك زجاجة إيروس الأوريجنال؟ بكام وحجم كام؟"],
            "probe": (
                "The available Eros original-shaped bottle is 100 ml at 1500 EGP. "
                "Do not say it is unavailable or confuse the original-shaped bottle "
                "with the origin of the store's compounded perfume. No order is requested."
            ),
            "assert_cart_by_turn": {1: {"items": [], "order_count": 0}},
        },
        {
            "id": "RECOMMENDATION",
            "category": "recommendation",
            "persona": "summer_shopper",
            "turns": ["عايز عطر رجالي فريش للصيف للشغل ميزانيتي 1200 جنيه"],
            "probe": (
                "Recommend stocked masculine fresh summer/office perfumes using recorded "
                "notes and prices within 1200 EGP. The customer supplied enough constraints; "
                "do not ask for the same preferences again."
            ),
            "assert_budget": 1200,
            "assert_cart_by_turn": {1: {"items": [], "order_count": 0}},
        },
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-set", choices=("luna", "legacy"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    parser.add_argument("--budget", default="0.30")
    parser.add_argument("--extended", action="store_true", help="Also run the complete core behavior suite")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--scenario-ids", help="Optional comma-separated unprefixed case IDs")
    args = parser.parse_args()

    model = "gpt-6-luna" if args.model_set == "luna" else "gpt-4.1-mini"
    smart = "gpt-6-luna" if args.model_set == "luna" else "gpt-5-mini"
    os.environ.update(OPENAI_MODEL=model, OPENAI_SMART_MODEL=smart,
                      OPENAI_RESOLVER_MODEL=smart, OPENAI_TEMPERATURE="0.3")

    from eval_harness.scenarios import SCENARIOS
    selected = scenarios()
    if args.extended:
        selected += [deepcopy(s) for s in SCENARIOS]
    elif args.model_set == "legacy":
        # Compare the existing absent-product findings under the old model settings.
        selected += [deepcopy(s) for s in SCENARIOS if s["id"] in {"P3", "F3"}]
    for scenario in selected:
        if scenario["id"] == "R3":
            scenario["assert_search_by_turn"] = [1]
        if scenario["id"] == "S1":
            scenario["assert_intent_by_turn"] = {1: {"similar_to": "Sauvage"}}
            scenario["assert_search_by_turn"] = [1]
        if scenario["id"] == "S3":
            scenario["assert_intent_by_turn"] = {1: {"similar_to": "Baccarat Rouge 540"}}
            scenario["assert_search_by_turn"] = [1]
        if scenario["id"] == "M3":
            scenario["assert_intent_by_turn"] = {
                1: {"max_price": 500},
                2: {"max_price": 500, "avoid_notes": ["oud"]},
                3: {"max_price": 500, "avoid_notes": ["oud"]},
            }
            scenario["assert_search_by_turn"] = [1, 2, 3]
    if args.scenario_ids:
        wanted = args.scenario_ids.split(",")
        available = {case["id"]: case for case in selected}
        if set(wanted) - available.keys():
            parser.error("Unknown selected case ID")
        selected = [available[identifier] for identifier in wanted]
    for scenario in selected:
        scenario["id"] = args.model_set.upper() + "_" + scenario["id"]
    SCENARIOS.extend(selected)

    from scripts.evaluate_agent_budgeted import main as evaluate
    selected_ids = ",".join(s["id"] for s in selected)
    sys.argv = [sys.argv[0], "--budget", args.budget, "--workers", str(args.workers), "--suite", "core",
                "--scenario-ids", selected_ids, "--rerun-ids", selected_ids,
                "--resume-from", str(args.resume_from), "--output", str(args.output)]
    return evaluate()


if __name__ == "__main__":
    raise SystemExit(main())
