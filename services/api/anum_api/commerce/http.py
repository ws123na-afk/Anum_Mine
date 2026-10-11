"""The HTTP layer every channel client shares (docs/commerce.md#rate-limits).

* A token bucket per operation, sized from the channel's documented rate and burst
  (SP-API publishes them per operation, for example ``getOrders`` 0.0167 requests a
  second with a burst of 20). A call waits for a token; if the wait would exceed
  ``ANUM_COMMERCE_MAX_RETRY_WAIT_SECONDS`` the sync stops with :class:`RateLimited` and
  the connector resumes after the wait.
* ``429`` and ``503`` are retried with the channel's ``Retry-After`` (or an exponential
  backoff), bounded the same way. SP-API's ``x-amzn-RateLimit-Limit`` header updates the
  bucket's rate when present.
* ``401`` and ``403`` raise :class:`ChannelAuthError`: the owner must reconnect.
* Bodies are capped (``MAX_BODY_BYTES``) and never logged; error messages carry the
  channel's error code and message only, never a credential.

Owner-supplied URLs (own store, Supabase) go through the SSRF guard of
``anum_api.model_egress``: public HTTPS only outside local development, resolved once and
pinned for the connection (no DNS rebinding), never following redirects.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..model_egress import EgressPolicy, PinnedEgressTransport, UnsafeModelEndpointError, check_endpoint
from .config import commerce_config

MAX_BODY_BYTES = 8 * 1024 * 1024
# Tests replace this with an httpx.MockTransport; production uses the network.
commerce_transport: httpx.AsyncBaseTransport | None = None
# Tests replace this so waits are instant.
sleep = asyncio.sleep
monotonic = time.monotonic


class ChannelError(Exception):
    """A channel call failed. ``message`` is safe to show to the owner."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class ChannelAuthError(ChannelError):
    """The channel refused the credentials (401/403): reconnect the channel."""


class RateLimited(ChannelError):
    """The channel's rate limit: retry after ``retry_after`` seconds."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message, status=429, code="rate_limited")
        self.retry_after = retry_after


class UnsafeEndpointError(ChannelError):
    """An owner-supplied URL the API will not call (SSRF guard)."""


@dataclass
class TokenBucket:
    rate: float  # tokens per second
    burst: float
    tokens: float = -1.0
    updated: float = field(default_factory=lambda: monotonic())

    def __post_init__(self) -> None:
        if self.tokens < 0:
            self.tokens = self.burst

    def wait_time(self) -> float:
        now = monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) / self.rate if self.rate > 0 else float("inf")

    def take(self) -> None:
        self.tokens -= 1


# Buckets live per connector and operation for the life of the process, so separate
# syncs of the same connector share them (as the channel counts per seller and app).
_buckets: dict[tuple[str, str], TokenBucket] = {}


def reset_buckets() -> None:
    _buckets.clear()


def bucket(connector_key: str, operation: str, rate: float, burst: float) -> TokenBucket:
    key = (connector_key, operation)
    found = _buckets.get(key)
    if found is None:
        found = _buckets[key] = TokenBucket(rate=rate, burst=burst)
    return found


def owner_url_policy() -> EgressPolicy:
    """The SSRF guard for owner-supplied URLs: no operator allow-list applies here."""
    policy = EgressPolicy.from_settings()
    return EgressPolicy(enforce=policy.enforce)


def check_owner_url(url: str) -> str:
    """Syntax checks for an owner-supplied base URL; returns it without a trailing slash."""
    value = (url or "").strip().rstrip("/")
    try:
        check_endpoint(value, owner_url_policy())
    except UnsafeModelEndpointError as exc:
        raise UnsafeEndpointError(str(exc).replace("base_url", "The store URL"), code="unsafe_url") from None
    return value


def _client(*, owner_url: bool) -> httpx.AsyncClient:
    timeout = commerce_config.request_timeout_seconds
    if commerce_transport is not None:
        return httpx.AsyncClient(transport=commerce_transport, timeout=timeout, follow_redirects=False)
    policy = owner_url_policy()
    if owner_url and policy.enforce:
        return httpx.AsyncClient(
            transport=PinnedEgressTransport(policy), timeout=timeout, follow_redirects=False, trust_env=False
        )
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def _retry_after(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return min(60.0, 2.0 ** attempt)


def _error_text(body: Any, fallback: str) -> tuple[str, str | None]:
    """The channel's own error code and message (SP-API ``errors[]``, generic ``error``/``message``)."""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            first = errors[0]
            return str(first.get("message") or fallback)[:300], (str(first.get("code")) if first.get("code") else None)
        for key in ("error_description", "message", "detail", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:300], (str(body.get("error")) if isinstance(body.get("error"), str) else None)
            if isinstance(value, dict) and isinstance(value.get("message"), str):
                return value["message"][:300], (str(value.get("code")) if value.get("code") else None)
    return fallback, None


async def request_json(
    method: str,
    url: str,
    *,
    channel_name: str,
    operation: str,
    connector_key: str,
    rate: float,
    burst: float,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    form: dict[str, str] | None = None,
    owner_url: bool = False,
    content: bytes | None = None,
) -> Any:
    """One rate-limited call that returns the decoded JSON body (or ``{}`` when empty)."""
    limiter = bucket(connector_key, operation, rate, burst)
    max_wait = commerce_config.max_retry_wait_seconds
    attempt = 0
    async with _client(owner_url=owner_url) as client:
        while True:
            wait = limiter.wait_time()
            if wait > max_wait:
                raise RateLimited(f"{channel_name} rate limit for {operation}: waiting for the next request slot.", wait)
            if wait > 0:
                await sleep(wait)
                limiter.wait_time()
            limiter.take()
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json_body if form is None and content is None else None,
                    data=form,
                    content=content,
                )
            except UnsafeModelEndpointError as exc:
                raise UnsafeEndpointError(str(exc).replace("base_url", "The store URL"), code="unsafe_url") from None
            except httpx.TimeoutException:
                raise ChannelError(f"{channel_name} did not answer in time ({operation}).", code="timeout") from None
            except httpx.HTTPError:
                raise ChannelError(f"{channel_name} could not be reached ({operation}).", code="unreachable") from None
            limit_header = response.headers.get("x-amzn-ratelimit-limit")
            if limit_header:
                try:
                    limiter.rate = max(0.001, float(limit_header))
                except ValueError:
                    pass
            if response.status_code in (429, 503):
                delay = _retry_after(response, attempt)
                attempt += 1
                if attempt > commerce_config.max_retries or delay > max_wait:
                    raise RateLimited(f"{channel_name} asked ANUM to slow down ({operation}).", max(delay, 1.0))
                await sleep(delay)
                continue
            raw = response.content[:MAX_BODY_BYTES]
            body: Any = {}
            if raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {"message": raw[:200].decode("utf-8", errors="replace")}
            if response.status_code in (401, 403):
                message, code = _error_text(body, "access denied")
                raise ChannelAuthError(
                    f"{channel_name} refused the credentials ({operation}): {message}", status=response.status_code, code=code
                )
            if response.status_code >= 400:
                message, code = _error_text(body, f"HTTP {response.status_code}")
                raise ChannelError(f"{channel_name} {operation} failed: {message}", status=response.status_code, code=code)
            return body
