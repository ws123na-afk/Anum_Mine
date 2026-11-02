"""Routes for IBKR trading under ``/api/v1/finance/ibkr`` (docs/finance-live-trading.md#api).

``finance:read`` reads. Every change, connection, order, approval and switch also needs the
workspace owner (``workspace:manage`` or an organisation administrator): the owner decided on
2 November 2026 that only they trade. Every record is the caller's own (owner-only RLS). Audit
rows carry ids and codes only: never an account id, amount, price, quantity or symbol.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import repository_context, require_owner_or_org_admin, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import ibkr_service as svc
from .api import _audit, _manage, _read
from .config import finance_config
from .execution import IntentStatus
from .gateway import expire
from .ibkr_client import GATEWAY_HOSTS
from .ibkr_rules import (
    SHARIAH_ETFS,
    US_ETFS,
    BrokerLimits,
    BrokerSettings,
    Check,
    Environment,
    us_session,
)
from .ibkr_store import MAX_EVENTS_PAGE, open_ibkr_stores

router = APIRouter(prefix="/api/v1/finance/ibkr", tags=["finance"])
OWNER_ONLY = "Only the workspace owner can connect IBKR, change its limits, approve orders or use the kill switch."
RISK_NOTE = ("Real money can be lost. Figures come from IBKR's own records; ANUM never predicts or promises earnings. "
             "Every order needs your approval, and nothing is sent outside your limits.")


def _owner(context: TenantContext) -> None:
    _manage(context)
    require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, OWNER_ONLY)


def _http(exc: svc.BrokerRefusal) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _denied(repository: AnumRepository, context: TenantContext, action: str, target: str, exc: svc.BrokerRefusal) -> None:
    meta: dict[str, Any] = {"code": exc.code}
    for key in ("unmet", "failed"):
        if exc.details.get(key):
            meta[key] = list(exc.details[key])
    repository.record_audit(AuditRecord(id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                        actor=context.user_id, action=action, target=target, outcome="denied", correlation_id=target,
                                        created_at=utc_now(), metadata=meta))


def _run(repository: AnumRepository, context: TenantContext, action: str, target: str, fn: Any, *args: Any, meta: dict[str, Any] | None = None) -> Any:
    try:
        result = fn(*args)
    except svc.BrokerRefusal as exc:
        _denied(repository, context, action, target, exc)
        raise _http(exc) from None
    _audit(repository, context, action, target, meta)
    return result


# -- overview ----------------------------------------------------------------------------------------


class Overview(BaseModel):
    mode: Environment
    deployment_live_enabled: bool
    settings: BrokerSettings
    connections: list[svc.ConnectionView]
    snapshots: dict[str, dict[str, Any]]
    statements: dict[str, dict[str, Any]]
    live_gates: list[Check]
    switch_gates: list[Check]
    paper_proof: list[Check]
    market: dict[str, Any]
    pending: list[dict[str, Any]]
    unread_alerts: int
    gateway_hosts: list[str]
    etfs: dict[str, str]
    shariah_etfs: list[str]
    approval_minutes: int
    paper_min_orders: int
    paper_min_days: int
    risk_note: str


def _settings_view(settings: BrokerSettings) -> BrokerSettings:
    live = settings.live.model_copy(update={"code_hash": None})
    recon = {k: v.model_copy(update={"filled_at_baseline": {}}) for k, v in settings.reconciliation.items()}
    return settings.model_copy(update={"live": live, "reconciliation": recon})


def _intent_view(intent: Any) -> dict[str, Any]:
    data = intent.model_dump(mode="json", include={"id", "mode", "symbol", "side", "order_type", "quantity", "limit_price", "amount", "currency",
                                                   "status", "payload_hash", "created_at", "expires_at", "requested_by", "reason", "refusal",
                                                   "refusal_code", "estimate", "result"})
    data["environment"] = "live" if intent.mode.value == "live" else "paper"
    for name in ("quantity", "limit_price"):  # shown plainly; the hash binds the stored exact values
        value = getattr(intent, name)
        if value is not None:
            data[name] = format(value.normalize(), "f")
    return data


@router.get("/overview", response_model=Overview)
def overview(context: TenantContext = Depends(tenant_context)) -> Overview:
    """Everything the IBKR screen shows, from ANUM's records and the last IBKR read (no IBKR call)."""
    _read(context)
    now = utc_now()
    with open_ibkr_stores(context) as (fstore, store):
        settings = svc._settings(store, context)
        connections = [c for c in store.list_connections(context) if c.status == "active"]
        intents, _ = fstore.list_intents(context, status=IntentStatus.PENDING.value, mode=None, limit=100, offset=0)
        mine = [i for i in expire(fstore, intents, now) if svc.is_broker_intent(i) and i.status == IntentStatus.PENDING]
        view = Overview(
            mode=settings.mode(now), deployment_live_enabled=finance_config.live_enabled and finance_config.live_broker == "ibkr",
            settings=_settings_view(settings), connections=[svc.connection_view(c) for c in connections],
            snapshots={c.environment.value: {k: v for k, v in c.snapshot.items()} for c in connections},
            statements={c.environment.value: c.statement for c in connections},
            live_gates=svc.live_gates(fstore, store, context, settings, now),
            switch_gates=svc.live_gates(fstore, store, context, settings, now, for_switch=True),
            paper_proof=svc.proof_checks(store, context, settings, now),
            market=us_session(now, settings.limits.session_buffer_minutes).model_dump(mode="json"),
            pending=[_intent_view(i) for i in mine], unread_alerts=store.unread_alerts(context), gateway_hosts=list(GATEWAY_HOSTS),
            etfs=US_ETFS, shariah_etfs=sorted(SHARIAH_ETFS), approval_minutes=finance_config.broker_approval_minutes, paper_min_orders=finance_config.broker_paper_min_orders,
            paper_min_days=finance_config.broker_paper_min_days, risk_note=RISK_NOTE,
        )
    return view


