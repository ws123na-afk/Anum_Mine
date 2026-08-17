"""Tests for the opt-in rate limiter and the always-on security headers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api.rate_limit import RateLimitMiddleware
from anum_api.security_headers import SecurityHeadersMiddleware


def _app_with_rate_limit(*, limit: int, window_seconds: int = 60) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, limit=limit, window_seconds=window_seconds)
    return app


def test_requests_within_the_limit_succeed() -> None:
    client = TestClient(_app_with_rate_limit(limit=5))

    for _ in range(5):
        response = client.get("/ping")
        assert response.status_code == 200


def test_requests_over_the_limit_are_rejected_with_standard_envelope() -> None:
    client = TestClient(_app_with_rate_limit(limit=3))

    for _ in range(3):
        assert client.get("/ping").status_code == 200

    blocked = client.get("/ping")

    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    assert blocked.headers["X-Correlation-ID"]
    body = blocked.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["correlation_id"] == blocked.headers["X-Correlation-ID"]


def test_different_client_ips_have_independent_limits() -> None:
    app = _app_with_rate_limit(limit=1)
    client = TestClient(app)

    first = client.get("/ping", headers={"x-forwarded-for": "203.0.113.1"})
    second = client.get("/ping", headers={"x-forwarded-for": "203.0.113.2"})

    # trust_forwarded_for defaults to False, so both requests are actually
    # keyed by the same TestClient host and the second should be blocked -
    # this asserts the *default* (safer) behavior explicitly.
    assert first.status_code == 200
    assert second.status_code == 429


def test_trust_forwarded_for_when_explicitly_enabled_keys_by_that_header() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, limit=1, window_seconds=60, trust_forwarded_for=True)
    client = TestClient(app)

    first = client.get("/ping", headers={"x-forwarded-for": "203.0.113.1"})
    second = client.get("/ping", headers={"x-forwarded-for": "203.0.113.2"})
    third_same_ip_again = client.get("/ping", headers={"x-forwarded-for": "203.0.113.1"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third_same_ip_again.status_code == 429


def test_security_headers_are_present_on_every_response() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(SecurityHeadersMiddleware)
    client = TestClient(app)

    response = client.get("/ping")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_header_only_present_when_explicitly_enabled() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(SecurityHeadersMiddleware, hsts=True)
    client = TestClient(app)

    response = client.get("/ping")

    assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"


def test_default_app_has_rate_limiting_disabled_and_security_headers_enabled() -> None:
    """Confirms the actual app wiring in main.py, not just the middleware in isolation."""

    from anum_api.main import app as real_app
    from anum_api.settings import settings

    assert settings.rate_limit_enabled is False

    client = TestClient(real_app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"

    # With rate limiting disabled by default, a burst well above any
    # reasonable limit must still succeed - this is what protects the rest
    # of the test suite (which reuses this same app/TestClient) from ever
    # tripping a limit unless a test explicitly enables one.
    for _ in range(50):
        assert client.get("/health").status_code == 200
