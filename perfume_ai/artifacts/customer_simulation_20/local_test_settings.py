"""Project settings with an in-memory cache, for running the suite on a machine without Redis.

No test may reach the real OpenAI API: perfume_ai.settings loads .env, so a test that misses a
mock would otherwise spend outside the task's USD ledger. The key is a placeholder, the base URL
is a dead local port, and any request that still escapes a mock is logged and fails as a
connection error.
"""
import os

os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"

from perfume_ai.settings import *  # noqa: F401,F403,E402

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
OPENAI_API_KEY = "test-key-unused"

import httpx  # noqa: E402
import openai  # noqa: E402
import openai.resources.chat.completions as _completions  # noqa: E402

LIVE_CALL_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_call_attempts.log")


def _blocked(self, *args, **kwargs):
    import traceback
    with open(LIVE_CALL_LOG, "a", encoding="utf-8") as handle:
        handle.write("".join(traceback.format_stack(limit=14)) + "\n----\n")
    raise openai.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions"))


_completions.Completions.create = _blocked
