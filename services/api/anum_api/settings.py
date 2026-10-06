from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ANUM API"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://anum:anum@localhost:5432/anum"
    repository_backend: str = "memory"
    keycloak_issuer: str = "http://localhost:8080/realms/anum"
    auth_mode: str = "headers"
    oidc_audience: str = "anum-api"
    # Optional override when the API reaches Keycloak on a different host than the issuer URL
    # (for example `http://keycloak:8080/...` inside Docker while tokens say `localhost`).
    oidc_jwks_url: str | None = None
    oidc_jwks_cache_seconds: int = 300
    oidc_jwks_min_refresh_seconds: int = 30
    oidc_leeway_seconds: int = 30
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Model provider: "mock" (default, placeholder text), "openai-compatible" (needs
    # ANUM_MODEL_API_KEY) or "ollama" (free, local, no key). For Ollama set
    # ANUM_MODEL_PROVIDER=ollama, ANUM_MODEL_NAME=llama3.2 (or qwen2.5) and optionally
    # ANUM_MODEL_BASE_URL (defaults to http://localhost:11434/v1 for ollama).
    # The task runtime and voice assistant build their gateway from these values at
    # startup; the per-workspace /model-config endpoint does not change them yet.
    model_provider: str = "mock"
    model_api_key: str | None = None
    model_name: str = "gpt-4.1-mini"
    model_base_url: str = "https://api.openai.com/v1"
    valkey_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    temporal_target: str = "localhost:7233"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "anum-local"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    external_webhook_url: str | None = None
    external_webhook_api_key: str | None = None
    automation_database_path: str = ".anum/automation.db"
    automation_backend: str = "local"

    model_config = SettingsConfigDict(
        env_prefix="ANUM_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )


settings = Settings()
