# -*- coding: utf-8 -*-
"""Drive the real agent through the scenarios and record everything it did.

Nothing in products/ is modified. Internal state (classification, extracted intent,
merged preferences, search result, sales stage) is captured by wrapping the functions
the router already calls, in this process only, and the wrappers are thread-local so
concurrent scenarios do not cross-contaminate.

Every scenario runs inside a transaction that is rolled back, so no order is created,
no stock moves, and no conversation rows survive. The evaluation cannot damage the
store it is evaluating.
"""

import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from django.db import connection, transaction  # noqa: E402

from eval_harness import checks  # noqa: E402
# EVAL_SCENARIOS=conv990 replays a single reported conversation instead of the full suite.
# Replaying the transcript a customer complained about is the fastest way to tell whether a
# fix actually landed — and running it twice matters, because extractor variance between runs
# is what exposed two separate bugs in the conv_990 work that a single run had hidden.
_REPLAYS = {
    "conv990": "scenarios_conv990",
    "conv997": "scenarios_conv997",
    "conv1005": "scenarios_conv1005",
    "conv726": "scenarios_conv726",
    "conv738": "scenarios_conv738",
    "conv768": "scenarios_conv768",
    "conv795": "scenarios_conv795",
    # One file, both transcripts: 798 and 799 are the same failure reported twice.
    "conv798": "scenarios_conv798",
    "conv799": "scenarios_conv798",
    # Likewise 816 and 817 — the same failure reached by re-typing the name instead of
    # chasing it with a pronoun.
    "conv816": "scenarios_conv816",
    "conv817": "scenarios_conv816",
}
_replay = _REPLAYS.get(os.environ.get("EVAL_SCENARIOS", ""))
if _replay:
    SCENARIOS = __import__(
        f"eval_harness.{_replay}", fromlist=["SCENARIOS"]
    ).SCENARIOS
else:
    from eval_harness.scenarios import SCENARIOS  # noqa: E402

STORE_NAME = "Perfamix"
_local = threading.local()


def _rec():
    """The current thread's recorder for the turn in flight."""
    if not hasattr(_local, "turn"):
        _local.turn = {}
    return _local.turn


# ─────────────────────────── instrumentation ───────────────────────────
def install_probes():
    """Wrap the functions the router calls so each turn's internals are observable."""
    from products.services import router
    from products.services.sales import objection as sales_objection
    from products.services.sales import stage as sales_stage

    original_classify = router.classify
    original_extract = router.extract_intent
    original_merge = router.merge_preferences
    original_search = router.search_products
    original_recommend = router.recommend
    original_finalize = router._finalize
    original_derive = sales_stage.derive
    original_detect = sales_objection.detect

    def classify(message, history=None):
        result = original_classify(message, history)
        _rec()["classification"] = result
        return result

    def extract_intent(message, history=None, store=None):
        result = original_extract(message, history, store)
        _rec()["raw_intent"] = result
        return result

    def merge_preferences(conversation, intent, message=None):
        result = original_merge(conversation, intent, message)
        _rec()["merged_intent"] = dict(result or {})
        return result

    def search_products(intent, store=None, keep=()):
        result = original_search(intent, store, keep=keep)
        products = list(result.get("products") or [])
        alternatives = list(result.get("alternatives") or [])
        _rec()["search"] = {
            "matched": [p.name for p in products],
            "alternatives": [p.name for p in alternatives],
            "similarity": result.get("similarity"),
            "reasons": {
                entry.product.name: {
                    "score": round(entry.score, 3),
                    "reasons": entry.reasons,
                    "mismatches": entry.mismatches,
                }
                for entry in (result.get("ranked") or {}).values()
            },
        }
        _rec()["similarity"] = result.get("similarity")
        _rec()["keeping"] = result.get("keeping")
        _rec()["dropped"] = result.get("dropped")
        return result

    def recommend_probe(*args, **kwargs):
        # Records that the router chose to recommend despite an unresolved gender —
        # the behaviour the gender-gate fix introduced.
        _rec()["gender_unknown_at_recommend"] = bool(kwargs.get("gender_unknown"))
        return original_recommend(*args, **kwargs)

    def _finalize(reply, stage):
        _rec()["stage"] = stage
        _rec()["raw_reply"] = reply
        return original_finalize(reply, stage)

    def derive(request_type, message=None, intent=None, objection=None, history=None):
        result = original_derive(request_type, message, intent, objection, history)
        _rec()["derived_stage"] = result
        return result

    def detect(message, history=None):
        result = original_detect(message, history)
        if result is not None:
            _rec()["objection"] = {
                "kind": result.kind,
                "matched": list(result.matched),
                "past_purchase": result.past_purchase,
            }
        return result

    router.classify = classify
    router.extract_intent = extract_intent
    router.merge_preferences = merge_preferences
    router.search_products = search_products
    router.recommend = recommend_probe
    router._finalize = _finalize
    sales_stage.derive = derive
    sales_objection.detect = detect


class _Rollback(Exception):
    """Raised to unwind the scenario's transaction once it has been recorded."""


