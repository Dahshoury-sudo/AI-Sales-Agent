"""Check helper syntax, incremental provenance, and disposable database cleanup."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASELINE = HERE.parent / "evaluation_20261018"
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def rubric(source):
    return next(ast.literal_eval(n.value) for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "RUBRIC" for t in n.targets))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    for filename in ("verify_run.py", "judge_saved_run.py", "build_report.py"):
        compile((HERE / filename).read_text(encoding="utf-8"), filename, "exec")
    request = read(HERE / "incremental_request.json")
    for relative, digest in request["baseline_sha256"].items():
        assert hashlib.sha256((BASELINE / relative).read_bytes()).hexdigest() == digest, relative
    old_commit = read(BASELINE / "summary.json")["commit"]
    old_judge = subprocess.check_output(["git", "show", old_commit + ":./eval_harness/judge.py"], cwd=ROOT).decode("utf-8")
    assert rubric(old_judge) == rubric((ROOT / "eval_harness/judge.py").read_text(encoding="utf-8")), "Rubric changed since baseline"
    if args.preflight:
        print(json.dumps({"helper_syntax": "valid", "baseline_hashes": "unchanged", "rubric": "identical to baseline"}))
        return
    runs = read(HERE / "live/runs.json")
    old_runs = {r["id"]: r for r in read(BASELINE / "live/runs.json")}
    manifest = read(HERE / "live/manifest.json")
    expected = {c["id"]: c["turns"] for group in manifest["suites"].values() for c in group}
    rerun = set(request["rerun_ids"])
    assert len(runs) == len(expected) == len(old_runs) == 63
    assert all(len(r["turns"]) == expected[r["id"]] for r in runs)
    assert all(r == old_runs[r["id"]] for r in runs if r["id"] not in rerun)
    assert not read(HERE / "live/skipped.json")
    usage = read(HERE / "live/usage.json")
    prior_usage = read(BASELINE / "live/usage.json")
    assert usage["calls"][:len(prior_usage["calls"])] == prior_usage["calls"]
    assert all(c["scenario"] in rerun for c in usage["calls"][len(prior_usage["calls"]):])
    from dotenv import dotenv_values
    import psycopg2
    config = dotenv_values(ROOT / ".env")
    database_url = os.environ.get("TEST_DATABASE_URL") or config.get("TEST_DATABASE_URL")
    assert urlsplit(database_url).hostname in {"localhost", "127.0.0.1", "::1"}
    temporary = manifest["temporary_database"]
    assert re.fullmatch(r"test_agent_eval_[a-f0-9]{10}", temporary)
    connection = psycopg2.connect(database_url)
    try:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT datname FROM pg_database WHERE datname = %s", (temporary,))
            remaining = [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()
    cleanup = {"checked_utc": datetime.now(timezone.utc).isoformat(), "temporary_databases_checked": [temporary],
               "remaining": remaining, "cleanup_verified": not remaining}
    write(HERE / "database_cleanup.json", cleanup)
    assert cleanup["cleanup_verified"], "Evaluation database still exists"
    raw_log = (HERE / "live_console.log").read_bytes()
    log = raw_log.decode("utf-16" if raw_log.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    findings = {"handled_name_confirmation_exceptions": log.count("ValueError: Invalid format specifier ' true'"),
                "unhandled_scenario_errors": [{"id": r["id"], "error": r["error"]} for r in runs if r["error"]],
                "catalogue_ground_truth_matches_baseline": read(HERE / "live/ground_truth.json") == read(BASELINE / "live/ground_truth.json"),
                "rubric_matches_baseline": True}
    write(HERE / "runtime_findings.json", findings)
    print(json.dumps({"completed": len(runs), "updated": len(rerun), "retained": len(runs) - len(rerun),
                      "cleanup_verified": True, **findings}))


if __name__ == "__main__":
    main()
