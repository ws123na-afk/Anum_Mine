"""Public organisation logos for email headers (docs/customer-organisations.md#public-logos).

A logo is a public brand asset, so its address is not tied to any secret: it is
content-addressed and stays valid through ``ANUM_SECRETS_KEY`` rotation.

* **Address.** ``/api/v1/public/logos/{org_public_id}/{sha256}.{png|jpg|webp}``: the
  organisation's stable public id (a hash of the tenant id, so the tenant id itself is not
  printed in emails) and the SHA-256 of the image bytes, which nobody can guess without
  the image. Served with no sign-in and immutable cache headers.
* **Versions.** Each logo is kept in object storage under its own hash. Uploading a new
  logo publishes a new address; old addresses keep serving the old image, so emails
  already sent never break.
* **Purge.** :func:`purge` deletes every version an organisation published (a manifest
  per organisation lists them; the object store cannot list by prefix). Deleting an
  organisation must call it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from threading import RLock

from fastapi import APIRouter, HTTPException, Response

logger = logging.getLogger("anum.public_logos")

router = APIRouter(prefix="/api/v1/public/logos", tags=["organisations"])

PATH_PREFIX = "/api/v1/public/logos/"
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
CONTENT_TYPES = {value: key for key, value in EXTENSIONS.items()}
_ORG_ID = re.compile(r"^[0-9a-f]{32}$")
_NAME = re.compile(r"^([0-9a-f]{64})\.(png|jpg|webp)$")
_lock = RLock()


def _storage():  # type: ignore[no-untyped-def]
    from .files import file_store

    return file_store.storage


def org_public_id(tenant_id: str) -> str:
    """Stable for the organisation's lifetime and independent of every secret."""
    return hashlib.sha256(f"anum-public-logo:{tenant_id}".encode()).hexdigest()[:32]


def _key(public_id: str, name: str) -> str:
    return f"public-logos/{public_id}/{name}"


def _manifest_key(public_id: str) -> str:
    return f"public-logos/{public_id}/manifest.json"


def _read_manifest(public_id: str) -> list[str]:
    try:
        names = json.loads(_storage().get(_manifest_key(public_id)))
    except (FileNotFoundError, ValueError):
        return []
    return [str(name) for name in names if _NAME.match(str(name))] if isinstance(names, list) else []


def publish(tenant_id: str, content: bytes, content_type: str) -> str:
    """Store this logo version (idempotent) and return its public path."""
    extension = EXTENSIONS.get(content_type)
    if extension is None:
        raise ValueError("unsupported logo type")
    public_id = org_public_id(tenant_id)
    name = f"{hashlib.sha256(content).hexdigest()}.{extension}"
    storage = _storage()
    with _lock:
        try:
            storage.get(_key(public_id, name))
        except FileNotFoundError:
            storage.put(_key(public_id, name), content, content_type)
        names = _read_manifest(public_id)
        if name not in names:
            storage.put(_manifest_key(public_id), json.dumps([*names, name]).encode(), "application/json")
    return f"{PATH_PREFIX}{public_id}/{name}"


def purge(tenant_id: str) -> int:
    """Delete every logo version the organisation published; the number deleted."""
    public_id = org_public_id(tenant_id)
    storage = _storage()
    with _lock:
        names = _read_manifest(public_id)
        for name in names:
            storage.delete(_key(public_id, name))
        storage.delete(_manifest_key(public_id))
    logger.info("public_logos_purged", extra={"anum_public_logos": {"event": "purged", "count": len(names)}})
    return len(names)


@router.get("/{public_id}/{name}")
def public_logo(public_id: str, name: str) -> Response:
    """One published logo version. No sign-in: a logo is a public brand asset."""
    match = _NAME.match(name)
    if not _ORG_ID.match(public_id) or match is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        content = _storage().get(_key(public_id, name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found") from None
    # The bytes must still be the ones the address names.
    if hashlib.sha256(content).hexdigest() != match.group(1):
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=content, media_type=CONTENT_TYPES[match.group(2)],
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'"},
    )
