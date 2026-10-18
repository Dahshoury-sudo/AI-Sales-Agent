"""Summarize this evaluation without making any API calls."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads((HERE / path).read_text(encoding="utf-8"))


def main():
    runs = read("live/runs.json")
    verdicts = read("final/verdicts.json")
    manifest = read("live/manifest.json")
    usage = read("final/usage.json")
    live_usage = read("live/usage.json")
    expected = {c["id"]: c["turns"] for group in manifest["suites"].values() for c in group}
    assert {r["id"] for r in runs} == set(expected) == {v["id"] for v in verdicts}
    assert all(len(r["turns"]) == expected[r["id"]] for r in runs)
    assert not read("live/skipped.json")
    assert Decimal(usage["reserved_usd"]) == 0
    assert all(c["status"] == "complete" for c in usage["calls"])
    cost = Decimal(usage["accounted_usd"])
    assert cost == sum(Decimal(c["cost_usd"]) for c in usage["calls"])
    assert cost <= Decimal("1.00")
    assert read("database_cleanup.json")["cleanup_verified"]
    offline = (HERE / "offline_tests.log").read_text(encoding="utf-8")
    tests, duration = re.search(r"Ran (\d+) tests in ([\d.]+)s", offline).groups()
    assert "\nOK\n" in offline and "UNMOCKED_HTTP_ATTEMPTS 0" in offline
    average = sum(v["mean_score"] for v in verdicts) / len(verdicts)
    dimensions, categories, suites = defaultdict(list), defaultdict(list), defaultdict(list)
    for verdict in verdicts:
        for dimension, value in verdict["scores"].items():
            if value is not None:
                dimensions[dimension].append(value)
        categories[verdict["category"]].append(verdict["mean_score"])
        suites[verdict["suite"]].append(verdict["mean_score"])
    aggregate = lambda groups: {key: {"score": sum(values) / len(values), "n": len(values)}
                                for key, values in sorted(groups.items())}
    findings = Counter(f[0] for r in runs for f in r["findings"])
    summary = {
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "commit": "aa7df41fd548357597d5582703c258268cb18284",
        "score_out_of_10": average, "rounded_score": round(average, 1),
        "score_method": "Unmodified repository rubric; gpt-5-mini low-effort judge. Average non-null dimensions within each scenario, then average all 63 scenarios equally. Offline pass rate is not part of this quality score.",
        "offline": {"tests": int(tests), "passed": int(tests), "seconds": float(duration), "unexpected_http": 0},
        "live": {"scenarios": len(runs), "turns": sum(len(r["turns"]) for r in runs),
                 "skipped": 0, "unhandled_scenario_errors": sum(bool(r["error"]) for r in runs),
                 "handled_resolver_exceptions": 24, "api_calls": len(live_usage["calls"]),
                 "raw_findings": dict(findings), "false_positive_price_flags": 2,
                 "scenarios_with_raw_flags": sum(bool(r["findings"]) for r in runs)},
        "grading": {"scenarios": len(verdicts), "api_calls": len(usage["calls"]) - len(live_usage["calls"]),
                    "malformed_responses_retried": ["CS1"]},
        "cost": {"budget_usd": "1.00", "live_usd": live_usage["accounted_usd"],
                 "grading_usd": str(cost - Decimal(live_usage["accounted_usd"])),
                 "total_usd": str(cost), "remaining_usd": str(Decimal("1.00") - cost),
                 "note": "Token-priced API usage, including cached-input discounts and the malformed judge attempt. Taxes and credits excluded."},
        "dimensions": aggregate(dimensions), "categories": aggregate(categories), "suites": aggregate(suites),
        "backtests": read("backtests.json"), "models": manifest["models"],
        "catalogue_products": manifest["catalogue_products"], "database_cleanup_verified": True,
        "pricing_sources": ["https://developers.openai.com/api/docs/models/gpt-4.1-mini",
                            "https://developers.openai.com/api/docs/models/gpt-5-mini"],
    }
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    by_id = {r["id"]: r for r in runs}
    with (HERE / "scenario_scores.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scenario", "suite", "category", "score_out_of_10", "turns", "raw_flags"])
        for v in sorted(verdicts, key=lambda v: v["mean_score"]):
            r = by_id[v["id"]]
            writer.writerow([v["id"], v["suite"], v["category"], round(v["mean_score"], 3), len(r["turns"]), len(r["findings"])])
    dimension_rows = "\n".join(f"| {key.replace('_', ' ')} | {value['score']:.2f} | {value['n']} |"
                                for key, value in sorted(summary["dimensions"].items(), key=lambda pair: pair[1]["score"]))
    report = f"""# Perfume agent evaluation — 18 October 2026

