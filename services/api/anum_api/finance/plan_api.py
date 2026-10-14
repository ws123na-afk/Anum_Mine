"""Routes for Finance stages 3 to 5 under ``/api/v1/finance`` (docs/finance.md#api).

* Safe-to-spend (``finance:read``) and its settings (``finance:manage`` to change).
* Guardian: the rules, their versions, the month's alerts and a dry-run evaluation
  (``finance:read``); proposing, confirming and rejecting a change (``finance:manage``).
  A change takes effect only when the person confirms it with the payload hash they were
  shown; the hash binds the change, the version it was made on and the exact rules.
* Connectors: the registry and the person's connections, exchange rates (``finance:read``);
  connecting, syncing, disconnecting and deleting, and typing a rate (``finance:manage``).

Every record is the caller's own (row policies on ``anum.user_id``). Audit records carry
ids, versions, rule codes and provider names only: never an amount, a rule's limits, a
description or a credential. Nothing here moves money.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..repository import AnumRepository
from ..dependencies import UnitsOfWork, repository_context, tenant_context, units_of_work
from ..schemas import TenantContext, new_id, utc_now
from . import fx
from .api import _audit, _clean, _invalid, _manage, _money, _not_found, _read
from .connectors import (
    ADAPTERS,
    PROVIDERS,
    Connection,
    ConnectionStatus,
    ConnectorKind,
    credential_hint,
    encrypt_credentials,
    is_read_only,
)
from .guardian import (
    PROPOSAL_TTL,
    ActionKind,
    Decision,
    GuardianAction,
    GuardianChange,
    GuardianPolicy,
    GuardianRules,
    gate,
    evaluate,
    loosening,
    payload_hash,
)
from .models import Category, CurrencyBuffer, FxRate, PlanSettings
from .money import MoneyError, normalize_currency, to_money
from .policy import GuardianAlerts, current_rules, ledger_state, month_alerts
from .safe_to_spend import SafeToSpend, build_safe_to_spend
from .store import MAX_CONNECTIONS, MAX_FX_RATES, FinanceStore, open_finance_store
from .summary import settings_for, today_in, zone
from .sync import SyncReport, sync_connection

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


# -- Stage 3: safe-to-spend ---------------------------------------------------------------------


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buffers: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    combine_currencies: bool = False
    fx_max_age_days: int = Field(default=7, ge=1, le=60)


@router.get("/safe-to-spend", response_model=SafeToSpend)
def safe_to_spend(context: TenantContext = Depends(tenant_context)) -> SafeToSpend:
    """What you can spend for the rest of this month and per day, line by line, per currency."""
    _read(context)
    with open_finance_store(context) as store:
        return build_safe_to_spend(store, context)


@router.get("/plan", response_model=PlanSettings)
def get_plan(context: TenantContext = Depends(tenant_context)) -> PlanSettings:
    _read(context)
    with open_finance_store(context) as store:
        return store.get_plan(context) or PlanSettings()


@router.put("/plan", response_model=PlanSettings)
def put_plan(
    body: PlanInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PlanSettings:
    _manage(context)
    buffers: dict[str, Decimal] = {}
    for row in body.buffers:
        try:
            currency = normalize_currency(str(row.get("currency") or ""))
        except MoneyError as exc:
            raise _invalid(f"Buffer: {exc}") from None
        if currency in buffers:
            raise _invalid(f"Buffer: {currency} is listed twice.")
        buffers[currency] = _money(row.get("amount"), currency, field=f"Buffer {currency}", positive=True)
    plan = PlanSettings(
        buffers=[CurrencyBuffer(currency=c, amount=a) for c, a in sorted(buffers.items())],
        combine_currencies=body.combine_currencies, fx_max_age_days=body.fx_max_age_days, updated_at=utc_now(),
    )
    with open_finance_store(context) as store:
        store.save_plan(context, plan)
    _audit(repository, context, "finance.plan.update", context.user_id, {"buffers": len(plan.buffers), "combine": plan.combine_currencies})
    return plan


# -- Stage 4: Guardian ----------------------------------------------------------------------------


class ChangeView(BaseModel):
    id: str
    base_version: int
    rules: GuardianRules
    proposed_by: Literal["person", "agent"]
    proposer: str
    reason: str | None
    payload_hash: str
    loosens: list[str]
    status: str
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class GuardianView(BaseModel):
    version: int
    rules: GuardianRules
    confirmed_at: datetime | None
    confirmed_by: str | None
    pending: list[ChangeView]
    kinds: list[str]


class VersionView(BaseModel):
    id: str
    version: int
    rules: GuardianRules
    change_id: str
    confirmed_by: str
    created_at: datetime


class VersionPage(BaseModel):
    items: list[VersionView]
    total: int
    limit: int
    offset: int


class ChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: dict[str, Any]
    reason: str | None = Field(default=None, max_length=500)


class ConfirmInput(BaseModel):
    """The change's ``payload_hash`` exactly as it was shown to you."""

    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActionKind = ActionKind.SPEND
    amount: str | int | Decimal
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    category: Category | None = None
    merchant: str | None = Field(default=None, max_length=200)
    at: datetime | None = None
    first_requested_at: datetime | None = None


