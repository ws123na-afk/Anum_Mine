from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from anum_api.hardening import (
    API_CONTENT_SECURITY_POLICY,
    BodyLimitRule,
    BodySizeLimitMiddleware,
    InMemoryTokenBucket,
    InsecureConfigurationError,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    docs_routes,
    enforce_startup_policy,
    insecure_configuration_problems,
)
from anum_api.main import app
from anum_api.settings import Settings

HEADERS = {
    "x-tenant-id": "tenant_hardening",
    "x-workspace-id": "workspace_hardening",
    "x-user-id": "user_hardening",
    "x-user-roles": "owner",
}


def _echo_app() -> FastAPI:
    echo = FastAPI()

    @echo.post("/echo")
    async def echo_body(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @echo.post("/upload/item")
    async def upload(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @echo.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @echo.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "pong"}

    return echo


# --- request body size limit ------------------------------------------------


def _limited_client() -> TestClient:
    limited = _echo_app()
    limited.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=10,
        rules=[BodyLimitRule("POST", "/upload", 100)],
    )
    return TestClient(limited)


def test_body_within_limit_is_accepted() -> None:
    response = _limited_client().post("/echo", content=b"x" * 10)
    assert response.status_code == 200
    assert response.json() == {"received": 10}


def test_declared_oversized_body_is_rejected_with_413_envelope() -> None:
    response = _limited_client().post("/echo", content=b"x" * 11)
    assert response.status_code == 413
    body = response.json()["error"]
    assert body["code"] == "payload_too_large"
    assert body["correlation_id"]
    assert response.headers["x-correlation-id"] == body["correlation_id"]


def test_chunked_body_without_content_length_is_counted() -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(5):
            yield b"x" * 4

    response = _limited_client().post("/echo", content=chunks())
    assert response.status_code == 413


def test_invalid_content_length_is_rejected() -> None:
    response = _limited_client().post("/echo", content=b"x", headers={"content-length": "abc"})
    assert response.status_code == 400


def test_upload_rule_allows_a_larger_body_only_on_its_prefix() -> None:
    client = _limited_client()
    assert client.post("/upload/item", content=b"x" * 100).status_code == 200
    assert client.post("/upload/item", content=b"x" * 101).status_code == 413
    assert client.post("/echo", content=b"x" * 50).status_code == 413


def test_api_rejects_oversized_json_body() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/tasks",
        headers=HEADERS,
        content=b"{" + b" " * 2_000_000 + b"}",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_file_uploads_keep_their_larger_allowance() -> None:
    middleware = BodySizeLimitMiddleware(
        app=_echo_app(),
        max_bytes=1_048_576,
        rules=[BodyLimitRule("POST", "/api/v1/files", 25 * 1024 * 1024)],
    )
    assert middleware.limit_for("POST", "/api/v1/files") == 25 * 1024 * 1024
    assert middleware.limit_for("POST", "/api/v1/filesystem") == 1_048_576
    assert middleware.limit_for("PUT", "/api/v1/files") == 1_048_576


# --- rate limiting ------------------------------------------------------------


def test_token_bucket_allows_burst_then_refills() -> None:
    bucket = InMemoryTokenBucket(rate_per_second=1.0, burst=2)
    assert bucket.acquire("a", now=0.0).allowed
    assert bucket.acquire("a", now=0.0).allowed
    denied = bucket.acquire("a", now=0.0)
    assert not denied.allowed
    assert denied.retry_after_seconds == 1
    assert bucket.acquire("b", now=0.0).allowed, "keys must be independent"
    assert bucket.acquire("a", now=1.0).allowed


def test_token_bucket_bounds_tracked_keys() -> None:
    bucket = InMemoryTokenBucket(rate_per_second=1.0, burst=1, max_keys=2)
    for key in ("a", "b", "c"):
        bucket.acquire(key, now=0.0)
    assert len(bucket._buckets) == 2


def test_rate_limit_returns_429_with_retry_after() -> None:
    limited = _echo_app()
    limited.add_middleware(
        RateLimitMiddleware,
        backend=InMemoryTokenBucket(rate_per_second=0.5, burst=2),
    )
    client = TestClient(limited)
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    response = client.get("/ping")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert response.json()["error"]["code"] == "rate_limited"
    assert client.get("/health").status_code == 200, "health checks are exempt"


def test_api_app_has_rate_limiting_enabled_with_retry_after_exposed() -> None:
    assert isinstance(app.state.rate_limiter, InMemoryTokenBucket)
    client = TestClient(app)
    response = client.options(
        "/api/v1/tasks",
        headers={"origin": "http://localhost:5173", "access-control-request-method": "GET"},
    )
    assert response.status_code == 200
    simple = client.get("/health", headers={"origin": "http://localhost:5173"})
    assert "retry-after" in simple.headers["access-control-expose-headers"].lower()


# --- security headers -----------------------------------------------------------


def test_api_responses_carry_security_headers_without_hsts_locally() -> None:
    client = TestClient(app)
    for response in (client.get("/health"), client.get("/does-not-exist")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
        assert "strict-transport-security" not in response.headers


def test_local_docs_page_keeps_working_without_the_api_csp() -> None:
    response = TestClient(app).get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


def test_hsts_is_sent_outside_local() -> None:
    hardened = _echo_app()
    hardened.add_middleware(SecurityHeadersMiddleware, environment="staging")
    response = TestClient(hardened).get("/ping")
    assert response.headers["strict-transport-security"].startswith("max-age=")


# --- startup policy -------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "staging",
        "cors_origins": ["https://app.staging.example.com"],
        "database_url": "postgresql+psycopg://anum_app:placeholder@db.internal:5432/anum",
        "s3_secret_key": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_local_environment_allows_development_defaults() -> None:
    assert insecure_configuration_problems(Settings(environment="local")) == []


def test_hardened_non_local_configuration_passes() -> None:
    enforce_startup_policy(_settings())


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.example.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://[::1]:5173",
        "https://app.localhost",
        "http://app.example.com",
    ],
)
def test_unsafe_cors_origins_are_refused_outside_local(origin: str) -> None:
    problems = insecure_configuration_problems(_settings(cors_origins=[origin]))
    assert any("ANUM_CORS_ORIGINS" in problem for problem in problems)


def test_compose_database_credentials_are_refused_outside_local() -> None:
    config = _settings(database_url="postgresql+psycopg://anum:anum@db.internal:5432/anum")
    with pytest.raises(InsecureConfigurationError) as raised:
        enforce_startup_policy(config)
    assert any("ANUM_DATABASE_URL" in problem for problem in raised.value.problems)


def test_compose_object_storage_secret_is_refused_outside_local() -> None:
    problems = insecure_configuration_problems(_settings(s3_secret_key="anum-local-secret"))
    assert any("ANUM_S3_SECRET_KEY" in problem for problem in problems)


def test_disabling_rate_limits_is_refused_outside_local() -> None:
    problems = insecure_configuration_problems(_settings(rate_limit_enabled=False))
    assert any("RATE_LIMIT" in problem for problem in problems)


def test_default_settings_are_refused_as_staging() -> None:
    problems = insecure_configuration_problems(Settings(environment="staging"))
    assert len(problems) >= 3


def test_interactive_docs_are_disabled_outside_local() -> None:
    assert docs_routes(Settings(environment="local")) == {}
    assert docs_routes(_settings()) == {"docs_url": None, "redoc_url": None}
