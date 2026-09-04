# -*- coding: utf-8 -*-
"""Print one line per turn of a `runs.json`, plus every deterministic finding.

Written for the deny-on-the-first-ask verification (`absence.py` and friends), where the
acceptance criteria are not "no findings" but two statements about the *content* of each
reply: an absent perfume must be denied and offered alternatives in the same message, and a
stocked perfume must never be denied at all. Neither is fully decidable by a regex, so the
replies have to be read — and reading them out of a 6000-line JSON by hand is how a
regression gets missed.

    python -m eval_harness.summarize_runs [path-to-runs.json ...]

Defaults to `results/runs.json`. Reads nothing else and writes nothing, so it is safe to run
against a saved copy while the harness is mid-flight on the live file.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The markers whose presence changes what the reply is allowed to say. Imported by name rather
# than hardcoded so a rename in `product_info` shows up here as an ImportError instead of as a
# summary that silently stops reporting the thing it exists to report.
try:  # pragma: no cover - convenience path when Django is not configured
    from products.services.product_info import (
        ABSENCE_DENIED_MARKER,
        NAME_UNREADABLE_MARKER,
    )
except Exception:  # noqa: BLE001 - a bare summary is still worth printing
    ABSENCE_DENIED_MARKER = "ABSENCE_DENIED"
    NAME_UNREADABLE_MARKER = "NAME_UNREADABLE"


def _verdict(context):
    context = context or ""
    if ABSENCE_DENIED_MARKER in context:
        return "DENIED"
    if NAME_UNREADABLE_MARKER in context:
        return "ABSTAIN"
    return "-"


def summarize(path):
    with open(path, encoding="utf-8") as handle:
        records = json.load(handle)

    print(f"\n{'=' * 78}\n{path}\n{'=' * 78}")
    total = 0
    for record in records:
        findings = record.get("findings") or []
        total += len(findings)
        print(f"\n── {record['id']}  ({len(findings)} findings)")
        if record.get("error"):
            print(f"   ERROR: {record['error']}")
        for turn in record.get("turns") or []:
            search = turn.get("search") or {}
            matched = search.get("matched")
            print(
                f"   [{turn['n']}] {_verdict(turn.get('context'))}"
                f"  stage={turn.get('stage')}  matched={matched}"
            )
            print(f"       U: {turn['user']}")
            print(f"       A: {(turn.get('reply') or '').replace(chr(10), ' / ')}")
        for finding in findings:
            print(f"   !! {finding}")
    print(f"\ntotal deterministic findings: {total}")
    return total


def main():
    paths = sys.argv[1:] or [os.path.join(HERE, "results", "runs.json")]
    grand = sum(summarize(path) for path in paths)
    print(f"\nacross {len(paths)} file(s): {grand} findings")


if __name__ == "__main__":
    main()
