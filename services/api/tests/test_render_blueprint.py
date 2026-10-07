"""The Render Blueprint (render.yaml) keeps ANUM's secret handling and startup policy.

render.yaml is the Render counterpart of the Helm chart (docs/deploy-render.md). Like
infra/helm/ci/lint.sh does for the chart, these tests fail when the Blueprint would put a
credential in git, leave out a setting the API needs outside local, give the API the
schema-owner login, or carry a value the API refuses at startup. No network.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from cryptography.fernet import Fernet

from anum_api.hardening import DEFAULT_DEV_SECRETS, insecure_configuration_problems
from anum_api.identity import LOCAL_ENVIRONMENTS, validate_auth_configuration
from anum_api.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = REPO_ROOT / "render.yaml"
REALM = REPO_ROOT / "infra" / "keycloak" / "anum-realm.json"
REALM_SCRIPT = REPO_ROOT / "infra" / "render" / "keycloak_realm.py"

# Names that hold credentials. Such a variable must never carry a literal `value`.
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|CREDENTIAL|_KEY$|_KEYS$|DATABASE_URL|_URL_PASSWORD)")
# Variables the API process must not see: Keycloak's admin account (refused at startup
# with the compose default, docs/security.md#startup-policy).
KEYCLOAK_ADMIN_VARS = {
    "KEYCLOAK_ADMIN",
    "KEYCLOAK_ADMIN_PASSWORD",
    "KC_BOOTSTRAP_ADMIN_USERNAME",
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
}
# What the API needs outside local, whatever the backends (docs/infrastructure.md#environments).
API_REQUIRED = {
    "ANUM_ENVIRONMENT",
    "ANUM_AUTH_MODE",
    "ANUM_KEYCLOAK_ISSUER",
    "ANUM_OIDC_AUDIENCE",
    "ANUM_CORS_ORIGINS",
    "ANUM_REPOSITORY_BACKEND",
    "ANUM_DATABASE_URL",
    "ANUM_MIGRATION_DATABASE_URL",
    "ANUM_SECRETS_KEY",
    "ANUM_OBJECT_STORAGE_BACKEND",
    "ANUM_MODEL_PROVIDER",
    "ANUM_RATE_LIMIT_ENABLED",
}
S3_REQUIRED = {"ANUM_S3_ENDPOINT", "ANUM_S3_REGION", "ANUM_S3_BUCKET", "ANUM_S3_ACCESS_KEY", "ANUM_S3_SECRET_KEY"}
CRON_REQUIRED = {"ANUM_ENVIRONMENT", "ANUM_REPOSITORY_BACKEND", "ANUM_DATABASE_URL", "ANUM_SECRETS_KEY"}

# Stand-ins for the values the owner types into the Dashboard (`sync: false`) or Render
# wires (`fromService`/`fromDatabase`), shaped like the real ones in docs/deploy-render.md.
OWNER_VALUES = {
    "ANUM_KEYCLOAK_ISSUER": "https://auth.example.test/realms/anum",
    "ANUM_CORS_ORIGINS": '["https://app.example.test"]',
    "ANUM_DATABASE_URL": "postgresql+psycopg://anum_app:example@dpg-example-a:5432/anum",
    "ANUM_MIGRATION_DATABASE_URL": "postgresql+psycopg://anum_migrator:example@dpg-example-a:5432/anum",
    "ANUM_S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.example",
    "ANUM_S3_REGION": "us-west-004",
    "ANUM_S3_BUCKET": "anum-staging-files",
    "ANUM_S3_ACCESS_KEY": "example",
    "ANUM_S3_SECRET_KEY": "example",
    "ANUM_VALKEY_URL": "redis://red-example:6379",
}


def _blueprint() -> dict[str, Any]:
    return yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))


def _services() -> dict[str, dict[str, Any]]:
    return {service["name"]: service for service in _blueprint()["services"]}


def _env(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["key"]: entry for entry in service.get("envVars", []) if "key" in entry}


def _provided(entry: dict[str, Any]) -> bool:
    if entry.get("sync") is False or entry.get("generateValue") is True:
        return True
    if "fromDatabase" in entry or "fromService" in entry:
        return True
    return str(entry.get("value", "")).strip() != ""


def _all_env_entries() -> list[tuple[str, dict[str, Any]]]:
    data = _blueprint()
    entries = [(service["name"], entry) for service in data["services"] for entry in service.get("envVars", [])]
    for group in data.get("envVarGroups", []) or []:
        entries.extend((f"group {group['name']}", entry) for entry in group.get("envVars", []))
    return entries


def test_blueprint_parses_and_has_the_expected_resources() -> None:
    data = _blueprint()
    services = _services()
    assert {name: service["type"] for name, service in services.items()} == {
        "anum-api": "web",
        "anum-web": "web",
        "anum-keycloak": "web",
        "anum-kv": "keyvalue",
        "anum-voice-retention": "cron",
    }
    assert {database["name"] for database in data["databases"]} == {"anum-db", "anum-keycloak-db"}
    # One region for everything: the private network and fromDatabase need it.
    regions = {resource["region"] for resource in [*services.values(), *data["databases"]]}
    assert len(regions) == 1
    assert data["previews"]["generation"] == "off"


def test_docker_services_build_from_the_repository_dockerfiles() -> None:
    for name, service in _services().items():
        if service.get("runtime") != "docker":
            continue
        dockerfile = REPO_ROOT / service["dockerfilePath"]
        context = REPO_ROOT / service["dockerContext"]
        assert dockerfile.is_file(), name
        assert context.is_dir(), name
    services = _services()
    assert services["anum-api"]["dockerfilePath"] == "./services/api/Dockerfile"
    assert services["anum-api"]["dockerContext"] == "./services/api"
    assert services["anum-web"]["dockerfilePath"] == "./apps/web/Dockerfile"
    assert services["anum-web"]["dockerContext"] == "."


def test_health_checks() -> None:
    services = _services()
    assert services["anum-api"]["healthCheckPath"] == "/health"
    assert services["anum-web"]["healthCheckPath"] == "/healthz"
    assert services["anum-keycloak"]["healthCheckPath"] == "/realms/anum/.well-known/openid-configuration"
    # The listen ports Render routes to match what the images serve.
    assert _env(services["anum-api"])["PORT"]["value"] == "8000"
    assert "--port 8000" in services["anum-api"]["dockerCommand"]
    assert _env(services["anum-web"])["PORT"]["value"] == "8080"
    assert _env(services["anum-keycloak"])["PORT"]["value"] == "8080"


def test_no_literal_secret_values() -> None:
    problems = []
    for owner, entry in _all_env_entries():
        key = entry.get("key", "")
        value = entry.get("value")
        if SECRET_NAME.search(key) and value is not None:
            problems.append(f"{owner}: {key} has a literal value")
        if value is not None:
            text = str(value)
            if text in DEFAULT_DEV_SECRETS:
                problems.append(f"{owner}: {key} is a compose development credential")
            if "://" in text and urlsplit(text).password:
                problems.append(f"{owner}: {key} embeds a password in a URL")
        if "generateValue" in entry and entry["generateValue"] is not True:
            problems.append(f"{owner}: {key} generateValue must be true")
    assert problems == []


def test_every_secret_is_owner_entered_generated_or_wired() -> None:
    for owner, entry in _all_env_entries():
        key = entry.get("key", "")
        if SECRET_NAME.search(key):
            assert (
                entry.get("sync") is False
                or entry.get("generateValue") is True
                or "fromDatabase" in entry
                or "fromService" in entry
            ), f"{owner}: {key}"
    api = _env(_services()["anum-api"])
    # Render's generateValue promises a random base64 value, not the url-safe base64
    # Fernet format; the owner generates the key (docs/deploy-render.md).
    assert api["ANUM_SECRETS_KEY"] == {"key": "ANUM_SECRETS_KEY", "sync": False}


def test_api_gets_every_setting_it_needs_outside_local() -> None:
    api = _env(_services()["anum-api"])
    missing = sorted(key for key in API_REQUIRED if key not in api or not _provided(api[key]))
    assert missing == []
    assert api["ANUM_ENVIRONMENT"]["value"] not in LOCAL_ENVIRONMENTS
    if api["ANUM_OBJECT_STORAGE_BACKEND"]["value"] == "s3":
        assert sorted(key for key in S3_REQUIRED if key not in api or not _provided(api[key])) == []
    if "valkey" in {api.get("ANUM_RUN_LOCK_BACKEND", {}).get("value"), api.get("ANUM_RATE_LIMIT_BACKEND", {}).get("value")}:
        assert _provided(api["ANUM_VALKEY_URL"])
        assert api["ANUM_VALKEY_URL"]["fromService"]["name"] == "anum-kv"


def test_cron_job_gets_what_voice_retention_needs() -> None:
    services = _services()
    cron = services["anum-voice-retention"]
    env = _env(cron)
    assert sorted(key for key in CRON_REQUIRED if key not in env or not _provided(env[key])) == []
    assert cron["dockerCommand"] == "python -m anum_api.voice_retention"
    assert env["ANUM_ENVIRONMENT"]["value"] == _env(services["anum-api"])["ANUM_ENVIRONMENT"]["value"]
    # The application login (RLS applies), copied from the API, never the migration login.
    for key in ("ANUM_DATABASE_URL", "ANUM_SECRETS_KEY"):
        assert env[key]["fromService"] == {"name": "anum-api", "type": "web", "envVarKey": key}
    assert "ANUM_MIGRATION_DATABASE_URL" not in env


def test_migrations_run_as_the_migration_login_and_the_api_never_keeps_it() -> None:
    services = _services()
    api = services["anum-api"]
    pre_deploy = api["preDeployCommand"]
    assert "alembic upgrade head" in pre_deploy
    assert 'export ANUM_DATABASE_URL="$ANUM_MIGRATION_DATABASE_URL"' in pre_deploy
    # Fails instead of migrating as the app login when the migration URL is missing.
    assert "${ANUM_MIGRATION_DATABASE_URL:?" in pre_deploy
    command = api["dockerCommand"]
    assert command.index("unset ANUM_MIGRATION_DATABASE_URL") < command.index("exec uvicorn anum_api.main:app")
    for name, service in services.items():
        if name != "anum-api":
            assert "ANUM_MIGRATION_DATABASE_URL" not in _env(service), name
            assert "preDeployCommand" not in service, name
    # No service is handed the Render database's default (admin) login for ANUM's data.
    for name, service in services.items():
        for entry in _env(service).values():
            if "fromDatabase" in entry:
                assert entry["fromDatabase"]["name"] == "anum-keycloak-db", (name, entry["key"])


def test_keycloak_admin_credentials_stay_out_of_anum_services() -> None:
    services = _services()
    for name in ("anum-api", "anum-voice-retention", "anum-web"):
        assert KEYCLOAK_ADMIN_VARS.isdisjoint(_env(services[name])), name
    keycloak = _env(services["anum-keycloak"])
    assert keycloak["KC_BOOTSTRAP_ADMIN_PASSWORD"] == {"key": "KC_BOOTSTRAP_ADMIN_PASSWORD", "generateValue": True}
    assert keycloak["KC_BOOTSTRAP_ADMIN_USERNAME"]["value"] not in {"admin", "anum"}
    assert keycloak["KC_HOSTNAME"]["sync"] is False
    assert keycloak["ANUM_WEB_ORIGIN"]["sync"] is False


def test_databases_and_key_value_are_private() -> None:
    data = _blueprint()
    for database in data["databases"]:
        assert database["ipAllowList"] == [], database["name"]
        assert int(database["postgresMajorVersion"]) >= 16, database["name"]
    assert _services()["anum-kv"]["ipAllowList"] == []


def test_web_bundle_settings_come_from_the_owner() -> None:
    web = _env(_services()["anum-web"])
    # Build arguments of apps/web/Dockerfile and the runtime CSP origins.
    dockerfile = (REPO_ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    for key in ("VITE_ANUM_API_URL", "VITE_ANUM_OIDC_ISSUER", "VITE_ANUM_OIDC_CLIENT_ID"):
        assert f"ARG {key}" in dockerfile
        assert _provided(web[key]), key
    assert web["VITE_ANUM_OIDC_ISSUER"]["sync"] is False
    assert web["ANUM_CSP_CONNECT_SRC"]["sync"] is False


@pytest.fixture
def blueprint_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings as the API builds them from the Blueprint plus owner-entered values."""
    import os

    for key in list(os.environ):
        if key.startswith("ANUM_") or key in KEYCLOAK_ADMIN_VARS:
            monkeypatch.delenv(key, raising=False)
    env = _env(_services()["anum-api"])
    for key, entry in env.items():
        if "value" in entry:
            monkeypatch.setenv(key, str(entry["value"]))
        elif key == "ANUM_SECRETS_KEY":
            monkeypatch.setenv(key, Fernet.generate_key().decode())
        else:
            monkeypatch.setenv(key, OWNER_VALUES[key])
    return Settings(_env_file=None)


