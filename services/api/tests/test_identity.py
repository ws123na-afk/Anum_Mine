import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from anum_api.identity import OidcValidator


class SigningKey:
    def __init__(self, key: object) -> None:
        self.key = key


class StaticJwksClient:
    def __init__(self, public_key: object) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _: str) -> SigningKey:
        return SigningKey(self.public_key)


def token_payload() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "sub": "user_oidc",
        "iss": "https://identity.example/realms/anum",
        "aud": "anum-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "tenant_id": "tenant_a",
        "workspace_id": "workspace_a",
        "realm_access": {"roles": ["owner", "offline_access"]},
    }


def test_oidc_validator_verifies_signature_scope_and_roles() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(token_payload(), private_key, algorithm="RS256")
    validator = OidcValidator(
        "https://identity.example/realms/anum",
        "anum-api",
        jwks_client=StaticJwksClient(private_key.public_key()),  # type: ignore[arg-type]
    )

    claims = asyncio.run(validator.validate(token))

    assert claims.subject == "user_oidc"
    assert claims.tenant_id == "tenant_a"
    assert claims.workspace_id == "workspace_a"
    assert "owner" in claims.roles


def test_oidc_validator_rejects_wrong_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = token_payload()
    payload["aud"] = "another-api"
    token = jwt.encode(payload, private_key, algorithm="RS256")
    validator = OidcValidator(
        "https://identity.example/realms/anum",
        "anum-api",
        jwks_client=StaticJwksClient(private_key.public_key()),  # type: ignore[arg-type]
    )

    with pytest.raises(jwt.InvalidAudienceError):
        asyncio.run(validator.validate(token))