# -- connections -------------------------------------------------------------------------------------


class ConnectBody(svc.ConnectInput):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


@router.post("/connections", response_model=svc.ConnectionView, status_code=status.HTTP_201_CREATED)
def connect(body: ConnectBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> svc.ConnectionView:
    """Save an IBKR connection from the owner's form: encrypted, never returned, never logged."""
    _owner(context)
    return _run(repository, context, "finance.ibkr.connect", context.user_id, svc.connect, context, body, utc_now(),
                meta={"environment": body.environment.value})


@router.post("/connections/{connection_id}/check", response_model=svc.ConnectionView)
def check(connection_id: str, reauthenticate: bool = Query(default=False), context: TenantContext = Depends(tenant_context),
          repository: AnumRepository = Depends(repository_context)) -> svc.ConnectionView:
    _owner(context)
    return _run(repository, context, "finance.ibkr.check", connection_id, lambda: svc.check_connection(context, connection_id, utc_now(),
                                                                                                         reauthenticate=reauthenticate))


@router.delete("/connections/{connection_id}", response_model=svc.ConnectionView)
def disconnect(connection_id: str, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> svc.ConnectionView:
    _owner(context)
    return _run(repository, context, "finance.ibkr.disconnect", connection_id, svc.disconnect, context, connection_id, utc_now())


@router.post("/sync")
def sync(environment: Environment = Query(...), context: TenantContext = Depends(tenant_context),
         repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """Read IBKR now: account, cash, positions, open orders, executions, today's P&L; reconcile."""
    _owner(context)
    return _run(repository, context, "finance.ibkr.sync", context.user_id, svc.sync, context, environment, utc_now(),
                meta={"environment": environment.value})


@router.post("/statements")
def statements(environment: Environment = Query(...), context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _owner(context)
    return _run(repository, context, "finance.ibkr.statements", context.user_id, svc.statements, context, environment, utc_now())


# -- settings ----------------------------------------------------------------------------------------


class LimitsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: BrokerLimits
    version: int = Field(ge=0)


@router.put("/limits", response_model=BrokerSettings)
def limits(body: LimitsBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.limits", context.user_id, svc.update_limits, context, body.limits, body.version,
                               utc_now()))


class SymbolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=10)
    # Required for an ETF that is not Shariah-screened by its issuer (owner decision, 2 Nov 2026).
    confirm_not_screened: bool = False


@router.post("/symbols", response_model=BrokerSettings)
def add_symbol(body: SymbolBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.symbol.add", context.user_id, lambda: svc.add_symbol(context, body.symbol, utc_now(),
                                                                                                         confirm_not_screened=body.confirm_not_screened)))


