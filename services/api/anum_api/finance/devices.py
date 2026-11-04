"""One iPhone device enrollment for every integration (docs/iphone-integrations.md).

The device tokens of the iPhone SMS integration (``finance_sms_devices``, docs/finance-sms.md)
are the one credential the owner's iPhone Shortcuts use. Each token carries **scopes**: the
integrations it may send to. This module is the shared layer other modules call:

* :data:`DEVICE_SCOPES` is every scope a token may hold (``finance.sms``, ``finance.wallet``,
  ``vehicle``, ``attendance``, ``documents``, ``purchases``).
* :class:`Integration` and :func:`register_integration`: a module registers its iPhone
  integration (key, scope, the permission its owner must hold, rate limits, its ingest address
  and an optional synthetic check). Settings, Integrations, iPhone lists the registered ones;
  a scope with no registered integration answers 404 ``integration_unavailable``, so a token can
  never reach a module that does not exist yet.
* :func:`authorize_scope` resolves ``Authorization: Bearer <device token>`` for one scope: the
  token's owner in their own tenant, workspace and user scope, the secret compared in constant
  time with the stored hash, the device active, the scope on the token, the owner still a member
  holding the integration's permission, the workspace not archived, the integration switched on,
  and the per-device, per-scope rate limit. It records an event (codes only) that drives the
  last connection and failed events on the iPhone page.
* :class:`ScopedDeviceAuthority` adapts it to the ``DeviceAuthority`` seam a module exposes
  (``anum_api.attendance.devices``); :func:`plug_module_authorities` registers it with every
  module seam that is installed.

Tokens stay hashed (SHA-256 of a 256-bit secret), are shown once, can be rotated, paused and
revoked, are refused over plain HTTP (:func:`require_https`) and never appear in a URL, a log or
an audit row. Every function here blocks on the database: call it from a worker thread (FastAPI's
thread pool for ``def`` routes, ``asyncio.to_thread`` from ``async def``), never on the event loop.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request

from ..authorization import Permission
from ..identity import is_local_environment
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings as app_settings

logger = logging.getLogger("anum_api.finance.devices")

DEVICE_SCOPES = ("finance.sms", "finance.wallet", "vehicle", "attendance", "documents", "purchases")
FINANCE_SCOPES = frozenset({"finance.sms", "finance.wallet"})
EVENT_RETENTION = timedelta(days=30)
MODULE_SEAMS = ("anum_api.attendance.devices", "anum_api.vault.devices", "anum_api.vehicles.devices", "anum_api.purchases.devices")


class DeviceError(HTTPException):
    """A refusal with a plain message and a stable code (never the token or a body)."""

    def __init__(self, status_code: int, message: str, code: str, retry_after: int | None = None) -> None:
        super().__init__(status_code=status_code, detail=message, headers={"Retry-After": str(retry_after)} if retry_after else None)
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class Integration:
    """One iPhone integration. ``key`` is stable (it names the switch and the events)."""

    key: str
    scope: str
    permission: Permission
    ingest_path: str
    method: str = "POST"
    per_minute: int = 30
    per_day: int = 1_000
    default_on: bool = False
    # A synthetic check run from the iPhone page: (context) -> list of (check, passed). It must record nothing.
    self_check: Callable[[TenantContext], list[tuple[str, bool]]] | None = None
    order: int = 100
    body_fields: tuple[str, ...] = field(default_factory=tuple)


_integrations: dict[str, Integration] = {}


def register_integration(integration: Integration) -> None:
    if integration.scope not in DEVICE_SCOPES:
        raise ValueError(f"unknown device scope {integration.scope}")
    _integrations[integration.key] = integration


def integrations() -> list[Integration]:
    return sorted(_integrations.values(), key=lambda i: (i.order, i.key))


def integration(key: str) -> Integration | None:
    return _integrations.get(key)


def integration_for_scope(scope: str) -> Integration | None:
    return next((i for i in integrations() if i.scope == scope), None)


def require_https(request: Request) -> None:
    """Device routes answer only over HTTPS (local and test environments excepted)."""
    if is_local_environment(app_settings.environment):
        return
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if request.url.scheme != "https" and forwarded != "https":
        raise DeviceError(403, "Use the https address.", "https_required")


def has_scope(scopes: list[str], scope: str) -> bool:
    """The generic scope check: an exact match on a known scope."""
    return scope in DEVICE_SCOPES and scope in scopes


# -- switches -----------------------------------------------------------------------------------


def switch_on(sms_settings: Any, item: Integration) -> bool:
    """Whether the owner turned this integration on. SMS forwarding is the SMS settings' own switch;
    Apple Wallet also needs it (the payments land in the same ledger)."""
    if item.key == "finance.sms":
        return bool(sms_settings.enabled)
    stored = (sms_settings.integration_switches or {}).get(item.key)
    on = item.default_on if stored is None else bool(stored)
    if item.scope in FINANCE_SCOPES:
        return on and bool(sms_settings.enabled)
    return on


# -- events -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DevicePrincipal:
    """Who sent a device request: the owner's context (with their current role) and the device."""

    context: TenantContext
    device_id: str | None
    via: str = "device_token"


