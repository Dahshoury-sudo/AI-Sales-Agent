"""Validate and report a mixed evaluation: updated cases plus unchanged baseline cases."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "evaluation_20261018"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    request = read(HERE / "incremental_request.json")
    runs = {r["id"]: r for r in read(HERE / "live/runs.json")}
    verdicts = {v["id"]: v for v in read(HERE / "judge/verdicts.json")}
    old_runs = {r["id"]: r for r in read(BASELINE / "live/runs.json")}
    old_verdicts = {v["id"]: v for v in read(BASELINE / "final/verdicts.json")}
    manifest = read(HERE / "live/manifest.json")
    usage = read(HERE / "judge/usage.json")
    live_usage = read(HERE / "live/usage.json")
    old_live_usage = read(BASELINE / "live/usage.json")
    old_usage = read(BASELINE / "final/usage.json")
    expected = {c["id"]: c["turns"] for group in manifest["suites"].values() for c in group}
    rerun = set(request["rerun_ids"])
    retained = set(runs) - rerun
    assert set(runs) == set(expected) == set(verdicts) == set(old_runs) == set(old_verdicts)
    assert rerun == set(manifest["superseded_case_ids"])
    assert not manifest["invalidated_harness_attempts"]
    assert all(len(r["turns"]) == expected[k] for k, r in runs.items())
    assert not read(HERE / "live/skipped.json") and not read(HERE / "judge/skipped.json")
    assert all(runs[k] == old_runs[k] and verdicts[k] == old_verdicts[k] for k in retained)
    assert live_usage["calls"][:len(old_live_usage["calls"])] == old_live_usage["calls"]
    new_live_calls = live_usage["calls"][len(old_live_usage["calls"]):]
    assert {c["scenario"] for c in new_live_calls} <= rerun
    inherited_calls = old_usage["calls"] + new_live_calls
    assert usage["calls"][:len(inherited_calls)] == inherited_calls
    new_judge_calls = usage["calls"][len(inherited_calls):]
    assert {c["scenario"] for c in new_judge_calls} == {"judge:" + k for k in rerun}
    assert Decimal(usage["reserved_usd"]) == Decimal(live_usage["reserved_usd"]) == 0
    assert all(c["status"] == "complete" for c in usage["calls"])
    cost = Decimal(usage["accounted_usd"])
    assert cost == sum(Decimal(c["cost_usd"]) for c in usage["calls"])
    cap = Decimal(request["cumulative_budget_usd"])
    assert cost <= cap
    for relative, digest in request["baseline_sha256"].items():
        assert hashlib.sha256((BASELINE / relative).read_bytes()).hexdigest() == digest, relative
    cleanup = read(HERE / "database_cleanup.json")
    runtime = read(HERE / "runtime_findings.json")
    assert cleanup["cleanup_verified"]
    assert manifest["temporary_database"] in cleanup["temporary_databases_checked"]
    for k in retained:
        # The live runner carries these in runs.json but does not emit their individual files.
        write(HERE / "live" / (k + ".json"), runs[k])
    for k in runs:
        assert read(HERE / "live" / (k + ".json")) == runs[k]
        assert read(HERE / "judge" / (k + ".json")) == verdicts[k]
        values = [v for v in verdicts[k]["scores"].values() if v is not None]
        assert abs(verdicts[k]["mean_score"] - sum(values) / len(values)) < 1e-12
    average = sum(v["mean_score"] for v in verdicts.values()) / len(verdicts)
    old_average = sum(v["mean_score"] for v in old_verdicts.values()) / len(old_verdicts)
    dimensions, categories, suites = defaultdict(list), defaultdict(list), defaultdict(list)
    for verdict in verdicts.values():
        for dimension, value in verdict["scores"].items():
            if value is not None:
                dimensions[dimension].append(value)
        categories[verdict["category"]].append(verdict["mean_score"])
        suites[verdict["suite"]].append(verdict["mean_score"])
    aggregate = lambda groups: {key: {"score": sum(values) / len(values), "n": len(values)} for key, values in sorted(groups.items())}
    changes = [{"id": k, "previous": old_verdicts[k]["mean_score"], "current": verdicts[k]["mean_score"],
                "delta": verdicts[k]["mean_score"] - old_verdicts[k]["mean_score"]} for k in sorted(rerun)]
    new_live_cost = Decimal(live_usage["accounted_usd"]) - Decimal(old_live_usage["accounted_usd"])
    new_judge_cost = sum(Decimal(c["cost_usd"]) for c in new_judge_calls)
    assert cost == Decimal(old_usage["accounted_usd"]) + new_live_cost + new_judge_cost
    findings = Counter(f[0] for r in runs.values() for f in r["findings"])
    old_summary = read(BASELINE / "summary.json")
    summary = {
        "completed_utc": datetime.now(timezone.utc).isoformat(), "commit": request["commit"],
        "score_out_of_10": average, "rounded_score": round(average, 1),
        "baseline_score_out_of_10": old_average, "score_delta": average - old_average,
        "score_method": "Mean applicable rubric dimensions per scenario, then equal-weight mean across all 63 scenarios; 24 updated and 39 retained.",
        "baseline": str(BASELINE), "baseline_files_unchanged": True,
        "incremental": {"rerun_ids": sorted(rerun), "retained_ids": sorted(retained),
                        "rerun_scenarios": len(rerun), "retained_scenarios": len(retained),
                        "rerun_turns": sum(len(runs[k]["turns"]) for k in rerun),
                        "transcripts_and_grades_preserved": True,
                        "selected_previous_score": sum(old_verdicts[k]["mean_score"] for k in rerun) / len(rerun),
                        "selected_current_score": sum(verdicts[k]["mean_score"] for k in rerun) / len(rerun)},
        "live": {"scenarios": len(runs), "turns": sum(len(r["turns"]) for r in runs.values()),
                 "new_api_calls": len(new_live_calls), "skipped": 0,
                 "unhandled_scenario_errors": sum(bool(r["error"]) for r in runs.values()),
                 "raw_findings": dict(findings)},
        "grading": {"scenarios": len(verdicts), "new_api_calls": len(new_judge_calls),
                    "retained_grades": len(retained), "model": "gpt-5-mini", "reasoning_effort": "low",
                    "checker_corrections": read(HERE / "judge/finding_adjudications.json")},
        "cost": {"budget_usd": str(cap), "baseline_total_usd": old_usage["accounted_usd"],
                 "incremental_live_usd": str(new_live_cost), "incremental_grading_usd": str(new_judge_cost),
                 "incremental_total_usd": str(new_live_cost + new_judge_cost), "total_usd": str(cost),
                 "remaining_usd": str(cap - cost)},
        "dimensions": aggregate(dimensions), "categories": aggregate(categories), "suites": aggregate(suites),
        "changes": changes, "models": manifest["models"], "catalogue_products": manifest["catalogue_products"],
        "database_cleanup_verified": True,
        "runtime_findings": runtime,
        "offline": {"rerun": False, "baseline_results": old_summary["offline"]},
        "backtests": {"rerun": False, "baseline_results": old_summary["backtests"]},
    }
    write(HERE / "summary.json", summary)
    with (HERE / "scenario_scores.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scenario", "suite", "category", "score_out_of_10", "previous_score", "delta", "status", "turns", "raw_flags"])
        for k, v in sorted(verdicts.items(), key=lambda pair: pair[1]["mean_score"]):
            writer.writerow([k, v["suite"], v["category"], round(v["mean_score"], 3), round(old_verdicts[k]["mean_score"], 3),
                             round(v["mean_score"] - old_verdicts[k]["mean_score"], 3), "updated" if k in rerun else "retained",
                             len(runs[k]["turns"]), len(runs[k]["findings"])])
    change_rows = "\n".join(f"| {c['id']} | {c['previous']:.2f} | {c['current']:.2f} | {c['delta']:+.2f} |" for c in sorted(changes, key=lambda c: c["delta"], reverse=True))
    dimension_rows = "\n".join(f"| {k.replace('_', ' ')} | {v['score']:.2f} | {v['n']} |" for k, v in sorted(summary["dimensions"].items(), key=lambda p: p[1]["score"]))
    weakest = sorted(rerun, key=lambda k: verdicts[k]["mean_score"])[:5]
    weak_lines = []
    for k in weakest:
        conclusion = " ".join(str(verdicts[k].get("salesperson_verdict", "")).split())
        weak_lines.append(f"- **{k}: {verdicts[k]['mean_score']:.2f}/10.** {conclusion}")
    weak_text = "\n".join(weak_lines)
    report = f"""# Incremental perfume agent evaluation — 19 October 2026

