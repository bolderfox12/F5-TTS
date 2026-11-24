"""Request telemetry scoped to the current dubbing operation."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

import httpx


_sink = ContextVar("dubbing_activity_sink", default=None)
API_REASONS = {
    "API_KEY_INVALID",
    "BILLING_DISABLED",
    "PERMISSION_DENIED",
    "RESOURCE_EXHAUSTED",
    "SERVICE_DISABLED",
    "UNAUTHENTICATED",
    "RATE_LIMIT_EXCEEDED",
    "UNAVAILABLE",
}


@contextmanager
def observe_requests(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def post(provider, operation, url, *, characters=0, **kwargs):
    sink = _sink.get()
    if sink is None:
        return httpx.post(url, **kwargs)

    label = f"{provider} · {operation}"
    suffix = f" · {characters} caracteres de fala" if characters else ""
    sink(f"API · {label} · enviando{suffix}", calls=1, characters=characters)
    started = perf_counter()
    try:
        response = httpx.post(url, **kwargs)
    except httpx.HTTPError as error:
        sink(f"API · {label} · erro de rede ({type(error).__name__})", failed=1)
        raise

    elapsed = perf_counter() - started
    status = response.status_code
    if not isinstance(status, int):
        return response
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    usage = body.get("usageMetadata") or body.get("usage") or {}
    tokens = (usage.get("totalTokenCount") or usage.get("total_tokens")) if isinstance(usage, dict) else None
    suffix = ""
    if type(tokens) is int and tokens >= 0:
        suffix = f" · {tokens} tokens informados"
    else:
        tokens = 0
    if status >= 400:
        error = body.get("error")
        if isinstance(error, dict):
            details = error.get("details")
            reasons = [error.get("status")]
            if isinstance(details, list):
                reasons = [detail.get("reason") for detail in details if isinstance(detail, dict)] + reasons
            reason = next((r for r in reasons if isinstance(r, str) and r in API_REASONS), None)
            if reason:
                suffix += f" · {reason}"
    sink(f"API · {label} · HTTP {status} · {elapsed:.2f} s{suffix}", tokens=tokens, failed=int(status >= 400))
    return response
