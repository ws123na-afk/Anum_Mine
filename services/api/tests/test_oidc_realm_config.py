"""Confirms the claim shape `infra/docker/keycloak/anum-realm.json` would
actually issue lines up with what `oidc_auth.py` expects.

There is no live Keycloak in this environment to import that realm into and
get a real token from, so this test does the next best thing: it builds a
JWT with exactly the claims the realm's protocol mappers are configured to
produce for the seeded `user_local` user (tenant_id/workspace_id from user
attributes, roles as a flat `roles` claim, `anum-api` audience) and confirms
`resolve_tenant_context_from_bearer` resolves it into the same
`TenantContext` the stub-header dev flow already produces for that same
tenant/workspace - i.e. flipping `ANUM_AUTH_MODE=oidc` against that realm
would not change who the "local dev user" is, just how they're verified.

If the mapper claim names in anum-realm.json and the field names read in
oidc_auth.resolve_tenant_context_from_bearer ever drift apart, this test is
what would catch it (short of running a real Keycloak).
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from anum_api.authorization import AuthorizationError, Permission, Role, WorkspaceMembership, policy
from anum_api.oidc_auth import JWKSClient, resolve_tenant_context_from_bearer
from anum_api.settings import settings


REALM_FILE = Path(__file__).parents[3] / "infra" / "docker" / "keycloak" / "anum-realm.json"
KID = "realm-config-test-key"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


@pytest.fixture
def realm_document() -> dict[str, Any]:
    return json.loads(REALM_FILE.read_text())


@pytest.fixture
def rsa_keys() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_client(rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> JWKSClient:
    _, public_key = rsa_keys
    numbers = public_key.public_numbers()
    document = {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url(_int_to_bytes(numbers.n)),
                "e": _b64url(_int_to_bytes(numbers.e)),
            }
        ]
    }
    return JWKSClient("https://unused-in-this-test/certs", fetcher=lambda _url: document)


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "keycloak_issuer", "http://localhost:8080/realms/anum")
    monkeypatch.setattr(settings, "oidc_audience", "anum-api")


def _find_client(realm_document: dict[str, Any], client_id: str) -> dict[str, Any]:
    return next(c for c in realm_document["clients"] if c["clientId"] == client_id)


def _find_user(realm_document: dict[str, Any], username: str) -> dict[str, Any]:
    return next(u for u in realm_document["users"] if u["username"] == username)


def test_realm_file_defines_the_audience_and_claim_mappers_oidc_auth_expects(
    realm_document: dict[str, Any],
) -> None:
    assert realm_document["realm"] == "anum"

    anum_web = _find_client(realm_document, "anum-web")
    mapper_names = {m["protocolMapper"] for m in anum_web["protocolMappers"]}
    assert "oidc-audience-mapper" in mapper_names
    assert "oidc-usermodel-attribute-mapper" in mapper_names
    assert "oidc-usermodel-realm-role-mapping-mapper" in mapper_names

    claim_names = {
        m["config"]["claim.name"]
        for m in anum_web["protocolMappers"]
        if "claim.name" in m["config"]
    }
    assert claim_names == {"tenant_id", "workspace_id", "roles"}

    audience_mapper = next(
        m for m in anum_web["protocolMappers"] if m["protocolMapper"] == "oidc-audience-mapper"
    )
    assert audience_mapper["config"]["included.client.audience"] == "anum-api"

    # The audience mapper targets a real client entry, or Keycloak silently
    # drops the audience at runtime.
    _find_client(realm_document, "anum-api")


def _claims_for_seeded_user(realm_document: dict[str, Any], username: str) -> dict[str, Any]:
    """Build the exact claim set the anum-web mappers would produce for a
    seeded realm user, by reading their attributes/roles straight out of the
    realm-export file - not by hand-guessing them.
    """

    user = _find_user(realm_document, username)
    now = datetime.now(timezone.utc)
    return {
        "iss": settings.keycloak_issuer,
        "aud": "anum-api",
        "sub": username,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "tenant_id": user["attributes"]["tenant_id"][0],
        "workspace_id": user["attributes"]["workspace_id"][0],
        "roles": user["realmRoles"],
    }


def test_seeded_owner_user_resolves_to_the_stub_dev_tenant(
    realm_document: dict[str, Any],
    rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    jwks_client: JWKSClient,
) -> None:
    private_key, _ = rsa_keys
    claims = _claims_for_seeded_user(realm_document, "user_local")
    token = jwt.encode(claims, key=private_key, algorithm="RS256", headers={"kid": KID})

    # No pytest-asyncio/anyio plugin is installed, so run the async
    # dependency synchronously, matching test_oidc_auth.py's pattern.
    context = asyncio.run(resolve_tenant_context_from_bearer(f"Bearer {token}", jwks_client))

    # Same tenant/workspace the stub-header dev flow already uses by
    # default (see dependencies.tenant_context / README's "Tenant Headers
    # for Phase 1") - switching auth_mode doesn't change who "the local dev
    # user" is, just how they're verified.
    assert context.tenant_id == "tenant_local"
    assert context.workspace_id == "workspace_foundation"
    assert context.user_id == "user_local"
    assert set(context.roles) == {"owner", "member"}

    membership = WorkspaceMembership(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        role=Role.OWNER,
    )
    policy.require(context, Permission.TASK_CREATE, membership)


def test_seeded_viewer_user_cannot_create_tasks(
    realm_document: dict[str, Any],
    rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    jwks_client: JWKSClient,
) -> None:
    private_key, _ = rsa_keys
    claims = _claims_for_seeded_user(realm_document, "user_beta_viewer")
    token = jwt.encode(claims, key=private_key, algorithm="RS256", headers={"kid": KID})

    context = asyncio.run(resolve_tenant_context_from_bearer(f"Bearer {token}", jwks_client))

    assert context.tenant_id == "tenant_beta"
    assert context.roles == ["viewer"]

    membership = WorkspaceMembership(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        role=Role.VIEWER,
    )
    with pytest.raises(AuthorizationError):
        policy.require(context, Permission.TASK_CREATE, membership)
