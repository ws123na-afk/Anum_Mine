from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from anum_api.errors import ApplicationError, ErrorCode
from anum_api.oidc_auth import (
    InvalidAlgorithmError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    JWKSClient,
    JWKSFetchError,
    MalformedTokenError,
    TokenExpiredError,
    TokenNotYetValidError,
    UnknownKeyError,
    oidc_tenant_context,
    validate_token,
)
from anum_api.settings import settings


ISSUER = "https://auth.example.test/realms/anum"
AUDIENCE = "anum-api"
JWKS_URL = "https://auth.example.test/realms/anum/protocol/openid-connect/certs"
KID = "test-signing-key"


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


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point settings at this test module's issuer/audience.

    `oidc_tenant_context` reads `settings.keycloak_issuer` / `oidc_audience`
    directly (it isn't parameterized per-request), so tests that exercise it
    need settings to match the tokens minted here.
    """

    monkeypatch.setattr(settings, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def other_rsa_keys() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """A second, unrelated keypair used to simulate a tampered/forged signature."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_document(rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> dict[str, Any]:
    _, public_key = rsa_keys
    return {"keys": [_jwk_from_public_key(public_key, KID)]}


class RecordingFetcher:
    """A stub JWKS fetcher: no network access, and it counts how often it's called."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.calls = 0

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls += 1
        return self.document


@pytest.fixture
def fetcher(jwks_document: dict[str, Any]) -> RecordingFetcher:
    return RecordingFetcher(jwks_document)


@pytest.fixture
def jwks_client(fetcher: RecordingFetcher) -> JWKSClient:
    return JWKSClient(JWKS_URL, fetcher=fetcher)


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
        "roles": ["member"],
    }
    claims.update(overrides)
    return claims


def sign_token(
    claims: dict[str, Any],
    *,
    private_key: rsa.RSAPrivateKey,
    kid: str | None = KID,
    algorithm: str = "RS256",
) -> str:
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, key=private_key, algorithm=algorithm, headers=headers)


# --------------------------------------------------------------------------
# JWKSClient
# --------------------------------------------------------------------------


def test_jwks_client_resolves_known_kid(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    key = jwks_client.get_signing_key(KID)
    assert key.public_numbers() == rsa_keys[1].public_numbers()


def test_jwks_client_caches_between_calls(fetcher: RecordingFetcher, jwks_client: JWKSClient) -> None:
    jwks_client.get_signing_key(KID)
    jwks_client.get_signing_key(KID)

    assert fetcher.calls == 1


def test_jwks_client_refetches_once_on_unknown_kid_then_raises(
    fetcher: RecordingFetcher, jwks_client: JWKSClient
) -> None:
    with pytest.raises(UnknownKeyError):
        jwks_client.get_signing_key("does-not-exist")

    # One initial fetch, plus one retry to tolerate key rotation.
    assert fetcher.calls == 2


def test_jwks_client_expires_cache_after_ttl(fetcher: RecordingFetcher, jwks_document: dict[str, Any]) -> None:
    client = JWKSClient(JWKS_URL, fetcher=fetcher, cache_ttl_seconds=0.01)
    client.get_signing_key(KID)
    time.sleep(0.02)
    client.get_signing_key(KID)

    assert fetcher.calls == 2


def test_jwks_client_wraps_fetch_errors() -> None:
    def broken_fetcher(url: str) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    client = JWKSClient(JWKS_URL, fetcher=broken_fetcher)

    with pytest.raises(JWKSFetchError):
        client.get_signing_key(KID)


def test_jwks_client_rejects_document_without_keys_array() -> None:
    client = JWKSClient(JWKS_URL, fetcher=lambda url: {"not_keys": []})

    with pytest.raises(JWKSFetchError):
        client.get_signing_key(KID)


# --------------------------------------------------------------------------
# validate_token
# --------------------------------------------------------------------------


def test_validate_token_accepts_valid_token(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(), private_key=private_key)

    claims = validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)

    assert claims["sub"] == "user-123"
    assert claims["tenant_id"] == "tenant_a"
    assert claims["workspace_id"] == "workspace_a"
    assert claims["roles"] == ["member"]


def test_validate_token_rejects_expired_token(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    now = datetime.now(timezone.utc)
    token = sign_token(
        make_claims(iat=now - timedelta(hours=1), exp=now - timedelta(minutes=1)),
        private_key=private_key,
    )

    with pytest.raises(TokenExpiredError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_not_yet_valid_token(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    now = datetime.now(timezone.utc)
    token = sign_token(
        make_claims(nbf=now + timedelta(minutes=10), exp=now + timedelta(minutes=20)),
        private_key=private_key,
    )

    with pytest.raises(TokenNotYetValidError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_wrong_issuer(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(iss="https://attacker.example/realms/evil"), private_key=private_key)

    with pytest.raises(InvalidIssuerError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_wrong_audience(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(aud="some-other-api"), private_key=private_key)

    with pytest.raises(InvalidAudienceError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_tampered_signature(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(), private_key=private_key)
    header, payload, signature = token.split(".")

    # Flip the payload (grant ourselves a different tenant) without re-signing.
    tampered_claims = make_claims(tenant_id="tenant_b")
    tampered_payload = _b64url(json.dumps(tampered_claims, default=str).encode())
    tampered_token = f"{header}.{tampered_payload}.{signature}"

    with pytest.raises(InvalidSignatureError):
        validate_token(tampered_token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_signature_from_wrong_key(
    jwks_client: JWKSClient, other_rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    forged_private_key, _ = other_rsa_keys
    # Signed with a key whose public half is *not* in the JWKS (kid still claims to match).
    token = sign_token(make_claims(), private_key=forged_private_key)

    with pytest.raises(InvalidSignatureError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_alg_none(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    token = jwt.encode(make_claims(), key=None, algorithm="none", headers={"kid": KID})

    with pytest.raises(InvalidAlgorithmError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_unknown_kid(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(), private_key=private_key, kid="some-other-kid")

    with pytest.raises(UnknownKeyError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


def test_validate_token_rejects_missing_kid(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(), private_key=private_key, kid=None)

    with pytest.raises(MalformedTokenError):
        validate_token(token, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b", "a.b.c.d"])
def test_validate_token_rejects_malformed_strings(jwks_client: JWKSClient, garbage: str) -> None:
    with pytest.raises(MalformedTokenError):
        validate_token(garbage, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)


# --------------------------------------------------------------------------
# oidc_tenant_context (FastAPI dependency)
# --------------------------------------------------------------------------


def _call_dependency(jwks_client: JWKSClient, authorization: str | None) -> Any:
    """Run the async dependency synchronously (no pytest-asyncio/anyio plugin needed)."""

    return asyncio.run(
        oidc_tenant_context(authorization=authorization, jwks_client=jwks_client)
    )


def test_oidc_tenant_context_maps_claims_for_valid_token(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    token = sign_token(make_claims(), private_key=private_key)

    context = _call_dependency(jwks_client, f"Bearer {token}")

    assert context.tenant_id == "tenant_a"
    assert context.workspace_id == "workspace_a"
    assert context.user_id == "user-123"
    assert context.roles == ["member"]


def test_oidc_tenant_context_rejects_missing_header(jwks_client: JWKSClient) -> None:
    with pytest.raises(ApplicationError) as exc_info:
        _call_dependency(jwks_client, None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_oidc_tenant_context_rejects_non_bearer_scheme(jwks_client: JWKSClient) -> None:
    with pytest.raises(ApplicationError) as exc_info:
        _call_dependency(jwks_client, "Basic dXNlcjpwYXNz")

    assert exc_info.value.status_code == 401


def test_oidc_tenant_context_rejects_invalid_token(jwks_client: JWKSClient) -> None:
    with pytest.raises(ApplicationError) as exc_info:
        _call_dependency(jwks_client, "Bearer not-a-real-jwt")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_oidc_tenant_context_rejects_token_missing_tenant_claims(
    jwks_client: JWKSClient, rsa_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
) -> None:
    private_key, _ = rsa_keys
    claims = make_claims()
    del claims["tenant_id"]
    token = sign_token(claims, private_key=private_key)

    with pytest.raises(ApplicationError) as exc_info:
        _call_dependency(jwks_client, f"Bearer {token}")

    assert exc_info.value.status_code == 401
