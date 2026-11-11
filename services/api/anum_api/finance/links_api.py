"""Routes for finance links, business allocations and Settings, Integrations, iPhone.

docs/finance-links.md#api and docs/iphone-integrations.md#api.

* ``/api/v1/finance/links`` and ``/api/v1/finance/allocations``: the owner's own rows
  (``finance:read`` reads, ``finance:manage`` changes; owner-only RLS).
* ``/api/v1/business/ledger-allocations``: what a business workspace's members see
  (``business:read`` in that workspace): allocation rows only, never the personal ledger.
* ``/api/v1/integrations/iphone``: devices, scopes, per-integration switches, last connection,
  failed events and synthetic tests; ``POST /api/v1/integrations/iphone/ping`` is the one route
  that takes a device token instead of a session.

Audit rows carry ids and codes only: never an amount, a merchant, a token or a message.
Every route is a plain ``def`` (FastAPI's thread pool), never on the event loop.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import devices as D
from . import links as K
from . import sms_parser as P
from . import sms_service as svc
from .api import _audit, _manage, _read
from .links_store import DIMENSIONS, BusinessAllocation, FinanceLink, LinkStatus, open_link_stores, open_workspace_allocations
from .sms_store import DeviceStatus, SmsDevice, open_sms_stores

logger = logging.getLogger("anum_api.finance.links")
router = APIRouter(tags=["finance"])

MAX_DEVICES = 5


def _error(exc: K.LinkError) -> JSONResponse:
    """A refusal the page and the Shortcut can act on: ``detail`` (plain words), ``code`` and any figures."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})


def refusals(route: Callable[..., Any]) -> Callable[..., Any]:
    """Turn :class:`links.LinkError` (and the database's own amount check) into a JSON refusal."""

    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return route(*args, **kwargs)
        except K.LinkError as exc:
            return _error(exc)
        except DBAPIError as exc:
            # The deferred check refused the write at commit (a race the owner's lock should already prevent).
            if "exceed the transaction amount" in str(exc.orig):
                return _error(K.LinkError(409, "This payment is already linked up to its amount.", "over_allocated"))
            raise

    return wrapper


# -- links ----------------------------------------------------------------------------------------


class LinkView(BaseModel):
    id: str
    canonical_id: str
    dimension: str
    role: str
    target_type: str
    target_id: str
    target_workspace_id: str | None
    amount: Decimal | None
    currency: str | None
    status: LinkStatus
    status_reason: str | None
    source_event_ids: list[str]
    document_id: str | None
    created_by: str
    created_at: Any
    updated_at: Any
    voided_at: Any = None
    version: int


class LinkPage(BaseModel):
    items: list[LinkView]
    total: int
    limit: int
    offset: int


class LinkResult(BaseModel):
    link: LinkView
    replayed: bool = False


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=80)
    dimension: Literal["vehicle", "purchase", "document", "trip", "project"]
    role: Literal["payment", "refund", "deposit", "evidence"] = "payment"
    target_type: str = Field(min_length=1, max_length=40)
    target_id: str = Field(min_length=1, max_length=80)
    amount: Decimal | None = None
    currency: str | None = Field(default=None, max_length=3)
    document_id: str | None = Field(default=None, max_length=80)


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)


class TransactionLinks(BaseModel):
    canonical_id: str
    live: bool
    amounts: dict[str, Decimal]  # what may be linked per currency
    remaining: dict[str, dict[str, Decimal]]  # dimension -> currency -> left to link
    links: list[LinkView]


def _view(link: FinanceLink) -> LinkView:
    return LinkView(**link.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "idem_key"}))


