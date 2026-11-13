"""Run the 20 adaptive customer simulations against a disposable catalogue, under one shared USD cap.

Usage:
  python scripts/simulate_customers.py --phase baseline
  python scripts/simulate_customers.py --phase debug-S10 --scenarios S10_dislikes
  python scripts/simulate_customers.py --phase final

Every paid request — seller, customer simulator and judge — goes through the budget meter from
scripts/evaluate_agent_budgeted.py, whose spending is persisted in ONE ledger for the whole task
(artifacts/customer_simulation_20/ledger.json). Each run loads the ledger, carries every earlier
charge forward (an interrupted in-flight request is charged at its full reservation), reserves the
maximum cost before each request, and refuses the request when the remaining cap cannot cover it.
The cap is never raised automatically.

Isolation: the Perfamix catalogue/store facts are copied read-only from the local TEST_DATABASE_URL
into a disposable test database; each scenario runs in a rolled-back transaction; outbound HTTP
(customer messaging) is disabled; checkout and orders run inside the test database only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_agent_budgeted import (  # noqa: E402
    BudgetStop, LUNA_COMPLETION_LIMITS, LUNA_CACHE_WRITE_MULTIPLIER, Meter, PRICES, write_json,
)

OUTPUT = ROOT / "artifacts" / "customer_simulation_20"
# Raised from 3.00 to 4.00 by the store owner on 2026-11-12, then set to 3.50 by the owner later the
# same day (at most USD 0.50 on top of the 2.9992 spent). On 2026-11-13 the owner set 3.64 for one
# verification run and then 4.06 for a fourth fix round (USD 0.50 on top of the 3.5680 spent). Every
# change is recorded in the ledger's cap_changes; the meter refuses to run when the two disagree.
TASK_CAP = Decimal("4.06")
SCORE_FLOOR = 6


class LedgerMeter(Meter):
    """The existing meter, persisted to one cumulative ledger instead of a per-run usage file."""

    def __init__(self, cap, ledger_path, phase, run_id):
        super().__init__(cap, ledger_path.parent)
        self.ledger_path, self.phase, self.run_id = ledger_path, phase, run_id
        self.runs = []
        self.cap_changes = []
        if ledger_path.exists():
            prior = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.cap_changes = prior.get("cap_changes", [])
            # A cap may change only through a recorded, owner-authorised entry; never implicitly.
            authorised = Decimal(self.cap_changes[-1]["to_usd"]) if self.cap_changes else Decimal(prior["cap_usd"])
            if Decimal(cap) != authorised:
                raise SystemExit("Ledger cap differs from the requested cap; refusing to change it")
            self.calls, self.runs = prior["calls"], prior.get("runs", [])
            for entry in self.calls:
                if entry["status"] == "in_flight":
                    entry.update(status="interrupted_charged_maximum", cost_usd=entry["reserved_usd"])
            self.spent = sum((Decimal(str(entry.get("cost_usd", 0))) for entry in self.calls), Decimal(0))
        self.runs.append({"run_id": run_id, "phase": phase, "started_utc": datetime.now(timezone.utc).isoformat(),
                          "spent_before_usd": str(self.spent)})

    def save(self):
        write_json(self.ledger_path, {
            "task": "customer_simulation_20", "cap_usd": self.cap, "cap_changes": self.cap_changes,
            "accounted_usd": self.spent,
            "reserved_usd": self.reserved, "remaining_usd": self.cap - self.spent - self.reserved,
            "runs": self.runs, "calls": self.calls, "pricing_per_million": PRICES,
            "cache_write_multiplier": {"gpt-6-luna": LUNA_CACHE_WRITE_MULTIPLIER},
            "note": "Cumulative for the whole task. Standard tier; cached reads and cache writes priced separately; "
                    "reasoning tokens are part of completion tokens; failed or uncertain requests keep their full "
                    "reservation. Taxes/credits excluded.",
        })


def acquire_lock(path):
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"{path} exists: another run may be spending. Remove it only after confirming no run is active.")
    os.write(handle, str(os.getpid()).encode())
    os.close(handle)


def preload_seller_code():
    """Import every seller module now and fingerprint the code that was loaded.

    A module first imported mid-run would pick up edits made on disk after the run started, so the
    run could mix two code states. Everything is imported up front; `code_sha256` in config.json is
    the exact source this run used (tests and migrations excluded).
    """
    import importlib
    import pkgutil
    import products.services
    failed = []
    for module in pkgutil.walk_packages(products.services.__path__, "products.services."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # recorded, never fatal: the module was not importable at start either
            failed.append(f"{module.name}: {type(exc).__name__}")
    digest = hashlib.sha256()
    files = [path for path in sorted([*ROOT.glob("products/**/*.py"), *ROOT.glob("eval_harness/*.py"),
                                      ROOT / "scripts" / "simulate_customers.py",
                                      ROOT / "scripts" / "evaluate_agent_budgeted.py"])
             if "__pycache__" not in path.parts and "migrations" not in path.parts
             and not path.name.startswith("test")]
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    return {"code_sha256": digest.hexdigest(), "files": len(files), "import_failures": failed}


def git_state():
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    diff = run("diff", "HEAD", "--", "products")
    return {"commit": run("rev-parse", "HEAD").strip(), "products_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "products_diff_lines": diff.count("\n")}


def _finding_code(value):
    """The code of a reviewed finding. The judge sometimes echoes it with its detail
    ("unknown_latin_token: 'dry'"); compared as the whole string, that explicit dismissal was ignored
    (S02 round 2 batch 2, S10 round 2 full run 1)."""
    match = re.match(r"\s*([A-Za-z_]+)", str(value or ""))
    return match.group(1) if match else ""


def verdict_for(record, judged):
    """One rule for baseline and verification alike."""
    reasons = []
    if record.get("error"):
        reasons.append("error: " + record["error"])
    review = (judged or {}).get("deterministic_review", [])
    keys = {verdict: {(item.get("turn"), _finding_code(item.get("code"))) for item in review
                      if item.get("verdict") == verdict} for verdict in ("dismissed", "confirmed")}
    dismissed = keys["dismissed"] - keys["confirmed"]  # a mixed review of one turn's code stands
    for turn in record["turns"]:
        for code, severity, detail in turn["findings"]:
            if severity in ("critical", "high") and (turn["n"], code) not in dismissed:
                reasons.append(f"turn {turn['n']} {severity} {code}: {detail[:140]}")
    turn_findings = {tuple(f) for t in record["turns"] for f in t["findings"]}
    # A conversation-level finding has no turn of its own; the judge reviews it at the turn it happened.
    dismissed_codes = {code for _, code in dismissed} - {code for _, code in keys["confirmed"]}
    for code, severity, detail in record["findings"]:
        if (code, severity, detail) not in turn_findings and severity in ("critical", "high") \
                and code not in dismissed_codes:
            reasons.append(f"conversation {severity} {code}: {detail[:140]}")
    if not judged or judged.get("error"):
        reasons.append("judge unavailable: " + str((judged or {}).get("error")))
    else:
        for name, score in (judged.get("scores") or {}).items():
            if isinstance(score, (int, float)) and score < SCORE_FLOOR:
                reasons.append(f"judge score {name}={score}")
        for issue in judged.get("issues") or []:
            if issue.get("severity") in ("critical", "high"):
                reasons.append(f"judge turn {issue.get('turn')} {issue.get('severity')} {issue.get('category')}: "
                               f"{str(issue.get('why') or issue.get('expected'))[:160]}")
        for criterion in judged.get("criteria") or []:
            if criterion.get("met") is False:
                reasons.append(f"criterion unmet: {str(criterion.get('criterion'))[:100]}")
    return ("pass" if not reasons else "fail"), reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", required=True, help="baseline, final, or debug-<label>")
    parser.add_argument("--scenarios", help="Comma-separated scenario IDs (default: all 20)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--judge-effort", choices=("none", "high"), default="high")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--attempt", help="Label for a repeated phase run; earlier attempts are kept")
    parser.add_argument("--rejudge", type=Path,
                        help="Re-grade the saved transcripts in this attempt folder with the current judge (no seller calls)")
    args = parser.parse_args()

    OUTPUT.mkdir(parents=True, exist_ok=True)
    phase_dir = OUTPUT / args.phase / (args.attempt or "attempt-1")
    if phase_dir.exists() and any(phase_dir.iterdir()):
        parser.error(f"{phase_dir} already has results; pass a new --attempt so earlier attempts are kept")
    phase_dir.mkdir(parents=True, exist_ok=True)
    lock = OUTPUT / "ledger.lock"
    acquire_lock(lock)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    try:
        return _run(args, phase_dir, run_id)
    finally:
        lock.unlink(missing_ok=True)


def _run(args, phase_dir, run_id):
    import dj_database_url
    from dotenv import dotenv_values
    config = dotenv_values(ROOT / ".env")
    url = os.environ.get("TEST_DATABASE_URL") or config.get("TEST_DATABASE_URL")
    if dj_database_url.parse(url or "").get("HOST") not in {"localhost", "127.0.0.1", "::1"}:
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
    from eval_harness import checks, judge, runner, simulation
    from eval_harness.scenarios_customer_20 import SCENARIOS, SCENARIOS_BY_ID
    code = preload_seller_code()
    print(json.dumps({"code": code}), flush=True)

    wanted = [s.strip() for s in (args.scenarios or "").split(",") if s.strip()]
    unknown = set(wanted) - set(SCENARIOS_BY_ID)
    if unknown:
        raise SystemExit("Unknown scenarios: " + ", ".join(sorted(unknown)))
    scenarios = [SCENARIOS_BY_ID[s] for s in wanted] if wanted else list(SCENARIOS)

    with transaction.atomic():
        with connections["default"].cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        source = Store.objects.get(name="Perfamix")
        source_id = source.pk
        public_settings = StoreSettings.objects.filter(store=source).values(
            "system_prompt", "business_facts", "promotions", "payment_instructions", "bottle_image_url").first() or {}
        fixtures = [(Brand, list(Brand.objects.filter(store=source).values())),
                    (Category, list(Category.objects.filter(store=source).values())),
                    (Product, list(Product.objects.filter(store=source).values())),
                    (ProductVariant, list(ProductVariant.objects.filter(product__store=source).values())),
                    (StaticFAQ, list(StaticFAQ.objects.filter(store=source).values()))]
    connections.close_all()
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.DATABASES["default"]["TEST"]["NAME"] = "test_customer_sim_" + uuid.uuid4().hex[:10]
    test_runner = DiscoverRunner(verbosity=0, interactive=False)
    test_runner.setup_test_environment()
    old = test_runner.setup_databases()
    meter = LedgerMeter(TASK_CAP, OUTPUT / "ledger.json", args.phase, run_id)
    meter.save()
    print(json.dumps({"run_id": run_id, "phase": args.phase, "spent_before_usd": str(meter.spent),
                      "remaining_usd": str(meter.cap - meter.spent)}), flush=True)
    records, summary = {}, []
    print_lock = threading.Lock()
    try:
        store = Store.objects.create(id=source_id, name="Perfamix")
        StoreSettings.objects.create(store=store, **public_settings)
        for model, rows in fixtures:
            model.objects.bulk_create([model(**row) for row in rows])
        truth = checks.build_ground_truth(store)
        tag_map = {p.name: list(p.scent_tags or []) for p in Product.objects.filter(store=store, is_active=True)}
        catalogue = {name: {"scent_tags": tag_map.get(name), "variants": variants}
                     for name, variants in truth["variant_prices"].items()}
        write_json(phase_dir / "catalog_facts.json", {"products": catalogue, "active": sorted(truth["available_names"])})
        store_facts_source = "current store data"
        saved_facts = Path(args.rejudge) / "store_facts.txt" if args.rejudge else None
        if saved_facts is not None and saved_facts.exists():
            # A re-grade judges saved conversations against the store facts they ran on. The owner
            # changed the store data on 2026-11-12 (95%, 750-2000, shipping and delivery moved from FAQ
            # entries into the business facts, "2 لـ 4 أيام" became "يومين ل 5"); graded against
            # today's facts, a baseline reply that quoted the facts of its day would count as wrong.
            facts_then = saved_facts.read_text(encoding="utf-8")
            judge.store_facts = lambda: facts_then
            store_facts_source = "saved with the re-graded run: " + str(saved_facts)
        (phase_dir / "store_facts.txt").write_text(judge.store_facts(), encoding="utf-8")
        runner.install_probes()
        simulation.install_claim_probe()
        ai.client = ai.client.with_options(max_retries=0, timeout=120)
        original = ai.client.chat.completions.create
        from products.services.ai.client import _profiles
        write_json(phase_dir / "config.json", {
            "run_id": run_id, "phase": args.phase, "attempt": args.attempt or "attempt-1",
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "seller_models": {key: getattr(settings, key) for key in
                              ("OPENAI_MODEL", "OPENAI_SMART_MODEL", "OPENAI_RESOLVER_MODEL", "OPENAI_TEMPERATURE")},
            "seller_profiles": _profiles(),
            "seller_reasoning_effort": "Luna: none for extract/converse, high for reason/resolve (client.chat)",
            "simulator": {"model": settings.OPENAI_MODEL, "reasoning_effort": "none", "temperature": 0.6,
                          "prompt_sha256": hashlib.sha256(simulation.CUSTOMER_SYSTEM_PROMPT.encode()).hexdigest()},
            "judge": {"model": settings.OPENAI_MODEL, "reasoning_effort": args.judge_effort, "enabled": not args.no_judge,
                      "rubric_sha256": hashlib.sha256(judge.SIMULATION_RUBRIC.encode()).hexdigest()},
            "max_completion_tokens": {"gpt-6-luna": LUNA_COMPLETION_LIMITS},
            "pass_rule": f"no error; no critical/high deterministic finding unless the judge dismissed it with a reason; "
                         f"no judge critical/high issue; every judge score >= {SCORE_FLOOR}; no unmet success criterion",
            "task_cap_usd": str(TASK_CAP), "workers": args.workers, "git": git_state(), "code": code,
            "temporary_database": settings.DATABASES["default"]["NAME"],
            "catalogue_products": len(fixtures[2][1]),
            "isolation": "Disposable local PostgreSQL copy; read-only source snapshot; scenario transactions rolled back; "
                         "outbound HTTP disabled; no automatic API retries",
            "scenarios": [s["id"] for s in scenarios],
            "rejudge_source": str(args.rejudge) if args.rejudge else None,
            "judge_store_facts": store_facts_source,
        })

        def simulate(scenario, turns):
            meter.local.role = "simulator"
            try:
                return simulation.next_customer_message(scenario, turns, ai.client.chat.completions.create,
                                                        settings.OPENAI_MODEL)
            finally:
                meter.local.role = "seller"

        def judge_record(record, scenario):
            meter.local.role = "judge"
            prompt = judge.build_simulation_prompt(record, scenario)
            record["judge_prompt"] = prompt
            kwargs = {"model": settings.OPENAI_MODEL, "reasoning_effort": args.judge_effort,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": judge.SIMULATION_RUBRIC},
                                   {"role": "user", "content": prompt}]}
            if args.judge_effort == "none":
                kwargs["temperature"] = 0
            try:
                response = ai.client.chat.completions.create(**kwargs)
                return judge.parse_simulation_verdict(response.choices[0].message.content)
            except BudgetStop:
                raise
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        def on_turn(record, turn):
            with print_lock:
                print(json.dumps({"scenario": record["id"], "turn": turn["n"], "user": turn["user"][:60],
                                  "reply": (turn["reply"] or "")[:80], "findings": [f[0] for f in turn["findings"]],
                                  "spent_usd": str(meter.spent)}, ensure_ascii=False), flush=True)

        def work(scenario):
            meter.local.scenario, meter.local.role = scenario["id"], "seller"
            try:
                if args.rejudge:
                    # Same saved transcript, current judge: only the grading is repeated.
                    record = json.loads((Path(args.rejudge) / f"{scenario['id']}.json").read_text(encoding="utf-8"))
                    record["rejudged_from"] = {"path": str(args.rejudge), "verdict": record.get("verdict"),
                                               "judge": record.get("judge")}
                    record.pop("usage", None)
                else:
                    record = simulation.run_simulated_scenario(scenario, truth, tag_map, simulate, on_turn=on_turn)
                judged = None
                if not args.no_judge and not record.get("budget_stopped"):
                    try:
                        judged = judge_record(record, scenario)
                    except BudgetStop as exc:
                        record["budget_stopped"] = True
                        judged = {"error": f"BudgetStop: {exc}"}
                record["judge"] = judged
                record["verdict"], record["verdict_reasons"] = verdict_for(record, judged)
                if record.get("budget_stopped"):
                    record["verdict"] = "incomplete"
                return record
            finally:
                connections.close_all()

        with ExitStack() as stack:
            stack.enter_context(patch.object(ai.client.chat.completions, "create", side_effect=meter.wrap(original)))
            stack.enter_context(patch("requests.sessions.Session.request",
                                      side_effect=RuntimeError("Customer delivery disabled during evaluation")))
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(work, scenario): scenario for scenario in scenarios}
                for future in as_completed(futures):
                    scenario = futures[future]
                    try:
                        record = future.result()
                    except BaseException as exc:  # keep going; record what happened
                        record = {"id": scenario["id"], "title": scenario["title"], "turns": [], "findings": [],
                                  "error": f"{type(exc).__name__}: {exc}", "verdict": "incomplete",
                                  "verdict_reasons": [str(exc)]}
                    calls = [c for c in meter.calls if c.get("run_id") == run_id and c.get("scenario") == scenario["id"]]
                    record["usage"] = {
                        "api_calls": len(calls),
                        "by_role": {role: {"calls": sum(1 for c in calls if c.get("role") == role),
                                           "cost_usd": str(sum((Decimal(str(c.get("cost_usd", 0))) for c in calls
                                                                if c.get("role") == role), Decimal(0)))}
                                    for role in ("seller", "simulator", "judge")},
                        "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
                        "cached_tokens": sum(c.get("cached_tokens", 0) for c in calls),
                        "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
                        "reasoning_tokens": sum(c.get("reasoning_tokens", 0) for c in calls),
                        "cost_usd": str(sum((Decimal(str(c.get("cost_usd", 0))) for c in calls), Decimal(0))),
                        "api_seconds": round(sum(c.get("seconds", 0) for c in calls), 1),
                        "failed_calls": [c for c in calls if c.get("status") != "complete" or c.get("response_error")],
                    }
                    records[scenario["id"]] = record
                    write_json(phase_dir / f"{scenario['id']}.json", record)
                    row = {"id": scenario["id"], "verdict": record.get("verdict"), "turns": len(record.get("turns") or []),
                           "ended_by": record.get("ended_by"),
                           "scores": (record.get("judge") or {}).get("scores"),
                           "reasons": record.get("verdict_reasons", [])[:12],
                           "cost_usd": record["usage"]["cost_usd"], "api_calls": record["usage"]["api_calls"]}
                    summary.append(row)
                    with print_lock:
                        print(json.dumps({"completed": scenario["id"], "verdict": row["verdict"], "turns": row["turns"],
                                          "cost_usd": row["cost_usd"], "spent_usd": str(meter.spent)},
                                         ensure_ascii=False), flush=True)
                    write_json(phase_dir / "summary.json", sorted(summary, key=lambda r: r["id"]))
    finally:
        meter.runs[-1].update(ended_utc=datetime.now(timezone.utc).isoformat(), spent_after_usd=str(meter.spent),
                              scenarios=sorted(records))
        meter.save()
        connections.close_all()
        test_runner.teardown_databases(old)
        test_runner.teardown_test_environment()
    run_calls = [c for c in meter.calls if c.get("run_id") == run_id]
    totals = {
        "run_id": run_id, "phase": args.phase, "conversations": len(records),
        "passed": sum(r.get("verdict") == "pass" for r in records.values()),
        "failed": sum(r.get("verdict") == "fail" for r in records.values()),
        "incomplete": sum(r.get("verdict") == "incomplete" for r in records.values()),
        "api_calls": len(run_calls),
        "run_cost_usd": str(sum((Decimal(str(c.get("cost_usd", 0))) for c in run_calls), Decimal(0))),
        "cumulative_spent_usd": str(meter.spent), "remaining_usd": str(meter.cap - meter.spent),
    }
    write_json(phase_dir / "totals.json", totals)
    print(json.dumps(totals), flush=True)
    return 0 if not totals["incomplete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
