import re

from pydantic import AliasChoices, BaseModel, Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    """USD price per million tokens for one model, used for estimated cost accounting.

    Prompt-cache prices (Anthropic) default to 1.25x the input price for cache writes
    (5-minute entries) and 0.1x for cache reads; set them where a model differs.
    """

    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    cache_write_per_million: float | None = Field(default=None, ge=0)
    cache_read_per_million: float | None = Field(default=None, ge=0)

    @property
    def cache_write_price(self) -> float:
        if self.cache_write_per_million is not None:
            return self.cache_write_per_million
        return self.input_per_million * 1.25

    @property
    def cache_read_price(self) -> float:
        if self.cache_read_per_million is not None:
            return self.cache_read_per_million
        return self.input_per_million * 0.1


# Published list prices (USD per 1M tokens) for the default hosted models. They are
# estimates for accounting only; override with ANUM_MODEL_PRICES as a JSON object.
DEFAULT_MODEL_PRICES: dict[str, ModelPrice] = {
    "gpt-4.1": ModelPrice(input_per_million=2.00, output_per_million=8.00),
    "gpt-4.1-mini": ModelPrice(input_per_million=0.40, output_per_million=1.60),
    "gpt-4.1-nano": ModelPrice(input_per_million=0.10, output_per_million=0.40),
    "gpt-4o": ModelPrice(input_per_million=2.50, output_per_million=10.00),
    "gpt-4o-mini": ModelPrice(input_per_million=0.15, output_per_million=0.60),
    # Anthropic list prices; Claude Opus 5.5 reads its cache at $0.20 (0.05x input).
    "claude-haiku-5-5": ModelPrice(input_per_million=0.10, output_per_million=0.50),
    "claude-sonnet-5-5": ModelPrice(input_per_million=2.00, output_per_million=10.00),
    "claude-opus-5-5": ModelPrice(
        input_per_million=4.00, output_per_million=20.00, cache_read_per_million=0.20
    ),
    "text-embedding-3-small": ModelPrice(input_per_million=0.02, output_per_million=0.0),
    "text-embedding-3-large": ModelPrice(input_per_million=0.13, output_per_million=0.0),
}

# Keycloak identity provider aliases ANUM's clients know how to offer (kc_idp_hint).
SIGN_IN_PROVIDERS = ("google", "apple")


