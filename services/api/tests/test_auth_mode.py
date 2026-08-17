"""Tests for the `settings.auth_mode` switch in `dependencies.tenant_context`.

Covers two things:

1. The DEFAULT ("stub_headers") behavior is unchanged from before this
   module existed: stub headers authenticate requests, missing headers
   still 401.
2. Opting into `"oidc"` (via a temporarily monkeypatched `settings.auth_mode`,
   always restored by `monkeypatch` after each test) routes authentication
   through `oidc_auth.resolve_tenant_context_from_bearer` instead, and the
   old stub headers stop working entirely while that mode is active.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import anum_api.dependencies as dependencies
from anum_api.dependencies import memory_note_repository
from anum_api.main import app, store
from anum_api.oidc_auth import JWKSClient
from anum_api.settings import settings


client = TestClient(app)

STUB_HEADERS = {
    "x-tenant-id": "tenant_a",
    "x-workspace-id": "workspace_a",
    "x-user-id": "user_a",
    "x-user-roles": "owner,member",
}

ISSUER = "https://auth.example.test/realms/anum"
AUDIENCE = "anum-api"
JWKS_URL = "https://auth.example.test/realms/anum/protocol/openid-connect/certs"
KID = "test-signing-key"


def setup_function() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    memory_note_repository._notes.clear()


# --------------------------------------------------------------------------
# JWT helpers (same pattern as tests/test_oidc_auth.py)
# --------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _jwk_from_public_key(public_key: rsa.RSAPublicKey, kid: str) -> dict[str, Any]:
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(_int_to_bytes(numbers.n)),
        "e": _b64url(_int_to_bytes(numbers.e)),
    }


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_document(rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> dict[str, Any]:
    _, public_key = rsa_keys
    return {"keys": [_jwk_from_public_key(public_key, KID)]}


@pytest.fixture
def jwks_client(jwks_document: dict[str, Any]) -> JWKSClient:
    return JWKSClient(JWKS_URL, fetcher=lambda url: jwks_document)


def make_claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "tenant_id": "tenant_a",
        "workspace_id": "workspace_a",
        "roles": ["owner", "member"],
    }
    claims.update(overrides)
    return claims


def sign_token(
    claims: dict[str, Any],
    *,
    private_key: rsa.RSAPrivateKey,
    kid: str | None = KID,
) -> str:
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, key=private_key, algorithm="RS256", headers=headers)


@pytest.fixture
def oidc_mode(monkeypatch: pytest.MonkeyPatch, jwks_client: JWKSClient) -> None:
    """Flip `settings.auth_mode` to "oidc" for the duration of one test.

    Uses `monkeypatch` for every mutated attribute (including the JWKS
    client accessor `dependencies.get_jwks_client`) so everything is
    automatically restored to its original value after the test, even on
    failure -- other test modules (which rely on the default
    "stub_headers" mode) are never affected.
    """

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(dependencies, "get_jwks_client", lambda: jwks_client)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Default behavior: "stub_headers" mode (unchanged from before this module)
# --------------------------------------------------------------------------


def test_default_auth_mode_is_stub_headers() -> None:
    assert settings.auth_mode == "stub_headers"


def test_default_mode_stub_headers_authenticate_requests() -> None:
    response = client.get("/api/v1/tasks", headers=STUB_HEADERS)

    assert response.status_code == 200
    assert response.json() == []


def test_default_mode_missing_stub_headers_still_401() -> None:
    response = client.get("/api/v1/tasks")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"


def test_default_mode_bearer_token_alone_does_not_authenticate() -> None:
    """In the default mode, a Bearer token is irrelevant -- stub headers are
    still what's required, so a request with only a token (no stub headers)
    must still be rejected."""

    response = client.get("/api/v1/tasks", headers=bearer("whatever.not.checked"))

    assert response.status_code == 401


# --------------------------------------------------------------------------
# Opt-in "oidc" mode
# --------------------------------------------------------------------------


def test_oidc_mode_valid_token_resolves_context_and_route_works(
    oidc_mode: None,
    rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(), private_key=private_key)

    response = client.get("/api/v1/tasks", headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == []


def test_oidc_mode_missing_token_returns_401_with_standard_envelope(oidc_mode: None) -> None:
    response = client.get("/api/v1/tasks")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert "correlation_id" in body["error"]


def test_oidc_mode_invalid_token_returns_401(oidc_mode: None) -> None:
    response = client.get("/api/v1/tasks", headers=bearer("not-a-real-jwt"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_oidc_mode_expired_token_returns_401(
    oidc_mode: None,
    rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
) -> None:
    private_key, _ = rsa_keys
    now = datetime.now(timezone.utc)
    token = sign_token(
        make_claims(iat=now - timedelta(hours=1), exp=now - timedelta(minutes=1)),
        private_key=private_key,
    )

    response = client.get("/api/v1/tasks", headers=bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_oidc_mode_ignores_old_stub_headers(oidc_mode: None) -> None:
    """The whole point of `auth_mode`: while "oidc" is active, the legacy
    stub headers must NOT work anymore, even though they still would in
    the default mode."""

    response = client.get("/api/v1/tasks", headers=STUB_HEADERS)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_oidc_mode_ignores_stub_headers_even_alongside_a_valid_token(
    oidc_mode: None,
    rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
) -> None:
    """A valid Bearer token's claims should be what's used -- not any stub
    headers that happen to also be present on the request."""

    private_key, _ = rsa_keys
    token = sign_token(make_claims(tenant_id="tenant_from_token"), private_key=private_key)
    combined_headers = {**STUB_HEADERS, **bearer(token)}

    response = client.get("/api/v1/tasks", headers=combined_headers)

    assert response.status_code == 200
