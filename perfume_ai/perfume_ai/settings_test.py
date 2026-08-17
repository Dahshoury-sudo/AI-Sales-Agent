"""Settings for running the test suite.

Usage:
    python manage.py test --settings=perfume_ai.settings_test

DATABASE_URL points at the production Railway Postgres, so the default settings
must never be used for tests — `manage.py test` creates and drops databases on
whatever server it is pointed at.

Set TEST_DATABASE_URL to run against a local Postgres, which matches production
and is worth doing before shipping anything touching orders: SQLite silently
ignores select_for_update() and is laxer than Postgres about DISTINCT with
ORDER BY, so an engine-specific bug can pass on SQLite and fail in production.
Django creates a separate test_<name> database, so local data is untouched.

    TEST_DATABASE_URL=postgres://user:pass@localhost:5432/AIAgent \
        python manage.py test --settings=perfume_ai.settings_test

Without it, tests fall back to in-memory SQLite so the suite still runs anywhere.
"""

import os

import dj_database_url

from .settings import *  # noqa: F401,F403

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL')

if TEST_DATABASE_URL:
    DATABASES = {'default': dj_database_url.parse(TEST_DATABASE_URL)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }

# Throttle counters don't need to be shared between test processes.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

# products.services.ai.client builds an OpenAI client at import time, so a
# placeholder keeps the suite runnable without real credentials. Tests must not
# make live API calls regardless.
OPENAI_API_KEY = OPENAI_API_KEY or 'test-key-unused'  # noqa: F405
OPENAI_MODEL = OPENAI_MODEL or 'gpt-4.1-mini'  # noqa: F405

# Keep test output clean and fast.
DEBUG = False
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