**Score: {average:.1f}/10. API spend: ${cost:.6f} of $1.00.**

This is the completed quality score across all 63 conversations. The earlier 5.9/10 update was provisional, based on the first 33 graded conversations.

| Check | Result |
|---|---|
| Full Django regression suite | {tests}/{tests} passed in {duration}s; zero external HTTP calls |
| Live core scenarios | 42 completed |
| Live historical replay scenarios | 21 completed |
| Conversation turns | 185 completed; none skipped |
| Quality grading | All 63 scored; one malformed grading response retried |
| Historical backtests | Placement and praise passed; name routing incomplete because conversation 1106 is absent locally |
| Runtime errors | No unhandled scenario errors; 24 caught name-confirmation exceptions |
| API requests | {len(live_usage['calls'])} agent requests + {len(usage['calls']) - len(live_usage['calls'])} grading requests |

The main problems to address are:

1. **Name confirmation fails at runtime.** A literal JSON example has unescaped braces inside an f-string at `products/services/product_resolver.py:721`. `confirm_unplaced()` raises `ValueError` before calling the model. This happened 24 times during the run, causing fallback clarification replies. A separate reproduction confirmed the bug with zero API calls. The offline tests mock this confirmation function, leaving this path untested. See [runtime findings](runtime_findings.json) and [reproduction](resolver_reproduction.txt).
2. **The agent sometimes answers about the wrong perfume.** C2 compared Oud Wood when the customer requested Oudora. P2 interpreted Ambero as the Emporio Armani brand and repeated the same selection question after receiving contact details.
3. **Checkout context can be lost.** In CONV1075, a valid 100 ml original Dior Sauvage remained in the cart at EGP 815, but the fourth reply said no option met the requirements and asked the customer to change size or budget. No order was created in this scenario.
4. **Replies can become repetitive or irrelevant.** M3 repeated the identical two price lines across three turns. G1 answered a gift request with an unrelated statement about being unable to verify a perfume specification. Several Arabic-name replays repeatedly asked the customer to retype names without resolving the request.

The deterministic checker produced 18 raw flags across 11 scenarios: 12 repeated replies, two filler questions, two long answers, and two wrong-price flags. **Both wrong-price flags were false positives:** the checker read cart totals of EGP 1,319 and EGP 2,119 as item prices. The line prices and sums were correct. This correction was supplied to the judge; the original flags are preserved. Cart budget compliance was evaluated separately. Flags are not independent defect counts.

| Quality dimension | Score / 10 | Scenarios |
|---|---:|---:|
{dimension_rows}

The overall score uses the repository's 12-dimension rubric: average applicable dimensions within each conversation, then weight all 63 conversations equally. Core scenarios scored {summary['suites']['core']['score']:.2f}/10 and historical replays scored {summary['suites']['replays']['score']:.2f}/10. The 100% offline test pass rate does not inflate the conversational quality score. Grading used `gpt-5-mini` with low reasoning effort and saved catalogue/store facts. This is one model-judged run, with manual review of the principal findings; it is not a statistical reliability estimate.

The tests used a disposable local PostgreSQL database and a snapshot of the {manifest['catalogue_products']}-product Perfamix catalogue. Both temporary test databases were confirmed removed. Customer delivery was blocked, and application source files were unchanged. The source revision was `aa7df41fd548357597d5582703c258268cb18284`.

The historical name-routing backtest scanned 3,070 messages across six stores, but could not check conversation 1106 or its dependent store-specific abstention cases. The separate mocked regression tests for conversation 1106 passed. Placement backtesting also reported missing historical examples and two documented misses, so its passing status does not imply complete placement accuracy.

Cost: ${Decimal(live_usage['accounted_usd']):.6f} for live conversations and ${cost - Decimal(live_usage['accounted_usd']):.6f} for grading, including the malformed attempt. Token prices were verified against the official [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and [GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini) documentation. Taxes and credits are excluded.

Evidence: [summary](summary.json), [all scenario scores](scenario_scores.csv), [offline test log](offline_tests.log), [live transcripts](live/runs.json), [grading verdicts](final/verdicts.json), [cumulative usage ledger](final/usage.json), [checker corrections](judge/finding_adjudications.json).
"""
    (HERE / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"score": round(average, 1), "cost": str(cost), "scenarios": len(verdicts), "report": str(HERE / "report.md")}))


if __name__ == "__main__":
    main()
