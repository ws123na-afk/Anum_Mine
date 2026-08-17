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

    model_config = SettingsConfigDict(env_prefix="ANUM_", env_file=".env", extra="ignore")


settings = Settings()