def run_scenario(scenario, truth):
    """Play one scenario end to end and grade it deterministically."""
    from products.models import Store
    from products.services.conversation_service import (
        build_llm_history,
        create_conversation,
        save_message,
    )
    from products.services.reply_sanitizer import sanitize_reply
    from products.services.router import route

    store = Store.objects.get(name=STORE_NAME)
    record = {
        "id": scenario["id"],
        "category": scenario["category"],
        "persona": scenario["persona"],
        "probe": scenario["probe"],
        "turns": [],
        "findings": [],
        "error": None,
    }

    started = time.time()
    budget_stated = False
    try:
        with transaction.atomic():
            conversation = create_conversation(store)
            for index, message in enumerate(scenario["turns"], start=1):
                _local.turn = {}
                history = build_llm_history(conversation)
                save_message(conversation, "user", message)

                try:
                    reply, context = route(message, history, store, conversation)
                    reply = sanitize_reply(reply, conversation)
                except Exception as exc:  # a crash IS a finding, not a lost scenario
                    reply, context = "", ""
                    record["findings"].append((
                        "crash", "critical",
                        f"turn {index} raised {type(exc).__name__}: {exc}",
                    ))
                    traceback.print_exc()

                save_message(conversation, "assistant", reply, internal_context=context)

                state = dict(_local.turn)
                turn_findings = checks.check_reply(
                    reply,
                    truth=truth,
                    context=context,
                    customer_text=message,
                    turn_state=state,
                    history_text="\n".join(
                        f"{turn['user']}\n{turn['reply']}\n{turn.get('context') or ''}"
                        for turn in record["turns"]
                    ),
                )

                budget = scenario.get("assert_budget")
                # Only from the turn the customer actually states it. Applying it to every
                # turn flagged Dior Sauvage's real 944 price quoted on turn 1 of M1 against a
                # budget the customer first mentioned on turn 3 — a finding about the future.
                if budget and not budget_stated:
                    budget_stated = (
                        str(int(budget)) in (message or "")
                        or bool((state.get("merged_intent") or {}).get("max_price"))
                    )
                if budget and budget_stated:
                    over = checks.check_budget_respected(reply, budget, truth)
                    if over:
                        turn_findings.append((
                            "over_budget_offer", "high",
                            f"quoted {over} against a stated budget of {budget}",
                        ))
                    stated = checks.check_stated_total(reply, budget, truth)
                    if stated:
                        turn_findings.append((
                            "over_budget_total", "critical",
                            f"stated an order total of {stated} against a stated budget of "
                            f"{budget}, with no acknowledgement",
                        ))

                for excluded in scenario.get("assert_excludes", []):
                    matched = (state.get("search") or {}).get("matched") or []
                    if excluded in matched:
                        turn_findings.append((
                            "exclusion_ignored", "high",
                            f"'{excluded}' was asked to be excluded but was still shortlisted",
                        ))

                record["turns"].append({
                    "n": index,
                    "user": message,
                    "reply": reply,
                    "classification": state.get("classification"),
                    "objection": state.get("objection"),
                    "raw_intent": state.get("raw_intent"),
                    "merged_intent": state.get("merged_intent"),
                    "stage": state.get("stage") or state.get("derived_stage"),
                    "search": state.get("search"),
                    "keeping": state.get("keeping"),
                    "dropped": state.get("dropped"),
                    "context_chars": len(context or ""),
                    "context": context,
                    "findings": [list(f) for f in turn_findings],
                })
                record["findings"].extend(turn_findings)

            saved_preferences = dict(conversation.preferences or {})
            record["final_preferences"] = saved_preferences
            raise _Rollback
    except _Rollback:
        pass
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    record["findings"] = [list(f) for f in record["findings"]]
    record["seconds"] = round(time.time() - started, 1)
    return record


def _worker(scenario, truth):
    try:
        return run_scenario(scenario, truth)
    finally:
        connection.close()


def main():
    from products.models import Store

    install_probes()
    store = Store.objects.get(name=STORE_NAME)
    truth = checks.build_ground_truth(store)
    print(f"store={store.name} catalogue={truth['catalog_size']} scenarios={len(SCENARIOS)}", flush=True)

    only = set(sys.argv[1:])
    todo = [s for s in SCENARIOS if not only or s["id"] in only]

    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_worker, s, truth): s for s in todo}
        for future in as_completed(futures):
            scenario = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                record = {"id": scenario["id"], "error": str(exc), "turns": [], "findings": []}
            results.append(record)
            flags = len(record.get("findings") or [])
            print(
                f"  [{record['id']}] {len(record.get('turns') or [])} turns, "
                f"{flags} deterministic findings, {record.get('seconds')}s",
                flush=True,
            )

    results.sort(key=lambda r: r["id"])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "runs.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {out}", flush=True)

    # Sanity: the evaluation must have left nothing behind.
    from products.models import Conversation, Order
    print(f"conversations in db after run: {Conversation.objects.count()}", flush=True)
    print(f"orders in db after run: {Order.objects.count()}", flush=True)


if __name__ == "__main__":
    main()
