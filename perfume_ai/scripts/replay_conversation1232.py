"""Capture SELECT-only evidence; replay changed goals in isolated in-memory storage."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / "artifacts/bug_fix_validation"
SOURCE = ARTIFACTS / "conversation1232-source.json"
CATALOGUE = ARTIFACTS / "conversation1232-catalogue.json"
TURNS = (7595, 7597, 7599, 7601, 7603, 7605)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def capture():
    from dotenv import dotenv_values
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
    assert urlparse(url or "").hostname in ("localhost", "127.0.0.1", "::1")
    os.environ["TEST_DATABASE_URL"] = url
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
    import django
    django.setup()
    from django.db import connection, transaction
    from products.models import Conversation, Product, StoreSettings
    fields = ("id", "name", "gender", "perfume_type", "season", "occasion", "longevity", "projection",
              "concentration", "top_notes", "middle_notes", "base_notes", "description", "is_active")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        conversation = Conversation.objects.select_related("store").get(pk=1232)
        source = {
            "conversation_id": 1232, "store_name": conversation.store.name,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "saved_preferences": conversation.preferences, "saved_sales_state": conversation.sales_state,
            "store_settings": StoreSettings.objects.filter(store=conversation.store).values(
                "system_prompt", "business_facts", "website_ordering_enabled").first() or {},
            "messages": list(conversation.messages.filter(id__lte=7606).order_by("created_at", "id").values(
                "id", "role", "content", "internal_context", "delivery_status")),
        }
        catalogue = []
        for p in Product.objects.filter(store=conversation.store).select_related("brand").prefetch_related("variants").order_by("pk"):
            catalogue.append({**{key: getattr(p, key) for key in fields}, "brand": p.brand.name,
                              "variants": list(p.variants.values("volume", "price", "bottle_type", "stock"))})
    assert all(any(row["id"] == target for row in source["messages"]) for target in TURNS)
    write_json(SOURCE, source)
    write_json(CATALOGUE, catalogue)
    print(json.dumps({"messages": len(source["messages"]), "products": len(catalogue)}))


def replay(args):
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
    from products.services.sales import described, ranking, recommendation_response, similarity
    import httpx

    settings.OPENAI_SMART_MODEL = os.environ.get("OPENAI_SMART_MODEL")
    settings.OPENAI_RESOLVER_MODEL = os.environ.get("OPENAI_RESOLVER_MODEL")
    assert connection.settings_dict["NAME"] == ":memory:"
    call_command("migrate", verbosity=0)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    store = Store.objects.create(name=source["store_name"])
    StoreSettings.objects.create(store=store, **source["store_settings"])
    brands = {}
    for row in catalogue:
        if row["brand"] not in brands:
            brands[row["brand"]] = Brand.objects.create(store=store, name=row["brand"])
        p = Product.objects.create(store=store, brand=brands[row["brand"]],
                                   **{key: value for key, value in row.items() if key not in ("id", "brand", "variants")})
        for variant in row["variants"]:
            ProductVariant.objects.create(product=p, **variant)
    products = {p.name: p for p in Product.objects.filter(store=store).select_related("brand")}
    record = {"database": "isolated in-memory SQLite", "catalogue_products": len(products),
              "models": {key: getattr(settings, key) for key in
                         ("OPENAI_MODEL", "OPENAI_SMART_MODEL", "OPENAI_RESOLVER_MODEL")},
              "setup": "Three sequential runs use original customer messages and newly generated replies. Historical cases use original preceding messages and reconstructed stale preferences, not historical preference snapshots.",
              "runs": [], "blocked_attempts": []}
    original_send = httpx.Client.send
    original_classify, original_extract, original_search = router.classify, router.extract_intent, router.search_products

    def denied(*unused, **kwargs):
        record["blocked_attempts"].append("non-model delivery/background operation")
        raise AssertionError("Replay delivery is disabled")

    def only_model_http(session, request, *a, **kw):
        if request.url.host != client.base_url.host:
            return denied()
        return original_send(session, request, *a, **kw)

    def run_turn(conversation, source_id, case, attempt):
        message = next(row["content"] for row in source["messages"] if row["id"] == source_id)
        turn = {"case": case, "run": attempt, "source_message_id": source_id, "message": message,
                "before_preferences": dict(conversation.preferences or {}), "errors": []}
        search_result, merged = {}, {}

        def classify(*a, **kw):
            result = original_classify(*a, **kw)
            turn["classification"] = result
            return result

        def extract(*a, **kw):
            result = original_extract(*a, **kw)
            turn["extracted_intent"] = dict(result)
            return result

        def search(*a, **kw):
            result = original_search(*a, **kw)
            merged.update(a[0])
            search_result.update(result)
            turn["merged_intent"] = dict(a[0])
            turn["search"] = {
                "products": [p.name for p in result["products"]],
                "alternatives": [p.name for p in result.get("alternatives") or ()],
                "keeping": list(result.get("keeping") or ()), "dropped": result.get("dropped"),
                "similarity": result.get("similarity"),
                "reference": getattr(result.get("reference_product"), "name", None),
            }
            return result

        history = build_llm_history(conversation)
        save_message(conversation, "user", message)
        try:
            with patch.object(router, "classify", new=classify), patch.object(router, "extract_intent", new=extract), patch.object(router, "search_products", new=search):
                reply, context = router.route(message, history, store, conversation)
            reply = sanitize_reply(reply, conversation)
            save_message(conversation, "assistant", reply, internal_context=context)
            conversation.refresh_from_db()
            selected = described.supported_offered_names(reply, context, list(products))
            turn.update(reply=reply, context=context, selected_products=selected,
                        merged_preferences=conversation.preferences, sales_state=conversation.sales_state)
            candidates = list(search_result.get("products") or search_result.get("alternatives") or ())
            if candidates:
                turn["errors"].extend(recommendation_response.response_issues(reply, candidates, merged, search_result))
            if source_id in (7597, 7601, 7603, 7605):
                if not selected:
                    turn["errors"].append("No concrete recommendation")
                if len(selected) > 2:
                    turn["errors"].append("More than two recommendations")
            if source_id in (7601, 7603):
                if turn.get("search", {}).get("reference") != "Terre d'Hermes":
                    turn["errors"].append("Terre reference was lost")
                if "Terre d'Hermes" in selected:
                    turn["errors"].append("Reference recommended as its own alternative")
                if not (search_result.get("similarity") or {}).get("has_close_match"):
                    turn["errors"].extend(similarity.alternative_wording_issues(reply))
            if source_id == 7605:
                if conversation.preferences.get("similar_to") or conversation.preferences.get("occasion") != "casual":
                    turn["errors"].append("Latest purpose did not replace similarity")
                if selected and ranking._occasion_verdict(products[selected[0]].occasion, "casual")[0] != ranking.MATCH:
                    turn["errors"].append("Lead perfume is not compatible with casual use")
                if selected and ranking.excessive_projection(products[selected[0]].projection, "moderate"):
                    turn["errors"].append("Lead perfume is too loud for subdued use")
            if conversation.preferences.get("occasion") == "multiple":
                turn["errors"].append("Unusable occasion persisted")
        except Exception as exc:
            turn["errors"].append(type(exc).__name__ + ": " + str(exc))
        record["runs"].append(turn)
        write_json(args.output, record)
        print(json.dumps({key: turn.get(key) for key in ("case", "run", "source_message_id", "selected_products", "errors")}, ensure_ascii=False), flush=True)

    with ExitStack() as guard:
        guard.enter_context(patch("requests.sessions.Session.request", side_effect=denied))
        guard.enter_context(patch("httpx.Client.send", new=only_model_http))
        guard.enter_context(patch("httpx.AsyncClient.send", side_effect=denied))
        guard.enter_context(patch("celery.app.task.Task.apply_async", side_effect=denied))
        guard.enter_context(patch("smtplib.SMTP", side_effect=denied))
        for attempt in range(1, args.runs + 1):
            with transaction.atomic():
                conversation = Conversation.objects.create(store=store)
                for source_id in TURNS:
                    run_turn(conversation, source_id, "sequential", attempt)
                transaction.set_rollback(True)
        if not args.skip_historical:
            for source_id in (7601, 7603, 7605):
                with transaction.atomic():
                    seed = {"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE", "brand_strict": False,
                            "notes": ["fresh", "clean"], "occasion": "multiple", "projection": "moderate"}
                    if source_id == 7605:
                        seed.update(similar_to="Terre d'Hermes", similar_to_notes=["vetiver"])
                    conversation = Conversation.objects.create(store=store, preferences=seed)
                    for row in source["messages"]:
                        if row["id"] >= source_id:
                            break
                        Message.objects.create(conversation=conversation,
                                               **{key: value for key, value in row.items() if key != "id"})
                    run_turn(conversation, source_id, "original_history", 1)
                    transaction.set_rollback(True)
    return int(bool(record["blocked_attempts"] or any(turn["errors"] for turn in record["runs"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--skip-historical", action="store_true")
    parser.add_argument("--output", type=Path, default=ARTIFACTS / "conversation1232-replays.json")
    args = parser.parse_args()
    raise SystemExit(capture() if args.capture else replay(args))
