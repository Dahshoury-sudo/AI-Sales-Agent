"""Per-identity request throttles.

Three defects are fixed here and worth stating, because each was invisible in normal use:

  * Both classes keyed on `HTTP_X_API_KEY`, which only the API-key views send. The five
    JWT dashboard views (handoff ×4, chat history) never send it, so they fell through to
    `return self.get_ident(request)` — a bare IP with *no scope prefix*, since that branch
    skipped `cache_format`. Per-store isolation was gone and every dashboard user behind
    one NAT shared a bucket.
  * The `rate` class attributes made `DEFAULT_THROTTLE_RATES` dead config:
    `SimpleRateThrottle.__init__` consults `get_rate()` only when `rate` is falsy, so
    tuning settings did nothing. The attributes are gone; settings is now the only source.
  * Keying on the raw API key wrote a store secret into the Redis keyspace, where
    `KEYS *`, `MONITOR` and key-level metrics can read it. Keying on `store.pk` does not.

Note that every limit here is only as good as `settings.NUM_PROXIES`. With it unset DRF
trusts the whole client-supplied `X-Forwarded-For`, and anything keyed on an IP can be
bypassed by varying one header.

`WebhookThrottle` used to live here. It was removed rather than fixed: DRF throttling runs
before the request body is parsed, so it cannot key per store, and its only failure mode
was a 429 that makes Meta retry and eventually disable the webhook subscription. Webhook
and per-sender limiting now happen in `products/tasks.py`, where the store and the sender
are both known and a message can be deferred instead of refused.
"""

from rest_framework.throttling import SimpleRateThrottle


class StoreScopedThrottle(SimpleRateThrottle):
    """Throttle keyed on the most specific identity the request carries.

    `request.store` is set by *both* auth classes — `StoreAPIKeyAuthentication` and
    `StoreOwnerAuthentication` — and authentication runs before throttling in DRF's
    `initial()`, so it is available here on every authenticated view regardless of which
    scheme was used. That is what gives real per-store isolation.
    """

    def get_cache_key(self, request, view):
        store = getattr(request, "store", None)
        if store is not None:
            ident = f"store-{store.pk}"
        else:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                ident = f"user-{user.pk}"
            else:
                # Anonymous: fall back to the client IP, and keep the scope prefix so
                # two throttles cannot share one bucket.
                ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class StoreKeyThrottle(StoreScopedThrottle):
    """General per-store limit, so one store cannot overwhelm the system."""

    scope = "store"


class ChatThrottle(StoreScopedThrottle):
    """Stricter limit on the AI chat endpoint, which spends model calls per request."""

    scope = "chat"


class LoginThrottle(SimpleRateThrottle):
    """Brute-force limit on credential submission.

    Per-IP, which cannot stop a distributed attack — a per-username lockout is the real
    defence and is deliberately out of scope here. This closes the case where a single
    host could previously make unlimited attempts.
    """

    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class RegisterThrottle(LoginThrottle):
    """Limit account creation from one host."""

    scope = "register"


class PasswordResetThrottle(LoginThrottle):
    """Limit password-reset requests.

    Shared by the forgot and reset endpoints: the first sends email, making it a spam
    vector as well as a way to probe which addresses have accounts.
    """

    scope = "password_reset"
