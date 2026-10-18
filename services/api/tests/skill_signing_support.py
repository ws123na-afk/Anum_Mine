"""A test publisher key for signed skill versions (docs/skills.md#signing).

Private keys are derived at runtime from a plain label, never committed, so there is no key
material in the repository for gitleaks to find. Tests that publish a skill they also install
trust this key once per tenant (``trust``) and sign the payload (``signed``).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from anum_api.skill_signing import INTEGRATION_SIGNATURE_CONTEXT, integration_digest, package_digest, signing_message

TEST_KEY_ID = "test-publisher"
KEYS = "/api/v1/skills/publisher-keys"


def private_key(label: str = "example") -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(f"anum-test-key:{label}".encode()).digest())


def public_key(signer: Ed25519PrivateKey | None = None) -> str:
    raw = (signer or private_key()).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def digest_of(payload: dict[str, Any]) -> str:
    return package_digest(
        skill_id=payload["skill_id"], version=payload["version"], name=payload["name"],
        description=payload["description"], instructions=payload["instructions"],
        required_tools=payload.get("required_tools", []), risk_level=payload.get("risk_level", "low"),
    )


def sign(payload: dict[str, Any], signer: Ed25519PrivateKey | None = None, key_id: str = TEST_KEY_ID) -> dict[str, str]:
    signature = (signer or private_key()).sign(signing_message(digest_of(payload)))
    return {"key_id": key_id, "signature": base64.b64encode(signature).decode("ascii")}


def trust(client: Any, headers: dict[str, str], signer: Ed25519PrivateKey | None = None, key_id: str = TEST_KEY_ID) -> None:
    response = client.post(KEYS, headers=headers, json={"id": key_id, "publisher": "Test publisher", "public_key": public_key(signer)})
    assert response.status_code in (201, 409), response.text


def signed(client: Any, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """Trust the test key for the caller's tenant and return the payload signed with it."""
    trust(client, headers)
    return {**payload, "signature": sign(payload)}


def package_digest_of(payload: dict[str, Any]) -> str:
    return integration_digest(
        package_id=payload["id"], name=payload["name"], kind=payload["kind"], version=payload["version"],
        publisher=payload["publisher"], permissions=payload.get("permissions", []), regions=payload.get("regions", []),
    )


def sign_package(payload: dict[str, Any], signer: Ed25519PrivateKey | None = None, key_id: str = TEST_KEY_ID) -> dict[str, str]:
    signature = (signer or private_key()).sign(signing_message(package_digest_of(payload), INTEGRATION_SIGNATURE_CONTEXT))
    return {"key_id": key_id, "signature": base64.b64encode(signature).decode("ascii")}


def signed_package(client: Any, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """Trust the test key and return an integration package payload signed with it."""
    trust(client, headers)
    return {**payload, "signature": sign_package(payload)}
