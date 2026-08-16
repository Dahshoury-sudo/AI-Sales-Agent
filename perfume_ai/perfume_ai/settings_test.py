"""Settings for running the test suite.

Usage:
    python manage.py test --settings=perfume_ai.settings_test

DATABASE_URL points at the production Railway Postgres, so the default settings
must never be used for tests — `manage.py test` creates and drops databases on
whatever server it is pointed at. This module swaps in a local in-memory SQLite
database and removes the Redis and OpenAI dependencies so the suite runs offline
with no credentials.
"""

from .settings import *  # noqa: F401,F403

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
