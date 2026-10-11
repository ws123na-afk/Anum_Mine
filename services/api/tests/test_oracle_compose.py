"""The single-VM deployment (infra/oracle) keeps ANUM's secret handling and startup policy.

infra/oracle/compose.yaml is the one-VM counterpart of render.yaml and the Helm chart
(docs/deploy-oracle.md). Like test_render_blueprint.py does for Render, these tests fail
when the compose file or its .env.example would put a credential in git, publish a port
other than Caddy's, give the API the schema-owner login, trust forwarded headers from
anyone but Caddy, or carry a value the API refuses at startup. No network, no Docker.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from cryptography.fernet import Fernet

from anum_api.hardening import DEFAULT_DEV_SECRETS, insecure_configuration_problems
from anum_api.identity import LOCAL_ENVIRONMENTS, validate_auth_configuration
from anum_api.model_egress import EgressPolicy, check_endpoint
from anum_api.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
ORACLE = REPO_ROOT / "infra" / "oracle"
COMPOSE = ORACLE / "compose.yaml"
ENV_EXAMPLE = ORACLE / ".env.example"
CADDYFILE = ORACLE / "Caddyfile"
CONFIGURE_ENV = ORACLE / "configure_env.py"

SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|CREDENTIAL|PASSPHRASE|_KEY$|_KEYS$|DATABASE_URL|_ADMIN_URL$)")
REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?:(:?[-?])([^}]*))?\}")
GENERATE = "generate-on-server"
OWNER_PLACEHOLDER = "replace-me"
KEYCLOAK_ADMIN_VARS = {
    "KEYCLOAK_ADMIN",
    "KEYCLOAK_ADMIN_PASSWORD",
    "KC_BOOTSTRAP_ADMIN_USERNAME",
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
}
LONG_RUNNING = {"caddy", "postgres", "api", "web", "keycloak", "valkey", "ollama"}
ONE_SHOT = {"db-init", "migrate", "retention", "backup", "restore-tools", "backup-upload", "ollama-pull"}
API_REQUIRED = {
    "ANUM_ENVIRONMENT",
    "ANUM_AUTH_MODE",
    "ANUM_KEYCLOAK_ISSUER",
    "ANUM_OIDC_AUDIENCE",
    "ANUM_CORS_ORIGINS",
    "ANUM_REPOSITORY_BACKEND",
    "ANUM_DATABASE_URL",
    "ANUM_SECRETS_KEY",
    "ANUM_OBJECT_STORAGE_BACKEND",
    "ANUM_MODEL_PROVIDER",
    "ANUM_RATE_LIMIT_ENABLED",
    "FORWARDED_ALLOW_IPS",
}
S3_REQUIRED = {"ANUM_S3_ENDPOINT", "ANUM_S3_REGION", "ANUM_S3_BUCKET", "ANUM_S3_ACCESS_KEY", "ANUM_S3_SECRET_KEY"}
RETENTION_REQUIRED = {"ANUM_ENVIRONMENT", "ANUM_REPOSITORY_BACKEND", "ANUM_DATABASE_URL", "ANUM_SECRETS_KEY"}
# The Ollama settings docs/deploy-oracle.md#ai-model and .env.example give for the ai profile.
OLLAMA_VALUES = {
    "ANUM_MODEL_PROVIDER": "ollama",
    "ANUM_MODEL_NAME": "qwen2.5:3b",
    "ANUM_MODEL_BASE_URL": "http://ollama:11434/v1",
    "ANUM_MODEL_ALLOWED_HOSTS": "ollama:11434",
    "ANUM_MODEL_TIMEOUT_SECONDS": "300",
}


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _services() -> dict[str, dict[str, Any]]:
    return _compose()["services"]


def _env(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment", {}) or {}
    if isinstance(environment, list):
        return dict(item.split("=", 1) for item in environment)
    return {key: "" if value is None else str(value) for key, value in environment.items()}


def _configure_env_module() -> types.ModuleType:
    # Executed from source, so no __pycache__ is written next to the server files.
    module = types.ModuleType("oracle_configure_env")
    module.__file__ = str(CONFIGURE_ENV)
    sys.modules[module.__name__] = module
    exec(compile(CONFIGURE_ENV.read_text(encoding="utf-8"), str(CONFIGURE_ENV), "exec"), module.__dict__)  # noqa: S102  # nosec B102
    return module


def _example() -> dict[str, str]:
    return _configure_env_module().read_entries(ENV_EXAMPLE)[1]


def _interpolate(value: str, variables: dict[str, str]) -> str:
    """Compose's ${VAR}, ${VAR:-default}, ${VAR-default} and ${VAR:?message}; $$ is $."""

    def replace(match: re.Match[str]) -> str:
        name, operator, argument = match.group(1), match.group(2), match.group(3)
        current = variables.get(name)
        if operator in (":-", ":?") and not current:
            if operator == ":?":
                raise AssertionError(f"{name} is required: {argument}")
            return argument or ""
        if operator == "-" and current is None:
            return argument or ""
        if current is None:
            raise AssertionError(f"{name} is not defined in .env.example")
        return current

    placeholder = "\0dollar\0"
    return REFERENCE.sub(replace, value.replace("$$", placeholder)).replace(placeholder, "$")


