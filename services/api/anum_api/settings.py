from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ANUM API"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://anum:anum@localhost:5432/anum"
    repository_backend: str = "memory"
    keycloak_issuer: str = "http://localhost:8080/realms/anum"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Which auth path `dependencies.tenant_context` uses. "stub_headers"
    # (the default) keeps today's behavior: trust the x-tenant-id/
    # x-workspace-id/x-user-id/x-user-roles headers as-is, with zero
    # verification. "oidc" opts into validating a Bearer token against the
    # configured Keycloak JWKS instead (see anum_api/oidc_auth.py). The
    # default MUST stay "stub_headers" so nothing changes for anyone who
    # hasn't explicitly opted in.
    auth_mode: str = "stub_headers"

    # OIDC bearer-token validation (see anum_api/oidc_auth.py). Only used
    # when `auth_mode == "oidc"`; `keycloak_issuer` above doubles as the
    # expected `iss` claim and, unless `oidc_jwks_url` is set, is used to
    # derive the Keycloak-standard JWKS endpoint
    # (`{issuer}/protocol/openid-connect/certs`).
    oidc_audience: str = "anum-api"
    oidc_jwks_url: str = ""
    oidc_jwks_cache_seconds: int = 300

    # Single-process fixed-window rate limiting (see anum_api/rate_limit.py).
    # Disabled by default so it never changes behavior (including in tests,
    # which reuse one long-lived app/TestClient) unless explicitly turned on.
    # Does NOT coordinate across multiple replicas - see the module
    # docstring before relying on this for a multi-instance deployment.
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    # Only enable when genuinely deployed behind a reverse proxy that sets
    # (and cannot be overridden by the client on) X-Forwarded-For - otherwise
    # this header is trivially spoofable and defeats the rate limit entirely.
    rate_limit_trust_forwarded_for: bool = False

    # Adds Strict-Transport-Security to every response (see
    # anum_api/security_headers.py). Only enable once this deployment is
    # actually served over HTTPS end-to-end - it tells browsers to require
    # HTTPS for this host going forward, which breaks plain http:// access
    # (e.g. local dev).
    security_headers_hsts_enabled: bool = False

    # Phase 2: Valkey-backed ephemeral coordination (see
    # anum_api/valkey_client.py). Unset (the default) keeps every existing
    # in-memory store (idempotency, rate limiting) exactly as it behaves
    # today - single-process, lost on restart. Set to a redis://... URL to
    # move that coordination into Valkey instead, so it survives restarts
    # and is shared across replicas. Valkey speaks the Redis protocol, so
    # any redis:// client library works against it unmodified.
    valkey_url: str | None = None

    # Phase 2: NATS JetStream event bus (see anum_api/events_nats.py).
    # Unset (the default) keeps domain events exactly as they are today -
    # written only to the repository's event log, read only by polling
    # GET /api/v1/events. Set to a nats://... URL to also publish every
    # domain event to JetStream, which anum_api/realtime.py's SSE endpoint
    # then fans out to live-subscribed clients.
    nats_url: str | None = None
    nats_stream_name: str = "ANUM_EVENTS"

    # Phase 2: Temporal-backed durable task execution (see
    # anum_api/workflows/). Unset (the default) keeps POST /tasks/{id}/run
    # exactly as it behaves today - a synchronous in-process call into
    # AgentRuntime that returns once the mock model call (and, if
    # triggered, the approval pause) completes. Set an address to instead
    # start a Temporal workflow for the run, giving it retries, durable
    # waits, and resumability across worker restarts.
    temporal_address: str | None = None
    temporal_namespace: str = "default"
    temporal_task_queue: str = "anum-tasks"

    # Phase 2: S3-compatible object storage (see anum_api/object_storage.py)
    # for task/memory attachments. Unset endpoint (the default) keeps file
    # upload/download endpoints returning 503 "not configured" - no change
    # for deployments that haven't provisioned a bucket yet.
    object_storage_endpoint_url: str | None = None
    object_storage_bucket: str = "anum-files"
    object_storage_region: str = "us-east-1"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None

    # Model gateway (see anum_api/model_gateway.py, docs/model-gateway.md).
    # "mock" (the default) keeps every task run exactly as it behaves
    # today - a deterministic, instant, non-network MockModelGateway call.
    # "anthropic" opts a deployment into real model calls; requires
    # anthropic_api_key to be set (deliberately never falls back to an
    # ambient ANTHROPIC_API_KEY env var - see that module's docstring).
    model_provider: str = "mock"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    model_config = SettingsConfigDict(
        env_prefix="ANUM_", env_file=".env", extra="ignore", protected_namespaces=()
    )


settings = Settings()
