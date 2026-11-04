"""Who sent an iPhone Shortcut event (docs/attendance.md#iphone-shortcuts).

The owner's plan is one device enrollment for every iPhone integration: the finance device
tokens of PR #364 widened with per-integration scopes (scope ``attendance`` here), managed on
Settings, Integrations, iPhone. That foundation is not on ``main`` yet, so this module is the
seam it plugs into:

* :class:`DeviceAuthority` resolves ``Authorization: Bearer <device token>`` to the device's
  owner (tenant, workspace, person, current role) and device id, and answers ``None`` for a
  token that is not a device token. The foundation PR calls :func:`register_device_authority`
  with its scope-aware implementation; nothing else in Attendance changes.
* Until then (and for any token that is not a device token) the request signs in exactly as
  every other API call does: the ordinary ANUM sign-in token, with the membership checked.
  ``X-Anum-Device`` may carry a short device label; it is cleaned and never trusted for access.

The device never sends coordinates: only the site the phone's own geofence matched.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Protocol

from fastapi import Header, Request

from ..dependencies import tenant_context
from ..schemas import TenantContext

DEVICE_LABEL = re.compile(r"[^A-Za-z0-9._ -]")
SCOPE = "attendance"


@dataclass(frozen=True)
class DevicePrincipal:
    context: TenantContext
    device_id: str | None
    via: str  # "device_token" (a scoped device token) or "session" (the ordinary sign-in)


class DeviceAuthority(Protocol):
    def authorize(self, authorization: str | None, scope: str) -> DevicePrincipal | None:
        """The device's owner for a device token holding ``scope``; ``None`` when the header is
        not a device token at all; raises ``fastapi.HTTPException`` (401, 403, 409) for a device
        token that is unknown, revoked, paused or lacks the scope."""
        ...


_authority: DeviceAuthority | None = None


def register_device_authority(authority: DeviceAuthority | None) -> None:
    global _authority
    _authority = authority


def device_label(value: str | None) -> str | None:
    cleaned = DEVICE_LABEL.sub("", value or "").strip()[:80]
    return cleaned or None


async def device_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
    x_anum_device: str | None = Header(default=None),
) -> DevicePrincipal:
    authority = _authority
    if authority is not None:
        # Token checks read the database: in a worker thread, never on the event loop.
        principal = await asyncio.to_thread(authority.authorize, authorization, SCOPE)
        if principal is not None:
            return principal
    context = await tenant_context(request, authorization, x_tenant_id, x_workspace_id, x_user_id, x_user_roles)
    return DevicePrincipal(context=context, device_id=device_label(x_anum_device), via="session")
