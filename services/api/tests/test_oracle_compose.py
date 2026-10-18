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
LONG_RUNNING = {"caddy", "postgres", "api", "web", "keycloak", "valkey", "ollama", "searxng", "clamd", "beszel", "beszel-agent", "glitchtip", "coturn"}
MONITORING = {"beszel", "beszel-agent", "glitchtip"}
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
# The WhatsApp history default (docs/whatsapp.md#retention) the retention job applies.
RETENTION_ENVIRONMENT = RETENTION_REQUIRED | {"ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS"}
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
    # Optional monitoring; the agent separately, as it needs the hub's key first.
    assert profiles["beszel"] == profiles["glitchtip"] == ("monitoring",)
    assert profiles["beszel-agent"] == ("monitoring-agent",)
    # The TURN relay for in-app calls is off unless calls.sh enable adds its profile.
    assert profiles["coturn"] == ("calls",)
    for name in ("caddy", "postgres", "db-init", "migrate", "api", "web", "keycloak", "valkey"):
        assert profiles[name] == (), name


def test_only_caddy_publishes_ports_and_only_80_and_443() -> None:
    services = _services()
    published = {name for name, service in services.items() if service.get("ports")}
    assert published == {"caddy"}
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name, service in services.items():
        # coturn alone uses the host network (a relay needs the VM's UDP ports) and
        # publishes nothing; calls.sh opens exactly its ports.
        assert service.get("network_mode") == ("host" if name == "coturn" else None), name
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
    assert set(services["caddy"]["networks"]) == {"edge", "sites"}
    assert set(services["web"]["networks"]) == {"edge"}
    assert "edge" not in services["ollama"]["networks"]


def _members(network: str) -> set[str]:
    return {name for name, service in _services().items() if network in (service.get("networks") or {})}


def test_other_sites_share_only_caddy_on_their_own_network() -> None:
    compose = _compose()
    sites = compose["networks"]["sites"]
    # A fixed name other compose projects join as `external: true`; created here, so it
    # exists after update.sh. Not internal: those stacks need outbound access.
    assert sites["name"] == "anum_sites"
    assert not sites.get("internal") and not sites.get("external")
    assert ipaddress.ip_network(sites["ipam"]["config"][0]["subnet"]) == ipaddress.ip_network("172.30.50.0/24")
    # Caddy is the only ANUM service on it, with no fixed address there; its edge address
    # (the one the API and Keycloak trust) is unchanged.
    assert _members("sites") == {"caddy"}
    caddy = compose["services"]["caddy"]["networks"]
    assert not (caddy["sites"] or {}).get("ipv4_address")
    assert caddy["edge"]["ipv4_address"] == "172.30.10.10"
    # ANUM's own networks keep exactly their members.
    assert _members("edge") == {"caddy", "web", "api", "keycloak", "beszel", "glitchtip"}
    backend = {"postgres", "valkey", "db-init", "migrate", "api", "keycloak", "retention", "backup"}
    backend |= {"restore-tools", "searxng", "clamd", "ollama", "ollama-pull", "glitchtip"}
    assert _members("backend") == backend
    # Every Caddyfile upstream is reached over `edge`, never over `sites`, so the peer
    # address the API and Keycloak see stays Caddy's edge address.
    for upstream in ("web", "api", "keycloak", "beszel", "glitchtip"):
        assert "sites" not in compose["services"][upstream]["networks"], upstream


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
    for name in LONG_RUNNING:
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
        "SEARXNG_SECRET",
        "ANUM_TURN_SECRET",
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
    assert set(environment) == RETENTION_ENVIRONMENT
    # The job and the API use the same deployment default.
    assert environment["ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS"] == _api_environment()["ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS"]
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
    caddy_env = _env(_services()["caddy"])
    for name in ("ACME_EMAIL", "ANUM_WEB_HOST", "ANUM_API_HOST", "ANUM_AUTH_HOST", "ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS"):
        assert name in caddy_env, name


def _auth_site() -> str:
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in caddyfile.splitlines() if not line.lstrip().startswith("#"))
    block = code[code.index("\n{$ANUM_AUTH_HOST} {") :]
    return block[: block.index("\n}\n") if "\n}\n" in block else len(block)]


