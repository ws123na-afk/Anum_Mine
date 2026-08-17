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

    model_config = SettingsConfigDict(env_prefix="ANUM_", env_file=".env", extra="ignore")


settings = Settings()