@router.delete("/symbols/{symbol}", response_model=BrokerSettings)
def remove_symbol(symbol: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.symbol.remove", context.user_id, svc.remove_symbol, context, symbol[:10], utc_now()))


class FractionalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.put("/fractional", response_model=BrokerSettings)
def fractional(body: FractionalBody, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.fractional", context.user_id, svc.set_fractional, context, body.enabled, utc_now()))


class PlansBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[svc.PlanInput] = Field(max_length=10)
    version: int = Field(ge=0)


@router.put("/plans", response_model=BrokerSettings)
def plans(body: PlansBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    """The owner's buy plans: a fixed amount of one allowed ETF on a day of the month. They only propose."""
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.plans", context.user_id, svc.save_plans, context, body.plans, body.version, utc_now()))


@router.post("/plans/propose")
def propose(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """Due plans each PROPOSE one buy into the approval queue (the screen calls this when opened). Nothing is sent."""
    _owner(context)
    result = _run(repository, context, "finance.ibkr.plans.propose", context.user_id, svc.propose_due_plans, context, utc_now())
    return {"proposed": [_intent_view(i) for i in result["proposed"]], "skipped": result["skipped"]}


# -- live switch -------------------------------------------------------------------------------------


@router.post("/live/request")
def live_request(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """Shows a one-time code for the owner to type. Every gate before the switch must hold."""
    _owner(context)
    return _run(repository, context, "finance.ibkr.live.request", context.user_id, svc.request_live, context, utc_now())


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=4, max_length=12)


@router.post("/live/confirm", response_model=BrokerSettings)
def live_confirm(body: CodeBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.live.on", context.user_id, svc.confirm_live, context, body.code, utc_now()))


@router.post("/live/off", response_model=BrokerSettings)
def live_off(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _manage(context)  # anyone allowed to manage their own finance may always turn live off
    return _settings_view(_run(repository, context, "finance.ibkr.live.off", context.user_id, svc.live_off, context, utc_now()))


# -- orders ------------------------------------------------------------------------------------------


class OrderBody(svc.OrderInput):
    model_config = ConfigDict(extra="forbid")


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(body: OrderBody, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """The order ticket: a limit order waiting for the owner's approval (nothing is sent yet)."""
    _owner(context)
    intent = _run(repository, context, "finance.ibkr.order.create", context.user_id, svc.create_order, context, body, utc_now())
    return _intent_view(intent)


@router.get("/quote")
def get_quote(symbol: str = Query(min_length=1, max_length=10), context: TenantContext = Depends(tenant_context),
              repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """IBKR's price for an allowed ETF, labelled Delayed when it comes from IBKR's free delayed data."""
    _owner(context)
    try:
        return svc.quote(context, symbol, utc_now())
    except svc.BrokerRefusal as exc:
        raise _http(exc) from None


class OrderPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


@router.get("/orders", response_model=OrderPage)
def list_orders(environment: Literal["paper", "live"] | None = Query(default=None), limit: int = Query(default=25, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=100000), context: TenantContext = Depends(tenant_context)) -> OrderPage:
    _read(context)
    with open_ibkr_stores(context) as (_, store):
        items, total = store.list_orders(context, environment=environment, limit=limit, offset=offset)
    return OrderPage(items=[o.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "owner_user_id"}) for o in items], total=total,
                     limit=limit, offset=offset)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/intents/{intent_id}/approve")
def approve(intent_id: str, body: ApproveBody, context: TenantContext = Depends(tenant_context),
            repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """The owner's approval of the exact order shown (payload hash, single use, expires). Then sent."""
    _owner(context)
    return _run(repository, context, "finance.ibkr.order.approve", intent_id, svc.approve, context, intent_id, body.payload_hash, utc_now())


@router.post("/intents/{intent_id}/reject")
def reject(intent_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _manage(context)
    now = utc_now()
    with open_ibkr_stores(context) as (fstore, _):
        intent = fstore.get_intent(context, intent_id)
        if intent is None or not svc.is_broker_intent(intent):
            raise HTTPException(status_code=404, detail="Order request not found. It may have expired.")
        if intent.status != IntentStatus.PENDING:
            raise HTTPException(status_code=409, detail="This request is not waiting for approval.")
        intent.status, intent.updated_at = IntentStatus.REJECTED, now
        fstore.save_intent(intent)
    _audit(repository, context, "finance.ibkr.order.reject", intent_id)
    return _intent_view(intent)


@router.post("/orders/{order_id}/send")
def resend(order_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """For an order IBKR did not answer: look it up by its id first; send only if IBKR has none."""
    _owner(context)
    return _run(repository, context, "finance.ibkr.order.send", order_id, svc.send, context, order_id, utc_now())


@router.post("/orders/{order_id}/refresh")
def refresh(order_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _owner(context)
    order = _run(repository, context, "finance.ibkr.order.refresh", order_id, svc.refresh_order, context, order_id, utc_now())
    return order.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "owner_user_id"})


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _owner(context)
    order = _run(repository, context, "finance.ibkr.order.cancel", order_id, svc.cancel, context, order_id, utc_now())
    return order.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "owner_user_id"})


# -- kill switch and reconciliation ------------------------------------------------------------------


class KillBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=300)


@router.post("/kill-switch")
def kill(body: KillBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """Stop: block new orders, cancel every open order at IBKR and confirm each cancel."""
    _manage(context)  # anyone allowed to manage their own finance may always stop
    return _run(repository, context, "finance.ibkr.kill", context.user_id, svc.kill, context, body.reason, utc_now())


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal["RESUME"]


@router.post("/kill-switch/release", response_model=BrokerSettings)
def release(body: ReleaseBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.kill.release", context.user_id, svc.release_kill, context, utc_now()))


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Environment
    note: str = Field(min_length=10, max_length=300)


@router.post("/reconciliation/accept", response_model=BrokerSettings)
def accept(body: AcceptBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BrokerSettings:
    """The owner explains a difference: IBKR's positions become ANUM's starting point again."""
    _owner(context)
    return _settings_view(_run(repository, context, "finance.ibkr.reconcile.accept", context.user_id, svc.accept_reconciliation, context,
                               body.environment, body.note.strip(), utc_now()))


# -- events and alerts -------------------------------------------------------------------------------


class EventPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    unread: int
    limit: int
    offset: int


@router.get("/events", response_model=EventPage)
def events(alerts: bool = Query(default=False), limit: int = Query(default=25, ge=1, le=MAX_EVENTS_PAGE), offset: int = Query(default=0, ge=0, le=100000),
           context: TenantContext = Depends(tenant_context)) -> EventPage:
    """The IBKR audit trail (and, with ``alerts``, only the alerts), newest first."""
    _read(context)
    with open_ibkr_stores(context) as (_, store):
        items, total = store.list_events(context, alerts_only=alerts, limit=limit, offset=offset)
        unread = store.unread_alerts(context)
    return EventPage(items=[e.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "owner_user_id"}) for e in items], total=total,
                     unread=unread, limit=limit, offset=offset)


@router.post("/events/read")
def read_alerts(context: TenantContext = Depends(tenant_context)) -> dict[str, int]:
    _read(context)
    with open_ibkr_stores(context) as (_, store):
        return {"marked": store.mark_read(context, utc_now())}


def live_gate_preconditions(context: TenantContext) -> list[Check]:
    """For the readiness preflight: the IBKR live gates for this person (read only)."""
    now = utc_now()
    with open_ibkr_stores(context) as (fstore, store):
        return svc.live_gates(fstore, store, context, svc._settings(store, context), now)


__all__ = ["live_gate_preconditions", "router"]