class Settings(BaseSettings):
    app_name: str = "ANUM API"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://anum:anum@localhost:5432/anum"
    # The API's connection pool (docs/realtime.md#event-loop). One user keeps an event
    # stream per open tab plus the requests each tab makes; 10 + 20 leaves room for many
    # tabs. A request that cannot get a connection fails after the timeout instead of
    # waiting 30 s, and a statement waits at most ``database_lock_timeout_ms`` for a row
    # lock (0 = no limit), so no wait can hold the process for long.
    database_pool_size: int = Field(default=10, ge=1, le=200)
    database_max_overflow: int = Field(default=20, ge=0, le=500)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    database_lock_timeout_ms: int = Field(default=10_000, ge=0, le=600_000)
    # An inline run's claim (the task set to running before the model is asked) left by
    # a process that stopped mid-run may be taken over after this long (inline_runs.py).
    inline_run_lease_seconds: float = Field(default=900.0, ge=60, le=86_400)
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
    # ANUM_MODEL_API_KEY), "anthropic" (the native Messages API with prompt caching;
    # needs ANUM_MODEL_API_KEY, base URL defaults to https://api.anthropic.com) or
    # "ollama" (free, local, no key). For Ollama set
    # ANUM_MODEL_PROVIDER=ollama, ANUM_MODEL_NAME=llama3.2 (or qwen2.5) and optionally
    # ANUM_MODEL_BASE_URL (defaults to http://localhost:11434/v1 for ollama).
    # The task runtime and voice assistant build their gateway from these values at
    # startup; a workspace that saved its own model (PUT /model-config, including
    # "anthropic" with its own routing) uses that instead.
    model_provider: str = "mock"
    model_api_key: str | None = None
    model_name: str = "gpt-4.1-mini"
    model_base_url: str = "https://api.openai.com/v1"
    # Model routing (docs/model-gateway.md#model-routing): chat and voice quick replies
    # use the fast model, task planning and agent runs the capable one. Empty means
    # ANUM_MODEL_NAME for both, as before; with ANUM_MODEL_PROVIDER=anthropic and no
    # ANUM_MODEL_NAME the defaults are claude-haiku-5-5 (fast) and claude-sonnet-5-5.
    model_fast_name: str = ""
    model_capable_name: str = ""
    # Anthropic only: output_config.effort per route ("" sends none, the model's
    # default). "low" keeps quick replies quick on models that think by default; clear
    # it for a fast model without effort support (claude-haiku-4-5).
    model_fast_effort: str = Field(default="low", pattern="^(|low|medium|high|xhigh|max)$")
    model_capable_effort: str = Field(default="", pattern="^(|low|medium|high|xhigh|max)$")
    # Gateway hardening: per-attempt timeout, bounded retries with exponential backoff
    # and full jitter (timeouts, connection errors, 429 and 5xx only), and the cap on
    # how long a provider's Retry-After header may ask us to wait before we give up.
    model_timeout_seconds: float = Field(default=60, gt=0)
    model_max_attempts: int = Field(default=3, ge=1, le=10)
    model_retry_base_seconds: float = Field(default=0.5, ge=0)
    model_retry_max_seconds: float = Field(default=8.0, ge=0)
    model_retry_after_max_seconds: float = Field(default=30.0, ge=0)
    # Upper bound on generated tokens per text call (``max_tokens``); conversational
    # replies are short, and an unbounded reply is what makes a slow model feel hung.
    # Structured (JSON) planning calls are not capped by it: a cut-off plan is invalid.
    model_max_output_tokens: int = Field(default=1024, ge=16, le=32768)
    # Streaming (chat, voice): each attempt must produce its first token within
    # ``model_first_token_timeout_seconds``; the whole stream, retries included, ends
    # after ``model_stream_timeout_seconds``. Nothing is retried once text has streamed.
    model_first_token_timeout_seconds: float = Field(default=20.0, gt=0)
    model_stream_timeout_seconds: float = Field(default=120.0, gt=0)
    # Vision in voice calls (docs/voice.md#call-mode): the model that reads a camera
    # frame. Empty means none for openai-compatible and ollama (a text-only model would
    # fail on an image); with the anthropic provider the fast model is used, since every
    # Claude model accepts images. Examples: gpt-4.1-mini, llava, qwen2.5vl.
    model_vision_name: str = ""
    # Optional server-side speech for voice calls (docs/voice.md#speech-engines). Both
    # speak the OpenAI audio API (POST {base}/audio/transcriptions and
    # {base}/audio/speech), which OpenAI and self-hosted Whisper/Piper servers such as
    # Speaches serve. Empty base URL or model = off; clients then use device speech.
    speech_stt_base_url: str = ""
    speech_stt_api_key: str | None = None
    speech_stt_model: str = ""
    # USD per audio minute, charged to the workspace's model budget; 0 for self-hosted.
    speech_stt_usd_per_minute: float = Field(default=0.0, ge=0, le=10)
    speech_tts_base_url: str = ""
    speech_tts_api_key: str | None = None
    speech_tts_model: str = ""
    speech_tts_voice_en: str = ""
    speech_tts_voice_ar: str = ""
    # USD per million characters of speech, charged to the workspace's model budget.
    speech_tts_usd_per_million_chars: float = Field(default=0.0, ge=0, le=1000)
    speech_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    # Conversations (anum_api/conversations.py): how many earlier turns go back to the
    # model with each question, and the character budget those turns may use.
    conversation_history_turns: int = Field(default=12, ge=0, le=50)
    conversation_history_chars: int = Field(default=24000, ge=1000, le=200000)
    # Conversation retention (docs/conversations.md#retention): the daily retention job
    # deletes conversations (and their turns) with no activity for this many days, the
    # same 30 days as voice transcripts by default; 0 keeps them until the user deletes
    # them. Migration 0019 lets discovery see only conversations idle for a day or more.
    conversation_retention_days: int = Field(default=30, ge=0, le=3650)
    # Privacy (docs/privacy.md): the public URL of the deployment's privacy policy, shown
    # on the Privacy page of every client (empty: the page says the owner has not
    # published one yet), and the largest "Download my data" export the API builds; a
    # larger export is refused with 413 rather than truncated.
    privacy_policy_url: str = Field(default="", max_length=2048, pattern=r"^(|https?://\S+)$")
    privacy_export_max_bytes: int = Field(default=25 * 1024 * 1024, ge=64 * 1024, le=512 * 1024 * 1024)
    # Organisation export and deletion (docs/organisation-lifecycle.md): the background
    # runner, the cooling-off period before a purge, the signed download link, how long a
    # finished archive is kept, its largest size and the batch sizes of each short
    # transaction.
    tenant_lifecycle_enabled: bool = True
    # Credit notes against business invoices (docs/business.md#credit-notes). Off until every replica runs a release that
    # knows ``amount_credited`` (migration 0096 is the expand step; a replica from before it computes a balance without
    # credits). Turn it on after the rollout; see the contract step in the docs.
    business_credit_notes_enabled: bool = False
    tenant_lifecycle_poll_seconds: float = Field(default=30.0, gt=0)
    tenant_deletion_cooling_off_hours: int = Field(default=168, ge=1, le=2160)
    tenant_export_link_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    tenant_export_retention_hours: int = Field(default=168, ge=1, le=2160)
    tenant_export_max_bytes: int = Field(default=512 * 1024 * 1024, ge=1024 * 1024, le=4 * 1024 * 1024 * 1024)
    tenant_export_batch_rows: int = Field(default=500, ge=10, le=10000)
    tenant_purge_batch_rows: int = Field(default=1000, ge=10, le=50000)
    # Removing a deleted organisation's Keycloak accounts (anum_api/keycloak_admin.py): a
    # confidential service-account client with realm-management view-users and
    # manage-users only. All three unset: off, and the operator removes the accounts.
    keycloak_admin_url: str | None = Field(default=None, pattern=r"^https?://\S+$")
    keycloak_admin_realm: str = Field(default="anum", pattern=r"^[A-Za-z0-9_-]{1,80}$")
    keycloak_admin_client_id: str | None = Field(default=None, max_length=200)
    keycloak_admin_client_secret: SecretStr | None = None
    # Sign-in options the clients offer before anyone is signed in
    # (GET /api/v1/auth/sign-in-options, docs/identity.md#google-and-apple-sign-in):
    # the Keycloak identity providers configured in realm anum ("google", "apple",
    # comma-separated; infra/keycloak/social_login.py prints the value to set) and
    # whether Keycloak's self sign-up page is on. They only decide which buttons the
    # clients show; Keycloak is what accepts or refuses a sign-in.
    sign_in_providers: str = ""
    self_sign_up: bool = False
    # Conversation memory (anum_api/conversation_memory.py, docs/memory.md#from-conversations).
    # After a reply completes, a small background model call (at most
    # ``conversation_memory_max_tokens``, metered against the budget) extracts durable
    # facts the user stated; they become private memories, deduplicated against the
    # user's earlier ones (cosine >= ``conversation_memory_dedupe_threshold`` updates
    # instead of adding). Each ask recalls the ``conversation_memory_recall_k`` nearest
    # memories, at most ``conversation_memory_recall_chars`` of text, into the system
    # prompt. ``false`` turns both off; a tenant also turns extraction off by leaving
    # "conversation" out of its memory governance ``allowed_source_types``.
    conversation_memory_enabled: bool = True
    conversation_memory_max_tokens: int = Field(default=256, ge=32, le=2048)
    conversation_memory_max_facts: int = Field(default=5, ge=1, le=20)
    conversation_memory_dedupe_threshold: float = Field(default=0.85, gt=0, le=1)
    conversation_memory_timeout_seconds: float = Field(default=60.0, gt=0)
    conversation_memory_recall_k: int = Field(default=4, ge=0, le=20)
    conversation_memory_recall_chars: int = Field(default=2000, ge=0, le=20_000)
    conversation_memory_recall_timeout_seconds: float = Field(default=3.0, gt=0)
    # What ANUM learned (anum_api/learning.py, docs/learning.md): feedback signals, the
    # style profile, routines and weekly improvement notes, per user. ``false`` stops
    # all new learning and the learned block in prompts; stored data stays viewable and
    # deletable. Signals older than ``learning_signal_retention_days`` are deleted the
    # next time their user's learning data is used.
    learning_enabled: bool = True
    learning_signal_retention_days: int = Field(default=180, ge=7, le=3650)
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
    # Retrieval into agent prompts (anum_api/retrieval.py, docs/memory.md#retrieval).
    # "gateway" embeds through the workspace's model gateway (budgets and the SSRF guard
    # apply; the mock provider uses the deterministic local embedder); "local" always
    # uses the deterministic hashing embedder, so no text leaves the API process.
    embedding_backend: str = Field(default="gateway", pattern="^(gateway|local)$")
    # Embedding model for "gateway"; empty picks a per-provider default
    # (nomic-embed-text for Ollama, text-embedding-3-small otherwise).
    embedding_model: str = ""
    retrieval_top_k: int = Field(default=5, ge=0, le=20)
    # Upper bounds on retrieved text placed into one prompt and into one labeled block.
    retrieval_max_chars: int = Field(default=6000, ge=0, le=100_000)
    retrieval_block_max_chars: int = Field(default=1500, ge=100, le=20_000)
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
    # Upload malware scanning (docs/files.md#malware-scanning): "off", or "clamav" for a
    # clamd reached over TCP with INSTREAM (anum_api/file_scanner_clamav.py). With clamav,
    # ANUM_FILE_SCAN_REQUIRED=true fails uploads closed (503, nothing stored) when clamd is
    # unreachable or times out; false accepts them unscanned and is refused outside local.
    file_scanner: str = Field(default="off", pattern="^(off|clamav)$")
    clamav_host: str = ""
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    file_scan_required: bool = True
    external_webhook_url: str | None = None
    external_webhook_api_key: str | None = None
    # Integration tool responses (REST and MCP) are read up to this many bytes; the rest
    # is dropped and the result is marked truncated (threat model T7/G3).
    tool_response_max_bytes: int = Field(default=256 * 1024, ge=1024, le=16 * 1024 * 1024)
    # Private web search (docs/tools-and-integrations.md#web-search): the base URL of a
    # self-hosted SearXNG on the internal network, e.g. http://searxng:8080. Empty turns
    # the `web_search` tool off (it is not registered). Operator configuration only:
    # workspaces can switch the tool off but never point it somewhere else (SSRF).
    searxng_url: str = ""
    web_search_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    web_search_max_results: int = Field(default=5, ge=1, le=20)
    # SearXNG safesearch level: 0 off, 1 moderate, 2 strict.
    web_search_safe_search: int = Field(default=1, ge=0, le=2)
    # Tyre planning (docs/tyre-planning.md): reads the owner's fleet data from their own
    # Supabase (PostgreSQL or REST) or Firebase (Firestore). Workspace-saved Supabase
    # hosts must be public HTTPS/TLS endpoints outside local; this comma-separated list of
    # host or host:port entries is the operator's escape hatch for a self-hosted database
    # on a private network (same format as ANUM_MODEL_ALLOWED_HOSTS).
    tyre_source_allowed_hosts: str = ""
    # Rows read per mapped table or collection in one sync; the rest is reported as cut.
    tyre_max_rows_per_entity: int = Field(default=50_000, ge=100, le=500_000)
    tyre_source_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    # Automation (docs/automation.md): workflows, schedules and runs live in this SQLite
    # file with ANUM_REPOSITORY_BACKEND=memory and in PostgreSQL with postgresql.
    automation_database_path: str = ".anum/automation.db"
    # Background loop in each API process that fires due schedules. Safe on every replica
    # with PostgreSQL (FOR UPDATE SKIP LOCKED plus one idempotency key per fire time); the
    # API login must be granted the anum_maintenance role to discover due schedules.
    automation_scheduler_enabled: bool = False
    automation_scheduler_poll_seconds: float = Field(default=30.0, gt=0)
    automation_scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    # Team work (docs/team-work.md#reminders): overdue reminders, ANUM follow-ups and
    # notification email retries run in the same background loop as the automation
    # scheduler (ANUM_AUTOMATION_SCHEDULER_ENABLED) and use the same anum_maintenance grant.
    team_reminder_poll_seconds: float = Field(default=60.0, gt=0)
    team_reminder_batch_size: int = Field(default=200, ge=1, le=2000)
    # Notification email (docs/team-work.md#notifications). Unset host: email is recorded
    # as not_configured and only the in-app notification is delivered. "tls" is implicit
    # TLS (port 465), "starttls" upgrades a plain connection and refuses to send if the
    # server does not offer it; "none" (no encryption) is honoured only in local and test.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: str = Field(default="starttls", pattern="^(starttls|tls|none)$")
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    # System email sent at once (invitations, notification email, the test message;
    # docs/notifications.md#email): a transient failure (unreachable server, 4xx) is tried
    # again after this many seconds, doubling each time, up to three attempts.
    smtp_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=30)
    # The web app's public address (for example https://anum-app.example.com), used for the
    # accept link in invitation emails. Empty: the link uses the address the inviting
    # browser was on when that origin is in ANUM_CORS_ORIGINS, else the email carries the
    # token and says where to paste it (docs/identity.md#invitation-email).
    public_app_url: str = Field(default="", max_length=2048, pattern=r"^(|https?://\S+)$")
    # HTTP hardening (anum_api/hardening.py, docs/security.md). Uploads to
    # /api/v1/files get max_upload_body_bytes; every other request gets the general limit.
    max_request_body_bytes: int = 1_048_576
    max_upload_body_bytes: int = 25 * 1024 * 1024
    # Workspace files (docs/files.md): bytes one workspace may store in total (every
    # workspace gets this quota), and how long a signed download or preview link works.
    files_workspace_quota_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    files_link_ttl_seconds: int = Field(default=300, ge=30, le=3600)
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
    # Error reporting to a Sentry-compatible server such as GlitchTip
    # (anum_api/error_reporting.py, docs/observability.md#error-reporting). Empty: off.
    # Outside local it must be https, or http to a private service name (no dots).
    sentry_dsn: SecretStr | None = None
    # Phone push through Firebase Cloud Messaging HTTP v1 (anum_api/push.py,
    # docs/notifications.md). The whole service-account JSON key of the Firebase project
    # (it carries a private key: keep it in a secret store). Empty: push is off and
    # GET /api/v1/push/status says so. Android and iOS both deliver through FCM.
    fcm_service_account_json: SecretStr | None = None
    # Bounded fan-out: at most this many recipients per event and devices per recipient,
    # each message tried at most push_max_attempts times with exponential backoff.
    push_max_recipients: int = Field(default=50, ge=1, le=500)
    push_max_devices_per_user: int = Field(default=10, ge=1, le=50)
    push_max_attempts: int = Field(default=4, ge=1, le=10)
    push_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # Events waiting for delivery in each process; beyond this the oldest are dropped.
    push_max_pending: int = Field(default=1000, ge=10, le=100_000)
    # In-app calls between teammates (anum_api/app_calls.py, docs/in-app-calls.md). Off
    # until a TURN relay is configured: ``calls_turn_urls`` (comma-separated turn: or
    # turns: URLs of the self-hosted coturn) plus ``calls_turn_secret`` (coturn's
    # static-auth-secret, 32+ characters) give "turn" mode, where the API mints short-lived
    # TURN credentials (HMAC-SHA1, coturn's REST scheme) per call. Without them,
    # ``calls_allow_stun_only=true`` gives "stun_only" mode: calls connect on many
    # networks but fail behind strict NATs and firewalls. ``calls_stun_urls`` (stun: URLs)
    # default to the TURN hosts.
    calls_turn_urls: str = ""
    calls_turn_secret: SecretStr | None = None
    calls_stun_urls: str = ""
    calls_allow_stun_only: bool = False
    calls_turn_ttl_seconds: int = Field(default=7200, ge=300, le=86_400)
    calls_ring_timeout_seconds: int = Field(default=45, ge=10, le=120)
    calls_video_enabled: bool = True
    # The signaling stream reads the database (in a thread, with its own short session)
    # when a call event wakes it (anum_api/app_call_wake.py) and, as a fallback, polls:
    # every ``calls_poll_seconds`` while idle and every ``calls_active_poll_seconds``
    # while the caller has a call ringing or in progress, or only every
    # ``calls_bus_fallback_poll_seconds`` while NATS delivers the wake-ups.
    calls_poll_seconds: float = Field(default=1.0, ge=0.1, le=10)
    calls_active_poll_seconds: float = Field(default=0.25, ge=0.05, le=5)
    calls_bus_fallback_poll_seconds: float = Field(default=10.0, ge=1, le=60)
    # Calls (and their signaling rows) older than this are purged lazily.
    calls_retention_days: int = Field(default=365, ge=7, le=3650)
    # Calls & Meetings (anum_api/meetings.py, docs/meetings.md). Recording uploads are
    # capped at ``meeting_recording_max_bytes`` (hosted Whisper APIs accept 25 MB) and
    # transcript files at ``meeting_transcript_max_bytes``. The analysis prompt holds at
    # most ``meeting_analysis_max_chars`` of transcript (the rest is marked truncated) and
    # the answer at most ``meeting_analysis_max_tokens``.
    meeting_recording_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024 * 1024, le=512 * 1024 * 1024)
    meeting_transcript_max_bytes: int = Field(default=5 * 1024 * 1024, ge=64 * 1024, le=50 * 1024 * 1024)
    meeting_analysis_max_chars: int = Field(default=60_000, ge=2_000, le=400_000)
    meeting_analysis_max_tokens: int = Field(default=2_000, ge=256, le=16_000)
    # Speech-to-text for recordings (anum_api/transcription.py). "none" (default): only
    # transcript files and Zoom/Teams transcripts can be imported. "openai-compatible":
    # POST {base_url}/audio/transcriptions, which a local Whisper server (faster-whisper
    # server, Speaches, whisper.cpp server, LocalAI) or a hosted provider (OpenAI, Groq)
    # answers. "deepgram": Deepgram's hosted pre-recorded API, with speaker labels.
    transcription_provider: str = Field(default="none", pattern="^(none|openai-compatible|deepgram)$")
    transcription_base_url: str = Field(default="", max_length=500)
    transcription_model: str = Field(default="", max_length=200)
    transcription_api_key: SecretStr | None = None
    transcription_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    # Zoom and Microsoft Teams transcript connectors (anum_api/meeting_connectors.py).
    # Each needs an OAuth app the owner registers (docs/meetings.md#connectors); empty
    # client ids switch the connector off. ``meetings_oauth_redirect_uri`` is the web
    # app's callback page, for example https://app.example.com/meetings/connected, and
    # must be registered as the redirect URL of both apps.
    zoom_client_id: str = Field(default="", max_length=200)
    zoom_client_secret: SecretStr | None = None
    microsoft_client_id: str = Field(default="", max_length=200)
    microsoft_client_secret: SecretStr | None = None
    microsoft_tenant: str = Field(default="organizations", pattern=r"^[A-Za-z0-9.\-]{1,100}$")
    meetings_oauth_redirect_uri: str = Field(default="", max_length=500, pattern=r"^(|https?://\S+)$")

    # Customer billing and self-serve sign-up (docs/billing.md). The issuer is the platform
    # owner's own organisation: its published plans are the public pricing page and every
    # sign-up is billed by it. Sign-up stays off until the owner turns it on; the Keycloak
    # provisioning client (a confidential client with a service account allowed to
    # manage users) links a new organisation to the account that created it.
    billing_issuer_tenant_id: str | None = Field(default=None, pattern=r"^(|[a-z0-9][a-z0-9_-]{2,79})$")
    billing_scheduler_enabled: bool = False
    billing_poll_seconds: float = Field(default=3600.0, ge=30, le=86_400)
    web_app_url: str = Field(default="", max_length=500, pattern=r"^(|https?://\S+)$")
    signup_enabled: bool = False
    signup_trial_days: int = Field(default=14, ge=0, le=90)
    signup_daily_limit: int = Field(default=50, ge=0, le=10_000)
    signup_ip_hourly_limit: int = Field(default=3, ge=1, le=100)
    keycloak_provisioner_client_id: str = Field(default="", max_length=200)
    keycloak_provisioner_client_secret: SecretStr | None = None
    # First-party skill signing keys (docs/skills.md#signing): "key-id=BASE64,key-id2=BASE64",
    # raw 32-byte Ed25519 public keys. Malformed entries are ignored with a warning.
    skill_platform_keys: str = Field(default="", max_length=4000)

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

    @field_validator("clamav_host")
    @classmethod
    def _clamav_needs_a_host(cls, value: str, info: ValidationInfo) -> str:
        host = value.strip()
        if info.data.get("file_scanner") == "clamav" and not host:
            raise ValueError("ANUM_CLAMAV_HOST is required when ANUM_FILE_SCANNER=clamav (the clamd host name)")
        if host and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]+\]", host):
            raise ValueError("ANUM_CLAMAV_HOST must be a host name or IP address, without a scheme or port")
        return host

    @field_validator("sign_in_providers")
    @classmethod
    def _valid_sign_in_providers(cls, value: str) -> str:
        names: list[str] = []
        for raw in value.split(","):
            name = raw.strip().lower()
            if not name:
                continue
            if name not in SIGN_IN_PROVIDERS:
                raise ValueError(
                    f"ANUM_SIGN_IN_PROVIDERS lists {name!r}; allowed: {', '.join(SIGN_IN_PROVIDERS)}"
                )
            if name not in names:
                names.append(name)
        return ",".join(names)

    @field_validator("sentry_dsn")
    @classmethod
    def _valid_sentry_dsn(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        from .error_reporting import dsn_problem

        problem = dsn_problem(value.get_secret_value())
        if problem:
            # The message names the setting only: a DSN carries the project's key.
            raise ValueError(problem)
        return value

    @field_validator("fcm_service_account_json")
    @classmethod
    def _valid_fcm_service_account(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        from .push_fcm import service_account_problem

        problem = service_account_problem(value.get_secret_value())
        if problem:
            # The message names the setting and the problem only, never the key.
            raise ValueError(problem)
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