def test_settings_validation_accepts_the_blueprint(blueprint_settings: Settings) -> None:
    settings = blueprint_settings
    validate_auth_configuration(settings)
    assert insecure_configuration_problems(settings) == []
    assert settings.auth_mode == "oidc"
    assert settings.repository_backend == "postgresql"
    assert settings.rate_limit_enabled is True


def test_blueprint_values_respect_the_chart_refusals(blueprint_settings: Settings) -> None:
    """The refusals infra/helm/ci/lint.sh checks for the chart, applied to render.yaml."""
    settings = blueprint_settings
    services = _services()
    assert settings.environment not in LOCAL_ENVIRONMENTS
    assert urlsplit(settings.keycloak_issuer).scheme == "https"
    assert settings.cors_origins and all(urlsplit(origin).scheme == "https" for origin in settings.cors_origins)
    if settings.environment == "production":
        assert settings.model_provider != "mock"
        assert settings.object_storage_backend == "s3"
    if settings.object_storage_backend != "s3":
        # Local files only on one instance with a persistent disk, never in production.
        api = services["anum-api"]
        assert api.get("numInstances", 1) == 1 and "disk" in api
    assert settings.runtime_backend == "inline" or any(
        service["type"] == "worker" for service in services.values()
    )
    # The literal (non-owner) values never name a local or plain-http endpoint.
    for key, entry in _env(services["anum-api"]).items():
        value = str(entry.get("value", ""))
        if value.startswith("http"):
            assert value.startswith("https://"), key