class EvaluateView(BaseModel):
    version: int
    action: GuardianAction
    rules_decision: Decision
    decision: Decision
    note: str


def _change_view(change: GuardianChange) -> ChangeView:
    return ChangeView(**change.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"}))


def _expire(store: FinanceStore, context: TenantContext, changes: list[GuardianChange], now: datetime) -> list[GuardianChange]:
    kept = []
    for change in changes:
        if change.lapsed(now):
            change.status, change.decided_at = "expired", now
            store.save_change(change)
        kept.append(change)
    return kept


def parse_rules(raw: dict[str, Any]) -> GuardianRules:
    try:
        return GuardianRules.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "rules"
        raise _invalid(f"Rules, {where}: {first.get('msg', 'not valid')}".replace("Value error, ", "")) from None


def propose_change(
    store: FinanceStore, context: TenantContext, rules: GuardianRules, *, proposed_by: Literal["person", "agent"], proposer: str,
    reason: str | None, now: datetime,
) -> GuardianChange:
    """A pending change on the current version. An agent may not propose loosening."""
    current, version = current_rules(store, context)
    loosens = loosening(current, rules)
    if proposed_by == "agent" and loosens:
        raise PermissionError("Only you can loosen your own rules: change them under Finance, Guardian.")
    change_id = new_id("fin_gchg")
    change = GuardianChange(
        id=change_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        base_version=version, rules=rules, proposed_by=proposed_by, proposer=proposer[:160],
        reason=_clean(reason, 500) if reason else None,
        payload_hash=payload_hash(change_id=change_id, owner_user_id=context.user_id, base_version=version, rules=rules),
        loosens=loosens, created_at=now, expires_at=now + PROPOSAL_TTL,
    )
    pending = store.list_changes(context, status="pending", limit=50)
    if len(pending) >= 20:
        raise OverflowError("There are 20 changes waiting. Confirm or reject some first.")
    return store.save_change(change)


@router.get("/guardian", response_model=GuardianView)
def guardian(context: TenantContext = Depends(tenant_context)) -> GuardianView:
    """Your rules (current version) and the changes waiting for your confirmation."""
    _read(context)
    now = utc_now()
    with open_finance_store(context) as store:
        policy = store.get_policy(context)
        pending = [c for c in _expire(store, context, store.list_changes(context, status="pending", limit=50), now) if c.status == "pending"]
    return GuardianView(
        version=policy.version if policy else 0, rules=policy.rules if policy else GuardianRules(),
        confirmed_at=policy.created_at if policy else None, confirmed_by=policy.confirmed_by if policy else None,
        pending=[_change_view(c) for c in pending], kinds=[k.value for k in ActionKind],
    )


@router.get("/guardian/versions", response_model=VersionPage)
def guardian_versions(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> VersionPage:
    _read(context)
    with open_finance_store(context) as store:
        items, total = store.list_policies(context, limit=limit, offset=offset)
    return VersionPage(items=[VersionView(**p.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"})) for p in items],
                       total=total, limit=limit, offset=offset)


@router.get("/guardian/alerts", response_model=GuardianAlerts)
def guardian_alerts(
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), context: TenantContext = Depends(tenant_context),
) -> GuardianAlerts:
    _read(context)
    with open_finance_store(context) as store:
        return month_alerts(store, context, month)


@router.get("/guardian/changes", response_model=list[ChangeView])
def guardian_changes(
    status_: Literal["pending", "confirmed", "rejected", "expired", "superseded"] | None = Query(default=None, alias="status"),
    context: TenantContext = Depends(tenant_context),
) -> list[ChangeView]:
    _read(context)
    now = utc_now()
    with open_finance_store(context) as store:
        changes = _expire(store, context, store.list_changes(context, status=None, limit=100), now)
    return [_change_view(c) for c in changes if status_ is None or c.status == status_]


@router.post("/guardian/changes", response_model=ChangeView, status_code=status.HTTP_201_CREATED)
def create_change(
    body: ChangeInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ChangeView:
    """Propose new rules. Nothing changes until you confirm with the payload hash shown."""
    _manage(context)
    rules = parse_rules(body.rules)
    with open_finance_store(context) as store:
        try:
            change = propose_change(store, context, rules, proposed_by="person", proposer=context.user_id, reason=body.reason, now=utc_now())
        except OverflowError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    _audit(repository, context, "finance.guardian.propose", change.id, {"base_version": change.base_version, "loosens": change.loosens, "by": "person"})
    return _change_view(change)


@router.post("/guardian/changes/{change_id}/confirm", response_model=GuardianView)
def confirm_change(
    change_id: str,
    body: ConfirmInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> GuardianView:
    """Make a proposed change your rules. The hash must be the one you were shown, the change
    must still be pending and made on the current version (else nothing changes)."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        change = store.get_change(context, change_id)
        if change is None:
            raise _not_found("Change")
        if change.lapsed(now):
            change.status, change.decided_at = "expired", now
            store.save_change(change)
        if change.status != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"This change is {change.status}. Propose it again.")
        _, version = current_rules(store, context)
        expected = payload_hash(change_id=change.id, owner_user_id=context.user_id, base_version=change.base_version, rules=change.rules)
        if body.payload_hash != change.payload_hash or body.payload_hash != expected:
            raise HTTPException(status.HTTP_409_CONFLICT, "The confirmation does not match the change shown. Reload it and confirm again.")
        if change.base_version != version:
            change.status, change.decided_at = "superseded", now
            store.save_change(change)
            raise HTTPException(status.HTTP_409_CONFLICT, "Your rules changed since this was proposed. Review it again.")
        policy = GuardianPolicy(
            id=new_id("fin_gpol"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            version=version + 1, rules=change.rules, change_id=change.id, confirmed_by=context.user_id, created_at=now,
        )
        if not store.add_policy(policy):
            raise HTTPException(status.HTTP_409_CONFLICT, "Your rules changed at the same moment. Review the change again.")
        change.status, change.decided_at = "confirmed", now
        store.save_change(change)
        # Every other pending change was made on the old version: it no longer applies.
        for other in store.list_changes(context, status="pending", limit=100):
            other.status, other.decided_at = "superseded", now
            store.save_change(other)
    _audit(repository, context, "finance.guardian.confirm", change.id,
           {"version": policy.version, "loosens": change.loosens, "proposed_by": change.proposed_by})
    return guardian(context)


@router.post("/guardian/changes/{change_id}/reject", response_model=ChangeView)
def reject_change(
    change_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ChangeView:
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        change = store.get_change(context, change_id)
        if change is None:
            raise _not_found("Change")
        if change.status != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"This change is {change.status} already.")
        change.status, change.decided_at = "rejected", now
        store.save_change(change)
    _audit(repository, context, "finance.guardian.reject", change.id, {"proposed_by": change.proposed_by})
    return _change_view(change)


def build_action(store: FinanceStore, context: TenantContext, body: EvaluateInput, *, requested_by: Literal["person", "agent"]) -> GuardianAction:
    settings = settings_for(store, context)
    try:
        currency = normalize_currency(body.currency or settings.base_currency)
        amount = to_money(body.amount, currency)
    except MoneyError as exc:
        raise _invalid(str(exc)) from None
    if amount <= 0:
        raise _invalid("Amount: enter an amount above zero.")
    at = body.at or utc_now()
    if at.tzinfo is None:
        at = at.replace(tzinfo=zone(settings.timezone))
    first = body.first_requested_at
    if first is not None and first.tzinfo is None:
        first = first.replace(tzinfo=zone(settings.timezone))
    return GuardianAction(kind=body.kind, amount=amount, currency=currency, category=body.category,
                          merchant=_clean(body.merchant, 200), at=at, first_requested_at=first, requested_by=requested_by)


DRY_RUN_NOTE = "A check only: nothing was paid, sent or saved. ANUM does not move money."


def dry_run(store: FinanceStore, context: TenantContext, body: EvaluateInput, *, requested_by: Literal["person", "agent"]) -> EvaluateView:
    action = build_action(store, context, body, requested_by=requested_by)
    rules, version = current_rules(store, context)
    day = action.at.astimezone(zone(settings_for(store, context).timezone)).date()
    state = ledger_state(store, context, day)
    return EvaluateView(version=version, action=action, rules_decision=evaluate(action, rules, state),
                        decision=gate(action, rules, state), note=DRY_RUN_NOTE)


@router.post("/guardian/evaluate", response_model=EvaluateView)
def evaluate_action(body: EvaluateInput, context: TenantContext = Depends(tenant_context)) -> EvaluateView:
    """Dry run: what your rules, and the kernel, say about an action. Nothing happens."""
    _read(context)
    with open_finance_store(context) as store:
        return dry_run(store, context, body, requested_by="person")


# -- Stage 5: connectors --------------------------------------------------------------------------


class ProviderView(BaseModel):
    id: str
    kind: ConnectorKind
    name: str
    summary: str
    scopes: list[str]
    available: bool
    needs_credentials: bool
    credential_fields: list[str]
    sync_hours: int
    source_url: str | None
    terms: str | None
    owner_action: str | None


class ConnectionView(BaseModel):
    id: str
    provider: str
    provider_name: str
    kind: ConnectorKind
    label: str
    status: ConnectionStatus
    scopes: list[str]
    has_credentials: bool
    credential_hint: str | None
    enabled: bool
    next_sync_at: datetime
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_counts: dict[str, int]
    created_at: datetime
    updated_at: datetime


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=60)
    label: str | None = Field(default=None, max_length=120)
    # Write only: encrypted at once, never returned, logged or shown to an assistant.
    credentials: dict[str, str] | None = None


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None


class RateView(BaseModel):
    id: str
    base: str
    quote: str
    rate: Decimal
    as_of: date
    source: str
    source_url: str | None
    connection_id: str | None
    fetched_at: datetime


class RateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str = Field(min_length=3, max_length=3)
    quote: str = Field(min_length=3, max_length=3)
    rate: str | int | Decimal
    as_of: date


def _connection_view(connection: Connection) -> ConnectionView:
    spec = PROVIDERS.get(connection.provider)
    return ConnectionView(
        **connection.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "credentials_encrypted", "settings"}),
        provider_name=spec.name if spec else connection.provider, has_credentials=bool(connection.credentials_encrypted),
    )


def _own_connection(store: FinanceStore, context: TenantContext, connection_id: str) -> Connection:
    found = store.get_connection(context, connection_id)
    if found is None:
        raise _not_found("Connection")
    return found


@router.get("/connectors", response_model=list[ProviderView])
def connectors(context: TenantContext = Depends(tenant_context)) -> list[ProviderView]:
    """Every source ANUM knows, read only. Those not available need an owner action first."""
    _read(context)
    return [
        ProviderView(
            id=s.id, kind=s.kind, name=s.name, summary=s.summary, scopes=list(s.scopes), available=s.available and s.id in ADAPTERS,
            needs_credentials=s.needs_credentials, credential_fields=list(s.credential_fields), sync_hours=s.sync_hours,
            source_url=s.source_url, terms=s.terms, owner_action=s.owner_action,
        )
        for s in PROVIDERS.values()
    ]


@router.get("/connections", response_model=list[ConnectionView])
def list_connections(context: TenantContext = Depends(tenant_context)) -> list[ConnectionView]:
    _read(context)
    with open_finance_store(context) as store:
        return [_connection_view(c) for c in store.list_connections(context)]


@router.post("/connections", response_model=ConnectionView, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: ConnectionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectionView:
    """Connect a source, read only. Only sources with a built adapter can be connected."""
    _manage(context)
    spec = PROVIDERS.get(body.provider)
    if spec is None:
        raise _invalid("Unknown source.")
    if not spec.available or spec.id not in ADAPTERS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{spec.name} is not available yet. {spec.owner_action or ''}".strip())
    if not is_read_only(spec.scopes):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only read-only sources can be connected.")
    credentials = {k: str(v).strip() for k, v in (body.credentials or {}).items() if k in spec.credential_fields and str(v).strip()}
    if spec.needs_credentials and set(credentials) != set(spec.credential_fields):
        raise _invalid("Enter every credential this source needs.")
    if any(len(v) > 4000 for v in credentials.values()):
        raise _invalid("A credential is too long.")
    now = utc_now()
    with open_finance_store(context) as store:
        mine = store.list_connections(context)
        if len(mine) >= MAX_CONNECTIONS:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_CONNECTIONS} connections. Delete one first.")
        if not spec.needs_credentials and any(c.provider == spec.id and c.status != ConnectionStatus.DISCONNECTED for c in mine):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{spec.name} is connected already.")
        connection = Connection(
            id=new_id("fin_conn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            provider=spec.id, kind=spec.kind, label=_clean(body.label, 120) or spec.name, scopes=list(spec.scopes),
            credentials_encrypted=encrypt_credentials(credentials) if credentials else None, credential_hint=credential_hint(credentials),
            next_sync_at=now, created_at=now, updated_at=now,
        )
        store.save_connection(connection)
    _audit(repository, context, "finance.connection.create", connection.id, {"provider": spec.id, "scopes": list(spec.scopes)})
    return _connection_view(connection)


@router.get("/connections/{connection_id}", response_model=ConnectionView)
def get_connection(connection_id: str, context: TenantContext = Depends(tenant_context)) -> ConnectionView:
    _read(context)
    with open_finance_store(context) as store:
        return _connection_view(_own_connection(store, context, connection_id))


@router.patch("/connections/{connection_id}", response_model=ConnectionView)
def update_connection(
    connection_id: str,
    body: ConnectionPatch,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectionView:
    _manage(context)
    changes = body.model_dump(exclude_unset=True)
    with open_finance_store(context) as store:
        connection = _own_connection(store, context, connection_id)
        if connection.status == ConnectionStatus.DISCONNECTED and body.enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "This connection is disconnected. Connect the source again.")
        if body.label is not None:
            connection.label = _clean(body.label, 120) or connection.label
        if body.enabled is not None:
            connection.enabled = body.enabled
        connection.updated_at = utc_now()
        store.save_connection(connection)
    _audit(repository, context, "finance.connection.update", connection_id, {"fields": sorted(changes)})
    return _connection_view(connection)


@router.post("/connections/{connection_id}/sync", response_model=SyncReport)
async def sync_now(
    connection_id: str,
    context: TenantContext = Depends(tenant_context),
    uow: UnitsOfWork = Depends(units_of_work),
) -> SyncReport:
    """Sync one connection now. The store work runs off the event loop in short units."""
    _manage(context)

    def check() -> Connection:
        with open_finance_store(context) as store:
            return _own_connection(store, context, connection_id)

    connection = await asyncio.to_thread(check)
    if connection.status == ConnectionStatus.DISCONNECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This connection is disconnected. Connect the source again.")
    report = await sync_connection(context, connection_id)

    def audit() -> None:
        # Its own short unit of work after the network call: no session is held across it.
        with uow(context) as repository:
            _audit(repository, context, "finance.connection.sync", connection_id, {"status": report.status, "provider": connection.provider})

    await asyncio.to_thread(audit)
    return report


@router.post("/connections/{connection_id}/disconnect", response_model=ConnectionView)
def disconnect(
    connection_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectionView:
    """Stop syncing and erase the saved credentials. Data already imported stays."""
    _manage(context)
    with open_finance_store(context) as store:
        connection = _own_connection(store, context, connection_id)
        connection.status, connection.enabled = ConnectionStatus.DISCONNECTED, False
        connection.credentials_encrypted, connection.credential_hint = None, None
        connection.updated_at = utc_now()
        store.save_connection(connection)
    _audit(repository, context, "finance.connection.disconnect", connection_id, {"provider": connection.provider})
    return _connection_view(connection)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Delete the connection, its credentials and the rates it fetched."""
    _manage(context)
    with open_finance_store(context) as store:
        if not store.delete_connection(context, connection_id):
            raise _not_found("Connection")
    _audit(repository, context, "finance.connection.delete", connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/fx-rates", response_model=list[RateView])
def fx_rates(limit: int = Query(default=100, ge=1, le=500), context: TenantContext = Depends(tenant_context)) -> list[RateView]:
    """Recorded exchange rates, newest first, each with its date and source."""
    _read(context)
    with open_finance_store(context) as store:
        rates = store.list_fx_rates(context, limit=limit)
    return [RateView(**r.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"})) for r in rates]


@router.post("/fx-rates", response_model=RateView, status_code=status.HTTP_201_CREATED)
def add_fx_rate(
    body: RateInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> RateView:
    """A rate you type yourself (source ``manual``), for a currency no connected source covers."""
    _manage(context)
    try:
        base, quote = normalize_currency(body.base), normalize_currency(body.quote)
        rate = fx.check_rate(body.rate)
    except (MoneyError, fx.FxError) as exc:
        raise _invalid(str(exc)) from None
    if base == quote:
        raise _invalid("A rate needs two different currencies.")
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        if body.as_of > today or body.as_of < today - timedelta(days=366):
            raise _invalid("The rate's date must be within the last year and not in the future.")
        if len(store.list_fx_rates(context, limit=MAX_FX_RATES)) >= MAX_FX_RATES:
            raise HTTPException(status.HTTP_409_CONFLICT, "Too many rates are recorded. Delete old ones first.")
        item = FxRate(
            id=new_id("fin_fx"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            base=base, quote=quote, rate=rate, as_of=body.as_of, source="manual", fetched_at=utc_now(),
        )
        if not store.save_fx_rates([item]):
            raise HTTPException(status.HTTP_409_CONFLICT, "You already typed a rate for these currencies on that date. Delete it first.")
    _audit(repository, context, "finance.fx_rate.create", item.id, {"pair": f"{base}/{quote}"})
    return RateView(**item.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"}))


@router.delete("/fx-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fx_rate(
    rate_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    _manage(context)
    with open_finance_store(context) as store:
        if not store.delete_fx_rate(context, rate_id):
            raise _not_found("Rate")
    _audit(repository, context, "finance.fx_rate.delete", rate_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
