"""Cache backend that degrades instead of taking requests down with it.

The default cache backs DRF's throttle counters, which means every throttled
endpoint consults it on every request. A plain RedisCache raises when Redis is
unreachable, so a brief Redis outage would turn into 500s on /api/chat/ rather
than a temporary loss of rate limiting. Losing throttle accuracy for the
duration of an outage is the better trade, so cache failures are logged and
treated as misses.
"""

import logging

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class ResilientRedisCache(RedisCache):
    """RedisCache that logs and no-ops when Redis is unavailable."""

    def _unavailable(self, operation, exc):
        logger.warning("Cache %s failed, continuing without cache: %s", operation, exc)

    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except RedisError as exc:
            self._unavailable("get", exc)
            return default

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().set(key, value, timeout, version)
        except RedisError as exc:
            self._unavailable("set", exc)

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().add(key, value, timeout, version)
        except RedisError as exc:
            self._unavailable("add", exc)
            return False

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().touch(key, timeout, version)
        except RedisError as exc:
            self._unavailable("touch", exc)
            return False

    def delete(self, key, version=None):
        try:
            return super().delete(key, version)
        except RedisError as exc:
            self._unavailable("delete", exc)
            return False

    def get_many(self, keys, version=None):
        try:
            return super().get_many(keys, version)
        except RedisError as exc:
            self._unavailable("get_many", exc)
            return {}

    def set_many(self, data, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().set_many(data, timeout, version)
        except RedisError as exc:
            self._unavailable("set_many", exc)
            return list(data)

    def has_key(self, key, version=None):
        try:
            return super().has_key(key, version)
        except RedisError as exc:
            self._unavailable("has_key", exc)
            return False
