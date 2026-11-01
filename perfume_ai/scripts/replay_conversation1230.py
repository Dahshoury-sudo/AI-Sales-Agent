"""Capture and replay conversation 1230 without changing customer data.

Capture once with --capture: SELECT-only local PostgreSQL reads save the catalogue,
public prompt settings, and messages through the last recommendation being tested.
Replay with --runs 3: only the configured model provider may receive HTTP; all
conversation storage is in-memory SQLite, and customer delivery is blocked.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / "artifacts/bug_fix_validation"
CASES = (
    (7585, "similarity", {"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE"}),
    (7591, "daytime", {"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE",
                       "notes": ["clean", "fresh"], "occasion": "day",
                       "projection": "moderate"}),
)
PRODUCT_FIELDS = (
    "id", "name", "gender", "perfume_type", "season", "occasion", "longevity",
    "projection", "concentration", "top_notes", "middle_notes", "base_notes",
    "description", "is_active",
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def capture(source_path, catalogue_path):
    from dotenv import dotenv_values
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
    if urlparse(url or "").hostname not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit("Capture requires a local PostgreSQL TEST_DATABASE_URL.")
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
    os.environ["TEST_DATABASE_URL"] = url
    import django
    django.setup()
    from django.db import connection, transaction
    from products.models import Conversation, Product, StoreSettings

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        conversation = Conversation.objects.select_related("store").get(pk=1230)
        store = conversation.store
        # Whitelist public prompt inputs; never copy integration credentials.
        settings = StoreSettings.objects.filter(store=store).values(
            "system_prompt", "business_facts", "website_ordering_enabled",
        ).first() or {}
        source = {
            "conversation_id": 1230, "store_name": store.name,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "store_settings": settings,
            "messages": list(conversation.messages.filter(
                id__lte=max(case[0] for case in CASES), role__in=("user", "assistant"),
            ).order_by("created_at", "id").values(
                "id", "role", "content", "internal_context", "delivery_status",
            )),
        }
        catalogue = []
        for product in Product.objects.filter(store=store).select_related("brand").prefetch_related("variants").order_by("pk"):
            row = {key: getattr(product, key) for key in PRODUCT_FIELDS}
            row["brand"] = product.brand.name
            row["variants"] = list(product.variants.values("volume", "price", "bottle_type", "stock"))
            catalogue.append(row)
    for source_id, _, _ in CASES:
        if not any(message["id"] == source_id for message in source["messages"]):
            raise SystemExit(f"Source message {source_id} is absent from conversation 1230.")
    write_json(source_path, source)
    write_json(catalogue_path, catalogue)
    print(json.dumps({"captured_messages": len(source["messages"]), "catalogue_products": len(catalogue)}))
    return 0


def brand_disclosed(reply, brand):
    """Accept explicit house attribution in either catalogue or known Arabic spelling."""
    from products.services.sales.naming import _HOUSE_ALIASES
    from products.services.static_faq_service import normalize_arabic
    text = normalize_arabic(reply or "")
    aliases = [brand] + [alias for alias, name in _HOUSE_ALIASES.items() if name.casefold() == brand.casefold()]
    for alias in aliases:
        label = re.escape(normalize_arabic(alias))
        if re.search(r"(?:من\s+(?:براند\s+)?|براند\s+|ماركه\s+)" + label + r"(?!\w)", text):
            return True
        if re.search(r"(?<!\w)" + label + r"(?!\w)", text) and re.search(
            r"براند\s+(?:تاني|مختلف|اخر)|غير\s+perfamix|مختلف\s+عن\s+perfamix", text,
        ):
            return True
    return False


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
    from products.services.product_formatting import product_claim_facts
    from products.services.reply_sanitizer import product_claim_issues, sanitize_reply
    from products.services.sales import described, ranking
    from products.services.sales.similarity import alternative_wording_issues
    import httpx

    source = json.loads(args.source.read_text(encoding="utf-8"))
    catalogue = json.loads(args.catalogue.read_text(encoding="utf-8"))
    settings.OPENAI_SMART_MODEL = os.environ.get("OPENAI_SMART_MODEL")
    settings.OPENAI_RESOLVER_MODEL = os.environ.get("OPENAI_RESOLVER_MODEL")
    assert connection.settings_dict["NAME"] == ":memory:"
    call_command("migrate", verbosity=0)
    store = Store.objects.create(name=source["store_name"])
    StoreSettings.objects.create(store=store, **source["store_settings"])
    brands = {}
    for row in catalogue:
        brand = brands.setdefault(row["brand"], None)
        if brand is None:
            brand = brands[row["brand"]] = Brand.objects.create(store=store, name=row["brand"])
        product = Product.objects.create(
            store=store, brand=brand,
            **{key: value for key, value in row.items() if key not in ("id", "brand", "variants")},
        )
        for variant in row["variants"]:
            ProductVariant.objects.create(product=product, **variant)
    products = {product.name: product for product in Product.objects.filter(store=store).select_related("brand")}
    names = list(products)
    record = {
        "database": "isolated in-memory SQLite", "catalogue_products": len(catalogue),
        "models": {key: getattr(settings, key) for key in
                   ("OPENAI_MODEL", "OPENAI_SMART_MODEL", "OPENAI_RESOLVER_MODEL")},
        "setup": "Original preceding history and saved public prompt settings. Seeds reconstruct earlier requests and intentionally retain legacy STORE_BRAND_EXCLUSIVE without brand_strict; they are not historical preference snapshots.",
        "source_message_ids": [case[0] for case in CASES], "runs": [],
    }
    original_send = httpx.Client.send
    model_host = client.base_url.host
    blocked_attempts = []

    def only_model_http(session, request, *send_args, **kwargs):
        if request.url.host != model_host:
            blocked_attempts.append("non-model HTTP")
            raise AssertionError("Replay attempted non-model HTTP")
        return original_send(session, request, *send_args, **kwargs)

    def denied(*unused, **kwargs):
        blocked_attempts.append("customer delivery or background task")
        raise AssertionError("Customer delivery/background tasks disabled during replay")

    original_classify, original_extract, original_search = router.classify, router.extract_intent, router.search_products
    with ExitStack() as guard:
        guard.enter_context(patch("requests.sessions.Session.request", side_effect=denied))
        guard.enter_context(patch("httpx.Client.send", new=only_model_http))
        guard.enter_context(patch("httpx.AsyncClient.send", side_effect=denied))
        guard.enter_context(patch("celery.app.task.Task.apply_async", side_effect=denied))
        guard.enter_context(patch("smtplib.SMTP", side_effect=denied))
        for attempt in range(1, args.runs + 1):
            for source_id, case, seed in CASES:
                with transaction.atomic(), ExitStack() as tracing:
                    conversation = Conversation.objects.create(store=store, preferences=dict(seed))
                    for row in source["messages"]:
                        if row["id"] >= source_id:
                            break
                        Message.objects.create(
                            conversation=conversation, role=row["role"], content=row["content"],
                            internal_context=row.get("internal_context", ""),
                            delivery_status=row.get("delivery_status", "sent"),
                        )
                    message = next(row["content"] for row in source["messages"] if row["id"] == source_id)
                    turn = {"run": attempt, "case": case, "source_message_id": source_id,
                            "message": message, "seed_preferences": dict(seed), "errors": []}

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
                        turn["merged_intent"] = dict(a[0])
                        reference = result.get("reference_product")
                        rows = list(result["products"]) or list(result.get("alternatives") or [])
                        turn["search"] = {
                            "products": [p.name for p in result["products"]],
                            "alternatives": [p.name for p in result.get("alternatives") or []],
                            "preferred_brand": result.get("preferred_brand"),
                            "cross_brand_ids": result.get("cross_brand_ids"),
                            "brand_strict": result.get("brand_strict"),
                            "keeping": result.get("keeping"), "dropped": result.get("dropped"),
                            "similarity": result.get("similarity"),
                            "candidate_facts": [{"name": p.name, "brand": p.brand.name,
                                                 "occasion": p.occasion, "projection": p.projection,
                                                 "longevity": p.longevity} for p in rows],
                            "reference": {key: getattr(reference, key) for key in
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
                        selected_names = described.supported_offered_names(reply, context, names)
                        selected = [products[name] for name in selected_names]
                        turn.update(
                            reply=reply, context=context,
                            selected_products=[{"name": p.name, "brand": p.brand.name,
                                                "occasion": p.occasion, "projection": p.projection,
                                                "longevity": p.longevity} for p in selected],
                            merged_preferences=conversation.preferences, sales_state=conversation.sales_state,
                        )
                        if conversation.preferences.get("brand_strict") is True:
                            turn["errors"].append("Legacy brand preference incorrectly remained strict")
                        if not selected:
                            turn["errors"].append("No product recommendation")
                        elif len(selected) > 2:
                            turn["errors"].append("More than two products recommended")
                        elif selected[0].brand.name.casefold() == store.name.casefold():
                            turn["errors"].append("Requested-brand product leads despite better-fitting other-brand options")
                        for product in selected:
                            if product.brand.name.casefold() != store.name.casefold() and not brand_disclosed(reply, product.brand.name):
                                turn["errors"].append(f"Other brand not clearly disclosed for {product.name}")
                        turn["claim_issues"] = product_claim_issues(reply, product_claim_facts(selected))
                        turn["errors"].extend(turn["claim_issues"])
                        if case == "similarity":
                            reference = turn.get("search", {}).get("reference") or {}
                            if not conversation.preferences.get("similar_to") or reference.get("name") != "Terre d'Hermes":
                                turn["errors"].append("Requested reference was not preserved")
                            summary = turn.get("search", {}).get("similarity") or {}
                            if not summary.get("has_close_match"):
                                turn["errors"].extend(alternative_wording_issues(reply))
                        elif selected:
                            lead = selected[0]
                            if ranking._occasion_verdict(lead.occasion, "daily")[0] != ranking.MATCH:
                                turn["errors"].append("Lead recommendation does not suit daytime")
                            if lead.projection.casefold() != "strong":
                                turn["errors"].append("Lead recommendation missed available strong daytime projection")
                    except Exception as exc:
                        turn["errors"].append(type(exc).__name__ + ": " + str(exc))
                    record["runs"].append(turn)
                    record["blocked_transport_attempts"] = list(blocked_attempts)
                    write_json(args.output, record)
                    print(json.dumps({key: turn[key] for key in ("run", "case", "errors")}, ensure_ascii=False), flush=True)
                    transaction.set_rollback(True)
    return int(bool(blocked_attempts) or any(turn["errors"] for turn in record["runs"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--source", type=Path, default=ARTIFACTS / "conversation1230-source.json")
    parser.add_argument("--catalogue", type=Path, default=ARTIFACTS / "conversation1230-catalogue.json")
    parser.add_argument("--output", type=Path, default=ARTIFACTS / "conversation1230-replays.json")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    return capture(args.source, args.catalogue) if args.capture else replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
