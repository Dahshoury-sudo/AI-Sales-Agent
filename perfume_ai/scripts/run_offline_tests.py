"""Run Django regressions on disposable local PostgreSQL, with HTTP blocked.

Usage: python scripts/run_offline_tests.py [test labels] --log path/to/results.log
"""
import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import urlparse
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="*")
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    from dotenv import dotenv_values
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
    if urlparse(url or "").hostname not in ("localhost", "127.0.0.1", "::1"):
        parser.error("TEST_DATABASE_URL must point to local PostgreSQL")
    os.environ["TEST_DATABASE_URL"] = url
    os.environ["DJANGO_SETTINGS_MODULE"] = "perfume_ai.settings_test"
    import django
    django.setup()
    from django.conf import settings
    from django.test.runner import DiscoverRunner
    settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_offline_" + uuid.uuid4().hex[:12]
    attempts = []

    def denied(*unused, **kwargs):
        attempts.append("unexpected HTTP")
        raise AssertionError("Offline test attempted external HTTP")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log), ExitStack() as stack:
        for target in ("requests.sessions.Session.request", "httpx.Client.send", "httpx.AsyncClient.send"):
            stack.enter_context(patch(target, side_effect=denied))
        failures = DiscoverRunner(verbosity=2, interactive=False).run_tests(args.labels)
        print("UNMOCKED_HTTP_ATTEMPTS", len(attempts))
    print(json.dumps({"failures": failures, "unexpected_http": len(attempts), "log": str(args.log)}))
    return int(bool(failures or attempts))


if __name__ == "__main__":
    raise SystemExit(main())
