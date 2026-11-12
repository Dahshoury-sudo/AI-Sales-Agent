"""Summarise customer-simulation attempt folders into markdown and JSON.

python eval_harness/summarize_simulation.py artifacts/customer_simulation_20/baseline/rejudged \
    [artifacts/customer_simulation_20/final/attempt-1] --out artifacts/customer_simulation_20/comparison.md
"""
import argparse
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def load(folder):
    records = {}
    for path in sorted(Path(folder).glob("S[0-9][0-9]_*.json")):
        records[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return records


def row(record):
    judged = record.get("judge") or {}
    scores = judged.get("scores") or {}
    numeric = [v for v in scores.values() if isinstance(v, (int, float))]
    high = [f for t in record.get("turns", []) for f in t.get("findings", []) if f[1] in ("critical", "high")]
    issues = [i for i in judged.get("issues") or [] if i.get("severity") in ("critical", "high")]
    return {
        "verdict": record.get("verdict"), "turns": len(record.get("turns") or []),
        "mean_score": round(sum(numeric) / len(numeric), 1) if numeric else None,
        "min_score": min(numeric) if numeric else None,
        "det_high": len(high), "judge_high": len(issues),
        "reasons": record.get("verdict_reasons") or [],
        "cost": (record.get("usage") or {}).get("cost_usd"),
        "calls": (record.get("usage") or {}).get("api_calls"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args()
    runs = [(folder, load(folder)) for folder in args.folders]
    ids = sorted({key for _, records in runs for key in records})
    lines = ["| Scenario | " + " | ".join(Path(f).parent.name + "/" + Path(f).name for f, _ in runs) + " |",
             "|---|" + "---|" * len(runs)]
    data = {}
    for scenario in ids:
        cells = []
        for folder, records in runs:
            if scenario not in records:
                cells.append("—")
                continue
            r = row(records[scenario])
            data.setdefault(scenario, {})[folder] = r
            cells.append(f"{r['verdict']} (mean {r['mean_score']}, min {r['min_score']}, det-hi {r['det_high']}, judge-hi {r['judge_high']})")
        lines.append(f"| {scenario} | " + " | ".join(cells) + " |")
    for folder, records in runs:
        verdicts = [row(r)["verdict"] for r in records.values()]
        cost = sum((Decimal(str(row(r)["cost"] or 0)) for r in records.values()), Decimal(0))
        means = [row(r)["mean_score"] for r in records.values() if row(r)["mean_score"] is not None]
        lines.append(f"\n**{folder}**: {verdicts.count('pass')} pass / {verdicts.count('fail')} fail / "
                     f"{verdicts.count('incomplete')} incomplete; mean judge score "
                     f"{round(sum(means) / len(means), 2) if means else None}; conversation cost ${cost:.4f}")
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        Path(args.out).with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
