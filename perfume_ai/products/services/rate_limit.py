"""Fixed-window rate counting for the message path.

Separate from `products/throttles.py` because those are DRF throttles and this is not a
DRF problem. The expensive path is `views_meta → tasks.process_incoming_message → route()`,
roughly two to three model calls per message, and it had no per-store or per-sender limit
of any kind:

  * `WebhookThrottle` keyed on the client IP, and every Meta webhook arrives from
    Facebook's addresses — so all stores shared one bucket and one busy store throttled the
    rest. It also counted HTTP requests while a single POST carries many batched events.
  * DRF throttling runs before the body is parsed, so it cannot key per store even in
    principle, and its only failure mode is a 429 — which makes Meta retry and, on
    sustained failure, disable the webhook subscription app-wide.
  * `usage_service.record_llm_message` counts but deliberately never blocks.

So the limit lives here, called from the Celery task where the store and the sender are
both known and a message can be *deferred* rather than refused.

Fixed window, not sliding: `add` establishes the TTL once and `incr` is a real Redis
INCRBY, which is atomic across the three gunicorn workers and two Celery slots. The cost is
that roughly a 2x burst can cross a window boundary. That is accepted — it is far cheaper
than DRF's per-key list of timestamps, and callers pair a minute window with an hour window,
which bounds what a boundary burst can achieve.

Fails open. A cache outage returns "allowed", matching the trade `products/cache.py`
already documents: losing accuracy for the duration of an outage beats taking the message
path down with it.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def hit(bucket, limit, window_seconds):
    """Count one event against `bucket`. Returns (allowed, retry_after_seconds).

    `retry_after_seconds` is the window length rather than the true time remaining: the
    counter carries no start timestamp, and storing one to shave a few seconds off a
    deferral is not worth a second round trip.
    """
    key = f"ratelimit:{bucket}"

    # add() only writes when the key is absent, so the TTL is set exactly once per window
    # and later increments cannot extend it into a rolling window.
    cache.add(key, 0, window_seconds)

    count = cache.incr(key)
    if count is None:
        # ResilientRedisCache returning None means Redis is unreachable. Fail open.
        return True, 0

    return count <= limit, window_seconds


def hit_all(buckets):
    """Count one event against several windows, refusing if any is exhausted.

    `buckets` is an iterable of (bucket, limit, window_seconds). Every window is counted
    even once one has refused, so a caller checking minute-and-hour limits keeps both
    tallies honest instead of leaving the hour counter short whenever the minute limit
    trips first.

    Returns (allowed, retry_after_seconds, exhausted_bucket). `retry_after_seconds` is the
    longest window that refused, so a sender who has burnt their hourly allowance is not
    re-dispatched sixty seconds later to be refused again.
    """
    allowed = True
    retry_after = 0
    exhausted = None

    for bucket, limit, window_seconds in buckets:
        bucket_allowed, bucket_retry = hit(bucket, limit, window_seconds)
        if not bucket_allowed:
            allowed = False
            if bucket_retry > retry_after:
                retry_after = bucket_retry
                exhausted = bucket

    return allowed, retry_after, exhausted


def reset(bucket):
    """Drop a bucket's counter. For tests and manual intervention."""
    cache.delete(f"ratelimit:{bucket}")


def peek(buckets):
    """Are these windows currently under their limits, without counting a new event?

    Needed because a deferred message re-enters the task and must not be charged twice.
    Counting on every retry would make a deferred message burn budget each time it came
    back, so a sender who tripped the limit once would be pushed further past it by the
    system's own retries rather than by anything they did.

    So: `hit_all` on first entry, `peek` on re-entry. One message consumes exactly one unit
    of budget however many times it is deferred.

    Returns (allowed, retry_after_seconds) with the same shape as `hit_all`'s first two
    values. A missing counter reads as zero, so this fails open exactly as `hit` does.
    """
    allowed = True
    retry_after = 0

    for bucket, limit, window_seconds in buckets:
        count = cache.get(f"ratelimit:{bucket}", 0) or 0
        if count >= limit:
            allowed = False
            retry_after = max(retry_after, window_seconds)

    return allowed, retry_after
