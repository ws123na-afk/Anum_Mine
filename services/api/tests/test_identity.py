"""OIDC (Keycloak) authentication: token validation, JWKS caching/rotation, API wiring, startup."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from anum_api import dependencies
from anum_api.authorization import Role
from anum_api.identity import (
    AuthConfigurationError,
    JwksCache,
    OidcValidator,
    TokenValidationError,
    validate_auth_configuration,
)
from anum_api.main import app, store
from cryptography.fernet import Fernet

from anum_api.schemas import Workspace, WorkspaceMembership, utc_now

TEST_SECRETS_KEY = Fernet.generate_key().decode()
from anum_api.settings import Settings, settings


ISSUER = "http://keycloak.test/realms/anum"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
AUDIENCE = "anum-api"
TENANT = "tenant_oidc"
WORKSPACE = "workspace_oidc"
SUBJECT = "4c0a1d2e-user-oidc"
API_ROOT = Path(__file__).parents[1]
REALM_PATH = API_ROOT.parents[1] / "infra" / "keycloak" / "anum-realm.json"


class SigningKey:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict[str, Any]:
        value = json.loads(RSAAlgorithm.to_jwk(self.private_key.public_key()))
        value.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return value

    def sign(self, **overrides: Any) -> str:
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sub": SUBJECT,
            "iss": ISSUER,
            "aud": [AUDIENCE, "account"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "azp": "anum-web",
            "tenant_id": TENANT,
            "workspace_id": WORKSPACE,
            "realm_access": {"roles": ["owner", "offline_access", "default-roles-anum"]},
        }
        payload.update(overrides)
        payload = {key: value for key, value in payload.items() if value is not None}
        return jwt.encode(payload, self.private_key, algorithm="RS256", headers={"kid": self.kid})


class FakeJwksEndpoint:
    """A Keycloak certs endpoint served through httpx.MockTransport."""

    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys)
        self.requests = 0
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        self.requests += 1
        if self.fail:
            return httpx.Response(503)
        encryption_key = {"kid": "enc-key", "kty": "RSA", "use": "enc", "alg": "RSA-OAEP",
                          "n": self.keys[0].jwk()["n"] if self.keys else "AQAB", "e": "AQAB"}
        return httpx.Response(200, json={"keys": [key.jwk() for key in self.keys] + [encryption_key]})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_validator(endpoint: FakeJwksEndpoint, **kwargs: Any) -> OidcValidator:
    return OidcValidator(ISSUER, AUDIENCE, transport=endpoint.transport, **kwargs)


def validate(validator: OidcValidator, token: str):
    return asyncio.run(validator.validate(token))


# --- Validator -------------------------------------------------------------------------------


def test_valid_token_yields_claims_and_caches_jwks() -> None:
    key = SigningKey("kid-1")
    endpoint = FakeJwksEndpoint(key)
    validator = make_validator(endpoint)

    claims = validate(validator, key.sign())
    validate(validator, key.sign())

    assert claims.subject == SUBJECT
    assert claims.tenant_id == TENANT
    assert claims.workspace_id == WORKSPACE
    assert claims.anum_roles == ["owner"]
    assert endpoint.requests == 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "another-api"}, "Bearer token audience is not accepted"),
        ({"iss": "http://evil.test/realms/anum"}, "Bearer token issuer is not accepted"),
        (
            {"exp": datetime.now(timezone.utc) - timedelta(minutes=5),
             "iat": datetime.now(timezone.utc) - timedelta(minutes=10)},
            "Bearer token has expired",
        ),
        ({"tenant_id": None}, "Bearer token is missing a valid tenant_id claim"),
        ({"tenant_id": "bad tenant!"}, "Bearer token is missing a valid tenant_id claim"),
        ({"sub": None}, "Bearer token is missing the sub claim"),
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "no-tenant", "bad-tenant", "no-sub"],
)
def test_invalid_tokens_are_rejected_with_reason(overrides: dict, reason: str) -> None:
    key = SigningKey("kid-1")
    validator = make_validator(FakeJwksEndpoint(key))

    with pytest.raises(TokenValidationError) as error:
        validate(validator, key.sign(**overrides))

    assert error.value.reason == reason


def test_token_signed_by_untrusted_key_with_known_kid_is_rejected() -> None:
    trusted = SigningKey("kid-1")
    attacker = SigningKey("kid-1")
    validator = make_validator(FakeJwksEndpoint(trusted))

    with pytest.raises(TokenValidationError, match="signature"):
        validate(validator, attacker.sign())


def test_non_rs256_and_kidless_tokens_are_rejected() -> None:
    validator = make_validator(FakeJwksEndpoint(SigningKey("kid-1")))
    hs_token = jwt.encode({"sub": "x"}, "shared-secret-for-test", algorithm="HS256",
                          headers={"kid": "kid-1"})
    key = SigningKey("kid-1")
    kidless = jwt.encode({"sub": "x"}, key.private_key, algorithm="RS256")

    with pytest.raises(TokenValidationError, match="algorithm"):
        validate(validator, hs_token)
    with pytest.raises(TokenValidationError, match="key id"):
        validate(validator, kidless)
    with pytest.raises(TokenValidationError, match="malformed"):
        validate(validator, "not-a-jwt")


def test_unknown_kid_triggers_exactly_one_jwks_refresh() -> None:
    key = SigningKey("kid-1")
    stranger = SigningKey("kid-unknown")
    endpoint = FakeJwksEndpoint(key)
    validator = make_validator(endpoint)
    validate(validator, key.sign())
    assert endpoint.requests == 1

    with pytest.raises(TokenValidationError, match="signing key is unknown"):
        validate(validator, stranger.sign())
    assert endpoint.requests == 2

    # Forced refreshes are rate limited: another unknown kid inside the window does not refetch.
    with pytest.raises(TokenValidationError, match="signing key is unknown"):
        validate(validator, SigningKey("kid-other").sign())
    assert endpoint.requests == 2


def test_key_rotation_is_picked_up_by_unknown_kid_refresh() -> None:
    old_key = SigningKey("kid-old")
    new_key = SigningKey("kid-new")
    endpoint = FakeJwksEndpoint(old_key)
    validator = make_validator(endpoint)
    validate(validator, old_key.sign())

    endpoint.keys = [new_key]  # Keycloak rotated its active key.
    claims = validate(validator, new_key.sign())

    assert claims.subject == SUBJECT
    assert endpoint.requests == 2


def test_jwks_cache_expiry_refetches_and_survives_outage() -> None:
    now = [1000.0]
    key = SigningKey("kid-1")
    endpoint = FakeJwksEndpoint(key)
    cache = JwksCache(JWKS_URL, cache_seconds=60, transport=endpoint.transport, clock=lambda: now[0])
    validator = OidcValidator(ISSUER, AUDIENCE, jwks=cache)
    validate(validator, key.sign())

    now[0] += 61
    endpoint.fail = True
    claims = validate(validator, key.sign())  # stale keys keep serving during an IdP outage

    assert claims.tenant_id == TENANT
    assert endpoint.requests == 2


def test_jwks_unavailable_without_cached_keys_is_reported() -> None:
    endpoint = FakeJwksEndpoint(SigningKey("kid-1"))
    endpoint.fail = True
    validator = make_validator(endpoint)

    with pytest.raises(TokenValidationError, match="unavailable"):
        validate(validator, SigningKey("kid-1").sign())


# --- API wiring (ANUM_AUTH_MODE=oidc) --------------------------------------------------------


@pytest.fixture
def oidc_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, SigningKey, FakeJwksEndpoint]]:
    key = SigningKey("kid-1")
    endpoint = FakeJwksEndpoint(key)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "repository_backend", "memory")
    dependencies.set_oidc_validator(make_validator(endpoint))
    store.memberships.clear()
    store.workspaces.clear()
    store.tenants.clear()
    try:
        yield TestClient(app), key, endpoint
    finally:
        dependencies.set_oidc_validator(None)
        store.memberships.clear()
        store.workspaces.clear()
        store.tenants.clear()


def add_membership(role: Role = Role.MEMBER, *, workspace_id: str = WORKSPACE, active: bool = True) -> None:
    now = utc_now()
    store.memberships[(TENANT, workspace_id, SUBJECT)] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=workspace_id, user_id=SUBJECT, role=role.value,
        active=active, created_at=now, updated_at=now,
    )


def bearer(token: str, **headers: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}", **headers}


def test_oidc_request_with_membership_uses_membership_role(oidc_api) -> None:
    client, key, _ = oidc_api
    add_membership(Role.MEMBER)

    response = client.get("/api/v1/workspace-memberships/current", headers=bearer(key.sign()))

    assert response.status_code == 200
    assert response.json()["role"] == "member"
    # Token realm role `owner` must not elevate a `member` membership.
    denied = client.post("/api/v1/workspaces", headers=bearer(key.sign()), json={"name": "Escalate"})
    assert denied.status_code == 403
    assert denied.json()["error"]["message"] == "Workspace permission denied"


def test_oidc_missing_membership_is_403(oidc_api) -> None:
    client, key, _ = oidc_api

    response = client.get("/api/v1/tasks", headers=bearer(key.sign()))

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Active workspace membership required"


def test_oidc_inactive_membership_is_403(oidc_api) -> None:
    client, key, _ = oidc_api
    add_membership(Role.OWNER, active=False)

    assert client.get("/api/v1/tasks", headers=bearer(key.sign())).status_code == 403


def test_oidc_workspace_header_is_validated_against_memberships(oidc_api) -> None:
    client, key, _ = oidc_api
    add_membership(Role.VIEWER, workspace_id="workspace_second")

    selected = client.get(
        "/api/v1/workspace-memberships/current",
        headers=bearer(key.sign(), **{"x-workspace-id": "workspace_second"}),
    )
    not_member = client.get(
        "/api/v1/workspace-memberships/current",
        headers=bearer(key.sign(), **{"x-workspace-id": "workspace_other"}),
    )

    assert selected.status_code == 200
    assert selected.json()["workspace_id"] == "workspace_second"
    assert selected.json()["role"] == "viewer"
    assert not_member.status_code == 403


def test_oidc_tenant_comes_from_token_not_headers(oidc_api) -> None:
    client, key, _ = oidc_api
    add_membership(Role.OWNER)

    response = client.get(
        "/api/v1/tasks", headers=bearer(key.sign(), **{"x-tenant-id": "tenant_someone_else"})
    )

    assert response.status_code == 403
    assert "x-tenant-id" in response.json()["error"]["message"]


def test_oidc_requires_workspace_selection(oidc_api) -> None:
    client, key, _ = oidc_api

    response = client.get("/api/v1/tasks", headers=bearer(key.sign(workspace_id=None)))

    assert response.status_code == 400
    assert "x-workspace-id" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("headers_for", "message"),
    [
        (lambda key: {}, "Bearer token required"),
        (lambda key: {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": SUBJECT},
         "Bearer token required"),
        (lambda key: bearer(key.sign(aud="another-api")), "Bearer token audience is not accepted"),
        (lambda key: bearer(key.sign(iss="http://evil.test/realms/anum")),
         "Bearer token issuer is not accepted"),
        (lambda key: bearer(key.sign(exp=datetime.now(timezone.utc) - timedelta(hours=1),
                                     iat=datetime.now(timezone.utc) - timedelta(hours=2))),
         "Bearer token has expired"),
        (lambda key: bearer(SigningKey("kid-rogue").sign()), "Bearer token signing key is unknown"),
        (lambda key: bearer("anum_local_forged"), "Local sessions are disabled when ANUM_AUTH_MODE=oidc"),
    ],
    ids=["no-token", "headers-only", "wrong-audience", "wrong-issuer", "expired", "unknown-kid",
         "local-session"],
)
def test_oidc_rejects_unauthenticated_requests_with_401(oidc_api, headers_for, message) -> None:
    client, key, _ = oidc_api
    add_membership(Role.OWNER)

    response = client.get("/api/v1/tasks", headers=headers_for(key))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["message"] == message
    assert response.headers["www-authenticate"].startswith('Bearer realm="anum"')


def test_oidc_identity_provider_outage_is_503(oidc_api) -> None:
    client, key, endpoint = oidc_api
    endpoint.fail = True

    response = client.get("/api/v1/tasks", headers=bearer(key.sign()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


def test_oidc_bootstrap_onboarding_then_membership_governs(oidc_api) -> None:
    client, key, _ = oidc_api
    headers = bearer(key.sign())

    assert client.get("/api/v1/tasks", headers=headers).status_code == 403
    onboarded = client.put(
        "/api/v1/onboarding",
        headers=headers,
        json={"organization_name": "OIDC Org", "workspace_name": "OIDC Workspace"},
    )
    assert onboarded.status_code == 200
    assert onboarded.json()["membership"]["user_id"] == SUBJECT
    assert client.get("/api/v1/tasks", headers=headers).status_code == 200


def test_oidc_bootstrap_requires_owner_realm_role(oidc_api) -> None:
    client, key, _ = oidc_api
    token = key.sign(realm_access={"roles": ["member"]})

    response = client.put(
        "/api/v1/onboarding",
        headers=bearer(token),
        json={"organization_name": "OIDC Org", "workspace_name": "OIDC Workspace"},
    )

    assert response.status_code == 403


def test_local_session_endpoints_are_unavailable_in_oidc_mode(oidc_api) -> None:
    client, _, _ = oidc_api

    response = client.post(
        "/api/v1/auth/local/session",
        json={"tenant_id": TENANT, "workspace_id": WORKSPACE, "user_id": "user_local"},
    )

    assert response.status_code == 404


# --- Startup configuration -------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["local", "test", "LOCAL"])
def test_header_mode_is_allowed_in_local_and_test(environment: str) -> None:
    validate_auth_configuration(Settings(environment=environment, auth_mode="headers", secrets_key=TEST_SECRETS_KEY))


@pytest.mark.parametrize("environment", ["staging", "production", "dev"])
def test_header_mode_is_refused_outside_local(environment: str) -> None:
    with pytest.raises(AuthConfigurationError, match="ANUM_AUTH_MODE=oidc"):
        validate_auth_configuration(Settings(environment=environment, auth_mode="headers", secrets_key=TEST_SECRETS_KEY))


def test_oidc_mode_is_accepted_in_production_and_unknown_modes_refused() -> None:
    validate_auth_configuration(Settings(environment="production", auth_mode="oidc", secrets_key=TEST_SECRETS_KEY))
    with pytest.raises(AuthConfigurationError, match="Unsupported"):
        validate_auth_configuration(Settings(environment="local", auth_mode="local"))
    with pytest.raises(AuthConfigurationError, match="ANUM_OIDC_AUDIENCE"):
        validate_auth_configuration(
            Settings(environment="production", auth_mode="oidc", oidc_audience="", secrets_key=TEST_SECRETS_KEY)
        )


def test_api_process_refuses_to_start_with_header_mode_in_production() -> None:
    env = {**os.environ, "ANUM_ENVIRONMENT": "production", "ANUM_AUTH_MODE": "headers", "ANUM_SECRETS_KEY": TEST_SECRETS_KEY}
    result = subprocess.run(
        [sys.executable, "-c", "import anum_api.main"],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "AuthConfigurationError" in result.stderr
    assert "anum_local_* bearer sessions are development-only" in result.stderr


def test_header_mode_runtime_guard_refuses_non_local_environment(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="local/test"):
        asyncio.run(
            dependencies.tenant_context(
                authorization=None, x_tenant_id="t_1", x_workspace_id="w_1",
                x_user_id="u_1", x_user_roles="owner",
            )
        )


# --- Realm as code ---------------------------------------------------------------------------


def load_realm() -> dict[str, Any]:
    return json.loads(REALM_PATH.read_text())


def test_realm_roles_match_authorization_roles() -> None:
    realm = load_realm()
    names = {role["name"] for role in realm["roles"]["realm"]}

    assert realm["realm"] == "anum"
    assert names == {role.value for role in Role}


def test_realm_issuer_matches_default_settings() -> None:
    assert Settings().keycloak_issuer.endswith(f"/realms/{load_realm()['realm']}")
    assert Settings().oidc_audience == AUDIENCE


def test_realm_public_clients_use_pkce_and_issue_anum_claims() -> None:
    clients = {client["clientId"]: client for client in load_realm()["clients"]}
    expected_redirects = {
        "anum-web": "http://localhost:5173/*",
        "anum-desktop": "tauri://localhost/*",
        "anum-android": "com.anum.mobile:/oauth2redirect",
        "anum-flutter": "com.anum.app:/oauth2redirect",
    }

    assert clients[AUDIENCE]["bearerOnly"] is True
    for client_id, redirect in expected_redirects.items():
        client = clients[client_id]
        assert client["publicClient"] is True, client_id
        assert client["standardFlowEnabled"] is True, client_id
        assert client["implicitFlowEnabled"] is False, client_id
        assert client["directAccessGrantsEnabled"] is False, client_id
        assert client["attributes"]["pkce.code.challenge.method"] == "S256", client_id
        assert redirect in client["redirectUris"], client_id
        assert "*" not in client["redirectUris"], client_id
        mappers = {mapper["protocolMapper"]: mapper for mapper in client["protocolMappers"]}
        audience = mappers["oidc-audience-mapper"]["config"]
        assert audience["included.custom.audience"] == AUDIENCE
        assert audience["access.token.claim"] == "true"
        claims = {
            mapper["config"]["claim.name"]: mapper["config"]["user.attribute"]
            for mapper in client["protocolMappers"]
            if mapper["protocolMapper"] == "oidc-usermodel-attribute-mapper"
        }
        assert claims == {"tenant_id": "tenant_id", "workspace_id": "default_workspace_id"}


def test_realm_tenant_attributes_are_admin_only() -> None:
    component = load_realm()["components"]["org.keycloak.userprofile.UserProfileProvider"][0]
    profile = json.loads(component["config"]["kc.user.profile.config"][0])
    attributes = {attribute["name"]: attribute for attribute in profile["attributes"]}

    for name in ("tenant_id", "default_workspace_id"):
        assert attributes[name]["permissions"]["edit"] == ["admin"], name
    assert {"username", "email", "firstName", "lastName"} <= attributes.keys()


def test_realm_dev_user_is_marked_dev_only() -> None:
    for user in load_realm().get("users", []):
        for credential in user.get("credentials", []):
            assert "dev-only" in credential["value"].lower()
            assert "DEV-ONLY" in credential["userLabel"]


def test_oidc_bootstrap_cannot_claim_a_workspace_that_already_has_members(oidc_api) -> None:
    client, key, _ = oidc_api
    now = utc_now()
    store.workspaces[WORKSPACE] = Workspace(id=WORKSPACE, tenant_id=TENANT, name="Taken", created_at=utc_now(), updated_at=utc_now())
    store.memberships[(TENANT, WORKSPACE, "someone_else")] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="someone_else", role="owner",
        active=True, created_at=now, updated_at=now,
    )

    response = client.post("/api/v1/workspace-memberships/current", headers=bearer(key.sign()))

    assert response.status_code == 403
    assert (TENANT, WORKSPACE, SUBJECT) not in store.memberships


def test_oidc_bootstrap_can_join_an_empty_workspace(oidc_api) -> None:
    client, key, _ = oidc_api
    store.workspaces[WORKSPACE] = Workspace(id=WORKSPACE, tenant_id=TENANT, name="Fresh", created_at=utc_now(), updated_at=utc_now())

    response = client.post("/api/v1/workspace-memberships/current", headers=bearer(key.sign()))

    assert response.status_code == 201
    assert response.json()["role"] == "owner"
