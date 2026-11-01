"""Replay the three corrected turns against saved data in an in-memory database.

Each turn starts from the original preceding messages, independently of other
replays. Preference seeds reconstruct the explicit requests before that turn;
they are not claimed to be historical database snapshots. No checkout/contact
messages after the daily-reference turn are imported. Only model HTTP is allowed.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = (
    (7712, "similarity", {"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE"}),
    (7718, "morning", {"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE",
                       "notes": ["clean"], "occasion": "daily", "projection": "moderate"}),
    (7724, "daily", {"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE",
                     "notes": ["clean"], "occasion": "daytime", "projection": "strong",
                     "longevity": "long-lasting"}),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/bug_fix_validation/conversation1233-replays.json")
    args = parser.parse_args()
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
    os.environ["TEST_DATABASE_URL"] = "sqlite:///:memory:"
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection, transaction
    from products.models import Brand, Conversation, Message, Product, ProductVariant, Store, StoreSettings
    from products.services import router
    from products.services.ai.client import client
    from products.services.conversation_service import build_llm_history, save_message
    from products.services.reply_sanitizer import sanitize_reply
    from products.services.sales import described, ranking
    from products.services.sales.similarity import alternative_wording_issues
    import httpx

    # Keep production's configured model profiles, with only storage replaced.
    settings.OPENAI_SMART_MODEL = os.environ.get("OPENAI_SMART_MODEL")
    settings.OPENAI_RESOLVER_MODEL = os.environ.get("OPENAI_RESOLVER_MODEL")
    assert connection.settings_dict["NAME"] == ":memory:"
    call_command("migrate", verbosity=0)
    folder = ROOT / "artifacts/bug_fix_validation"
    catalogue = json.loads((folder / "conversation1233-catalogue.json").read_text(encoding="utf-8"))
    source = json.loads((folder / "conversation1233-source.json").read_text(encoding="utf-8"))
    store = Store.objects.create(name="Perfamix")
    StoreSettings.objects.create(store=store)
    brands = {}
    for row in catalogue:
        if row["brand"] not in brands:
            brands[row["brand"]] = Brand.objects.create(store=store, name=row["brand"])
        product = Product.objects.create(
            store=store, brand=brands[row["brand"]],
            **{k: v for k, v in row.items() if k not in ("id", "brand", "variants")},
        )
        for variant in row["variants"]:
            ProductVariant.objects.create(product=product, **variant)
    names = list(Product.objects.filter(store=store).values_list("name", flat=True))
    record = {
        "database": "isolated in-memory SQLite", "catalogue_products": len(catalogue),
        "models": {key: getattr(settings, key) for key in
                   ("OPENAI_MODEL", "OPENAI_SMART_MODEL", "OPENAI_RESOLVER_MODEL")},
        "setup": "Original history before each target; explicit preference seeds reconstructed from preceding customer requests. Default store prompt; no saved custom store-settings snapshot available.",
        "runs": [],
    }
    original_send = httpx.Client.send
    model_host = client.base_url.host

    def only_model_http(session, request, *send_args, **kwargs):
        if request.url.host != model_host:
            raise AssertionError("Replay attempted non-model HTTP")
        return original_send(session, request, *send_args, **kwargs)

    def denied(*unused, **kwargs):
        raise AssertionError("Customer delivery/background tasks disabled during replay")

    original_classify = router.classify
    original_extract = router.extract_intent
    original_search = router.search_products
    with ExitStack() as guard:
        guard.enter_context(patch("requests.sessions.Session.request", side_effect=denied))
        guard.enter_context(patch("httpx.Client.send", new=only_model_http))
        guard.enter_context(patch("httpx.AsyncClient.send", side_effect=denied))
        guard.enter_context(patch("celery.app.task.Task.apply_async", side_effect=denied))
        guard.enter_context(patch("smtplib.SMTP", side_effect=denied))
        for attempt in range(1, args.runs + 1):
            for source_id, case, seed in CASES:
                with transaction.atomic(), ExitStack() as tracing:
                    conversation = Conversation.objects.create(store=store, preferences=seed)
                    for row in source:
                        if row["id"] >= source_id:
                            break
                        Message.objects.create(
                            conversation=conversation, role=row["role"], content=row["content"],
                            internal_context=row.get("internal_context", ""),
                            delivery_status=row.get("delivery_status", "sent"),
                        )
                    message = next(row["content"] for row in source if row["id"] == source_id)
                    turn = {"run": attempt, "case": case, "source_message_id": source_id,
                            "message": message, "seed_preferences": seed, "errors": []}

                    def classify(*a, **kw):
                        result = original_classify(*a, **kw)
                        turn["classification"] = result
                        return result

                    def extract(*a, **kw):
                        result = original_extract(*a, **kw)
                        turn["extracted_intent"] = result
                        return result

                    def search(*a, **kw):
                        result = original_search(*a, **kw)
                        turn["merged_intent"] = a[0]
                        reference = result.get("reference_product")
                        turn["search"] = {
                            "products": [p.name for p in result["products"]],
                            "alternatives": [p.name for p in result.get("alternatives") or []],
                            "keeping": result.get("keeping"), "dropped": result.get("dropped"),
                            "similarity": result.get("similarity"),
                            "reference": {k: getattr(reference, k) for k in
                                          ("name", "top_notes", "middle_notes", "base_notes")} if reference else None,
                        }
                        return result

                    tracing.enter_context(patch.object(router, "classify", new=classify))
                    tracing.enter_context(patch.object(router, "extract_intent", new=extract))
                    tracing.enter_context(patch.object(router, "search_products", new=search))
                    history = build_llm_history(conversation)
                    save_message(conversation, "user", message)
                    try:
                        reply, context = router.route(message, history, store, conversation)
                        reply = sanitize_reply(reply, conversation)
                        conversation.refresh_from_db()
                        selected = described.supported_offered_names(reply, context, names)
                        turn.update(reply=reply, context=context, selected_products=selected,
                                    merged_preferences=conversation.preferences, sales_state=conversation.sales_state)
                        if case in ("similarity", "morning") and len(selected) > 2:
                            turn["errors"].append("More than two products recommended")
                        if case == "similarity":
                            reference = turn.get("search", {}).get("reference") or {}
                            if not conversation.preferences.get("similar_to") or reference.get("name") != "Terre d'Hermes":
                                turn["errors"].append("Requested reference was not preserved")
                            summary = turn.get("search", {}).get("similarity") or {}
                            if not summary.get("has_close_match"):
                                turn["errors"].extend(alternative_wording_issues(reply))
                            if not selected:
                                turn["errors"].append("No alternatives offered")
                        elif case == "morning":
                            if not selected:
                                turn["errors"].append("No daytime recommendation")
                            elif ranking._occasion_verdict(Product.objects.get(store=store, name=selected[0]).occasion, "daily")[0] != ranking.MATCH:
                                turn["errors"].append("Lead recommendation is not suitable for daytime")
                        else:
                            if not all(name in reply for name in ("Bonded", "Showmix")) or "تقصد أنهي" not in reply:
                                turn["errors"].append("Daily pointer did not clarify between the two daytime offers")
                            if any(name in reply for name in ("Elite", "Dark Aura")):
                                turn["errors"].append("Daily pointer drifted to evening products")
                    except Exception as exc:
                        turn["errors"].append(type(exc).__name__ + ": " + str(exc))
                    record["runs"].append(turn)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                    print(json.dumps({k: turn[k] for k in ("run", "case", "errors")}, ensure_ascii=False), flush=True)
                    transaction.set_rollback(True)
    return int(any(turn["errors"] for turn in record["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