@router.get("/api/v1/finance/links", response_model=LinkPage)
@refusals
def list_links(canonical_id: str | None = Query(default=None, max_length=80), dimension: str | None = Query(default=None, max_length=20),
               target_type: str | None = Query(default=None, max_length=40), target_id: str | None = Query(default=None, max_length=80),
               status_filter: LinkStatus | None = Query(default=None, alias="status"), limit: int = Query(default=50, ge=1, le=200),
               offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> LinkPage:
    _read(context)
    with open_link_stores(context) as (_finance, _sms, links):
        rows, total = links.list_links(context, canonical_id=canonical_id, dimension=dimension, target_type=target_type, target_id=target_id,
                                       status=status_filter.value if status_filter else None, limit=limit, offset=offset)
    return LinkPage(items=[_view(l) for l in rows], total=total, limit=limit, offset=offset)


@router.get("/api/v1/finance/links/transactions/{canonical_id}", response_model=TransactionLinks)
@refusals
def transaction_links(canonical_id: str, context: TenantContext = Depends(tenant_context)) -> TransactionLinks:
    """A payment's links and how much of it is left to link in each dimension."""
    _read(context)
    with open_link_stores(context) as (_finance, store, links):
        chain = store.chain(context, canonical_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Transaction not found. It may have been deleted.")
        live = links.live_links(context, canonical_id)
        rows, _ = links.list_links(context, canonical_id=canonical_id, dimension=None, target_type=None, target_id=None, status=None, limit=200, offset=0)
    caps = K.capacity(chain)
    remaining = {d: {c: max(cap - K.used(live, d, c), Decimal(0)) for c, cap in caps.items()} for d in DIMENSIONS}
    return TransactionLinks(canonical_id=canonical_id, live=K.live_entry(chain) is not None, amounts=caps, remaining=remaining,
                            links=[_view(l) for l in rows])


class TransferSide(BaseModel):
    transaction_id: str | None
    account_id: str | None
    account_name: str | None
    posted_on: Any = None
    amount: Decimal | None
    currency: str | None
    description: str | None


class TransferPair(BaseModel):
    """An own transfer this payment is one side of (docs/finance.md#own-transfers): the pair's link and the other side."""

    paired: bool
    link_id: str | None = None
    link_version: int | None = None
    role: Literal["out", "in"] | None = None
    other: TransferSide | None = None


@router.get("/api/v1/finance/links/transfer", response_model=TransferPair)
@refusals
def transfer_pair(transaction_id: str = Query(min_length=1, max_length=80), context: TenantContext = Depends(tenant_context)) -> TransferPair:
    """Whether this transaction is paired as an own transfer, and with which row. Unpairing is ``POST /finance/links/{link_id}/void``."""
    from .commitments import TRANSFER_DIMENSION

    _read(context)
    with open_link_stores(context) as (finance, sms, links):
        row = finance.get_transaction(context, transaction_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Transaction not found. It may have been deleted.")
        entry = sms.entry_for_transaction(context, row.id)
        if entry is None:
            return TransferPair(paired=False)
        canonical = entry.canonical_id or entry.id
        outgoing, _ = links.list_links(context, canonical_id=canonical, dimension=TRANSFER_DIMENSION, target_type=None, target_id=None, status=None,
                                       limit=5, offset=0)
        incoming, _ = links.list_links(context, canonical_id=None, dimension=TRANSFER_DIMENSION, target_type=None, target_id=canonical, status=None,
                                       limit=5, offset=0)
        link = next((item for item in outgoing + incoming if item.status != LinkStatus.VOID), None)
        if link is None:
            return TransferPair(paired=False)
        role: Literal["out", "in"] = "out" if link.canonical_id == canonical else "in"
        other = K.live_entry(sms.chain(context, link.target_id if role == "out" else link.canonical_id))
        side = TransferSide(transaction_id=None, account_id=None, account_name=None, amount=None, currency=None, description=None)
        if other is not None:
            leg = next((p for p in other.postings if p.account_id), None)
            other_row = finance.get_transaction(context, leg.transaction_id) if leg is not None and leg.transaction_id else None
            account = finance.get_account(context, leg.account_id) if leg is not None and leg.account_id else None
            side = TransferSide(transaction_id=leg.transaction_id if leg else None, account_id=leg.account_id if leg else None,
                                account_name=account.name if account is not None else None, posted_on=other.posted_on,
                                amount=leg.amount if leg else None, currency=leg.currency if leg else None,
                                description=(other_row.description if other_row is not None else None) or other.merchant or other.description)
    return TransferPair(paired=True, link_id=link.id, link_version=link.version, role=role, other=side)


@router.post("/api/v1/finance/links", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def create_link(body: LinkInput, idempotency_key: str | None = Header(default=None, max_length=120),
                context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    request = K.LinkRequest(canonical_id=body.canonical_id, dimension=body.dimension, role=body.role, target_type=body.target_type,
                            target_id=body.target_id, amount=body.amount, currency=body.currency, document_id=body.document_id,
                            idempotency_key=idempotency_key)
    created = K.create(context, request)
    if not created.replayed:
        _audit(repository, context, "finance.link.create", created.link.id, {"dimension": created.link.dimension, "role": created.link.role})
    result = LinkResult(link=_view(created.link), replayed=created.replayed)
    return JSONResponse(status_code=200 if created.replayed else 201, content=result.model_dump(mode="json"))


@router.post("/api/v1/finance/links/{link_id}/void", response_model=LinkView)
@refusals
def void_link(link_id: str, body: VersionInput, context: TenantContext = Depends(tenant_context),
              repository: AnumRepository = Depends(repository_context)) -> LinkView:
    _manage(context)
    link = K.void(context, link_id, expected_version=body.version)
    _audit(repository, context, "finance.link.void", link_id, {"dimension": link.dimension})
    return _view(link)


@router.post("/api/v1/finance/links/{link_id}/confirm", response_model=LinkView)
@refusals
def confirm_link(link_id: str, body: VersionInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> LinkView:
    _manage(context)
    link = K.confirm(context, link_id, expected_version=body.version)
    _audit(repository, context, "finance.link.confirm", link_id, {"dimension": link.dimension})
    return _view(link)


# -- business allocations ---------------------------------------------------------------------------


class AllocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=80)
    workspace_id: str = Field(min_length=1, max_length=80)
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    category: str | None = Field(default=None, max_length=40)
    project_id: str | None = Field(default=None, max_length=80)
    memo: str | None = Field(default=None, max_length=400)
    treatment: Literal["owner_contribution", "reimbursable"] = "owner_contribution"


class AllocationView(BaseModel):
    id: str
    source_link_id: str
    source_ref: str
    allocated_by: str
    posted_on: Any
    amount: Decimal
    currency: str
    category: str
    project_id: str | None
    memo: str | None
    treatment: str
    status: str
    created_at: Any


class AllocationPage(BaseModel):
    items: list[AllocationView]
    total: int
    limit: int
    offset: int
    totals: dict[str, Decimal]  # per currency on this page set, never added across currencies


def _allocation_view(row: BusinessAllocation) -> AllocationView:
    return AllocationView(**row.model_dump(exclude={"tenant_id", "workspace_id", "source_workspace_id", "updated_at"}))


@router.post("/api/v1/finance/allocations", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def create_allocation(body: AllocationInput, idempotency_key: str | None = Header(default=None, max_length=120),
                      context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    created = K.allocate(context, canonical_id=body.canonical_id, workspace_id=body.workspace_id, amount=body.amount, currency=body.currency,
                         category=body.category, project_id=body.project_id, memo=body.memo, treatment=body.treatment,
                         idempotency_key=idempotency_key)
    if not created.replayed:
        _audit(repository, context, "finance.allocation.create", created.link.id, {"workspace": body.workspace_id})
    result = LinkResult(link=_view(created.link), replayed=created.replayed)
    return JSONResponse(status_code=200 if created.replayed else 201, content=result.model_dump(mode="json"))


@router.delete("/api/v1/finance/allocations/{link_id}", response_model=LinkView)
@refusals
def delete_allocation(link_id: str, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> LinkView:
    _manage(context)
    with open_link_stores(context) as (_finance, _sms, links):
        found = links.get_link(context, link_id)
    if found is None or found.dimension != "business" or found.status == LinkStatus.VOID:
        raise K.LinkError(404, "Allocation not found. It may have been removed.", "link_not_found")
    link = K.void(context, link_id, expected_version=None)
    _audit(repository, context, "finance.allocation.delete", link_id, {"workspace": link.target_workspace_id})
    return _view(link)


@router.get("/api/v1/business/ledger-allocations", response_model=AllocationPage)
@refusals
def business_allocations(currency: str | None = Query(default=None, min_length=3, max_length=3), limit: int = Query(default=50, ge=1, le=200),
                         offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> AllocationPage:
    """What members allocated to this business from their own payments: allocation rows only."""
    require_permission(context, Permission.BUSINESS_READ)
    with open_workspace_allocations(context) as store:
        rows, total = store.list_allocations(context, currency=currency.upper() if currency else None, limit=limit, offset=offset)
        totals = store.allocation_totals(context, currency=currency.upper() if currency else None)
    return AllocationPage(items=[_allocation_view(r) for r in rows], total=total, limit=limit, offset=offset, totals=totals)


# -- Settings, Integrations, iPhone -------------------------------------------------------------------


class DeviceView(BaseModel):
    id: str
    name: str
    status: DeviceStatus
    token_hint: str
    scopes: list[str]
    created_at: Any
    rotated_at: Any = None
    revoked_at: Any = None
    last_seen_at: Any = None
    last_ok_at: Any = None
    last_error: str | None = None
    last_error_at: Any = None


class IntegrationView(BaseModel):
    key: str
    scope: str
    enabled: bool
    allowed: bool  # the caller holds the integration's permission
    ingest_path: str
    method: str
    body_fields: list[str]
    per_minute: int
    per_day: int
    devices_with_scope: int
    last_connection_at: Any = None
    last_ok_at: Any = None
    failed_7d: int = 0
    needs_safety_check: bool = False


class IphoneOverview(BaseModel):
    devices: list[DeviceView]
    integrations: list[IntegrationView]
    scopes: list[str]  # every scope a token may hold
    max_devices: int
    safety_check_passed: bool
    ping_path: str


class NewDeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = Field(min_length=1, max_length=10)


class TokenView(BaseModel):
    device: DeviceView
    token: str  # shown once; ANUM keeps only its hash
    headers: dict[str, str]


class SwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class CheckView(BaseModel):
    check: str
    passed: bool


class TestResult(BaseModel):
    integration: str
    passed: bool
    checks: list[CheckView]


class EventView(BaseModel):
    id: str
    device_id: str
    outcome: str
    code: str | None
    created_at: Any


class EventPage(BaseModel):
    items: list[EventView]
    total: int
    limit: int
    offset: int


PING_PATH = "/api/v1/integrations/iphone/ping"


def _device_view(device: SmsDevice) -> DeviceView:
    return DeviceView(**device.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "token_hash", "message_count"}))


def _known(context: TenantContext) -> None:
    """Any active member may open the page: it shows only their own devices and the integrations they may use."""
    require_permission(context, Permission.INTEGRATION_READ)


def _require_scopes(context: TenantContext, scopes: list[str]) -> None:
    for scope in scopes:
        item = D.integration_for_scope(scope)
        if item is None:
            raise K.LinkError(404, "This integration is not available in ANUM yet.", "integration_unavailable")
        if not has_permission(context, item.permission):
            raise K.LinkError(403, "Your role cannot use this integration. A workspace owner or admin can.", "not_allowed")


@router.get("/api/v1/integrations/iphone", response_model=IphoneOverview)
@refusals
def iphone_overview(context: TenantContext = Depends(tenant_context)) -> IphoneOverview:
    _known(context)
    now = utc_now()
    with open_link_stores(context) as (_finance, store, links):
        settings = svc.settings_for(store, context)
        devices = store.list_devices(context)
        summary = {s.integration: s for s in links.event_summary(context, now - timedelta(days=7))}
    views = []
    for item in D.integrations():
        active = [d for d in devices if d.status == DeviceStatus.ACTIVE and item.scope in d.scopes]
        seen = summary.get(item.key)
        views.append(IntegrationView(
            key=item.key, scope=item.scope, enabled=D.switch_on(settings, item), allowed=has_permission(context, item.permission),
            ingest_path=item.ingest_path, method=item.method, body_fields=list(item.body_fields), per_minute=item.per_minute, per_day=item.per_day,
            devices_with_scope=len(active), last_connection_at=seen.last_at if seen else None, last_ok_at=seen.last_ok_at if seen else None,
            failed_7d=seen.failed_7d if seen else 0,
            needs_safety_check=item.scope in D.FINANCE_SCOPES and settings.self_test_passed_at is None))
    return IphoneOverview(devices=[_device_view(d) for d in devices], integrations=views, scopes=list(D.DEVICE_SCOPES), max_devices=MAX_DEVICES,
                          safety_check_passed=settings.self_test_passed_at is not None, ping_path=PING_PATH)


@router.post("/api/v1/integrations/iphone/devices", response_model=TokenView, status_code=status.HTTP_201_CREATED)
@refusals
def create_device(body: NewDeviceInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> TokenView:
    _known(context)
    scopes = sorted(set(body.scopes))
    if any(scope not in D.DEVICE_SCOPES for scope in scopes):
        raise K.LinkError(422, "Choose integrations from the list.", "invalid_scope")
    _require_scopes(context, scopes)
    now = utc_now()
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
        if set(scopes) & D.FINANCE_SCOPES and settings.self_test_passed_at is None:
            raise K.LinkError(409, "Run the finance safety check first (Finance, iPhone SMS).", "safety_check")
        if sum(1 for d in store.list_devices(context) if d.status != DeviceStatus.REVOKED) >= MAX_DEVICES:
            raise K.LinkError(409, f"You can connect up to {MAX_DEVICES} devices. Disconnect one you no longer use first.", "too_many_devices")
        device_id = new_id("fin_smsdev")
        token, digest, hint = svc.mint(context, device_id)
        device = SmsDevice(id=device_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                           name=P.normalize(body.name)[:60] or "iPhone", status=DeviceStatus.ACTIVE, token_hash=digest, token_hint=hint,
                           scopes=scopes, created_at=now)
        store.save_device(device)
    _audit(repository, context, "integrations.iphone.device.create", device_id, {"scopes": scopes})
    return TokenView(device=_device_view(device), token=token, headers={"Authorization": "Bearer <token>", "Content-Type": "application/json"})


@router.post("/api/v1/integrations/iphone/devices/{device_id}/{change}", response_model=None)
@refusals
def change_device(device_id: str, change: Literal["rotate", "pause", "resume", "revoke", "scopes"], body: dict[str, Any] | None = None,
                  context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Rotate (a new token, the old one stops), pause, resume, revoke, or change the scopes (``{"scopes": [...]}``).
    Pausing and revoking your own device always work; the others need each scope's permission."""
    _known(context)
    now = utc_now()
    token = None
    with open_sms_stores(context) as (_finance, store):
        device = store.get_device(context, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found. It may have been removed.")
        if device.status == DeviceStatus.REVOKED:
            raise K.LinkError(409, "This device is disconnected. Connect it again as a new device.", "revoked")
        if change in ("rotate", "resume"):
            _require_scopes(context, device.scopes)
        if change == "rotate":
            token, device.token_hash, device.token_hint = svc.mint(context, device.id)
            device.rotated_at, device.status = now, DeviceStatus.ACTIVE
        elif change == "pause":
            device.status = DeviceStatus.PAUSED
        elif change == "resume":
            device.status = DeviceStatus.ACTIVE
        elif change == "revoke":
            # The hash is replaced too, so no token for this device can ever match again.
            device.status, device.revoked_at, device.token_hash = DeviceStatus.REVOKED, now, svc.secret_hash(new_id("revoked"))
        else:
            scopes = sorted(set((body or {}).get("scopes") or []))
            if not scopes or len(scopes) > 10 or any(not isinstance(s, str) or s not in D.DEVICE_SCOPES for s in scopes):
                raise K.LinkError(422, "Choose integrations from the list.", "invalid_scope")
            added = [s for s in scopes if s not in device.scopes]
            _require_scopes(context, added)  # adding needs the permission; removing never does
            if set(added) & D.FINANCE_SCOPES and svc.settings_for(store, context).self_test_passed_at is None:
                raise K.LinkError(409, "Run the finance safety check first (Finance, iPhone SMS).", "safety_check")
            device.scopes = scopes
        store.save_device(device)
    _audit(repository, context, f"integrations.iphone.device.{change}", device_id, {"scopes": device.scopes} if change == "scopes" else None)
    if token is not None:
        return TokenView(device=_device_view(device), token=token, headers={"Authorization": "Bearer <token>", "Content-Type": "application/json"})
    return _device_view(device)


@router.put("/api/v1/integrations/iphone/integrations/{key}", response_model=IntegrationView)
@refusals
def switch_integration(key: str, body: SwitchInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> IntegrationView:
    _known(context)
    item = D.integration(key)
    if item is None:
        raise K.LinkError(404, "This integration is not available in ANUM yet.", "integration_unavailable")
    _require_scopes(context, [item.scope])
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
        updated = settings.model_copy(deep=True)
        if key == "finance.sms":
            if body.enabled and settings.self_test_passed_at is None:
                raise K.LinkError(409, "Run the finance safety check first (Finance, iPhone SMS).", "safety_check")
            updated.enabled = body.enabled
        else:
            updated.integration_switches = {**settings.integration_switches, key: body.enabled}
        updated.version, updated.updated_at = settings.version + 1, utc_now()
        if not store.save_settings(context, updated, expected_version=settings.version):
            raise K.LinkError(409, "Settings changed at the same time. Reload and try again.", "stale")
    _audit(repository, context, "integrations.iphone.switch", key, {"enabled": body.enabled})
    overview = iphone_overview(context)
    return next(i for i in overview.integrations if i.key == key)


@router.post("/api/v1/integrations/iphone/integrations/{key}/test", response_model=TestResult)
@refusals
def test_integration(key: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TestResult:
    """A synthetic test from the page: checks the setup with made-up data and records nothing in the module."""
    _known(context)
    item = D.integration(key)
    if item is None:
        raise K.LinkError(404, "This integration is not available in ANUM yet.", "integration_unavailable")
    _require_scopes(context, [item.scope])
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
        devices = [d for d in store.list_devices(context) if d.status == DeviceStatus.ACTIVE and item.scope in d.scopes]
    checks = [("device_with_scope", bool(devices)), ("switched_on", D.switch_on(settings, item))]
    if item.self_check is not None:
        checks.extend((name, passed) for name, passed in item.self_check(context) if name != "switched_on")
    passed = all(ok for _, ok in checks)
    _audit(repository, context, "integrations.iphone.test", key, {"passed": passed})
    return TestResult(integration=key, passed=passed, checks=[CheckView(check=c, passed=p) for c, p in checks])


@router.get("/api/v1/integrations/iphone/integrations/{key}/events", response_model=EventPage)
@refusals
def integration_events(key: str, outcome: Literal["ok", "failed", "denied", "test", "problem"] | None = Query(default="problem"),
                       limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
                       context: TenantContext = Depends(tenant_context)) -> EventPage:
    """The person's own device events for one integration (default: the failed and refused ones)."""
    _known(context)
    if D.integration(key) is None:
        raise K.LinkError(404, "This integration is not available in ANUM yet.", "integration_unavailable")
    with open_link_stores(context) as (_finance, _sms, links):
        rows, total = links.list_events(context, integration=key, outcome=outcome, limit=limit, offset=offset)
    return EventPage(items=[EventView(id=e.id, device_id=e.device_id, outcome=e.outcome, code=e.code, created_at=e.created_at) for e in rows],
                     total=total, limit=limit, offset=offset)


class PingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration: str = Field(min_length=1, max_length=40)


@router.post(PING_PATH)
@refusals
def ping(request: Request, body: PingBody, authorization: str | None = Header(default=None)) -> Any:
    """The test a Shortcut runs on the phone: proves the address, the token, its scope and the switch; records only the test."""
    item = D.integration(body.integration)
    try:
        D.require_https(request)
        if item is None:
            raise D.DeviceError(404, "This integration is not available in ANUM yet.", "integration_unavailable")
        D.authorize_scope(authorization, item.scope, outcome="test")
    except D.DeviceError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code},
                            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None)
    return {"status": "test_ok", "integration": item.key, "text": "Connected. ANUM received the test and recorded nothing."}  # type: ignore[union-attr]


__all__ = ["router"]

