"""Turn-local reuse of validated structured answers; never cache business objects."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.conf import settings

metadata = ContextVar("ai_metadata", default=None)
memo = ContextVar("ai_turn_memo", default=None)
pending = ContextVar("ai_validating_calls", default=None)
transport_attempts = ContextVar("ai_transport_attempts", default=None)


@contextmanager
def call_context(**values):
    token = metadata.set({**(metadata.get() or {}), **values})
    try:
        yield
    finally:
        metadata.reset(token)


@contextmanager
def turn_context(**values):
    token = memo.set({})
    try:
        with call_context(**values):
            yield
    finally:
        memo.reset(token)


def routed_turn(func):
    @wraps(func)
    def wrapped(message, history=None, store=None, conversation=None, *args, **kwargs):
        with turn_context(store_id=getattr(store, "pk", None),
                          conversation_id=getattr(conversation, "pk", None)):
            return func(message, history, store, conversation, *args, **kwargs)
    return wrapped


def validated_extraction(accept):
    """Commit raw answers only after domain validation succeeds.

    On reuse the decorated function still runs: it rebuilds the exact request,
    parses JSON and repeats catalogue, identity and business validation.
    """
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            cache = memo.get()
            if cache is None or not getattr(settings, "AI_REUSE_EXTRACTIONS", True):
                return func(*args, **kwargs)
            candidates = {}
            token = pending.set(candidates)
            try:
                result = func(*args, **kwargs)
                if accept(result):
                    cache.update(candidates)
                return result
            finally:
                pending.reset(token)
        return wrapped
    return decorate
