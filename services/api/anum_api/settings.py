from pydantic import AliasChoices, BaseModel, Field, SecretStr, ValidationInfo, field_validator
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
    # Keycloak's bootstrap admin password (Keycloak's own variable names, no ANUM_ prefix).
    # The API never uses it; it is read only so startup can refuse the compose default
    # admin/admin outside local when it leaks into the API's environment or .env
    # (anum_api/hardening.py, docs/security.md#startup-policy).
    keycloak_admin_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("KC_BOOTSTRAP_ADMIN_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD"),
    )
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
    # SSRF guard for workspace model base URLs (anum_api/model_egress.py, threat model G1).
    # Outside local/test only public HTTPS endpoints on port 443 are called. This
    # comma-separated list of host or host:port entries lets an operator allow a
    # self-hosted model on a private network, e.g. "ollama.internal:11434".
    model_allowed_hosts: str = ""
    # Fernet key(s) that encrypt stored provider credentials (comma-separated: the first
    # encrypts, all decrypt, for rotation). Required outside ANUM_ENVIRONMENT=local.
    secrets_key: SecretStr | None = Field(default=None, validate_default=True)
    valkey_url: str = "redis://localhost:6379/0"
    # Socket connect/read timeout for Valkey calls (rate limiting, run locks).
    valkey_timeout_seconds: float = Field(default=0.5, gt=0)
    # Run coordination lock (anum_api/valkey.py, docs/agent-runtime.md): "none" relies on
    # the database row lock only; "valkey" also takes a distributed lock per task so API
    # replicas and workers never run, resume or decide the same task concurrently.
    run_lock_backend: str = Field(default="none", pattern="^(none|valkey)$")
    run_lock_ttl_seconds: float = Field(default=300, gt=0)
    run_lock_wait_seconds: float = Field(default=0, ge=0)
    nats_url: str = "nats://localhost:4222"
    event_bus: str = "memory"
    nats_stream: str = "ANUM_EVENTS"
    # Durable outbox relay (PostgreSQL backend + NATS bus only; docs/events.md). The relay
    # connects with this URL when set (a login granted only the anum_outbox_relay role),
    # otherwise with ANUM_DATABASE_URL, and always runs as anum_outbox_relay.
    outbox_database_url: str | None = None
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_poll_seconds: float = Field(default=1.0, gt=0)
    # Agent run execution (docs/agent-runtime.md): "inline" runs inside the API request;
    # "temporal" starts a durable workflow that `python -m anum_api.worker` executes.
    runtime_backend: str = Field(default="inline", pattern="^(inline|temporal)$")
    temporal_target: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "anum-agent-runs"
    # Pending approvals lapse after this many seconds (docs/approvals-and-risk.md); an
    # expired approval can no longer be approved and its run fails.
    approval_ttl_seconds: int = Field(default=86_400, ge=1, le=30 * 86_400)
    # Workspace file bytes (docs/files.md): "local" filesystem, "memory", or "s3"
    # (any S3-compatible endpoint, SeaweedFS locally).
    object_storage_backend: str = Field(default="local", pattern="^(local|memory|s3)$")
    object_storage_local_path: str = ".anum-data/objects"
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "anum-local"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    # Server-side encryption header for every object ("AES256", "aws:kms", or empty for none).
    s3_server_side_encryption: str = ""
    s3_create_bucket: bool = False
    external_webhook_url: str | None = None
    external_webhook_api_key: str | None = None
    # Integration tool responses (REST and MCP) are read up to this many bytes; the rest
    # is dropped and the result is marked truncated (threat model T7/G3).
    tool_response_max_bytes: int = Field(default=256 * 1024, ge=1024, le=16 * 1024 * 1024)
    # Automation (docs/automation.md): workflows, schedules and runs live in this SQLite
    # file with ANUM_REPOSITORY_BACKEND=memory and in PostgreSQL with postgresql.
    automation_database_path: str = ".anum/automation.db"
    # Background loop in each API process that fires due schedules. Safe on every replica
    # with PostgreSQL (FOR UPDATE SKIP LOCKED plus one idempotency key per fire time); the
    # API login must be granted the anum_maintenance role to discover due schedules.
    automation_scheduler_enabled: bool = False
    automation_scheduler_poll_seconds: float = Field(default=30.0, gt=0)
    automation_scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    # HTTP hardening (anum_api/hardening.py, docs/security.md). Uploads to
    # /api/v1/files get max_upload_body_bytes; every other request gets the general limit.
    max_request_body_bytes: int = 1_048_576
    max_upload_body_bytes: int = 25 * 1024 * 1024
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 600
    rate_limit_burst: int = 120
    # "memory" keeps limits per process; "valkey" shares them across API replicas.
    rate_limit_backend: str = Field(default="memory", pattern="^(memory|valkey)$")
    # OpenTelemetry (anum_api/telemetry.py, docs/observability.md). Export is off unless
    # an OTLP/HTTP endpoint is set here or in the standard OTEL_EXPORTER_OTLP_ENDPOINT
    # (for example http://otel-collector:4318). OTEL_SDK_DISABLED=true always wins.
    otel_exporter_otlp_endpoint: str | None = None
    # Overrides the process default ("anum-api" for the API, "anum-worker" for the worker).
    otel_service_name: str | None = None
    otel_metric_export_interval_seconds: float = Field(default=15, gt=0)
    # Ship Python log records over OTLP too (stdout logging is unchanged either way).
    otel_logs_enabled: bool = True
    # Head sampling ratio for new traces (parent-based, so a sampled caller stays sampled).
    otel_traces_sampler_ratio: float = Field(default=1.0, ge=0, le=1)

    model_config = SettingsConfigDict(
        env_prefix="ANUM_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
        # Startup validation errors must never echo secrets (keys, passwords) into logs.
        hide_input_in_errors=True,
    )

    @field_validator("model_allowed_hosts")
    @classmethod
    def _valid_model_allowed_hosts(cls, value: str) -> str:
        from .model_egress import parse_allowed_hosts

        parse_allowed_hosts(value)
        return value

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
