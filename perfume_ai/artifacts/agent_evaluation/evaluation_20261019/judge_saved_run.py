"""Grade only replaced transcripts; preserve baseline grades and every API charge."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from openai import OpenAI
from scripts.evaluate_agent_budgeted import BudgetStop, Meter, write_json

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "evaluation_20261018"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=Decimal, default=Decimal("1.50"))
    parser.add_argument("--resume", action="store_true", help="Retry only missing grades, retaining all charges")
    args = parser.parse_args()
    live, output = HERE / "live", HERE / "judge"
    output.mkdir(exist_ok=True)
    exists = (output / "usage.json").exists()
    if exists and not args.resume:
        parser.error("Existing cost evidence: use --resume to retry missing grades")
    baseline_live = read(BASELINE / "live/usage.json")
    baseline_final = read(BASELINE / "final/usage.json")
    live_usage = read(live / "usage.json")
    assert not Decimal(live_usage["reserved_usd"]), "Live requests still running"
    assert not read(live / "skipped.json"), "Live run incomplete"
    old_calls = baseline_live["calls"]
    assert live_usage["calls"][:len(old_calls)] == old_calls
    assert baseline_final["calls"][:len(old_calls)] == old_calls
    new_calls = live_usage["calls"][len(old_calls):]
    records = {r["id"]: r for r in read(live / "runs.json")}
    old_records = {r["id"]: r for r in read(BASELINE / "live/runs.json")}
    old_verdicts = {v["id"]: v for v in read(BASELINE / "final/verdicts.json")}
    rerun = set(read(live / "manifest.json")["superseded_case_ids"])
    assert set(records) == set(old_records) == set(old_verdicts)
    assert {c["scenario"] for c in new_calls} <= rerun
    retained = set(records) - rerun
    assert all(records[k] == old_records[k] for k in retained)
    tree = ast.parse((ROOT / "eval_harness/judge.py").read_text(encoding="utf-8"))
    rubric = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "RUBRIC" for t in n.targets))
    meter = Meter(args.budget, output)
    if exists:
        previous = read(output / "usage.json")
        meter.spent = Decimal(previous["accounted_usd"]) + Decimal(previous["reserved_usd"])
        meter.calls = previous["calls"]
        for entry in meter.calls:
            if entry["status"] == "in_flight":
                entry.update(status="interrupted_charged_maximum", cost_usd=entry["reserved_usd"])
        verdicts = {v["id"]: v for v in read(output / "verdicts.json")}
        assert all(verdicts[k] == old_verdicts[k] for k in retained)
    else:
        meter.spent = Decimal(baseline_final["accounted_usd"]) + Decimal(baseline_final["reserved_usd"])
        meter.spent += Decimal(live_usage["accounted_usd"]) - Decimal(baseline_live["accounted_usd"])
        meter.calls = baseline_final["calls"] + new_calls
        verdicts = {k: old_verdicts[k] for k in retained}
    if meter.spent >= args.budget:
        parser.error("Prior charges already consume the cumulative budget")
    assert meter.spent == sum(Decimal(c["cost_usd"]) for c in meter.calls)
    meter.save()
    write_json(output / "verdicts.json", sorted(verdicts.values(), key=lambda v: v["id"]))
    # Prior manual corrections describe old replies and must not be applied to new ones.
    adjudications_path = HERE / "finding_adjudications.json"
    adjudications = read(adjudications_path) if adjudications_path.exists() else {}
    assert set(adjudications) <= rerun
    write_json(output / "finding_adjudications.json", adjudications)
    write_json(output / "manifest.json", {
        "rubric": "eval_harness/judge.py:RUBRIC (unmodified)",
        "rubric_sha256": hashlib.sha256(rubric.encode()).hexdigest(),
        "model": "gpt-5-mini", "reasoning_effort": "low", "workers": 3,
        "source": str(live), "baseline_verdicts": str(BASELINE / "final/verdicts.json"),
        "rerun_ids": sorted(rerun), "retained_ids": sorted(retained),
        "expected_scenarios": len(records), "cumulative_budget_usd": str(args.budget),
        "baseline_total_usd": baseline_final["accounted_usd"],
        "incremental_live_usd": str(Decimal(live_usage["accounted_usd"]) - Decimal(baseline_live["accounted_usd"])),
        "score_method": "Mean of non-null dimensions per scenario; equal-weight mean across all scenarios",
    })
    config = dotenv_values(ROOT / ".env")
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY") or config.get("OPENAI_API_KEY"), max_retries=0, timeout=90)
    create = meter.wrap(client.chat.completions.create)

    def grade(record):
        meter.local.scenario = "judge:" + record["id"]
        evidence = record["judge_evidence"]
        if record["id"] in adjudications:
            evidence += "\n\nVERIFIED CHECKER CORRECTION:\n" + adjudications[record["id"]]
        response = create(model="gpt-5-mini", reasoning_effort="low",
                          response_format={"type": "json_object"},
                          messages=[{"role": "system", "content": rubric}, {"role": "user", "content": evidence}])
        raw = response.choices[0].message.content
        # Keep malformed attempts as evidence if a missing verdict needs a later retry.
        attempt = sum(c["scenario"] == meter.local.scenario for c in meter.calls)
        write_json(output / f"raw_{record['id']}_{attempt}.json", {"content": raw})
        verdict = json.loads(raw)
        scores = verdict.get("scores", {})
        dimensions = {"intent_understanding", "constraint_extraction", "memory", "recommendation_quality",
                      "similarity_accuracy", "product_accuracy", "confidence_calibration", "objection_handling",
                      "sales_effectiveness", "closing_timing", "human_feel", "trust"}
        nullable = {"similarity_accuracy", "objection_handling", "closing_timing"}
        if set(scores) != dimensions or any(
            (v is None and k not in nullable) or (v is not None and
            (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 10))
            for k, v in scores.items()
        ):
            raise ValueError("Judge returned incomplete or malformed scores")
        values = [v for v in scores.values() if v is not None]
        verdict.update(id=record["id"], category=record["category"], suite=record["suite"], mean_score=sum(values) / len(values))
        return verdict

    skipped = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(grade, records[k]): k for k in sorted(rerun - verdicts.keys())}
            for future in as_completed(futures):
                identifier = futures[future]
                try:
                    verdict = future.result()
                except (BudgetStop, Exception) as exc:
                    skipped.append({"id": identifier, "reason": str(exc), "type": type(exc).__name__})
                    print(json.dumps(skipped[-1]), flush=True)
                else:
                    verdicts[identifier] = verdict
                    write_json(output / (identifier + ".json"), verdict)
                    print(json.dumps({"id": identifier, "score": round(verdict["mean_score"], 2), "cumulative_usd": str(meter.spent)}), flush=True)
                write_json(output / "verdicts.json", sorted(verdicts.values(), key=lambda v: v["id"]))
                write_json(output / "skipped.json", skipped)
    finally:
        meter.save()
        client.close()
    for identifier in retained:
        write_json(output / (identifier + ".json"), verdicts[identifier])
    write_json(output / "skipped.json", skipped)
    print(json.dumps({"graded_new": len(rerun & verdicts.keys()), "retained": len(retained), "skipped": len(skipped), "cumulative_usd": str(meter.spent)}), flush=True)
    return int(bool(skipped))


if __name__ == "__main__":
    raise SystemExit(main())