**Overall score: {average:.2f}/10 (previously {old_average:.2f}; change {average - old_average:+.2f}).**

The combined result contains {len(runs)} scenarios: {len(rerun)} newly run and graded, plus {len(retained)} unchanged transcripts and grades from [18 October](../evaluation_20261018/report.md). Only the 24 supplied scenario IDs were rerun. Successful cases outside that list generated no new live or grading requests. This is an incremental score combining two source revisions, not a fresh full-suite evaluation.

| Check | Result |
|---|---|
| Updated conversations | {len(rerun)}; {summary['incremental']['rerun_turns']} turns |
| Retained conversations | {len(retained)}; transcripts and grades verified identical |
| Combined evidence | {len(runs)} conversations; {summary['live']['turns']} turns |
| Completion | No skipped cases or grades; {summary['live']['unhandled_scenario_errors']} unhandled scenario errors |
| Caught name-confirmation exceptions in updated cases | {runtime['handled_name_confirmation_exceptions']} |
| New API requests | {len(new_live_calls)} agent + {len(new_judge_calls)} judge |
| Updated subset score | {summary['incremental']['selected_previous_score']:.2f} → {summary['incremental']['selected_current_score']:.2f} |
| Combined core / historical scores | {summary['suites']['core']['score']:.2f} / {summary['suites']['replays']['score']:.2f} |
| Baseline artifacts | All original file hashes unchanged |
| Disposable database | Cleanup verified |

