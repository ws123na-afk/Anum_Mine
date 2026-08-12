from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ANUM API"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://anum:anum@localhost:5432/anum"
    repository_backend: str = "memory"
    keycloak_issuer: str = "http://localhost:8080/realms/anum"
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = SettingsConfigDict(env_prefix="ANUM_", env_file=".env", extra="ignore")


settings = Settings()
