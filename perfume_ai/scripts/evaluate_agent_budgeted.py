"""Run the existing agent scenarios against a disposable catalogue under a USD cap.

Usage: python scripts/evaluate_agent_budgeted.py --budget 0.30 --suite all
Only public catalogue/store facts are copied from local TEST_DATABASE_URL.
Source data and previous evaluation artifacts are never modified.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# USD per million tokens, verified against official model pages on 2026-10-12.
PRICES = {
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("0.10"), Decimal("1.60")),
    "gpt-5-mini": (Decimal("0.25"), Decimal("0.025"), Decimal("2.00")),
}


class BudgetStop(BaseException):
    """Bypass application fallbacks: budget exhaustion is not an agent failure."""


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    def encode(item):
        return sorted(item) if isinstance(item, (set, frozenset)) else str(item)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode), encoding="utf-8")
    # Windows readers can briefly deny replacement while opening the cost ledger.
    for attempt in range(100):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.02)


class Meter:
    def __init__(self, cap, destination):
        self.cap = Decimal(str(cap))
        self.spent = Decimal(0)
        self.reserved = Decimal(0)
        self.condition = threading.Condition()
        self.calls = []
        self.halted = False
        self.local = threading.local()
        self.destination = destination

    def save(self):
        write_json(self.destination / "usage.json", {
            "budget_usd": self.cap, "accounted_usd": self.spent,
            "reserved_usd": self.reserved, "calls": self.calls,
            "pricing_per_million": PRICES,
            "note": "Actual response usage with cached-input discounts; uncertain requests retain their entire reservation. Taxes/credits excluded.",
        })

    def wrap(self, original):
        def create(*args, **kwargs):
            if args or kwargs.get("stream") or kwargs.get("tools") or kwargs.get("n", 1) != 1:
                raise BudgetStop("Unsupported request cannot be budgeted")
            model = kwargs["model"]
            if model not in PRICES:
                raise BudgetStop("Unpriced model: " + model)
            rates = PRICES[model]
            # For text-only BPE, UTF-8 bytes are a conservative token upper bound.
            # Include serialized framing plus 1,024 tokens for protocol overhead.
            for message in kwargs["messages"]:
                if not isinstance(message.get("content"), str):
                    raise BudgetStop("Only text messages are supported")
            upper_input = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8")) + 1024
            maximum = 8192 if model.startswith("gpt-5") else 2048
            kwargs["max_completion_tokens"] = maximum
            reservation = (upper_input * rates[0] + maximum * rates[2]) / 1_000_000
            with self.condition:
                if self.halted:
                    raise BudgetStop("Budget guard already stopped the run")
                while self.spent + self.reserved + reservation > self.cap and self.reserved:
                    self.condition.wait(timeout=1)
                if self.halted:
                    raise BudgetStop("Budget guard already stopped the run")
                if self.spent + self.reserved + reservation > self.cap:
                    self.halted = True
                    self.condition.notify_all()
                    raise BudgetStop("Remaining budget cannot cover this request's maximum cost")
                self.reserved += reservation
                entry = {"scenario": getattr(self.local, "scenario", "unknown"), "model": model,
                         "reserved_usd": reservation, "status": "in_flight"}
                self.calls.append(entry)
                self.save()
            started = time.monotonic()
            try:
                response = original(**kwargs)
                if response.usage is None:
                    raise BudgetStop("Provider returned no usage; retaining reservation")
                usage = response.usage
                cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
                cost = ((usage.prompt_tokens - cached) * rates[0] + cached * rates[1]
                        + usage.completion_tokens * rates[2]) / 1_000_000
                with self.condition:
                    self.spent += cost
                    self.reserved -= reservation
                    entry.update(status="complete", prompt_tokens=usage.prompt_tokens,
                                 cached_tokens=cached, completion_tokens=usage.completion_tokens,
                                 cost_usd=cost, seconds=round(time.monotonic() - started, 3),
                                 finish_reason=response.choices[0].finish_reason,
                                 response_model=response.model)
                    self.save()
                    self.condition.notify_all()
                if usage.prompt_tokens > upper_input or cost > reservation:
                    raise BudgetStop("Provider usage exceeded conservative reservation")
                return response
            except BaseException:
                with self.condition:
                    if entry["status"] == "in_flight":
                        self.spent += reservation
                        self.reserved -= reservation
                        entry.update(status="uncertain_charged_maximum", cost_usd=reservation)
                        self.save()
                        self.condition.notify_all()
                raise
        return create


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=Decimal, default=Decimal("0.30"))
    parser.add_argument("--suite", choices=("core", "all"), default="all")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--scenario-ids", help="Comma-separated scenario IDs in priority order; selects only these cases")
    parser.add_argument("--resume-from", type=Path, help="Carry prior spend (including unresolved reservations) and completed cases into a fresh output directory")
    parser.add_argument("--rerun-ids", help="With --resume-from, replace these completed cases while retaining all previous spend")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "agent_evaluation" / "live")
    args = parser.parse_args()
    if args.budget <= 0 or args.workers < 1:
        parser.error("Budget and workers must be positive")
    destination = args.output.resolve()
    if (destination / "usage.json").exists():
        parser.error("Choose a fresh output directory; existing spend evidence must not be overwritten")
    prior = None
    prior_runs = []
    rerun_ids = set((args.rerun_ids or "").split(",")) - {""}
    if rerun_ids and not args.resume_from:
        parser.error("--rerun-ids requires --resume-from for cumulative accounting")
    if args.resume_from:
        prior = json.loads((args.resume_from / "usage.json").read_text(encoding="utf-8"))
        prior_runs = json.loads((args.resume_from / "runs.json").read_text(encoding="utf-8"))
        if Decimal(prior["accounted_usd"]) + Decimal(prior["reserved_usd"]) >= args.budget:
            parser.error("Prior spend/reservations already consume the total budget")
    destination.mkdir(parents=True, exist_ok=True)
    import dj_database_url
    from dotenv import dotenv_values
    config = dotenv_values(ROOT / ".env")
    url = os.environ.get("TEST_DATABASE_URL") or config.get("TEST_DATABASE_URL")
    database = dj_database_url.parse(url or "")
    if database.get("HOST") not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("TEST_DATABASE_URL must select local PostgreSQL")
    os.environ["DATABASE_URL"] = url
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings"
    import django
    django.setup()
    from django.conf import settings
    from django.db import connections, transaction
    from django.test.runner import DiscoverRunner
    from products.models import Store, StoreSettings, Brand, Category, Product, ProductVariant, StaticFAQ
    from products.services.ai import client as ai
    from eval_harness import runner, checks, judge
    from eval_harness.scenarios import SCENARIOS

    with transaction.atomic():
        with connections["default"].cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        source = Store.objects.get(name="Perfamix")
        source_id = source.pk
        public_settings = StoreSettings.objects.filter(store=source).values(
            "system_prompt", "business_facts", "payment_instructions", "bottle_image_url").first() or {}
        fixtures = [(Brand, list(Brand.objects.filter(store=source).values())),
                    (Category, list(Category.objects.filter(store=source).values())),
                    (Product, list(Product.objects.filter(store=source).values())),
                    (ProductVariant, list(ProductVariant.objects.filter(product__store=source).values())),
                    (StaticFAQ, list(StaticFAQ.objects.filter(store=source).values()))]
    connections.close_all()
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.DATABASES["default"]["TEST"]["NAME"] = "test_agent_eval_" + uuid.uuid4().hex[:10]
    test_runner = DiscoverRunner(verbosity=0, interactive=False)
    test_runner.setup_test_environment()
    old = test_runner.setup_databases()
    meter = Meter(args.budget, destination)
    records = []
    skipped = []
    invalidated = []
    if args.resume_from:
        meter.spent = Decimal(prior["accounted_usd"]) + Decimal(prior["reserved_usd"])
        meter.calls = prior["calls"]
        for entry in meter.calls:
            if entry["status"] == "in_flight":
                entry.update(status="interrupted_charged_maximum", cost_usd=entry["reserved_usd"])
        for record in prior_runs:
            if record["id"] in rerun_ids:
                continue
            if any(f[0] == "crash" and "PermissionError" in f[2] for f in record.get("findings", [])):
                invalidated.append(record["id"])
            else:
                records.append(record)
    try:
        store = Store.objects.create(id=source_id, name="Perfamix")
        StoreSettings.objects.create(store=store, **public_settings)
        for model, rows in fixtures:
            model.objects.bulk_create([model(**row) for row in rows])
        truth = checks.build_ground_truth(store)
        write_json(destination / "ground_truth.json", truth)
        (destination / "store_facts.txt").write_text(judge.store_facts(), encoding="utf-8")
        runner.install_probes()
        ai.client = ai.client.with_options(max_retries=0, timeout=90)
        original = ai.client.chat.completions.create
        suites = [("core", list(SCENARIOS))]
        if args.suite == "all":
            replay_scenarios = []
            for module in dict.fromkeys(runner._REPLAYS.values()):
                replay_scenarios.extend(importlib.import_module("eval_harness." + module).SCENARIOS)
            suites.append(("replays", replay_scenarios))
        if args.scenario_ids:
            wanted = list(dict.fromkeys(args.scenario_ids.split(",")))
            cases = {s["id"]: (name, s) for name, group in suites for s in group}
            unknown = set(wanted) - cases.keys()
            if unknown:
                raise ValueError("Unknown scenario IDs: " + ", ".join(sorted(unknown)))
            suites = [("selected", [cases[identifier][1] for identifier in wanted])]
        write_json(destination / "manifest.json", {
            "started_utc": datetime.now(timezone.utc).isoformat(), "budget_usd": args.budget,
            "resume_from": str(args.resume_from) if args.resume_from else None,
            "invalidated_harness_attempts": invalidated,
            "superseded_case_ids": sorted(rerun_ids),
            "temporary_database": settings.DATABASES["default"]["NAME"],
            "models": {key: getattr(settings, key) for key in
                       ("OPENAI_MODEL", "OPENAI_SMART_MODEL", "OPENAI_RESOLVER_MODEL")},
            "suites": {name: [{"id": s["id"], "turns": len(s["turns"])} for s in cases]
                       for name, cases in suites}, "catalogue_products": len(fixtures[2][1]),
            "max_completion_tokens": {"gpt-4.1-mini": 2048, "gpt-5-mini": 8192},
            "isolation": "Disposable local PostgreSQL; read-only source snapshot; every scenario rolls back; requests delivery disabled; no automatic API retries",
        })

        def work(scenario, suite):
            meter.local.scenario = scenario["id"]
            try:
                if meter.halted:
                    raise BudgetStop("Budget guard already stopped the run")
                record = runner.run_scenario(scenario, truth)
                record["suite"] = suite
                # Save the original judge's complete evidence for review without an API charge.
                record["judge_evidence"] = judge.build_prompt(record, record.get("findings", []))
                return record
            finally:
                connections.close_all()

        with ExitStack() as stack:
            stack.enter_context(patch.object(ai.client.chat.completions, "create", side_effect=meter.wrap(original)))
            stack.enter_context(patch("requests.sessions.Session.request", side_effect=RuntimeError("Customer delivery disabled during evaluation")))
            for suite, scenarios in suites:
                completed_ids = {r["id"] for r in records}
                scenarios = [s for s in scenarios if s["id"] not in completed_ids]
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = {pool.submit(work, scenario, suite): scenario for scenario in scenarios}
                    for future in as_completed(futures):
                        scenario = futures[future]
                        try:
                            record = future.result()
                        except BudgetStop as exc:
                            skipped.append({"id": scenario["id"], "suite": suite, "reason": str(exc)})
                            print(json.dumps({"budget_stopped": scenario["id"], "spent_usd": str(meter.spent)}), flush=True)
                        else:
                            records.append(record)
                            write_json(destination / (record["id"] + ".json"), record)
                            print(json.dumps({"completed": record["id"], "turns": len(record["turns"]),
                                              "findings": len(record["findings"]), "error": record["error"],
                                              "spent_usd": str(meter.spent)}, ensure_ascii=False), flush=True)
                        write_json(destination / "runs.json", sorted(records, key=lambda r: r["id"]))
                        write_json(destination / "skipped.json", skipped)
    finally:
        meter.save()
        connections.close_all()
        test_runner.teardown_databases(old)
        test_runner.teardown_test_environment()
    print(json.dumps({"completed": len(records), "skipped": len(skipped), "accounted_usd": str(meter.spent)}), flush=True)
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())
