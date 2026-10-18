"""Ed25519 signing of skill versions (docs/skills.md#signing).

A skill version is signed over its canonical package digest: the SHA-256 of a canonical
JSON document of the fields agents act on (id, version, name, description, instructions,
required tools, risk). The signed message is ``SIGNATURE_CONTEXT`` followed by the digest's
lowercase hex, so a signature for a skill can never be replayed as a signature for anything
else ANUM signs.

Trust comes from three places, checked in this order:

* versions whose content is a skill shipped in this release's blueprints are first-party
  (``key_source == "first_party"``): the server already trusts its own code;
* operator-configured platform keys (``ANUM_SKILL_PLATFORM_KEYS``), for first-party skills
  signed outside ANUM;
* the tenant's trusted publisher keys, managed by organization administrators.

Verification runs every time a version is read, against the keys as they are now, so a
revoked or rotated key flags installed versions at once.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PACKAGE_FORMAT = "anum-skill-v1"
SIGNATURE_CONTEXT = b"anum-skill-signature-v1\n"
KEY_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{2,79}$"
PLATFORM_PUBLISHER = "ANUM platform"
FIRST_PARTY_SIGNER = "ANUM (shipped with this release)"


class PublisherKeyStatus(StrEnum):
    ACTIVE = "active"
    # Replaced by a newer key: still verifies what it signed before ``rotated_at``.
    ROTATED = "rotated"
    # Never trusted again: its versions cannot be installed and installed ones are flagged.
    REVOKED = "revoked"


class KeySource(StrEnum):
    TENANT = "tenant"
    PLATFORM = "platform"
    FIRST_PARTY = "first_party"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNSIGNED = "unsigned"
    INVALID = "invalid"
    REVOKED = "revoked"
    UNKNOWN_KEY = "unknown_key"


class PublisherKey(BaseModel):
    id: str = Field(pattern=KEY_ID_PATTERN)
    tenant_id: str | None = None
    publisher: str = Field(min_length=1, max_length=160)
    # Raw 32-byte Ed25519 public key, standard base64.
    public_key: str
    fingerprint: str
    source: KeySource = KeySource.TENANT
    status: PublisherKeyStatus = PublisherKeyStatus.ACTIVE
    created_by: str
    created_at: datetime
    rotated_at: datetime | None = None
    replaced_by: str | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None


class SkillSignature(BaseModel):
    key_id: str = Field(pattern=KEY_ID_PATTERN)
    # Standard base64 of the 64-byte Ed25519 signature.
    signature: str = Field(min_length=1, max_length=200)


class SkillVerification(BaseModel):
    status: VerificationStatus
    installable: bool
    digest: str
    key_id: str | None = None
    signer: str | None = None
    key_source: KeySource | None = None
    fingerprint: str | None = None
    signed_at: datetime | None = None
    detail: str


class SigningError(ValueError):
    """A key or signature that cannot be used; the message says what to fix."""


def canonical_package(
    *, skill_id: str, version: str, name: str, description: str, instructions: str,
    required_tools: Iterable[str], risk_level: str,
) -> bytes:
    """The exact bytes the digest covers: sorted keys, no whitespace, UTF-8, tools sorted and unique."""
    document = {
        "format": PACKAGE_FORMAT,
        "skill_id": skill_id,
        "version": version,
        "name": name,
        "description": description,
        "instructions": instructions,
        "required_tools": sorted(set(required_tools)),
        "risk_level": risk_level,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def package_digest(
    *, skill_id: str, version: str, name: str, description: str, instructions: str,
    required_tools: Iterable[str], risk_level: str,
) -> str:
    return hashlib.sha256(canonical_package(
        skill_id=skill_id, version=version, name=name, description=description,
        instructions=instructions, required_tools=required_tools, risk_level=risk_level,
    )).hexdigest()


def signing_message(digest: str) -> bytes:
    return SIGNATURE_CONTEXT + digest.encode("ascii")


def decode_public_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SigningError("The public key is not valid base64") from exc
    if len(raw) != 32:
        raise SigningError("The public key must be a raw 32-byte Ed25519 key in base64 (44 characters)")
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:  # pragma: no cover - any 32 bytes load; kept for safety
        raise SigningError("The public key is not an Ed25519 key") from exc
    return raw


def normalize_public_key(value: str) -> str:
    return base64.b64encode(decode_public_key(value)).decode("ascii")


def key_fingerprint(public_key: str) -> str:
    digest = hashlib.sha256(decode_public_key(public_key)).hexdigest()[:32]
    return ":".join(digest[i:i + 4] for i in range(0, len(digest), 4))


def signature_matches(public_key: str, digest: str, signature: str) -> bool:
    try:
        raw_signature = base64.b64decode(signature.strip(), validate=True)
        Ed25519PublicKey.from_public_bytes(decode_public_key(public_key)).verify(raw_signature, signing_message(digest))
    except (binascii.Error, ValueError, InvalidSignature, SigningError):
        return False
    return True


@lru_cache(maxsize=8)
def _parse_platform_keys(config: str) -> tuple[PublisherKey, ...]:
    from .schemas import utc_now

    keys: list[PublisherKey] = []
    for entry in (part.strip() for part in config.split(",")):
        if not entry:
            continue
        key_id, _, value = entry.partition("=")
        try:
            public_key = normalize_public_key(value)
            keys.append(PublisherKey(
                id=key_id.strip(), publisher=PLATFORM_PUBLISHER, public_key=public_key,
                fingerprint=key_fingerprint(public_key), source=KeySource.PLATFORM,
                created_by="operator", created_at=utc_now(),
            ))
        except (SigningError, ValueError):
            # Never echo the value; name the entry so the operator can fix it.
            logger.warning("Ignoring malformed ANUM_SKILL_PLATFORM_KEYS entry %r", key_id.strip()[:80])
    return tuple(keys)


def platform_keys() -> tuple[PublisherKey, ...]:
    """Operator-configured first-party keys (``ANUM_SKILL_PLATFORM_KEYS="key-id=BASE64,..."``)."""
    from .settings import settings

    return _parse_platform_keys(settings.skill_platform_keys)


def first_party_match(
    *, skill_id: str, version: str, name: str, description: str, instructions: str,
    required_tools: Sequence[str], risk_level: str,
) -> bool:
    """True when the content is a skill this release ships in its blueprints.

    Setup plans publish blueprint skills with only the tools this server has, so the tools
    may be a subset of the blueprint's; every other field must be identical.
    """
    from .setup_plans import load_blueprints

    for blueprint in load_blueprints().values():
        for skill in blueprint.skills:
            if (
                skill.skill_id == skill_id and skill.version == version and skill.name == name
                and skill.description == description and skill.instructions == instructions
                and skill.risk_level == risk_level and set(required_tools) <= set(skill.tools)
            ):
                return True
    return False


def verify(
    *, skill_id: str, version: str, name: str, description: str, instructions: str,
    required_tools: Sequence[str], risk_level: str, signature: SkillSignature | None,
    signed_at: datetime | None, keys: Iterable[PublisherKey],
) -> SkillVerification:
    """The verification status of one version against the trusted keys as they are now."""
    fields = {
        "skill_id": skill_id, "version": version, "name": name, "description": description,
        "instructions": instructions, "required_tools": required_tools, "risk_level": risk_level,
    }
    digest = package_digest(**fields)
    if first_party_match(**fields):
        return SkillVerification(
            status=VerificationStatus.VERIFIED, installable=True, digest=digest,
            signer=FIRST_PARTY_SIGNER, key_source=KeySource.FIRST_PARTY,
            detail="First-party skill: its content matches a skill shipped with this ANUM release.",
        )
    if signature is None:
        return SkillVerification(
            status=VerificationStatus.UNSIGNED, installable=False, digest=digest,
            detail="This version is not signed. An organization administrator signs it with a trusted publisher key before anyone can install it.",
        )
    key = next((item for item in keys if item.id == signature.key_id), None)
    base = {"digest": digest, "key_id": signature.key_id, "signed_at": signed_at}
    if key is None:
        return SkillVerification(
            status=VerificationStatus.UNKNOWN_KEY, installable=False, **base,
            detail=f"Signed with key {signature.key_id}, which is not a trusted publisher key in this organization.",
        )
    base |= {"signer": key.publisher, "key_source": key.source, "fingerprint": key.fingerprint}
    if key.status == PublisherKeyStatus.REVOKED:
        reason = f": {key.revoke_reason}" if key.revoke_reason else ""
        return SkillVerification(
            status=VerificationStatus.REVOKED, installable=False, **base,
            detail=f"Signed with key {key.id}, which an organization administrator revoked{reason}. Sign it again with a trusted key, or roll back to another signed version.",
        )
    if not signature_matches(key.public_key, digest, signature.signature):
        return SkillVerification(
            status=VerificationStatus.INVALID, installable=False, **base,
            detail="The signature does not match this version's content: it was changed after signing, or signed with a different key.",
        )
    if key.status == PublisherKeyStatus.ROTATED and key.rotated_at is not None and (signed_at is None or signed_at > key.rotated_at):
        return SkillVerification(
            status=VerificationStatus.INVALID, installable=False, **base,
            detail=f"Signed with key {key.id} after it was rotated. Sign it with the newer key {key.replaced_by or ''}".rstrip() + ".",
        )
    return SkillVerification(
        status=VerificationStatus.VERIFIED, installable=True, **base,
        detail=f"Signed by {key.publisher} with key {key.id}.",
    )
