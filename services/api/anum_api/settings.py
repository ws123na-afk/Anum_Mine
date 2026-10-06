from pydantic import BaseModel, Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    """USD price per million tokens for one model, used for estimated cost accounting."""

    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)


# Published list prices (USD per 1M tokens) for the default hosted models. They are
# estimates for accounting only; override with ANUM_MODEL_PRICES as a JSON object.
DEFAULT_MODEL_PRICES: dict[str, ModelPrice] = {
    "gpt-4.1": ModelPrice(input_per_million=2.00, output_per_million=8.00),
    "gpt-4.1-mini": ModelPrice(input_per_million=0.40, output_per_million=1.60),
    "gpt-4.1-nano": ModelPrice(input_per_million=0.10, output_per_million=0.40),
    "gpt-4o": ModelPrice(input_per_million=2.50, output_per_million=10.00),
    "gpt-4o-mini": ModelPrice(input_per_million=0.15, output_per_million=0.60),
}


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
    # Gateway hardening: per-attempt timeout, bounded retries with exponential backoff
    # and full jitter (timeouts, connection errors, 429 and 5xx only), and the cap on
    # how long a provider's Retry-After header may ask us to wait before we give up.
    model_timeout_seconds: float = Field(default=60, gt=0)
    model_max_attempts: int = Field(default=3, ge=1, le=10)
    model_retry_base_seconds: float = Field(default=0.5, ge=0)
    model_retry_max_seconds: float = Field(default=8.0, ge=0)
    model_retry_after_max_seconds: float = Field(default=30.0, ge=0)
    # Price table for estimated_cost_usd, keyed by model name (longest prefix wins).
    # Mock and Ollama calls always cost 0; unknown hosted models report no estimate.
    model_prices: dict[str, ModelPrice] = Field(default_factory=lambda: dict(DEFAULT_MODEL_PRICES))
    # Fernet key(s) that encrypt stored provider credentials (comma-separated: the first
    # encrypts, all decrypt, for rotation). Required outside ANUM_ENVIRONMENT=local.
    secrets_key: SecretStr | None = Field(default=None, validate_default=True)
    valkey_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    event_bus: str = "memory"
    nats_stream: str = "ANUM_EVENTS"
    temporal_target: str = "localhost:7233"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "anum-local"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    external_webhook_url: str | None = None
    external_webhook_api_key: str | None = None
    automation_database_path: str = ".anum/automation.db"
    automation_backend: str = "local"
    # HTTP hardening (anum_api/hardening.py, docs/security.md). Uploads to
    # /api/v1/files get max_upload_body_bytes; every other request gets the general limit.
    max_request_body_bytes: int = 1_048_576
    max_upload_body_bytes: int = 25 * 1024 * 1024
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 600
    rate_limit_burst: int = 120

    model_config = SettingsConfigDict(
        env_prefix="ANUM_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
        # Startup validation errors must never echo secrets (keys, passwords) into logs.
        hide_input_in_errors=True,
    )

    @field_validator("secrets_key")
    @classmethod
    def _require_secrets_key_outside_local(
        cls, value: SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        # A field validator (not a model validator) so a startup error never echoes
        # other settings, such as provider API keys, back into the logs.
        if str(info.data.get("environment", "local")).strip().lower() not in {"local", "test"} and not (
            value and value.get_secret_value().strip()
        ):
            raise ValueError("ANUM_SECRETS_KEY is required outside ANUM_ENVIRONMENT=local or test")
        if value is not None and value.get_secret_value().strip():
            from cryptography.fernet import Fernet

            for key in value.get_secret_value().split(","):
                try:
                    Fernet(key.strip())
                except (ValueError, TypeError):
                    raise ValueError(
                        "ANUM_SECRETS_KEY must be comma-separated url-safe base64 Fernet keys"
                    ) from None
        return value


settings = Settings()