def _realm_module():
    spec = importlib.util.spec_from_file_location("keycloak_realm", REALM_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_realm_has_no_seeded_users_or_dev_redirects() -> None:
    source = json.loads(REALM.read_text(encoding="utf-8"))
    realm = _realm_module().shared_realm(source)
    assert "users" in source and "users" not in realm
    clients = {client["clientId"]: client for client in realm["clients"]}
    web = clients["anum-web"]
    assert web["redirectUris"] == ["${ANUM_WEB_ORIGIN}/*"]
    assert web["webOrigins"] == ["${ANUM_WEB_ORIGIN}"]
    assert web["attributes"]["post.logout.redirect.uris"] == "${ANUM_WEB_ORIGIN}/*"
    desktop = clients["anum-desktop"]
    serialized = json.dumps(desktop)
    assert "localhost:5173" not in serialized and "127.0.0.1:5173" not in serialized
    assert "http://127.0.0.1/*" in desktop["redirectUris"]
    assert "tauri://localhost/*" in desktop["redirectUris"]
    # Everything else (roles, mappers, other clients, PKCE) is unchanged.
    source_clients = {client["clientId"]: client for client in source["clients"]}
    for client_id in ("anum-api", "anum-android", "anum-flutter"):
        assert clients[client_id] == source_clients[client_id]
    assert web["protocolMappers"] == source_clients["anum-web"]["protocolMappers"]
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert realm["roles"] == source["roles"]
    assert "anum-dev-only-password" not in json.dumps(realm)


def test_realm_script_writes_the_import_file(tmp_path: Path) -> None:
    target = tmp_path / "anum-realm.json"
    assert _realm_module().main([str(REALM), str(target)]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["realm"] == "anum"
    assert "users" not in written
