"""Totals from the shared customer-simulation ledger: calls, tokens, cost and latency.

python eval_harness/ledger_totals.py [artifacts/customer_simulation_20/ledger.json] [--out totals.md]
"""
import argparse
import io
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _seconds(entry):
    for key in ("seconds", "latency_s"):
        if entry.get(key) is not None:
            return float(entry[key])
    if entry.get("started_utc") and entry.get("ended_utc"):
        return (datetime.fromisoformat(entry["ended_utc"]) - datetime.fromisoformat(entry["started_utc"])).total_seconds()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", nargs="?", default="artifacts/customer_simulation_20/ledger.json")
    parser.add_argument("--out")
    args = parser.parse_args()
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    groups = defaultdict(lambda: {"calls": 0, "cost": Decimal(0), "prompt": 0, "cached": 0, "completion": 0,
                                  "seconds": [], "statuses": defaultdict(int), "models": set()})
    for entry in ledger["calls"]:
        for key in (("ALL", "ALL"), (entry.get("phase") or "?", "ALL"), ("ALL", entry.get("role") or "?"),
                    (entry.get("phase") or "?", entry.get("role") or "?")):
            group = groups[key]
            group["calls"] += 1
            group["cost"] += Decimal(str(entry.get("cost_usd") or 0))
            group["prompt"] += int(entry.get("prompt_tokens") or 0)
            group["cached"] += int(entry.get("cached_tokens") or 0)
            group["completion"] += int(entry.get("completion_tokens") or 0)
            seconds = _seconds(entry)
            if seconds is not None:
                group["seconds"].append(seconds)
            group["statuses"][entry.get("status") or "?"] += 1
            group["models"].add(entry.get("model") or "?")
    lines = ["| Phase | Role | Calls | Prompt tok | Cached tok | Completion tok | Cost USD | Mean latency s | p95 latency s | Statuses |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for (phase, role), group in sorted(groups.items(), key=lambda item: (item[0][0] != "ALL", item[0])):
        seconds = sorted(group["seconds"])
        mean = round(sum(seconds) / len(seconds), 2) if seconds else None
        p95 = round(seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))], 2) if seconds else None
        statuses = ", ".join(f"{k}={v}" for k, v in sorted(group["statuses"].items()))
        lines.append(f"| {phase} | {role} | {group['calls']} | {group['prompt']} | {group['cached']} | "
                     f"{group['completion']} | {group['cost']:.6f} | {mean} | {p95} | {statuses} |")
    lines.append(f"\nCap ${ledger['cap_usd']}; accounted ${Decimal(str(ledger['accounted_usd'])):.6f}; "
                 f"remaining ${Decimal(str(ledger['remaining_usd'])):.6f}; models: "
                 f"{sorted(groups[('ALL', 'ALL')]['models'])}; runs: {len(ledger.get('runs') or [])}")
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