def _references(text: str) -> set[str]:
    return {match.group(1) for match in REFERENCE.finditer(text.replace("$$", ""))}


def _services_referencing(variable: str) -> set[str]:
    names = set()
    for name, service in _services().items():
        if variable in _references(yaml.safe_dump(service)):
            names.add(name)
    return names


def _masked(text: str) -> str:
    """References replaced by REF_<NAME>, so a URL with ${VAR:?message} parses."""
    return REFERENCE.sub(lambda match: f"REF_{match.group(1)}", text.replace("$$", ""))


def _all_env_entries() -> list[tuple[str, str, str]]:
    return [(name, key, value) for name, service in _services().items() for key, value in _env(service).items()]


# --------------------------------------------------------------------------- structure


def test_compose_has_the_expected_services_and_profiles() -> None:
    services = _services()
    assert set(services) == LONG_RUNNING | ONE_SHOT
    assert _compose()["name"] == "anum"
    profiles = {name: tuple(service.get("profiles", ())) for name, service in services.items()}
    for name in ("retention", "backup", "restore-tools", "backup-upload"):
        assert profiles[name] == ("jobs",), name
    for name in ("ollama", "ollama-pull"):
        assert profiles[name] == ("ai",), name
    for name in ("caddy", "postgres", "db-init", "migrate", "api", "web", "keycloak", "valkey"):
        assert profiles[name] == (), name


def test_only_caddy_publishes_ports_and_only_80_and_443() -> None:
    services = _services()
    published = {name for name, service in services.items() if service.get("ports")}
    assert published == {"caddy"}
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name, service in services.items():
        assert service.get("network_mode") is None, name
        assert not service.get("privileged"), name
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume), name


def test_database_and_valkey_stay_on_the_internal_network() -> None:
    compose = _compose()
    networks = compose["networks"]
    assert networks["backend"]["internal"] is True
    assert not networks["edge"].get("internal")
    services = compose["services"]
    for name in ("postgres", "valkey", "db-init", "migrate", "retention", "backup", "restore-tools"):
        assert set(services[name]["networks"]) == {"backend"}, name
    # Caddy, the internet-facing container, cannot reach the database or Valkey.
    assert set(services["caddy"]["networks"]) == {"edge"}
    assert set(services["web"]["networks"]) == {"edge"}
    assert "edge" not in services["ollama"]["networks"]


def test_restart_policies_health_checks_and_log_rotation() -> None:
    services = _services()
    for name in LONG_RUNNING:
        service = services[name]
        assert service["restart"] == "unless-stopped", name
        check = service.get("healthcheck")
        assert check and not check.get("disable") and check.get("test"), name
    for name in ONE_SHOT:
        # A one-shot with a restart policy would loop.
        assert services[name]["restart"] == "no", name
    for name, service in services.items():
        logging = service["logging"]
        assert logging["driver"] == "json-file", name
        assert logging["options"]["max-size"] and int(logging["options"]["max-file"]) >= 1, name
    for name in ("caddy", "postgres", "api", "web", "keycloak", "valkey", "ollama"):
        assert services[name].get("mem_limit"), name