def record_event(context: TenantContext, device_id: str, integration_key: str, scope: str, outcome: str, code: str | None = None,
                 *, now: datetime | None = None) -> None:
    """One event row (codes only) in its own short unit of work. Never raises."""
    from .links_store import DeviceEvent, open_link_stores

    now = now or utc_now()
    try:
        with open_link_stores(context) as (_finance, _sms, links):
            links.add_event(DeviceEvent(id=new_id("fin_devev"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                        owner_user_id=context.user_id, device_id=device_id, integration=integration_key, scope=scope,
                                        outcome=outcome, code=code, created_at=now))
            links.purge_events(context, now - EVENT_RETENTION)
    except Exception:
        logger.exception("finance_device_event_not_recorded")


def report_failure(context: TenantContext, device_id: str | None, scope: str, code: str) -> None:
    """A module refused or failed a request after the token was accepted: a ``failed`` event for the iPhone page."""
    item = integration_for_scope(scope)
    if item is not None and device_id:
        record_event(context, device_id, item.key, scope, "failed", code[:40])


def authorize_scope(authorization: str | None, scope: str, *, now: datetime | None = None, outcome: str = "ok") -> DevicePrincipal:
    """The device's owner for a token holding ``scope`` (docs/iphone-integrations.md#checks).

    Raises :class:`DeviceError`: 401 (no, unknown, revoked token or a former member), 403 (lacks the
    scope or the permission), 404 (no such integration), 409 (paused, switched off, archived), 429
    (rate limit, with ``Retry-After``). An accepted request is recorded as an ``ok`` event; a refusal
    after the token was matched is recorded as ``denied``."""
    from . import sms_service as svc
    from .sms_store import DeviceStatus, open_sms_stores

    now = now or utc_now()
    if scope not in DEVICE_SCOPES:
        raise DeviceError(404, "This integration is not available.", "integration_unavailable")
    item = integration_for_scope(scope)
    if item is None:
        raise DeviceError(404, "This integration is not available in ANUM yet.", "integration_unavailable")
    try:
        context, device_id, secret = _owner(authorization, item)
    except svc.IngestError as exc:
        raise DeviceError(exc.status_code, exc.message, exc.code, exc.retry_after) from None
    refusal: DeviceError | None = None
    with open_sms_stores(context) as (_finance, store):
        try:
            device = svc.check_device(store.get_device(context, device_id), secret, scope)
        except svc.IngestError as exc:
            if exc.code not in ("paused", "scope_missing"):
                raise DeviceError(exc.status_code, exc.message, exc.code, exc.retry_after) from None  # unknown token or secret: nothing recorded
            # The secret matched but the device is paused or lacks the scope: shown under the integration's failed events.
            refusal = DeviceError(exc.status_code, exc.message, exc.code, exc.retry_after)
            device = None
        if device is not None:
            settings = svc.settings_for(store, context)
            device.last_seen_at = now
            if not switch_on(settings, item):
                refusal = DeviceError(409, "This iPhone integration is off. Turn it on in ANUM under Settings, Integrations, iPhone.", "disabled")
            if refusal is None:
                from .links_store import links_store_for

                links = links_store_for(store)
                if links.count_events(context, device.id, scope, now - timedelta(minutes=1)) >= item.per_minute:
                    refusal = DeviceError(429, "Too many requests from this device. Try again in a minute.", "rate_limited", retry_after=60)
                elif links.count_events(context, device.id, scope, now - timedelta(days=1)) >= item.per_day:
                    refusal = DeviceError(429, "This device reached its daily limit for this integration.", "rate_limited", retry_after=3600)
            if refusal is not None:
                device.last_error, device.last_error_at = refusal.code, now
            else:
                device.last_ok_at, device.last_error, device.last_error_at = now, None, None
            if device.status == DeviceStatus.ACTIVE:
                store.save_device(device)
    record_event(context, device_id, item.key, scope, "denied" if refusal else outcome, refusal.code if refusal else None, now=now)
    if refusal is not None:
        raise refusal
    return DevicePrincipal(context=context, device_id=device_id)


def _owner(authorization: str | None, item: Integration) -> tuple[TenantContext, str, str]:
    """The token's owner with their current role, checked for the integration's permission."""
    from ..dependencies import has_permission, workspace_archived
    from . import sms_service as svc

    if not authorization or not authorization.lower().startswith("bearer "):
        raise svc.IngestError(401, "Send the device token as Authorization: Bearer <token>.", "missing_token")
    parsed = svc.parse_token(authorization.split(" ", 1)[1])
    if parsed is None:
        raise svc.IngestError(401, svc.UNKNOWN, "invalid_token")
    context, device_id, secret = parsed
    context = context.model_copy(update={"roles": svc._membership_roles(context)})
    if not has_permission(context, item.permission):
        raise svc.IngestError(403, "The person this device belongs to can no longer use this integration (their role changed).", "not_allowed")
    if workspace_archived(context):
        raise svc.IngestError(409, "This workspace is archived, so it accepts nothing new.", "archived")
    return context, device_id, secret


class ScopedDeviceAuthority:
    """The ``DeviceAuthority`` a module seam expects: ``None`` for a header that is not a device token."""

    def authorize(self, authorization: str | None, scope: str) -> DevicePrincipal | None:
        from . import sms_service as svc

        token = (authorization or "").split(" ", 1)[1] if (authorization or "").lower().startswith("bearer ") else ""
        if not token.startswith(f"{svc.DEVICE_PREFIX}."):
            return None
        return authorize_scope(authorization, scope)


def plug_module_authorities() -> list[str]:
    """Register :class:`ScopedDeviceAuthority` with every installed module seam; returns their names."""
    plugged = []
    for name in MODULE_SEAMS:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name in (name, name.rsplit(".", 1)[0]):
                continue
            raise
        register = getattr(module, "register_device_authority", None)
        if callable(register):
            register(ScopedDeviceAuthority())
            plugged.append(name)
    from .links import plug_vault_links

    plug_vault_links()
    return plugged


# -- the finance integrations -------------------------------------------------------------------


def _sms_check(context: TenantContext) -> list[tuple[str, bool]]:
    """Parse a synthetic message and look at the settings: records nothing."""
    from . import sms_parser as P
    from .sms_store import open_sms_stores
    from . import sms_service as svc

    reading = P.parse("anumtest", "SYNTHETIC. Purchase of SAR 10.00 at ANUM TEST with card **0000")
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
    return [("reader", reading.amount is not None), ("safety_check", settings.self_test_passed_at is not None), ("switched_on", settings.enabled)]


def _wallet_check(context: TenantContext) -> list[tuple[str, bool]]:
    from . import wallet as W
    from .sms_store import open_sms_stores
    from . import sms_service as svc

    try:
        amount_ok = W.parse_amount("SAR 10.00").amount > 0
    except W.WalletError:
        amount_ok = False
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
    return [("amount_reader", amount_ok), ("cards_mapped", bool(settings.wallet_cards)), ("switched_on", settings.enabled)]


register_integration(Integration(key="finance.sms", scope="finance.sms", permission=Permission.FINANCE_MANAGE, ingest_path="/api/v1/finance/sms/ingest",
                                 per_minute=30, per_day=1_000, default_on=False, self_check=_sms_check, order=10, body_fields=("message", "sender")))
register_integration(Integration(key="finance.wallet", scope="finance.wallet", permission=Permission.FINANCE_MANAGE,
                                 ingest_path="/api/v1/finance/wallet/ingest", per_minute=30, per_day=1_000, default_on=True,
                                 self_check=_wallet_check, order=20, body_fields=("source", "amount", "merchant", "card", "name")))

# Work attendance (docs/attendance.md#iphone-shortcuts): arrive and leave automations. Its route reads the
# token through ``anum_api.attendance.devices``, which ``plug_module_authorities`` connects.
register_integration(Integration(key="attendance.location", scope="attendance", permission=Permission.ATTENDANCE_USE,
                                 ingest_path="/api/v1/attendance/device/events", per_minute=10, per_day=200, default_on=False, order=30,
                                 body_fields=("event", "site", "occurred_at", "idempotency_key")))

# Document vault (docs/documents.md#scanning-from-an-iphone): "Scan a document", Share Sheet and Files uploads.
# Its upload route reads shared tokens through ``anum_api.vault.devices`` (its own older tokens keep working).
register_integration(Integration(key="documents.scan", scope="documents", permission=Permission.DOCUMENT_WRITE,
                                 ingest_path="/api/v1/documents/device/upload", per_minute=10, per_day=200, default_on=False, order=40,
                                 body_fields=("file",)))


__all__ = [
    "DEVICE_SCOPES",
    "DeviceError",
    "DevicePrincipal",
    "Integration",
    "ScopedDeviceAuthority",
    "authorize_scope",
    "has_scope",
    "integration",
    "integration_for_scope",
    "integrations",
    "plug_module_authorities",
    "record_event",
    "register_integration",
    "report_failure",
    "require_https",
    "switch_on",
]
