from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ANUM API"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://anum:anum@localhost:5432/anum"
    repository_backend: str = "memory"
    keycloak_issuer: str = "http://localhost:8080/realms/anum"
    auth_mode: str = "headers"
    oidc_audience: str = "anum-api"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
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
