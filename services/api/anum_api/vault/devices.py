"""iPhone Shortcut tokens for document scanning (docs/documents.md#scanning-from-an-iphone).

A token is shown once and never stored: the vault keeps its SHA-256 and the last four characters.
It names its tenant, workspace, owner and device id, so the API finds the device row in the owner's
own RLS context; it carries the ``documents`` scope only and works only on
``POST /api/v1/documents/device/upload``. The owner must still be an active member who may add
documents: a token stops working when its owner leaves or becomes a viewer.

Interface note (owner spec, five modules): PR #364 adds finance device tokens and the shared
"Settings > Integrations > iPhone" page will generalise them with per-integration scopes. Until
that foundation merges, the vault keeps its own ``vault_devices`` table behind
:class:`DeviceTokens` with the same token format (id plus a 256-bit secret, SHA-256 at rest,
constant-time comparison); the foundation PR moves these rows to the shared table and points
:func:`device_tokens` at it, without changing the Shortcut or the upload endpoint.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
from collections import deque
from typing import Protocol

from ..schemas import TenantContext
from .models import DeviceRecord

PREFIX = "anumdoc"
SCOPE = "documents"
SAFE_ID = re.compile(r"vdev_[0-9a-f]{32}")
UNKNOWN = "This device token is not valid. Create a new one in ANUM under Document vault, Settings."


class DeviceRefused(Exception):
    def __init__(self, status_code: int, message: str, code: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_after = retry_after


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str | None:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mint(context: TenantContext, device_id: str) -> tuple[str, str, str]:
    """``(token, hash to store, hint)``. The token is shown once."""
    if not SAFE_ID.fullmatch(device_id):
        raise ValueError("a device id that cannot be part of a token")
    secret = secrets.token_urlsafe(32)
    token = ".".join((PREFIX, _b64(context.tenant_id), _b64(context.workspace_id), _b64(context.user_id), device_id, secret))
    return token, secret_hash(secret), token[-4:]


def parse_token(token: str) -> tuple[TenantContext, str, str] | None:
    parts = (token or "").strip().split(".")
    if len(parts) != 6 or parts[0] != PREFIX or not SAFE_ID.fullmatch(parts[4]) or not (30 <= len(parts[5]) <= 100):
        return None
    tenant, workspace, user = (_unb64(part) for part in parts[1:4])
    if not tenant or not workspace or not user or len(tenant) > 80 or len(workspace) > 80 or len(user) > 160:
        return None
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=[]), parts[4], parts[5]


def check_device(device: DeviceRecord | None, secret: str) -> DeviceRecord:
    # One answer for an unknown device and a wrong secret.
    if device is None or not hmac.compare_digest(device.token_hash, secret_hash(secret)):
        raise DeviceRefused(401, UNKNOWN, "invalid_token")
    if device.status == "revoked":
        raise DeviceRefused(401, "This device was disconnected. Create a new token in ANUM under Document vault, Settings.", "revoked")
    if SCOPE not in device.scopes:
        raise DeviceRefused(403, "This token may not add documents. Create a token with the documents permission.", "scope_missing")
    return device


class DeviceTokens(Protocol):
    """What the upload endpoint needs from a device credential store (see the module note)."""

    def find(self, context: TenantContext, device_id: str) -> DeviceRecord | None: ...
    def seen(self, context: TenantContext, device: DeviceRecord, *, error: str | None = None, uploaded: bool = False) -> None: ...


class HourlyCap:
    """Uploads per device in the last hour, in this process."""

    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = {}

    def take(self, key: str, per_hour: int, now: float | None = None) -> int | None:
        """None when allowed, else the seconds until the next upload is allowed."""
        moment = time.monotonic() if now is None else now
        calls = self._calls.setdefault(key, deque())
        while calls and moment - calls[0] >= 3600:
            calls.popleft()
        if len(calls) >= per_hour:
            return max(1, int(3600 - (moment - calls[0])))
        calls.append(moment)
        return None

    def reset(self) -> None:
        self._calls.clear()


upload_cap = HourlyCap()


__all__ = ["PREFIX", "SCOPE", "DeviceRefused", "DeviceTokens", "HourlyCap", "check_device", "mint", "parse_token", "secret_hash", "upload_cap"]