def test_keycloak_admin_and_master_realm_follow_the_allow_list() -> None:
    block = _auth_site()
    matcher = block[block.index("@admin_denied {") :]
    matcher = matcher[: matcher.index("\n\t}\n")]
    # The console, the admin REST API and the master realm (where admins sign in) are
    # refused unless the TCP peer is on the list and is not a Docker address; the anum
    # realm, which every ANUM user signs in through, is not in the matcher.
    assert "path /admin /admin/* /realms/master /realms/master/*" in matcher
    assert "/realms/anum" not in matcher
    allowed = (
        "not {\n"
        "\t\t\tremote_ip {$ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS}\n"
        "\t\t\tnot remote_ip 172.30.0.0/16\n"
        "\t\t}"
    )
    assert allowed in matcher
    assert "client_ip" not in matcher.replace("@client_ip", "")  # the peer, never a forwarded header
    assert block.index("respond @admin_denied") < block.index("reverse_proxy keycloak:8080")
    # Every compose network is inside the range that is never trusted.
    docker = ipaddress.ip_network("172.30.0.0/16")
    for network in _compose()["networks"].values():
        assert ipaddress.ip_network(network["ipam"]["config"][0]["subnet"]).subnet_of(docker)
    # The address Caddy sees, for the allow-list and smoke.sh.
    assert 'respond @client_ip "{remote_host}" 200' in block


def test_caddy_entrypoint_turns_the_comma_list_into_caddy_ranges() -> None:
    caddy = _services()["caddy"]
    assert _env(caddy)["ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS"] == "${ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS:-}"
    assert caddy["command"] == ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
    script = caddy["entrypoint"][2].replace("$$", "$")
    if shutil.which("sh") is None:
        return
    for value, expected in (
        ("", "0.0.0.0/32 ::/128"),
        (" , ", "0.0.0.0/32 ::/128"),
        ("203.0.113.7/32,198.51.100.0/24", "203.0.113.7/32 198.51.100.0/24"),
        ("203.0.113.7, 2001:db8::/32", "203.0.113.7 2001:db8::/32"),
    ):
        result = subprocess.run(  # noqa: S603
            ["sh", "-c", script, "caddy-entrypoint", "sh", "-c", 'printf %s "$ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS"'],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS": value},
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == expected, value


def test_keycloak_admin_console_moves_to_the_tunnel_without_an_allow_list() -> None:
    services = _services()
    keycloak = services["keycloak"]
    edge = keycloak["networks"]["edge"]["ipv4_address"]
    subnet = ipaddress.ip_network(_compose()["networks"]["edge"]["ipam"]["config"][0]["subnet"])
    assert ipaddress.ip_address(edge) in subnet
    assert edge != services["caddy"]["networks"]["edge"]["ipv4_address"]
    assert "ports" not in keycloak
    script = keycloak["entrypoint"][2]
    assert _env(keycloak)["ADMIN_CONSOLE_ALLOWED_IPS"] == "${ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS:-}"
    assert "export KC_HOSTNAME_ADMIN=http://localhost:8080" in script
    assert "unset ADMIN_CONSOLE_ALLOWED_IPS" in script
    docs = (REPO_ROOT / "docs" / "deploy-oracle.md").read_text(encoding="utf-8")
    assert f"ssh -L 8080:{edge}:8080" in docs
    assert f"ssh -L 8080:{edge}:8080" in (ORACLE / "install.sh").read_text(encoding="utf-8")


def test_example_closes_the_admin_console_by_default() -> None:
    assert _example()["ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS"] == ""


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


HOSTS_OK = {
    "ANUM_WEB_HOST": "anum-app-owner.duckdns.org",
    "ANUM_API_HOST": "anum-api-owner.duckdns.org",
    "ANUM_AUTH_HOST": "anum-auth-owner.duckdns.org",
    "ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS": "",
}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"ANUM_API_HOST": "anum-app-owner.duckdns.org"}, "ANUM_WEB_HOST and ANUM_API_HOST are both"),
        ({"ANUM_AUTH_HOST": "ANUM-API-owner.duckdns.org"}, "capital letters"),
        ({"ANUM_AUTH_HOST": "anum-api-owner.duckdns.org"}, "ANUM_API_HOST and ANUM_AUTH_HOST are both"),
        ({"ANUM_WEB_HOST": ""}, "ANUM_WEB_HOST is empty"),
        ({"ANUM_AUTH_HOST": "https://anum-auth-owner.duckdns.org"}, "not a valid host name"),
        ({"ANUM_API_HOST": "anum-api-owner.duckdns.org:8443"}, "not a valid host name"),
        ({"ANUM_WEB_HOST": "app.replace-me.example"}, "still the placeholder"),
        ({"ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS": "203.0.113.7/32,home"}, "'home' is not an IP address"),
        ({"ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS": "203.0.113.7/33"}, "is not an IP address"),
    ],
)
def test_configure_env_check_refuses_bad_hosts_and_allow_lists(overrides: dict[str, str], fragment: str) -> None:
    module = _configure_env_module()
    problems, _ = module.check_deployment({**HOSTS_OK, **overrides}, strict=False)
    assert any(fragment in problem for problem in problems), problems