The live runner resumed from `artifacts/agent_evaluation/evaluation_20261018/live`, using `--rerun-ids` and a live cumulative cap of ${request['live_budget_usd']}. The ${request['prior_grading_usd']} previously spent on grading is also included in the final ${cap:.2f} cumulative cap.

## Updated scores

| Scenario | Previous / 10 | Updated / 10 | Change |
|---|---:|---:|---:|
{change_rows}

## Lowest scoring updated cases

The following are the judge's assessments; the saved transcripts and verdicts contain the supporting evidence.

{weak_text}

## Combined rubric scores

| Dimension | Score / 10 | Scenarios |
|---|---:|---:|
{dimension_rows}

Scores use the repository's unmodified 12-dimension rubric with `gpt-5-mini`, low reasoning effort: average non-null dimensions within each scenario, then weight scenarios equally. Retained grades are copied from the baseline's final verdicts. Old manual checker corrections are not automatically reused on changed transcripts; this run's corrections are saved in [finding adjudications](judge/finding_adjudications.json). Raw checker flags are preserved: `{dict(findings)}`. These flags are diagnostics, not independent defect counts. Scores reflect one model-judged sample and can vary between runs.

The live log contains {runtime['handled_name_confirmation_exceptions']} caught `ValueError: Invalid format specifier ' true'` exceptions from the name-confirmation path in `products/services/product_resolver.py`. These are distinct from unhandled scenario errors: the agent falls back and continues. See [runtime findings](runtime_findings.json). Catalogue ground truth matches the baseline: {runtime['catalogue_ground_truth_matches_baseline']}.

## Cost and provenance

| Cost component | USD |
|---|---:|
| Baseline live and grading | {Decimal(old_usage['accounted_usd']):.8f} |
| New live calls | {new_live_cost:.8f} |
| New grading calls | {new_judge_cost:.8f} |
| Incremental total | {new_live_cost + new_judge_cost:.8f} |
| Cumulative total | {cost:.8f} |
| Cumulative cap | {cap:.2f} |
| Remaining | {cap - cost:.8f} |

The ledger includes all baseline attempts and every new request, with cached-input discounts. Taxes and credits are excluded. No unresolved reservations remain. New cases used source revision `{request['commit']}` and a read-only snapshot of the {manifest['catalogue_products']}-product catalogue in a disposable local PostgreSQL database. Customer delivery was disabled. Retained evidence comes from baseline revision `{old_summary['commit']}`.

Offline tests and historical backtests were not rerun. Their previous results remain in the [baseline report](../evaluation_20261018/report.md) and do not count toward this quality score or establish current regression coverage.

Evidence: [summary](summary.json), [all scenario scores](scenario_scores.csv), [live transcripts](live/runs.json), [judge verdicts](judge/verdicts.json), [cumulative usage](judge/usage.json), [live log](live_console.log), [judge log](judge_console.log), [request and baseline hashes](incremental_request.json), [database cleanup](database_cleanup.json).
"""
    (HERE / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"score": average, "previous": old_average, "incremental_usd": str(new_live_cost + new_judge_cost),
                      "cumulative_usd": str(cost), "updated": len(rerun), "retained": len(retained), "report": str(HERE / "report.md")}))


if __name__ == "__main__":
    main()
