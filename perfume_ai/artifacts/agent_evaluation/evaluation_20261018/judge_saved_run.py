"""Grade saved evidence using the repository rubric and cumulative USD accounting.

Run only after the live evaluation finishes. No database access or customer delivery.
"""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from openai import OpenAI
from scripts.evaluate_agent_budgeted import BudgetStop, Meter, write_json


def main():
    folder = Path(__file__).resolve().parent
    live = folder / "live"
    output = folder / "judge"
    output.mkdir(exist_ok=True)
    if (output / "usage.json").exists():
        raise SystemExit("Existing cost evidence must not be overwritten")
    previous = json.loads((live / "usage.json").read_text(encoding="utf-8"))
    if Decimal(previous["reserved_usd"]):
        raise SystemExit("The live run must finish before grading starts")
    records = json.loads((live / "runs.json").read_text(encoding="utf-8"))
    tree = ast.parse((ROOT / "eval_harness" / "judge.py").read_text(encoding="utf-8"))
    rubric = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "RUBRIC" for t in node.targets))
    config = dotenv_values(ROOT / ".env")
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY") or config.get("OPENAI_API_KEY"),
                    max_retries=0, timeout=90)
    meter = Meter(Decimal("1.00"), output)
    meter.spent = Decimal(previous["accounted_usd"])
    meter.calls = previous["calls"]
    meter.save()
    create = meter.wrap(client.chat.completions.create)
    adjudications = {
        "CONV931": (
            "Manual verification of deterministic findings: the two wrong_variant_price "
            "flags are false positives. At turn 5, 1319 is explicitly the CART TOTAL: "
            "650 + 669 = 1319. At turn 7, 2119 is explicitly the CART TOTAL: "
            "650 + 669 + (2 * 400) = 2119. The preceding line prices are correct. "
            "Do not count these totals as incorrect per-item prices. Assess compliance "
            "with the customer's budget independently; this correction does not excuse "
            "any failure to acknowledge an over-budget cart. Raw checker output is retained."
        )
    }
    write_json(output / "finding_adjudications.json", adjudications)
    write_json(output / "manifest.json", {
        "rubric": "eval_harness/judge.py:RUBRIC (unmodified)",
        "model": "gpt-5-mini", "reasoning_effort": "low", "workers": 3,
        "source": str(live), "prior_spend_usd": meter.spent,
        "score_method": "Mean of non-null dimensions within each scenario, then equal-weight mean across scenarios. Offline tests reported separately.",
        "expected_scenarios": len(records), "cumulative_budget_usd": "1.00",
    })

    def grade(record):
        meter.local.scenario = "judge:" + record["id"]
        evidence = record["judge_evidence"]
        if record["id"] in adjudications:
            evidence += "\n\nVERIFIED CHECKER CORRECTION:\n" + adjudications[record["id"]]
        response = create(model="gpt-5-mini", reasoning_effort="low",
                          response_format={"type": "json_object"},
                          messages=[{"role": "system", "content": rubric},
                                    {"role": "user", "content": evidence}])
        verdict = json.loads(response.choices[0].message.content)
        verdict.update(id=record["id"], category=record["category"], suite=record["suite"])
        scores = verdict.get("scores", {})
        dimensions = {"intent_understanding", "constraint_extraction", "memory",
                      "recommendation_quality", "similarity_accuracy", "product_accuracy",
                      "confidence_calibration", "objection_handling", "sales_effectiveness",
                      "closing_timing", "human_feel", "trust"}
        nullable = {"similarity_accuracy", "objection_handling", "closing_timing"}
        if set(scores) != dimensions or any(
            (value is None and key not in nullable)
            or (value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))))
            for key, value in scores.items()
        ):
            raise ValueError("Judge returned incomplete or malformed dimensions")
        values = [v for v in scores.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not values or any(not 0 <= v <= 10 for v in values):
            raise ValueError("Judge returned invalid scores")
        verdict["mean_score"] = sum(values) / len(values)
        return verdict

    verdicts, skipped = [], []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(grade, record): record for record in records}
            for future in as_completed(futures):
                record = futures[future]
                try:
                    verdict = future.result()
                except (BudgetStop, Exception) as exc:
                    skipped.append({"id": record["id"], "reason": str(exc), "type": type(exc).__name__})
                    print(json.dumps(skipped[-1]), flush=True)
                else:
                    verdicts.append(verdict)
                    write_json(output / (record["id"] + ".json"), verdict)
                    print(json.dumps({"id": record["id"], "score": round(verdict["mean_score"], 2),
                                      "cumulative_usd": str(meter.spent)}), flush=True)
                write_json(output / "verdicts.json", sorted(verdicts, key=lambda v: v["id"]))
                write_json(output / "skipped.json", skipped)
    finally:
        meter.save()
        client.close()
    print(json.dumps({"graded": len(verdicts), "skipped": len(skipped),
                      "cumulative_usd": str(meter.spent)}), flush=True)
    return int(bool(skipped))


if __name__ == "__main__":
    raise SystemExit(main())