def test_configure_env_check_accepts_good_values_and_flags_a_world_open_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _configure_env_module()
    assert module.check_deployment(HOSTS_OK, strict=True) == ([], [])
    listed = {**HOSTS_OK, "ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS": "203.0.113.7/32, 198.51.100.0/24,2001:db8::/48"}
    assert module.check_deployment(listed, strict=True) == ([], [])
    # Older .env files used spaces; an open console stops install.sh, update.sh warns.
    world = {**HOSTS_OK, "ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS": "0.0.0.0/0 ::/0"}
    problems, warnings = module.check_deployment(world, strict=True)
    assert len(problems) == 1 and "whole internet" in problems[0] and not warnings
    problems, warnings = module.check_deployment(world, strict=False)
    assert not problems and len(warnings) == 1

    target = tmp_path / ".env"
    target.write_text("".join(f"{key}={value}\n" for key, value in HOSTS_OK.items()), encoding="utf-8")
    assert module.main([str(target), "--check", "--strict"]) == 0
    target.write_text(
        target.read_text(encoding="utf-8").replace("anum-auth-owner", "anum-api-owner"), encoding="utf-8"
    )
    assert module.main([str(target), "--check"]) == 4
    assert "ANUM_API_HOST and ANUM_AUTH_HOST are both" in capsys.readouterr().err
    assert module.main([str(tmp_path / "missing.env"), "--check"]) == 2


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
    # Migrations (alembic upgrade head) run as their own one-shot before the API is
    # recreated, and the API waits for them on every `up`.
    assert _services()["migrate"]["command"] == ["python", "-m", "alembic", "upgrade", "head"]
    assert _services()["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    # Host names and the admin allow-list are checked before anything is built.
    assert update.index('configure_env.py" "$ENV_FILE" --check') < update.index("build --pull")
    check = install.index('configure_env.py" "$ENV_FILE" --check --strict')
    assert check < install.index('update.sh" --no-pull')
    # install.sh ends with the smoke test, as the owner, after the timers.
    smoke = install.index('run_as_owner bash "$ORACLE_DIR/smoke.sh"')
    assert smoke > install.index("systemctl daemon-reload")


def test_smoke_test_needs_no_sudo_and_covers_the_edge() -> None:
    smoke = (ORACLE / "smoke.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in smoke.splitlines() if not line.lstrip().startswith("#"))
    # No sudo and no Docker access: it runs as the owner on the VM or from any machine.
    assert "sudo" not in code
    assert not re.search(r"^\s*(docker|dc) ", code, re.MULTILINE)
    for needle in (
        "/realms/anum/.well-known/openid-configuration",
        "/health",
        "/api/v1/tasks",
        "/admin/",
        "/realms/master/.well-known/openid-configuration",
        "/anum/client-ip",
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
    ):
        assert needle in code, needle
    # The probed path really needs a token: it is an authenticated API route.
    from anum_api.main import app

    assert "/api/v1/tasks" in app.openapi()["paths"]
    if shutil.which("bash") is None:
        return
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", str(ORACLE / "smoke.sh"), "app.example.org", "app.example.org", "auth.example.org"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert "three different names" in result.stdout


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


def test_searxng_is_private_and_only_the_api_uses_it() -> None:
    services = _services()
    searxng = services["searxng"]
    # Reachable from the API on the internal network, never through Caddy.
    assert set(searxng["networks"]) == {"backend", "egress"}
    assert not searxng.get("ports") and not searxng.get("expose")
    assert "searxng" not in CADDYFILE.read_text(encoding="utf-8").lower()
    assert searxng["mem_limit"]
    assert _env(searxng)["SEARXNG_SECRET"].startswith("${SEARXNG_SECRET:?")
    assert any(str(volume).endswith("/etc/searxng/settings.yml:ro") for volume in searxng["volumes"])
    assert _env(services["api"])["ANUM_SEARXNG_URL"] == "http://searxng:8080"
    assert _services_referencing("SEARXNG_SECRET") == {"searxng"}
    searxng_settings = yaml.safe_load((REPO_ROOT / "infra" / "searxng" / "settings.yml").read_text(encoding="utf-8"))
    assert "json" in searxng_settings["search"]["formats"]
    assert searxng_settings["server"]["limiter"] is False
    assert searxng_settings["server"]["public_instance"] is False


def test_clamd_scans_every_upload_privately_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    services = _services()
    clamd = services["clamd"]
    # Reachable from the API on the internal network only; egress for signature updates.
    assert set(clamd["networks"]) == {"backend", "egress"}
    assert not clamd.get("ports") and not clamd.get("expose")
    assert "clamd" not in CADDYFILE.read_text(encoding="utf-8").lower()
    assert clamd["image"].startswith("clamav/clamav:") and clamd["image"].endswith("-debian13-slim")
    assert any(str(volume).startswith("clamav-db:") for volume in clamd["volumes"])
    # A bounded share of the 24 GB VM, and clamd accepts a full-size upload.
    assert clamd["mem_limit"] == "3g"
    assert _env(clamd)["CLAMD_CONF_StreamMaxLength"] == "26M"
    settings = _settings(monkeypatch, _api_environment())
    assert settings.file_scanner == "clamav" and settings.file_scan_required is True
    assert (settings.clamav_host, settings.clamav_port) == ("clamd", 3310)
    assert 26 * 1024 * 1024 > settings.max_upload_body_bytes


# --------------------------------------------------------------------------- monitoring

MONITORING_CADDY = ORACLE / "monitoring.caddy"
MONITORING_SH = ORACLE / "monitoring.sh"


def test_monitoring_is_opt_in_and_reachable_only_through_caddy() -> None:
    compose = _compose()
    services = compose["services"]
    # Off unless monitoring.sh adds the profiles.
    assert "monitoring" not in _example()["COMPOSE_PROFILES"]
    for name in MONITORING:
        service = services[name]
        assert not service.get("ports") and not service.get("expose"), name
        assert service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume) and ":/proc" not in str(volume), name
    assert compose["networks"]["monitoring"]["internal"] is True
    assert set(services["beszel-agent"]["networks"]) == {"monitoring"}
    assert set(services["beszel"]["networks"]) == {"edge", "monitoring"}
    # GlitchTip reaches PostgreSQL; Caddy reaches GlitchTip and the hub on `edge`.
    assert set(services["glitchtip"]["networks"]) == {"edge", "backend"}
    assert _env(services["beszel-agent"])["DOCKER_HOST"] == ""
    assert _env(services["beszel"])["CONTAINER_DETAILS"] == "false"
    # Pinned images, both Beszel parts on the same release.
    hub, agent = services["beszel"]["image"], services["beszel-agent"]["image"]
    assert re.fullmatch(r"henrygd/beszel:\d+\.\d+\.\d+", hub)
    assert hub.split(":")[1] == agent.split(":")[1]
    assert re.fullmatch(r"glitchtip/glitchtip:\d+\.\d+\.\d+", services["glitchtip"]["image"])
    # The three stay within the RAM budget in docs/monitoring-oracle.md (1.4 GB).
    limits = {"256m": 256, "128m": 128, "1g": 1024}
    assert sum(limits[services[name]["mem_limit"]] for name in MONITORING) <= 1408


def test_glitchtip_has_its_own_database_login_and_no_shared_valkey() -> None:
    services = _services()
    glitchtip = _env(services["glitchtip"])
    assert glitchtip["DATABASE_USER"] == glitchtip["DATABASE_NAME"] == "glitchtip"
    assert glitchtip["DATABASE_PASSWORD"] == "${GLITCHTIP_DB_PASSWORD:-}"
    # The API's Valkey is noeviction and holds rate limits and run locks.
    assert glitchtip["VALKEY_URL"] == ""
    assert glitchtip["ENABLE_USER_REGISTRATION"] == "false"
    assert "glitchtip" in glitchtip["ALLOWED_HOSTS"].split(",")
    # Never GlitchTip's built-in default secret key.
    assert '"$${#SECRET_KEY}" -lt 40' in services["glitchtip"]["command"][2]
    assert _services_referencing("GLITCHTIP_DB_PASSWORD") == {"db-init", "glitchtip"}
    assert _services_referencing("GLITCHTIP_SECRET_KEY") == {"glitchtip"}
    assert _services_referencing("BESZEL_AGENT_TOKEN") == {"beszel-agent"}
    assert "max_locks_per_transaction=512" in services["postgres"]["command"]
    sql = (ORACLE / "db" / "glitchtip.sql").read_text(encoding="utf-8").lower()
    assert "create role glitchtip login password %l nosuperuser nobypassrls nocreatedb nocreaterole" in sql
    assert "revoke connect, temporary on database glitchtip from public" in sql
    script = (ORACLE / "db-init.sh").read_text(encoding="utf-8")
    assert "\\\\set glitchtip_password" in script and 'if [ -n "$glitchtip_password" ]' in script


def test_monitoring_sites_are_imported_only_when_enabled_and_need_a_login() -> None:
    caddy = _services()["caddy"]
    volumes = caddy["volumes"]
    assert "./monitoring.caddy:/etc/caddy/sites-available/monitoring.caddy:ro" in volumes
    assert "./sites-enabled:/etc/caddy/sites-enabled:ro" in volumes
    assert (ORACLE / "sites-enabled").is_dir()
    assert not list((ORACLE / "sites-enabled").glob("*.caddy"))
    assert "infra/oracle/sites-enabled/*.caddy" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "\nimport /etc/caddy/sites-enabled/*.caddy\n" in CADDYFILE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in MONITORING_CADDY.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    assert "{$ANUM_MONITORING_BASIC_AUTH_USER} {$ANUM_MONITORING_BASIC_AUTH_HASH}" in code
    for host, upstream in (("{$ANUM_MONITOR_HOST}", "beszel:8090"), ("{$ANUM_ERRORS_HOST}", "glitchtip:8000")):
        block = code[code.index(f"\n{host} {{") :]
        block = block[: block.index("\n}\n") + 3] if "\n}\n" in block else block
        assert "import common" in block and "import upstream_headers" in block, host
        assert f"reverse_proxy {upstream} {{" in block, host
    errors = code[code.index("\n{$ANUM_ERRORS_HOST} {") :]
    # Only the SDK ingest endpoints skip basic auth (the DSN key authenticates them).
    assert "@ingest path_regexp ingest ^/api/[0-9]+/(envelope|store|security|minidump)/$" in errors
    assert errors.index("handle @ingest") < errors.index("import monitoring_auth")
    monitor = code[code.index("\n{$ANUM_MONITOR_HOST} {") : code.index("\n{$ANUM_ERRORS_HOST} {")]
    assert "import monitoring_auth" in monitor and "handle" not in monitor
    caddy_env = _env(caddy)
    for name in ("ANUM_MONITOR_HOST", "ANUM_ERRORS_HOST", "ANUM_MONITORING_BASIC_AUTH_USER", "ANUM_MONITORING_BASIC_AUTH_HASH"):
        assert caddy_env[name] == f"${{{name}:-}}", name


def test_api_reports_errors_to_glitchtip_on_the_private_network(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _env(_services()["api"])["ANUM_SENTRY_DSN"] == "${ANUM_SENTRY_DSN:-}"
    assert _example()["ANUM_SENTRY_DSN"] == ""
    # What monitoring.sh dsn writes passes the startup policy in production.
    environment = _api_environment(
        {"ANUM_ENVIRONMENT": "production", **OLLAMA_VALUES, "ANUM_SENTRY_DSN": "http://examplekey@glitchtip:8000/1"}
    )
    settings = _settings(monkeypatch, environment)
    assert insecure_configuration_problems(settings) == []
    assert settings.sentry_dsn is not None
    public = _settings(monkeypatch, {**environment, "ANUM_SENTRY_DSN": "http://examplekey@errors.example.org/1"})
    assert any("ANUM_SENTRY_DSN" in problem for problem in insecure_configuration_problems(public))


def test_monitoring_script_asks_for_secrets_privately_and_avoids_sudo() -> None:
    script = MONITORING_SH.read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail" in script
    for command in ("enable)", "disable)", "status)", "agent)", "dsn)", "purge)"):
        assert command in script, command
    # Hidden prompts for every secret; values reach .env on stdin through configure_env.py
    # (mode 600, written as the .env owner), never on a command line.
    assert script.count("read -r -s -p") >= 4
    assert "write_private" in script and "as_env_owner" in script
    assert "sudo -n true 2>/dev/null || sudo -v" in script
    assert not [line for line in script.splitlines() if line.strip() == "sudo -v"]
    # GlitchTip's admin password goes to Django on stdin.
    assert "manage.py shell -c" in script and "DJANGO_SUPERUSER_PASSWORD" not in script
    # The new sites are validated before Caddy is recreated, and removed if refused.
    assert script.index("caddy_config_cmd validate") < script.index("dc up -d --no-deps caddy\n  caddy_config_cmd reload")
    # exec'd caddy commands rewrite the admin allow-list as the Caddy entrypoint does.
    entrypoint = _services()["caddy"]["entrypoint"][2].replace("$$", "$")
    assert "tr ',' ' '" in entrypoint and 'tr "," " "' in script
    assert "0.0.0.0/32 ::/128" in entrypoint and "0.0.0.0/32 ::/128" in script
    assert 'rm -f "$SITE_LINK"' in script
    assert {"ANUM_MONITOR_HOST", "ANUM_ERRORS_HOST"} <= _configure_env_module().HOST_NAMES


def test_monitoring_script_edits_env_through_configure_env(tmp_path: Path) -> None:
    """The script's .env helper keeps other lines, quotes bcrypt hashes and stays 600."""
    script = MONITORING_SH.read_text(encoding="utf-8")
    helper = script[script.index("ENV_HELPER='") + len("ENV_HELPER='") :]
    helper = helper[: helper.index("\n'\n")]
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PROFILES=ai\nANUM_WEB_HOST=app.example.org\n", encoding="utf-8")

    def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-c", helper, str(CONFIGURE_ENV), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    fake_hash = "$2a$14$" + "example" * 7 + "exa"
    assert run("set", str(env_file), "ANUM_MONITORING_BASIC_AUTH_HASH", stdin=fake_hash + "\n").returncode == 0
    assert run("set", str(env_file), "COMPOSE_PROFILES", stdin="ai,monitoring\n").returncode == 0
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    text = env_file.read_text(encoding="utf-8")
    assert "ANUM_WEB_HOST=app.example.org\n" in text
    assert f"ANUM_MONITORING_BASIC_AUTH_HASH='{fake_hash}'\n" in text
    assert text.count("COMPOSE_PROFILES=") == 1
    assert run("get", str(env_file), "ANUM_MONITORING_BASIC_AUTH_HASH").stdout.strip() == fake_hash
    assert run("get", str(env_file), "COMPOSE_PROFILES").stdout.strip() == "ai,monitoring"
    secret = run("secret", str(env_file), "GLITCHTIP_DB_PASSWORD").stdout.strip()
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", secret)
    assert run("valid", str(env_file), "ANUM_ERRORS_HOST", stdin="https://errors.example.org\n").returncode == 1
    assert run("valid", str(env_file), "ANUM_ERRORS_HOST", stdin="anum-errors-me.duckdns.org\n").returncode == 0
    assert not (ORACLE / "__pycache__").exists()


# --------------------------------------------------------------------------- browser error reporting


def test_web_error_reporting_is_an_optional_build_argument_with_a_build_time_csp_origin() -> None:
    web = _services()["web"]
    args = web["build"]["args"]
    assert args["VITE_SENTRY_DSN"] == "${ANUM_WEB_SENTRY_DSN:-}"
    assert _example()["ANUM_WEB_SENTRY_DSN"] == ""
    # The runtime connect-src stays the API and Keycloak only; the DSN origin is fixed at build.
    assert "SENTRY" not in _env(web)["ANUM_CSP_CONNECT_SRC"]
    dockerfile = (REPO_ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    template = (REPO_ROOT / "apps" / "web" / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    assert "ARG VITE_SENTRY_DSN=\n" in dockerfile and "VITE_SENTRY_DSN=$VITE_SENTRY_DSN" in dockerfile
    assert "apps/web/scripts/sentry-csp-origin.ts" in dockerfile
    assert 'sed "s|__ANUM_CSP_ERROR_REPORTING_SRC__|${origin}|"' in dockerfile
    csp = next(line for line in template.splitlines() if line.strip().startswith("set $csp"))
    connect = next(part for part in csp.split(";") if part.strip().startswith("connect-src"))
    # Only connect-src names the reporting origin; script-src and the rest do not change.
    assert "__ANUM_CSP_ERROR_REPORTING_SRC__" in connect
    assert csp.count("__ANUM_CSP_ERROR_REPORTING_SRC__") == 1


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("", ""),
        ("https://examplekey@errors.example.org/4", "https://errors.example.org"),
        ("https://examplekey@errors.example.org:8443/4", "https://errors.example.org:8443"),
    ],
)
def test_csp_origin_script_prints_only_a_validated_origin(dsn: str, expected: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603
        [node, "--experimental-strip-types", str(REPO_ROOT / "apps" / "web" / "scripts" / "sentry-csp-origin.ts")],
        env={**os.environ, "VITE_SENTRY_DSN": dsn},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "dsn",
    ["http://examplekey@errors.example.org/4", "https://errors.example.org/4", "https://examplekey@errors.example.org/4?x=1;evil"],
)
def test_csp_origin_script_refuses_bad_dsns_without_echoing_them(dsn: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603
        [node, "--experimental-strip-types", str(REPO_ROOT / "apps" / "web" / "scripts" / "sentry-csp-origin.ts")],
        env={**os.environ, "VITE_SENTRY_DSN": dsn},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout.strip() == ""
    assert "examplekey" not in result.stderr


def test_monitoring_script_sets_the_web_dsn_and_rebuilds_the_web_image() -> None:
    script = MONITORING_SH.read_text(encoding="utf-8")
    assert "web-dsn)" in script and "cmd_web_dsn" in script
    body = script[script.index("cmd_web_dsn() {") : script.index("cmd_enable() {")]
    # Public https DSN for this GlitchTip only, then a rebuild (the DSN is compiled in).
    assert 'parts.scheme == "https"' in body and 'os.environ["ERRORS_HOST"]' in body
    assert "set_env_value ANUM_WEB_SENTRY_DSN" in body
    assert "dc up -d --build --no-deps web" in body
    caddy = (ORACLE / "monitoring.caddy").read_text(encoding="utf-8")
    # Browsers reach GlitchTip's ingest without the basic-auth login.
    assert "@ingest path_regexp ingest ^/api/[0-9]+/(envelope|store|security|minidump)/$" in caddy


def test_module_settings_from_the_example_pass_the_apis_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANUM_EMAIL_*, ANUM_WHATSAPP_* and ANUM_LEARNING_* reach the API and parse (docs/deploy-oracle.md)."""
    from anum_api.email_inbox import EmailSettings
    from anum_api.whatsapp import WhatsAppSettings

    environment = _api_environment()
    for key in ("ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS", "ANUM_LEARNING_ENABLED", "ANUM_LEARNING_SIGNAL_RETENTION_DAYS", "ANUM_EMAIL_SYNC_INTERVAL_SECONDS"):
        assert key in environment
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    email = EmailSettings(_env_file=None)
    assert email.redirect_uri.startswith("https://") and email.web_url.startswith("https://")
    assert WhatsAppSettings(_env_file=None).message_retention_days == 0
    settings = _settings(monkeypatch, environment)
    assert settings.learning_enabled is True and settings.learning_signal_retention_days == 180


def test_owner_integrations_reach_the_api_with_callbacks_on_the_real_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every integration in docs/integrations-setup.md can be switched on from infra/oracle/.env."""
    from anum_api.marketing.config import MarketingSettings

    environment = _api_environment()
    for key in (
        "ANUM_SMTP_HOST", "ANUM_SMTP_PASSWORD", "ANUM_TRANSCRIPTION_PROVIDER", "ANUM_ZOOM_CLIENT_ID",
        "ANUM_MICROSOFT_CLIENT_SECRET", "ANUM_MARKETING_META_APP_ID", "ANUM_MARKETING_GOOGLE_CLIENT_SECRET",
        "ANUM_EXTERNAL_WEBHOOK_URL", "ANUM_DEVELOPER_TENANT_ID", "ANUM_COMMERCE_SYNC_ENABLED",
    ):
        assert key in environment, key
    example = _example()
    assert environment["ANUM_MEETINGS_OAUTH_REDIRECT_URI"] == f"https://{example['ANUM_WEB_HOST']}/meetings/connected"
    assert environment["ANUM_MARKETING_OAUTH_REDIRECT_URL"] == f"https://{example['ANUM_WEB_HOST']}/marketing/callback"
    assert environment["ANUM_MARKETING_PUBLIC_API_URL"] == f"https://{example['ANUM_API_HOST']}"
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    marketing = MarketingSettings(_env_file=None)
    assert marketing.oauth_redirect_url.endswith("/marketing/callback")
    settings = _settings(monkeypatch, environment)
    assert settings.transcription_provider == "none" and settings.smtp_port == 587
    assert settings.meetings_oauth_redirect_uri.startswith("https://")


def test_turn_relay_is_locked_down_and_its_secret_never_reaches_a_command_line() -> None:
    coturn = _services()["coturn"]
    assert not coturn.get("ports") and not coturn.get("expose")
    assert coturn["cap_drop"] == ["ALL"] and coturn["security_opt"] == ["no-new-privileges:true"]
    script = coturn["entrypoint"][2]
    assert "use-auth-secret" in script and "no-cli" in script and "no-multicast-peers" in script
    for denied in ("10.0.0.0-10.255.255.255", "127.0.0.0-127.255.255.255", "169.254.0.0-169.254.255.255", "172.16.0.0-172.31.255.255", "192.168.0.0-192.168.255.255"):
        assert f"denied-peer-ip={denied}" in script, denied
    # The secret is written into a file inside the container, never passed as an argument.
    assert "--static-auth-secret" not in script and "-c /tmp/turnserver.conf" in script
    api = _env(_services()["api"])
    assert api["ANUM_CALLS_TURN_SECRET"] == "${ANUM_TURN_SECRET:-}"
    # Off by default: no TURN URLs until calls.sh enable writes them.
    assert _example()["ANUM_CALLS_TURN_URLS"] == "" and _example()["ANUM_CALLS_ALLOW_STUN_ONLY"] == "false"


def test_calls_script_opens_only_the_relay_ports_before_the_reject_rule() -> None:
    script = (ORACLE / "calls.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in script
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "ufw" not in code and "netfilter-persistent" not in code
    assert '$2 == "REJECT"' in script and "/etc/iptables/rules.v4" in script
    assert "--dport 3478" in script and "udp" in script and "ANUM_TURN_MIN_PORT" in script
    assert "sudo -n true" in script