def test_start_order_waits_for_roles_and_migrations() -> None:
    services = _services()
    assert services["db-init"]["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert services["migrate"]["depends_on"] == {"db-init": {"condition": "service_completed_successfully"}}
    api = services["api"]["depends_on"]
    assert api["migrate"] == {"condition": "service_completed_successfully"}
    assert api["postgres"] == {"condition": "service_healthy"}
    assert api["valkey"] == {"condition": "service_healthy"}
    assert services["keycloak"]["depends_on"] == {"db-init": {"condition": "service_completed_successfully"}}
    assert services["migrate"]["command"] == ["python", "-m", "alembic", "upgrade", "head"]
    assert services["retention"]["command"] == ["python", "-m", "anum_api.voice_retention"]


def test_builds_use_the_repository_dockerfiles_and_images_are_pinned() -> None:
    services = _services()
    for name, service in services.items():
        if "build" in service:
            build = service["build"]
            context = (ORACLE / build["context"]).resolve()
            assert context.is_dir(), name
            dockerfile = context / build.get("dockerfile", "Dockerfile")
            assert dockerfile.is_file(), name
            assert re.fullmatch(r"anum/[a-z]+:local", service["image"]), name
        else:
            image = service["image"]
            assert ":" in image and not image.endswith(":latest"), name
    assert (ORACLE / services["api"]["build"]["context"]).resolve() == REPO_ROOT / "services" / "api"
    assert services["web"]["build"]["dockerfile"] == "apps/web/Dockerfile"
    assert services["keycloak"]["build"]["dockerfile"] == "infra/render/keycloak.Dockerfile"
    assert (ORACLE / services["backup"]["build"]["context"]).resolve() == REPO_ROOT / "infra" / "backup"
    # PostgreSQL 17 with pgvector; pg_dump in the backup image must be as new as the server.
    assert re.search(r"pg17", services["postgres"]["image"])
    assert "postgres:17" in (REPO_ROOT / "infra" / "backup" / "Dockerfile").read_text(encoding="utf-8")


def test_web_bundle_settings_are_build_arguments_of_the_web_dockerfile() -> None:
    web = _services()["web"]
    dockerfile = (REPO_ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    for key in web["build"]["args"]:
        assert f"ARG {key}" in dockerfile, key
    args = {key: _interpolate(value, _example()) for key, value in web["build"]["args"].items()}
    assert args["VITE_ANUM_API_URL"].startswith("https://")
    assert args["VITE_ANUM_OIDC_ISSUER"].startswith("https://") and args["VITE_ANUM_OIDC_ISSUER"].endswith("/realms/anum")
    csp = _interpolate(_env(web)["ANUM_CSP_CONNECT_SRC"], _example()).split()
    assert csp == [args["VITE_ANUM_API_URL"], args["VITE_ANUM_OIDC_ISSUER"].removesuffix("/realms/anum")]


# --------------------------------------------------------------------------- secrets


def test_no_literal_secret_values_in_compose() -> None:
    problems = []
    for owner, key, value in _all_env_entries():
        text = value.strip()
        if SECRET_NAME.search(key) and text in DEFAULT_DEV_SECRETS:
            problems.append(f"{owner}: {key} is a compose development credential")
        if "://" in text:
            parts = urlsplit(_masked(text))
            if parts.password and not parts.password.startswith("REF_"):
                problems.append(f"{owner}: {key} embeds a literal password in a URL")
            if (parts.username, parts.password) == ("anum", "anum"):
                problems.append(f"{owner}: {key} uses the compose database credentials")
        elif SECRET_NAME.search(key) and text and not REFERENCE.fullmatch(text):
            problems.append(f"{owner}: {key} has a literal value")
    raw = COMPOSE.read_text(encoding="utf-8")
    assert GENERATE not in raw and OWNER_PLACEHOLDER not in raw
    assert problems == []


def test_env_example_holds_placeholders_only() -> None:
    example = _example()
    for key, value in example.items():
        assert value not in DEFAULT_DEV_SECRETS, key
        if SECRET_NAME.search(key):
            assert value in {GENERATE, OWNER_PLACEHOLDER, ""}, key
        if "://" in value:
            assert urlsplit(value).password is None, key
    # Every credential install.sh must generate is marked, and nothing an owner must
    # provide is generated.
    generated = {key for key, value in example.items() if value == GENERATE}
    assert generated == {
        "ANUM_POSTGRES_PASSWORD",
        "ANUM_MIGRATOR_PASSWORD",
        "ANUM_APP_PASSWORD",
        "ANUM_RELAY_PASSWORD",
        "ANUM_BACKUP_DB_PASSWORD",
        "KEYCLOAK_DB_PASSWORD",
        "ANUM_SECRETS_KEY",
        "KC_BOOTSTRAP_ADMIN_PASSWORD",
        "ANUM_BACKUP_ENCRYPTION_PASSPHRASE",
    }
    owner = {key for key, value in example.items() if OWNER_PLACEHOLDER in value}
    assert {"ANUM_WEB_HOST", "ANUM_API_HOST", "ANUM_AUTH_HOST", "ACME_EMAIL"} <= owner
    assert S3_REQUIRED <= owner


def test_every_compose_variable_is_in_the_example_and_used() -> None:
    raw = COMPOSE.read_text(encoding="utf-8")
    example = _example()
    # From the parsed file, so references quoted in comments do not count.
    data = yaml.safe_dump(_compose()).replace("$$", "")
    required = {match.group(1) for match in REFERENCE.finditer(data) if match.group(2) in (None, ":?")}
    assert sorted(required - set(example)) == []
    scripts = "\n".join(path.read_text(encoding="utf-8") for path in ORACLE.glob("*.sh"))
    unused = [key for key in example if key not in raw and key not in scripts and key != "COMPOSE_PROFILES"]
    assert unused == []


def test_gitignore_keeps_the_real_env_file_out_of_git() -> None:
    lines = {line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ".env" in lines or "infra/oracle/.env" in lines
    assert "!infra/oracle/.env" not in lines


def test_migration_and_admin_logins_reach_only_their_one_shots() -> None:
    assert _services_referencing("ANUM_MIGRATOR_PASSWORD") == {"db-init", "migrate"}
    assert _services_referencing("ANUM_POSTGRES_PASSWORD") == {"postgres", "db-init", "restore-tools"}
    assert _services_referencing("ANUM_BACKUP_DB_PASSWORD") == {"db-init", "backup", "restore-tools"}
    assert _services_referencing("KEYCLOAK_DB_PASSWORD") == {"db-init", "keycloak"}
    assert _services_referencing("ANUM_BACKUP_ENCRYPTION_PASSPHRASE") == {"backup-upload"}
    services = _services()
    migrate_url = urlsplit(_masked(_env(services["migrate"])["ANUM_DATABASE_URL"]))
    assert migrate_url.username == "anum_migrator"
    for name in ("api", "retention"):
        url = urlsplit(_masked(_env(services[name])["ANUM_DATABASE_URL"]))
        assert url.username == "anum_app", name
        assert url.password == "REF_ANUM_APP_PASSWORD", name
        assert "ANUM_MIGRATION_DATABASE_URL" not in _env(services[name]), name
    assert migrate_url.password == "REF_ANUM_MIGRATOR_PASSWORD"
    assert urlsplit(_masked(_env(services["backup"])["ANUM_BACKUP_DATABASE_URL"])).username == "anum_backup"


def test_keycloak_admin_and_database_stay_out_of_anum_services() -> None:
    services = _services()
    for name, service in services.items():
        if name != "keycloak":
            assert KEYCLOAK_ADMIN_VARS.isdisjoint(_env(service)), name
    keycloak = _env(services["keycloak"])
    # Its own login, not one of ANUM's roles.
    assert keycloak["KC_DB_USERNAME"] == "keycloak"
    assert keycloak["KC_DB_URL_DATABASE"] == "keycloak"
    assert not any(key.startswith("ANUM_") and key != "ANUM_WEB_ORIGIN" for key in keycloak)
    for key in ("KC_BOOTSTRAP_ADMIN_USERNAME", "KC_BOOTSTRAP_ADMIN_PASSWORD"):
        # Empty after day one without a compose warning.
        assert REFERENCE.fullmatch(keycloak[key]) and ":-" in keycloak[key], key
    assert _example()["KC_BOOTSTRAP_ADMIN_USERNAME"] not in {"admin", "anum"}


# --------------------------------------------------------------------------- startup policy


def _api_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    variables = dict(_example())
    variables["ANUM_SECRETS_KEY"] = Fernet.generate_key().decode()
    variables.update(overrides or {})
    return {key: _interpolate(value, variables) for key, value in _env(_services()["api"]).items()}


def _settings(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> Settings:
    import os

    for key in list(os.environ):
        if key.startswith("ANUM_") or key in KEYCLOAK_ADMIN_VARS:
            monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_api_gets_every_setting_it_needs_outside_local() -> None:
    api = _api_environment()
    assert sorted(key for key in API_REQUIRED if not api.get(key)) == []
    assert api["ANUM_ENVIRONMENT"] not in LOCAL_ENVIRONMENTS
    assert api["ANUM_OBJECT_STORAGE_BACKEND"] == "s3"
    assert sorted(key for key in S3_REQUIRED if not api.get(key)) == []
    assert api["ANUM_RUN_LOCK_BACKEND"] == api["ANUM_RATE_LIMIT_BACKEND"] == "valkey"
    assert urlsplit(api["ANUM_VALKEY_URL"]).hostname == "valkey"
    # Keys are fetched from Keycloak on the private network; tokens still carry the
    # public https issuer.
    assert api["ANUM_OIDC_JWKS_URL"] == "http://keycloak:8080/realms/anum/protocol/openid-connect/certs"


def test_startup_policy_accepts_the_example(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, _api_environment())
    validate_auth_configuration(settings)
    assert insecure_configuration_problems(settings) == []
    assert settings.environment == "staging"
    assert settings.auth_mode == "oidc"
    assert settings.repository_backend == "postgresql"
    assert settings.rate_limit_enabled is True
    assert urlsplit(settings.keycloak_issuer).scheme == "https"
    assert settings.cors_origins and all(urlsplit(origin).scheme == "https" for origin in settings.cors_origins)
    assert settings.runtime_backend == "inline" and settings.event_bus == "memory"


def test_production_with_the_ollama_profile_passes_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, _api_environment({"ANUM_ENVIRONMENT": "production", **OLLAMA_VALUES}))
    validate_auth_configuration(settings)
    assert insecure_configuration_problems(settings) == []
    assert settings.model_provider == "ollama"
    assert settings.object_storage_backend == "s3"
    # A workspace may point at the private Ollama host only because the operator listed it.
    policy = EgressPolicy.from_settings(settings)
    assert policy.enforce
    endpoint = check_endpoint(settings.model_base_url, policy)
    assert (endpoint.host, endpoint.port) == ("ollama", 11434)
    assert _interpolate(_env(_services()["ollama-pull"])["OLLAMA_MODEL"], {}) == OLLAMA_VALUES["ANUM_MODEL_NAME"]


def test_example_refuses_compose_development_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check the policy test relies on is live: compose's anum:anum would be refused."""
    environment = _api_environment({"ANUM_APP_PASSWORD": "anum"})
    environment["ANUM_DATABASE_URL"] = environment["ANUM_DATABASE_URL"].replace("anum_app:", "anum:")
    problems = insecure_configuration_problems(_settings(monkeypatch, environment))
    assert any("ANUM_DATABASE_URL" in problem for problem in problems)


def test_retention_environment_alone_is_enough_for_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    variables = dict(_example())
    variables["ANUM_SECRETS_KEY"] = Fernet.generate_key().decode()
    environment = {key: _interpolate(value, variables) for key, value in _env(_services()["retention"]).items()}
    assert set(environment) == RETENTION_REQUIRED
    settings = _settings(monkeypatch, environment)
    assert settings.environment == _api_environment()["ANUM_ENVIRONMENT"]
    assert settings.repository_backend == "postgresql"


# --------------------------------------------------------------------------- proxy


def test_only_caddy_may_set_forwarded_headers() -> None:
    compose = _compose()
    services = compose["services"]
    caddy_ip = services["caddy"]["networks"]["edge"]["ipv4_address"]
    subnet = ipaddress.ip_network(compose["networks"]["edge"]["ipam"]["config"][0]["subnet"])
    assert ipaddress.ip_address(caddy_ip) in subnet
    assert _env(services["api"])["FORWARDED_ALLOW_IPS"] == caddy_ip
    assert _env(services["keycloak"])["KC_PROXY_TRUSTED_ADDRESSES"] == f"{caddy_ip}/32"
    dockerfile = (REPO_ROOT / "infra" / "render" / "keycloak.Dockerfile").read_text(encoding="utf-8")
    assert "KC_PROXY_HEADERS=xforwarded" in dockerfile
    assert "--proxy-headers" in (REPO_ROOT / "services" / "api" / "Dockerfile").read_text(encoding="utf-8")
    # The fixed subnets do not overlap.
    subnets = [ipaddress.ip_network(net["ipam"]["config"][0]["subnet"]) for net in compose["networks"].values()]
    assert all(not a.overlaps(b) for i, a in enumerate(subnets) for b in subnets[i + 1 :])


def test_caddy_replaces_client_forwarded_headers_and_serves_the_three_hosts() -> None:
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in caddyfile.splitlines() if not line.lstrip().startswith("#"))
    assert "trusted_proxies" not in code
    assert "header_up X-Forwarded-For {remote_host}" in code
    for host, upstream in (
        ("{$ANUM_WEB_HOST}", "web:8080"),
        ("{$ANUM_API_HOST}", "api:8000"),
        ("{$ANUM_AUTH_HOST}", "keycloak:8080"),
    ):
        block = code[code.index(f"\n{host} {{") :]
        block = block[: block.index("\n}\n") if "\n}\n" in block else len(block)]
        assert f"reverse_proxy {upstream} {{\n\t\timport upstream_headers\n" in block, host
    assert "protocols h1 h2" in code
    assert "email {$ACME_EMAIL}" in code
    assert "not remote_ip {$ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS}" in code
    caddy_env = _env(_services()["caddy"])
    for name in ("ACME_EMAIL", "ANUM_WEB_HOST", "ANUM_API_HOST", "ANUM_AUTH_HOST", "ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS"):
        assert name in caddy_env, name


# --------------------------------------------------------------------------- database roles


def test_db_init_uses_the_shared_bootstrap_and_keeps_rls() -> None:
    db_init = _services()["db-init"]
    volumes = db_init["volumes"]
    assert "../helm/bootstrap-database.sql:/init/bootstrap-database.sql:ro" in volumes
    for volume in volumes:
        source = volume.split(":", 1)[0]
        assert (ORACLE / source).resolve().exists(), source
    script = (ORACLE / "db-init.sh").read_text(encoding="utf-8")
    # Passwords go to psql on stdin, never as -v arguments on a command line.
    assert "\\\\set migrator_password" in script
    assert "-v migrator_password" not in script and "-v app_password" not in script
    sql = "\n".join(
        line
        for path in sorted((ORACLE / "db").glob("*.sql"))
        for line in path.read_text(encoding="utf-8").lower().splitlines()
        if not line.lstrip().startswith("--")
    )
    assert "security definer" not in sql
    assert "superuser" not in sql.replace("nosuperuser", "")
    # BYPASSRLS only for the backup login; everyone else is re-asserted NOBYPASSRLS.
    assert sql.replace("nobypassrls", "").count("bypassrls") == 2
    assert "role anum_backup login password %l nosuperuser bypassrls" in sql
    assert "grant pg_read_all_data to anum_backup" in sql
    assert "revoke connect on database anum from public" in sql
    assert "revoke connect, temporary on database keycloak from public" in sql


# --------------------------------------------------------------------------- configure_env.py


def test_configure_env_generates_secrets_privately_and_idempotently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _configure_env_module()
    target = tmp_path / ".env"
    assert module.configure(target, ENV_EXAMPLE, interactive=False) == 3  # owner values missing
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    _, values = module.read_entries(target)
    generated = {key: value for key, value in _example().items() if value == GENERATE}
    for key in generated:
        assert values[key] != GENERATE and len(values[key]) >= 40, key
    assert len({values[key] for key in generated}) == len(generated)
    Fernet(values["ANUM_SECRETS_KEY"].encode())
    assert len(values["ANUM_SECRETS_KEY"]) == 44
    output = capsys.readouterr()
    for key in generated:
        assert values[key] not in output.out + output.err
    assert "ANUM_WEB_HOST" in output.err

    # The owner fills in the hosts and keys; a second run keeps every value.
    text = target.read_text(encoding="utf-8").replace("replace-me.example", "example.org")
    text = re.sub(r"=(.*)replace-me(.*)", r"=\1filled\2", text)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o644)
    assert module.configure(target, ENV_EXAMPLE, interactive=False) == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    _, again = module.read_entries(target)
    for key in generated:
        assert again[key] == values[key], key

    # A variable added to .env.example later is appended; an emptied value stays empty.
    text = target.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^KC_BOOTSTRAP_ADMIN_PASSWORD=.*$", "KC_BOOTSTRAP_ADMIN_PASSWORD=", text)
    text = re.sub(r"(?m)^OLLAMA_MODEL=.*\n", "", text)
    target.write_text(text, encoding="utf-8")
    assert module.configure(target, ENV_EXAMPLE, interactive=False) == 0
    _, final = module.read_entries(target)
    assert final["KC_BOOTSTRAP_ADMIN_PASSWORD"] == ""
    assert final["OLLAMA_MODEL"] == _example()["OLLAMA_MODEL"]


def test_configure_env_quotes_values_compose_would_interpolate() -> None:
    module = _configure_env_module()
    assert module.format_value("abc-DEF_123=") == "abc-DEF_123="
    assert module.format_value("0.0.0.0/0 ::/0") == "'0.0.0.0/0 ::/0'"
    assert module.format_value("a$b") == "'a$b'"
    with pytest.raises(ValueError):
        module.format_value("it's")
    assert module.validate("ANUM_WEB_HOST", "https://app.example.org") is not None
    assert module.validate("ANUM_WEB_HOST", "app.example.org") is None
    assert module.validate("ANUM_S3_ENDPOINT", "http://s3.example.org") is not None


# --------------------------------------------------------------------------- scripts


def test_scripts_are_strict_and_units_point_at_existing_files() -> None:
    for path in ORACLE.glob("*.sh"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("#!/"), path.name
        assert "set -euo pipefail" in text or "set -eu" in text, path.name
    install = (ORACLE / "install.sh").read_text(encoding="utf-8")
    for script in re.findall(r"\$ORACLE_DIR/([a-z_]+\.(?:sh|py))", install):
        assert (ORACLE / script).is_file(), script
    for timer in ("anum-retention.timer", "anum-backup.timer", "anum-update.timer", "anum-duckdns.timer"):
        assert f"write_unit {timer}" in install, timer
    assert "run --rm retention" in install
    # The firewall step edits Oracle's rules file instead of saving live rules (which
    # would store Docker's chains) and never enables ufw.
    assert "netfilter-persistent save" not in install.replace("never run `netfilter-persistent save`", "")
    assert "ufw enable" not in install
    update = (ORACLE / "update.sh").read_text(encoding="utf-8")
    assert update.index("run --rm --no-deps migrate") < update.index("up -d --remove-orphans")


def test_install_does_not_ask_for_a_password_with_nopasswd_sudo() -> None:
    # Oracle's ubuntu user has no password: a bare `sudo -v` prompts even under NOPASSWD.
    install = (ORACLE / "install.sh").read_text(encoding="utf-8")
    assert "sudo -n true 2>/dev/null || sudo -v" in install
    assert not [line for line in install.splitlines() if line.strip() == "sudo -v"]


def test_keycloak_starts_after_the_bootstrap_admin_is_emptied() -> None:
    # Emptied bootstrap values reach Keycloak as set-but-empty variables, which it
    # refuses; the entrypoint unsets them, and still runs the image's start command.
    service = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["keycloak"]
    entrypoint = service["entrypoint"]
    assert entrypoint[:2] == ["bash", "-c"]
    assert "unset KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD" in entrypoint[2]
    assert 'exec /opt/keycloak/bin/kc.sh "$$@"' in entrypoint[2]
    assert service["command"] == ["start", "--optimized", "--import-realm"]


def test_install_runs_owner_commands_when_started_with_sudo() -> None:
    # `sudo bash install.sh` leaves SUDO empty; run_as_owner must not run a bare `-u`.
    install = (ORACLE / "install.sh").read_text(encoding="utf-8")
    start = install.index("run_as_owner() {")
    function = install[start : install.index("\n}\n", start) + 3]
    assert 'elif [ -z "$SUDO" ]; then runuser -u "$OWNER" -- "$@"' in function
    if os.geteuid() != 0 or shutil.which("runuser") is None:
        return
    script = f'SUDO=""; OWNER=nobody\n{function}run_as_owner id -un\n'
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "nobody"
