"""IBKR live trading: the flows (docs/finance-live-trading.md).

Every flow follows the repository rule for outside calls: read and claim in one short unit of
work, call IBKR with **no** transaction open, then record the answer in another short unit of
work and re-check what was claimed. Routes are plain ``def`` (FastAPI runs them in a worker
thread), so nothing here runs on the event loop.

The order path, in order:

1. An order **intent** is created by the owner's click on the ticket (or proposed by an agent
   in paper mode). It is a row in ``finance_exec_intents`` (mode ``paper`` or ``live``, kind
   ``trade``, ``account_id = "ibkr:<connection>"``) with the payload hash the owner approves.
2. **Approval** (owner only, payload hash, at most ``ANUM_FINANCE_BROKER_APPROVAL_MINUTES``
   old, used once): fresh IBKR data is read and every hard limit is checked. If any fails the
   intent is refused and nothing is sent. Otherwise the intent is claimed and the order row is
   written (state ``submitting``) in the same unit of work.
3. **Immediately before sending**: the settings and kill switches are read again, IBKR is read
   again (session, quote, positions, open orders, today's profit or loss), the client order id
   is looked up at IBKR, and every limit is checked once more. Only then is the limit order
   sent with ``cOID`` = the intent id. A timeout leaves the order ``unknown``: it is looked up
   by that id, never sent again blind.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext, new_id
from ..secret_box import secret_cipher
from .config import finance_config
from .execution import ExecMode, ExecSettings, GlobalSwitch, Intent, IntentKind, IntentStatus
from .gateway import intent_hash
from .guardian import ActionKind, Decision, GuardianAction, LiveClearance, gate
from .ibkr_client import (
    DONE_STATUSES,
    Execution,
    IbkrClient,
    IbkrCredentials,
    IbkrError,
    LiveOrder,
    Transport,
    is_paper_account,
    normalize_account,
    normalize_pin,
)
from .ibkr_rules import (
    CODE_ALPHABET,
    NEW_YORK,
    DONE_STATES,
    OPEN_STATES,
    SHARIAH_ETFS,
    US_ETFS,
    US_EXCHANGES,
    AllowedSymbol,
    BrokerConnection,
    BrokerEvent,
    BrokerLimits,
    BrokerOrder,
    BrokerSettings,
    BuyPlan,
    Check,
    Environment,
    OrderFacts,
    OrderState,
    ProofFacts,
    Reconciliation,
    check_quantity,
    code_hash,
    code_matches,
    failed,
    new_baseline,
    new_york_day,
    order_checks,
    paper_proof,
    reconcile,
    us_session,
)
from .ibkr_store import IbkrStore, open_ibkr_stores
from .money import quantize
from .policy import current_rules, ledger_state
from .store import FinanceStore
from .summary import settings_for, zone
from .trading import InstrumentKind, OrderType, Side, TradingError, check_price
from .trading import check_quantity as check_trade_quantity

# Tests put a fake IBKR gateway here; production leaves it None (real HTTPS, pinned).
TRANSPORT: Transport | None = None
ACCOUNT_PREFIX = "ibkr:"
CODE_MINUTES = 10
STALE_SUBMIT_SECONDS = 120
ERASED = ""  # a revoked connection keeps no encrypted details (database check ck_finance_broker_connections_secret)
LIMIT_CODES = frozenset({"max_order", "max_capital", "daily_loss", "orders_per_day", "price_band", "cash", "no_short", "market_hours"})


class BrokerRefusal(Exception):
    """A refused IBKR step. ``message`` is safe to show; ``code`` is stable."""

    def __init__(self, code: str, message: str, *, status: int = 409, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def utcnow() -> datetime:
    return datetime.now(UTC)


# -- small helpers -----------------------------------------------------------------------------------


def _client(connection: BrokerConnection) -> IbkrClient:
    values = json.loads(secret_cipher().decrypt(connection.secret)) if connection.secret else {}
    if not values:
        raise BrokerRefusal("not_connected", "This IBKR connection was disconnected. Connect it again.")
    return IbkrClient(IbkrCredentials(**values), TRANSPORT, pace=TRANSPORT is None)


def _settings(store: IbkrStore, context: TenantContext) -> BrokerSettings:
    return store.get_settings(context) or BrokerSettings()


def _save_settings(store: IbkrStore, context: TenantContext, settings: BrokerSettings, now: datetime) -> BrokerSettings:
    expected = settings.version
    settings.version, settings.updated_at = expected + 1, now
    if not store.save_settings(context, settings, expected_version=expected):
        raise BrokerRefusal("stale", "Your IBKR settings changed in another tab. Reload and try again.")
    return settings


def _event(store: IbkrStore, context: TenantContext, kind: str, message: str, *, now: datetime, environment: Environment | None = None,
           severity: Literal["info", "warning", "critical"] = "info", alert: bool = False, ref: str | None = None) -> None:
    store.add_event(BrokerEvent(
        id=new_id("fin_bev"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        environment=environment, kind=kind, severity=severity, message=message[:400], ref=ref, alert=alert, created_at=now,
    ))


def _connection(store: IbkrStore, context: TenantContext, environment: Environment) -> BrokerConnection:
    found = store.active_connection(context, environment)
    if found is None:
        raise BrokerRefusal("not_connected", f"No {environment.value} IBKR connection. Connect one first.", status=409)
    return found


def _kills(fstore: FinanceStore, context: TenantContext, settings: BrokerSettings) -> tuple[bool, bool, bool]:
    halt = fstore.get_global_switch(context) or GlobalSwitch()
    mine = fstore.get_exec_settings(context) or ExecSettings()
    return halt.engaged, mine.kill_switch, settings.kill_switch


def _session_failure(exc: IbkrError) -> Literal["expired", "unreachable"]:
    return "expired" if exc.code in {"session_expired", "session_competing"} else "unreachable"


# -- gates -------------------------------------------------------------------------------------------


def _proof_facts(store: IbkrStore, context: TenantContext, settings: BrokerSettings) -> ProofFacts:
    sent = [o for o in store.orders_for(context, Environment.PAPER) if o.broker_order_id]
    first = min((o.submitted_at for o in sent if o.submitted_at), default=None)
    recon = settings.recon(Environment.PAPER)
    unexplained = max(0, recon_episodes(recon) - recon.explained)
    return ProofFacts(paper_orders=len(sent), first_paper_at=first, unexplained_mismatches=unexplained)


def recon_episodes(recon: Reconciliation) -> int:
    return recon.episodes


def proof_checks(store: IbkrStore, context: TenantContext, settings: BrokerSettings, now: datetime) -> list[Check]:
    return paper_proof(_proof_facts(store, context, settings), min_orders=finance_config.broker_paper_min_orders,
                       min_days=finance_config.broker_paper_min_days, now=now)


def live_gates(fstore: FinanceStore, store: IbkrStore, context: TenantContext, settings: BrokerSettings, now: datetime,
               *, for_switch: bool = False) -> list[Check]:
    """Every gate a live order needs, each on its own. ``for_switch``: the ones before the owner's switch."""
    connection = store.active_connection(context, Environment.LIVE)
    proof_ok = not failed(proof_checks(store, context, settings, now))
    halt_global, halt_person, halt_broker = _kills(fstore, context, settings)
    effective = settings.live.effective_at
    checks = [
        Check(code="deployment_flag", ok=finance_config.live_enabled,
              message="The operator turned on ANUM_FINANCE_LIVE_ENABLED." if finance_config.live_enabled
              else "The operator setting ANUM_FINANCE_LIVE_ENABLED is off."),
        Check(code="live_broker", ok=finance_config.live_broker == "ibkr",
              message="The reviewed IBKR executor is named (ANUM_FINANCE_LIVE_BROKER=ibkr)." if finance_config.live_broker == "ibkr"
              else "The operator has not named the reviewed IBKR executor (ANUM_FINANCE_LIVE_BROKER=ibkr)."),
        Check(code="live_connection", ok=connection is not None and connection.verified_at is not None,
              message="Your live IBKR account is connected and checked." if connection is not None and connection.verified_at is not None
              else "Connect your live IBKR account and check it."),
        Check(code="paper_proof", ok=proof_ok, message="The paper period is complete." if proof_ok
              else "The paper period is not complete yet."),
        Check(code="limits", ok=bool(settings.allowed), message="Your allowed ETF list is set." if settings.allowed
              else "Add at least one ETF to your allowed list."),
        Check(code="reconciliation", ok=settings.recon(Environment.LIVE).state == "ok",
              message="Your live IBKR records match ANUM's." if settings.recon(Environment.LIVE).state == "ok"
              else "Your live IBKR records are not checked or do not match."),
        Check(code="kill_switches", ok=not (halt_global or halt_person or halt_broker),
              message="No kill switch is on." if not (halt_global or halt_person or halt_broker) else "A kill switch is on."),
    ]
    if not for_switch:
        checks.append(Check(code="owner_switch", ok=settings.live.on, message="You turned live orders on with your code." if settings.live.on
                            else "You have not turned live orders on."))
        cooled = effective is not None and now >= effective
        checks.append(Check(code="cooling_off", ok=cooled,
                            message="The cooling-off period is over." if cooled else
                            (f"Your cooling-off period ends at {effective.isoformat()}." if effective else "No cooling-off has started.")))
    return checks


def clearance(checks: list[Check]) -> LiveClearance | None:
    return LiveClearance([c.code for c in checks]) if checks and not failed(checks) else None


# -- views -------------------------------------------------------------------------------------------


class ConnectionView(BaseModel):
    id: str
    environment: Environment
    gateway_host: str
    hint: str
    status: str
    session: str
    session_message: str | None
    session_checked_at: datetime | None
    verified_at: datetime | None
    trading_type: str | None
    last_sync_at: datetime | None
    last_error: str | None
    statements_connected: bool
    created_at: datetime


def connection_view(connection: BrokerConnection) -> ConnectionView:
    return ConnectionView(**connection.model_dump(include=set(ConnectionView.model_fields) - {"statements_connected"}),
                          statements_connected=bool(connection.snapshot.get("statements_connected")))


# -- connecting --------------------------------------------------------------------------------------


class ConnectInput(BaseModel):
    environment: Environment
    gateway_host: str
    tls_fingerprint: str = Field(min_length=64, max_length=120)
    account_id: str = Field(min_length=5, max_length=20)
    flex_token: str = Field(default="", max_length=64)
    flex_query_id: str = Field(default="", max_length=12)
    cash_account_confirmed: bool = False


def connect(context: TenantContext, body: ConnectInput, now: datetime) -> ConnectionView:
    """Save an owner's IBKR connection (encrypted), then check it against the gateway."""
    if not body.cash_account_confirmed:
        raise BrokerRefusal("cash_account", "Confirm that this IBKR account is a cash account. ANUM never uses margin.", status=422)
    try:
        account = normalize_account(body.account_id)
        credentials = IbkrCredentials(gateway_host=body.gateway_host, tls_pin=normalize_pin(body.tls_fingerprint), account_id=account,
                                      flex_token=body.flex_token.strip(), flex_query_id=body.flex_query_id.strip())
    except ValueError as exc:
        raise BrokerRefusal("invalid", str(exc), status=422) from None
    if body.environment == Environment.PAPER and not is_paper_account(account):
        raise BrokerRefusal("not_paper", "A paper connection needs your IBKR paper account id (it starts with D).", status=422)
    if body.environment == Environment.LIVE:
        if is_paper_account(account):
            raise BrokerRefusal("not_live", "This is a paper account id. A live connection needs your real IBKR account id.", status=422)
        if not (finance_config.live_enabled and finance_config.live_broker == "ibkr"):
            raise BrokerRefusal("live_not_enabled", "Live trading is not enabled on this deployment, so a live account cannot be connected yet.")
    connection = BrokerConnection(
        id=new_id("fin_ibkr"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        environment=body.environment, gateway_host=credentials.gateway_host, secret=secret_cipher().encrypt(json.dumps(credentials.as_dict())),
        hint=f"{account[:2]}...{account[-3:]}", snapshot={"statements_connected": bool(credentials.flex_token)}, created_at=now, updated_at=now,
    )
    with open_ibkr_stores(context) as (_, store):
        if not store.save_connection(connection):
            raise BrokerRefusal("already_connected", f"A {body.environment.value} IBKR connection exists. Disconnect it first.")
        _event(store, context, "connected", f"IBKR {body.environment.value} account connected.", now=now, environment=body.environment)
    return check_connection(context, connection.id, now)


def check_connection(context: TenantContext, connection_id: str, now: datetime, *, reauthenticate: bool = False) -> ConnectionView:
    """Ask the gateway whether the IBKR session is live; verify the account and margin type once."""
    with open_ibkr_stores(context) as (_, store):
        connection = store.get_connection(context, connection_id)
        if connection is None or connection.status != "active":
            raise BrokerRefusal("not_found", "IBKR connection not found. It may have been disconnected.", status=404)
        client = _client(connection)
    update: dict[str, Any] = {"session_checked_at": now}
    positions: dict[str, Decimal] | None = None
    try:
        if reauthenticate:
            client.reauthenticate()
        status = client.auth_status()
        if status.authenticated:
            client.tickle()
            update.update(session="authenticated", session_message=None)
            accounts = {a.account_id: a for a in client.accounts()}
            mine = accounts.get(client.account_id)
            if mine is None:
                update.update(verified_at=None, last_error="This IBKR login cannot see the account id you entered.")
            elif mine.trading_type and ("MRGN" in mine.trading_type.upper() or "MARGIN" in mine.trading_type.upper()):
                update.update(verified_at=None, trading_type=mine.trading_type, last_error="IBKR reports a margin account. ANUM trades only in a cash account.")
            elif mine.paper != (connection.environment == Environment.PAPER):
                update.update(verified_at=None, last_error="IBKR reports a different account type (paper or live) than this connection.")
            else:
                update.update(verified_at=connection.verified_at or now, trading_type=mine.trading_type, last_error=None)
                if connection.verified_at is None:
                    positions = {str(p.conid): p.quantity for p in client.positions()}
        else:
            update.update(session="expired", session_message="competing" if status.competing else "login_needed")
    except IbkrError as exc:
        update.update(session=_session_failure(exc), last_error=exc.message[:300])
    with open_ibkr_stores(context) as (_, store):
        current = store.get_connection(context, connection_id)
        if current is None:
            raise BrokerRefusal("not_found", "IBKR connection not found.", status=404)
        was = current.session
        current = current.model_copy(update={**update, "updated_at": now})
        store.save_connection(current)
        if current.session == "expired" and was != "expired":
            _event(store, context, "session_expired", "Your IBKR session ended. Log in to IBKR again in the gateway.", now=now,
                   environment=current.environment, severity="warning", alert=True)
        if positions is not None:
            settings = _settings(store, context)
            settings.reconciliation[current.environment.value] = new_baseline(
                settings.recon(current.environment), positions, store.orders_for(context, current.environment), now, explained=False)
            _save_settings(store, context, settings, now)
            _event(store, context, "verified", "IBKR account checked. Its current positions are ANUM's starting point.", now=now,
                   environment=current.environment)
        return connection_view(current)


def disconnect(context: TenantContext, connection_id: str, now: datetime) -> ConnectionView:
    """Revoke: the encrypted details are erased. Open orders at IBKR are not touched (use the kill switch)."""
    with open_ibkr_stores(context) as (_, store):
        connection = store.get_connection(context, connection_id)
        if connection is None or connection.status != "active":
            raise BrokerRefusal("not_found", "IBKR connection not found. It may have been disconnected already.", status=404)
        connection = connection.model_copy(update={"status": "revoked", "secret": ERASED, "session": "unknown", "updated_at": now})
        store.save_connection(connection)
        settings = _settings(store, context)
        if connection.environment == Environment.LIVE and settings.live.on:
            settings.live = settings.live.model_copy(update={"on": False, "switched_off_at": now, "effective_at": None})
            _save_settings(store, context, settings, now)
        _event(store, context, "disconnected", f"IBKR {connection.environment.value} connection removed; its saved details were erased.",
               now=now, environment=connection.environment)
        return connection_view(connection)


# -- reading and reconciling ------------------------------------------------------------------------


def _order_update(order: BrokerOrder, live: LiveOrder | None, fills: list[Execution], now: datetime) -> BrokerOrder:
    filled = sum((e.quantity for e in fills), Decimal(0))
    average = (sum((e.quantity * e.price for e in fills), Decimal(0)) / filled).quantize(Decimal("0.0001")) if filled else None
    update: dict[str, Any] = {"filled_quantity": min(filled, order.quantity), "average_price": average, "updated_at": now}
    if live is not None:
        update["broker_order_id"] = order.broker_order_id or live.order_id
        status = live.status
        if status == "Filled" or filled >= order.quantity:
            update["state"] = OrderState.FILLED
        elif status == "Cancelled":
            update["state"], update["message"] = OrderState.CANCELLED, (f"Cancelled after {filled} filled." if filled else "Cancelled.")
        elif status == "Inactive":
            update["state"], update["message"] = OrderState.REJECTED, "IBKR made the order inactive (refused or expired)."
        elif order.state == OrderState.CANCEL_REQUESTED:
            update["state"] = OrderState.CANCEL_REQUESTED
        else:
            update["state"] = OrderState.PARTIALLY_FILLED if filled else OrderState.SUBMITTED
    elif filled >= order.quantity:
        update["state"] = OrderState.FILLED
    elif order.broker_order_id and order.created_at.astimezone(UTC) and new_york_day(order.created_at) < new_york_day(now):
        update["state"], update["message"] = OrderState.CANCELLED, ("The day order ended at the close." + (f" {filled} filled." if filled else ""))
    elif order.state == OrderState.SUBMITTING and (now - order.updated_at).total_seconds() > STALE_SUBMIT_SECONDS:
        update["state"], update["message"] = OrderState.UNKNOWN, "ANUM cannot tell whether IBKR received this order. Check again before anything else."
    return order.model_copy(update=update)


def _announce(store: IbkrStore, context: TenantContext, before: BrokerOrder, after: BrokerOrder, now: datetime) -> None:
    if after.state == before.state and after.filled_quantity == before.filled_quantity:
        return
    label = f"{after.side} {after.quantity} {after.symbol} at {after.limit_price}"
    texts: dict[OrderState, tuple[str, Literal["info", "warning", "critical"]]] = {
        OrderState.FILLED: (f"Filled: {label}, average price {after.average_price}.", "info"),
        OrderState.PARTIALLY_FILLED: (f"Partly filled: {after.filled_quantity} of {label}.", "info"),
        OrderState.CANCELLED: (f"Cancelled: {label}. {after.message or ''}".strip(), "info"),
        OrderState.REJECTED: (f"Rejected: {label}. {after.message or ''}".strip(), "warning"),
        OrderState.UNKNOWN: (f"No answer from IBKR for: {label}. ANUM will look it up by its id, never send it twice.", "warning"),
    }
    if after.state in texts:
        text, severity = texts[after.state]
        _event(store, context, f"order_{after.state.value}", text, now=now, environment=after.environment, severity=severity, alert=True, ref=after.id)


class Snapshot(BaseModel):
    """What IBKR reported at the last sync. Real broker figures only, never projections."""

    account_ok: bool = False
    cash: dict[str, str] = Field(default_factory=dict)
    settled_cash: dict[str, str] = Field(default_factory=dict)
    net_liquidation: str | None = None
    daily_pnl: str | None = None
    unrealized_pnl: str | None = None
    realized_pnl: str | None = None
    dividends_ledger: str | None = None
    positions: list[dict[str, Any]] = Field(default_factory=list)
    open_orders: list[dict[str, Any]] = Field(default_factory=list)
    executions: list[dict[str, Any]] = Field(default_factory=list)
    synced_at: datetime | None = None


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def sync(context: TenantContext, environment: Environment, now: datetime) -> dict[str, Any]:
    """Read account, cash, positions, open orders, executions and P&L; update orders; reconcile."""
    with open_ibkr_stores(context) as (_, store):
        connection = _connection(store, context, environment)
        client = _client(connection)
    try:
        client.require_session()
        client.tickle()
        accounts = {a.account_id for a in client.accounts()}
        ledger = client.ledger()
        positions = client.positions()
        live = client.live_orders()
        trades = client.trades()
        pnl = client.pnl()
    except IbkrError as exc:
        with open_ibkr_stores(context) as (_, store):
            current = _connection(store, context, environment)
            was = current.session
            store.save_connection(current.model_copy(update={"session": _session_failure(exc), "last_error": exc.message[:300], "updated_at": now,
                                                             "session_checked_at": now}))
            if _session_failure(exc) == "expired" and was != "expired":
                _event(store, context, "session_expired", "Your IBKR session ended. Log in to IBKR again in the gateway.", now=now,
                       environment=environment, severity="warning", alert=True)
        raise BrokerRefusal(exc.code, exc.message, status=502) from None
    base = ledger.get("BASE") or ledger.get("USD")
    usd = ledger.get("USD")
    snapshot = Snapshot(
        account_ok=client.account_id in accounts,
        cash={k: str(v.cash) for k, v in ledger.items() if v.cash is not None and k != "BASE"},
        settled_cash={k: str(v.settled_cash) for k, v in ledger.items() if v.settled_cash is not None and k != "BASE"},
        net_liquidation=_text(pnl.net_liquidation if pnl.net_liquidation is not None else (base.net_liquidation if base else None)),
        daily_pnl=_text(pnl.daily), unrealized_pnl=_text(pnl.unrealized if pnl.unrealized is not None else (base.unrealized_pnl if base else None)),
        realized_pnl=_text(usd.realized_pnl if usd else None), dividends_ledger=_text(usd.dividends if usd else None),
        positions=[p.model_dump(mode="json") for p in positions[:200]],
        open_orders=[o.model_dump(mode="json") for o in live if o.is_open][:200],
        executions=[e.model_dump(mode="json") for e in trades[:200]], synced_at=now,
    )
    with open_ibkr_stores(context) as (_, store):
        connection = _connection(store, context, environment)
        settings = _settings(store, context)
        orders = store.orders_for(context, environment)
        updated: list[BrokerOrder] = []
        for order in orders:
            if order.state in DONE_STATES and order.state != OrderState.CANCELLED:
                updated.append(order)
                continue
            match = next((o for o in live if o.order_ref == order.id or (order.broker_order_id and o.order_id == order.broker_order_id)), None)
            fills = [e for e in trades if e.order_ref == order.id or (order.broker_order_id and e.order_id == order.broker_order_id)]
            after = _order_update(order, match, fills, now) if order.state not in DONE_STATES or fills else order
            if after != order:
                store.save_order(after)
                _announce(store, context, order, after, now)
            updated.append(after)
        allowed = {a.conid for a in settings.allowed}
        broker_orders = [{"order_ref": o.order_ref, "conid": o.conid, "order_id": o.order_id, "filled_quantity": str(o.filled_quantity),
                          "open": o.is_open} for o in live]
        before = settings.recon(environment)
        if before.baseline_at is None:
            after_recon = new_baseline(before, {str(p.conid): p.quantity for p in positions}, updated, now, explained=False)
        else:
            after_recon = reconcile(orders=updated, broker_orders=broker_orders, positions={str(p.conid): p.quantity for p in positions},
                                    recon=before, allowed_conids=allowed, now=now)
        if after_recon.state == "mismatch" and before.state != "mismatch":
            after_recon = after_recon.model_copy(update={"episodes": before.episodes + 1})
            _event(store, context, "mismatch", "IBKR's records and ANUM's differ. New orders are blocked until you check and explain it.",
                   now=now, environment=environment, severity="critical", alert=True)
        settings.reconciliation[environment.value] = after_recon
        _save_settings(store, context, settings, now)
        if pnl.daily is not None and pnl.daily <= -settings.limits.max_daily_loss:
            _event(store, context, "limit_hit", f"Today's loss reached your USD {settings.limits.max_daily_loss} limit. No new orders today.",
                   now=now, environment=environment, severity="warning", alert=True)
        store.save_connection(connection.model_copy(update={"snapshot": {**snapshot.model_dump(mode="json"),
                                                                         "statements_connected": connection.snapshot.get("statements_connected", False)},
                                                            "session": "authenticated", "last_sync_at": now, "last_error": None,
                                                            "session_checked_at": now, "updated_at": now}))
    return {"environment": environment.value, "reconciliation": after_recon.state, "synced_at": now.isoformat()}


def statements(context: TenantContext, environment: Environment, now: datetime) -> dict[str, Any]:
    """Collect dividends and trade counts from IBKR's Flex statement (the owner's own query)."""
    with open_ibkr_stores(context) as (_, store):
        connection = _connection(store, context, environment)
        client = _client(connection)
        reference = connection.statement.get("pending_reference")
    try:
        statement = client.flex_statement(reference if isinstance(reference, str) else None)
    except IbkrError as exc:
        raise BrokerRefusal(exc.code, exc.message, status=502 if exc.code != "not_configured" else 409) from None
    with open_ibkr_stores(context) as (_, store):
        connection = _connection(store, context, environment)
        if statement.ready:
            received = sum((d.amount for d in statement.dividends if d.kind == "dividend"), Decimal(0))
            withheld = sum((d.amount for d in statement.dividends if d.kind == "withholding_tax"), Decimal(0))
            value = {"ready": True, "from_date": statement.from_date, "to_date": statement.to_date, "trades": statement.trades,
                     "dividends_received": str(received), "withholding_tax": str(withheld),
                     "dividends": [d.model_dump(mode="json") for d in statement.dividends[:100]], "fetched_at": now.isoformat()}
        else:
            value = {**connection.statement, "ready": bool(connection.statement.get("ready")), "message": statement.message,
                     "fetched_at": now.isoformat()}
        store.save_connection(connection.model_copy(update={"statement": value, "updated_at": now}))
        return value


# -- settings ----------------------------------------------------------------------------------------


def update_limits(context: TenantContext, limits: BrokerLimits, version: int, now: datetime) -> BrokerSettings:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        if settings.version != version:
            raise BrokerRefusal("stale", "Your IBKR settings changed in another tab. Reload and try again.")
        loosened = limits.loosened(settings.limits)
        if loosened and settings.live.on:
            raise BrokerRefusal("live_on", "Live orders are on: limits can only be made stricter. Turn live off first to loosen them.",
                                details={"loosened": loosened})
        settings.limits = limits
        saved = _save_settings(store, context, settings, now)
        _event(store, context, "limits_changed", "Your IBKR limits changed." + (" Some were loosened." if loosened else ""), now=now)
        return saved


def set_fractional(context: TenantContext, enabled: bool, now: datetime) -> BrokerSettings:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        if enabled and settings.live.on:
            raise BrokerRefusal("live_on", "Live orders are on: turn live off before allowing fractional orders.")
        settings.fractional_ok = enabled
        return _save_settings(store, context, settings, now)


def add_symbol(context: TenantContext, symbol: str, now: datetime, *, confirm_not_screened: bool = False) -> BrokerSettings:
    """Put a US ETF on the allowed list, with its IBKR contract id looked up at IBKR.

    Shariah-screened ETFs are the default; one that is not screened needs the owner's explicit confirm.
    """
    wanted = symbol.strip().upper()
    if wanted not in US_ETFS:
        raise BrokerRefusal("not_us_etf", f"{wanted} is not on ANUM's list of plain US-listed ETFs.", status=422)
    if wanted not in SHARIAH_ETFS and not confirm_not_screened:
        raise BrokerRefusal("not_screened", f"{wanted} is not Shariah-screened by its issuer. Confirm that you want it anyway.", status=422)
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        if settings.allowed_symbol(wanted):
            return settings
        if len(settings.allowed) >= 25:
            raise BrokerRefusal("too_many", "At most 25 ETFs can be allowed.", status=422)
        connection = store.active_connection(context, settings.mode(now)) or store.active_connection(context, Environment.PAPER)
        if connection is None:
            raise BrokerRefusal("not_connected", "Connect IBKR first, so ANUM can look up the ETF's contract.")
        client = _client(connection)
    try:
        matches = [m for m in client.find_stock(wanted) if m.is_us and m.exchange.upper() in US_EXCHANGES]
    except IbkrError as exc:
        raise BrokerRefusal(exc.code, exc.message, status=502) from None
    if not matches:
        raise BrokerRefusal("not_found", f"IBKR found no US-listed contract for {wanted}.", status=422)
    match = matches[0]
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        if not settings.allowed_symbol(wanted):
            settings.allowed = [*settings.allowed, AllowedSymbol(symbol=wanted, conid=match.conid, name=US_ETFS[wanted], exchange=match.exchange,
                                                               shariah_screened=wanted in SHARIAH_ETFS)]
            settings = _save_settings(store, context, settings, now)
            _event(store, context, "symbol_added", f"{wanted} added to your allowed ETFs" +
                   (" (Shariah-screened by its issuer)." if wanted in SHARIAH_ETFS else " (not Shariah-screened, confirmed by you)."), now=now)
        return settings


def remove_symbol(context: TenantContext, symbol: str, now: datetime) -> BrokerSettings:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        settings.allowed = [a for a in settings.allowed if a.symbol != symbol.strip().upper()]
        settings = _save_settings(store, context, settings, now)
        _event(store, context, "symbol_removed", f"{symbol.strip().upper()} removed from your allowed ETFs.", now=now)
        return settings


# -- the live switch ---------------------------------------------------------------------------------


def request_live(context: TenantContext, now: datetime) -> dict[str, Any]:
    """Every gate before the switch must hold; then a one-time code is shown for the owner to type."""
    with open_ibkr_stores(context) as (fstore, store):
        settings = _settings(store, context)
        unmet = failed(live_gates(fstore, store, context, settings, now, for_switch=True))
        if unmet:
            raise BrokerRefusal("gates_unmet", "Live orders cannot be turned on yet: " + " ".join(c.message for c in unmet),
                                details={"unmet": [c.code for c in unmet]})
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        settings.live = settings.live.model_copy(update={"code_hash": code_hash(code, context.user_id), "code_expires_at": now + timedelta(minutes=CODE_MINUTES),
                                                         "requested_at": now})
        _save_settings(store, context, settings, now)
        return {"code": code, "expires_at": (now + timedelta(minutes=CODE_MINUTES)).isoformat(), "cooling_off_hours": settings.cooling_off_hours}


def confirm_live(context: TenantContext, code: str, now: datetime) -> BrokerSettings:
    with open_ibkr_stores(context) as (fstore, store):
        settings = _settings(store, context)
        live = settings.live
        if live.code_expires_at is None or now >= live.code_expires_at or not code_matches(code, context.user_id, live.code_hash):
            raise BrokerRefusal("bad_code", "The code does not match or has expired. Ask for a new code.", status=422)
        unmet = failed(live_gates(fstore, store, context, settings, now, for_switch=True))
        if unmet:
            raise BrokerRefusal("gates_unmet", "Live orders cannot be turned on: " + " ".join(c.message for c in unmet),
                                details={"unmet": [c.code for c in unmet]})
        settings.live = live.model_copy(update={"on": True, "code_hash": None, "code_expires_at": None, "switched_on_at": now,
                                                "effective_at": now + timedelta(hours=settings.cooling_off_hours)})
        saved = _save_settings(store, context, settings, now)
        _event(store, context, "live_on", f"Live orders turned on. The first one can be sent after the {settings.cooling_off_hours} hour cooling-off.",
               now=now, environment=Environment.LIVE, severity="warning", alert=True)
        return saved


def live_off(context: TenantContext, now: datetime) -> BrokerSettings:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        settings.live = settings.live.model_copy(update={"on": False, "code_hash": None, "code_expires_at": None, "effective_at": None,
                                                         "switched_off_at": now})
        saved = _save_settings(store, context, settings, now)
        _event(store, context, "live_off", "Live orders turned off. New orders go to your paper account.", now=now, environment=Environment.LIVE,
               alert=True)
        return saved


# -- order intents -----------------------------------------------------------------------------------


class OrderInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    limit_price: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    reason: str | None = Field(default=None, max_length=300)


def _guardian(fstore: FinanceStore, context: TenantContext, intent: Intent, amount: Decimal, now: datetime, live: LiveClearance | None) -> tuple[Decision, int]:
    rules, version = current_rules(fstore, context)
    day = now.astimezone(zone(settings_for(fstore, context).timezone)).date()
    action = GuardianAction(kind=ActionKind.TRADE, amount=amount, currency="USD", merchant=intent.symbol, at=now,
                            first_requested_at=intent.created_at, requested_by=intent.requested_by)
    return gate(action, rules, ledger_state(fstore, context, day), simulated=intent.mode == ExecMode.PAPER, live=live), version


def create_order(context: TenantContext, body: OrderInput, now: datetime, *, requested_by: Literal["person", "agent"] = "person",
                 proposer: str | None = None) -> Intent:
    """An order waiting for the owner's approval. Live only when every live gate holds; agents only paper."""
    symbol = body.symbol.strip().upper()
    try:  # the same exact scale the database keeps, so the approval hash survives a round trip
        body = body.model_copy(update={"quantity": check_trade_quantity(body.quantity), "limit_price": check_price(body.limit_price)})
    except TradingError as exc:
        raise BrokerRefusal("invalid", str(exc), status=422) from None
    with open_ibkr_stores(context) as (fstore, store):
        settings = _settings(store, context)
        environment = settings.mode(now)
        if requested_by == "agent" and environment == Environment.LIVE:
            raise BrokerRefusal("agent_live", "An assistant can only propose paper orders. You place live orders yourself.", status=403)
        connection = _connection(store, context, environment)
        if connection.verified_at is None:
            raise BrokerRefusal("not_verified", "Check your IBKR connection first (log in to IBKR, then press Check).")
        allowed = settings.allowed_symbol(symbol)
        if allowed is None:
            raise BrokerRefusal("symbol_not_allowed", f"{symbol} is not on your allowed list of US ETFs.", status=422)
        problem = check_quantity(body.quantity, settings.fractional_ok)
        if problem:
            raise BrokerRefusal("quantity", problem, status=422)
        amount = quantize(body.quantity * body.limit_price, "USD")
        if amount > settings.limits.max_order:
            raise BrokerRefusal("max_order", f"The order is USD {amount}, over your USD {settings.limits.max_order} per order.", status=422)
        halt_global, halt_person, halt_broker = _kills(fstore, context, settings)
        if halt_global or halt_person or halt_broker:
            raise BrokerRefusal("kill_switch", "A kill switch is on: no new orders until you turn it off.")
        cleared: LiveClearance | None = None
        if environment == Environment.LIVE:
            gates = live_gates(fstore, store, context, settings, now)
            cleared = clearance(gates)
            if cleared is None:
                unmet = failed(gates)
                raise BrokerRefusal("gates_unmet", "Live orders are blocked: " + " ".join(c.message for c in unmet),
                                    details={"unmet": [c.code for c in unmet]})
        window = timedelta(minutes=finance_config.broker_approval_minutes)
        intent = Intent(
            id=new_id("fin_int"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            mode=ExecMode.LIVE if environment == Environment.LIVE else ExecMode.PAPER, kind=IntentKind.TRADE,
            instrument_kind=InstrumentKind.SECURITY, symbol=symbol, currency="USD", side=Side(body.side), order_type=OrderType.LIMIT,
            quantity=body.quantity, limit_price=body.limit_price, amount=amount, account_id=ACCOUNT_PREFIX + connection.id,
            requested_by=requested_by, proposer=(proposer or context.user_id)[:160], reason=body.reason, idempotency_key=new_id("fin_idem"),
            payload_hash="0" * 64, status=IntentStatus.PENDING, created_at=now, expires_at=now + window, updated_at=now,
            estimate={"broker": "ibkr", "environment": environment.value, "conid": allowed.conid, "notional": str(amount),
                      "fee_buffer": str(settings.limits.fee_buffer), "simulated": environment == Environment.PAPER},
        )
        decision, version = _guardian(fstore, context, intent, amount, now, cleared)
        intent.decision, intent.guardian_version = decision.model_dump(mode="json"), version
        if decision.outcome == "deny":
            intent.status = IntentStatus.DENIED
        intent.payload_hash = intent_hash(intent)
        fstore.save_intent(intent)
        _event(store, context, "intent", f"Order waiting for your approval: {body.side} {body.quantity} {symbol} at {body.limit_price}.",
               now=now, environment=environment, ref=intent.id)
        return intent


def is_broker_intent(intent: Intent) -> bool:
    return bool(intent.account_id and intent.account_id.startswith(ACCOUNT_PREFIX))


def _gather(client: IbkrClient, conid: int, settings: BrokerSettings, kills: tuple[bool, bool, bool], orders_today: int,
            recon_ok: bool, now: datetime) -> tuple[OrderFacts, list[LiveOrder]]:
    try:
        client.require_session()
        ledger = client.ledger()
        positions = client.positions()
        live = client.live_orders()
        pnl = client.pnl()
        quote = client.quote(conid)
    except IbkrError as exc:
        if exc.code in {"session_expired", "session_competing"}:
            return OrderFacts(session_ok=False, session_message=exc.message, kill_global=kills[0], kill_person=kills[1], kill_broker=kills[2]), []
        raise
    usd = ledger.get("USD")
    cash = None if usd is None else (usd.settled_cash if usd.settled_cash is not None else usd.cash)
    open_live = [o for o in live if o.is_open]
    age = None if quote.updated_ms is None else max(0.0, now.timestamp() - quote.updated_ms / 1000)
    facts = OrderFacts(
        session_ok=True, quote_last=None if quote.frozen else quote.last, quote_age_seconds=age, quote_is_close=quote.closing_price,
        quote_delayed=quote.delayed, cash=cash,
        positions_value=sum((abs(p.market_value) for p in positions if p.market_value is not None), Decimal(0)),
        position_quantity=sum((p.quantity for p in positions if p.conid == conid), Decimal(0)),
        open_buy_value=sum(((o.remaining_quantity or Decimal(0)) * (o.limit_price or Decimal(0)) for o in open_live if o.side == "BUY"), Decimal(0)),
        open_sell_quantity=sum((o.remaining_quantity or Decimal(0) for o in open_live if o.side == "SELL" and o.conid == conid), Decimal(0)),
        daily_pnl=pnl.daily, orders_today=orders_today, reconciliation_ok=recon_ok,
        kill_global=kills[0], kill_person=kills[1], kill_broker=kills[2],
    )
    return facts, live


def _orders_today(store: IbkrStore, context: TenantContext, environment: Environment, now: datetime, exclude: str | None = None) -> int:
    today = new_york_day(now)
    return sum(1 for o in store.orders_for(context, environment) if o.attempts > 0 and o.id != exclude and new_york_day(o.created_at) == today)


def _refuse_intent(context: TenantContext, intent_id: str, code: str, message: str, now: datetime, environment: Environment,
                   details: dict[str, Any] | None = None) -> BrokerRefusal:
    with open_ibkr_stores(context) as (fstore, store):
        intent = fstore.get_intent(context, intent_id)
        if intent is not None and intent.status == IntentStatus.PENDING:
            intent.status, intent.refusal_code, intent.refusal, intent.updated_at = IntentStatus.REFUSED, code[:40], message[:400], now
            fstore.save_intent(intent)
        limit = code in LIMIT_CODES
        _event(store, context, "limit_hit" if limit else "refused", f"Not sent: {message}", now=now, environment=environment,
               severity="warning", alert=True, ref=intent_id)
    return BrokerRefusal(code, message, details=details)


def approve(context: TenantContext, intent_id: str, shown_hash: str, now: datetime) -> dict[str, Any]:
    """The owner's approval of one IBKR order: checked in full, used once, re-checked, then sent."""
    # 1. Read the intent and everything the checks need; nothing is claimed yet.
    with open_ibkr_stores(context) as (fstore, store):
        intent = fstore.get_intent(context, intent_id)
        if intent is None or not is_broker_intent(intent):
            raise BrokerRefusal("not_found", "Order request not found. It may have expired.", status=404)
        if intent.owner_user_id != context.user_id:
            raise BrokerRefusal("not_owner", "Only the person who owns this request can approve it.", status=403)
        if intent.lapsed(now):
            intent.status, intent.updated_at = IntentStatus.EXPIRED, now
            fstore.save_intent(intent)
        if intent.status != IntentStatus.PENDING:
            raise BrokerRefusal("not_pending", f"This request is {intent.status.value.replace('_', ' ')}: it cannot be approved. Make a new one.")
        if shown_hash != intent.payload_hash or shown_hash != intent_hash(intent):
            raise BrokerRefusal("hash_mismatch", "The approval does not match the order shown. Reload it and approve again.")
        environment = Environment.LIVE if intent.mode == ExecMode.LIVE else Environment.PAPER
        settings = _settings(store, context)
        if settings.mode(now) != environment:
            raise BrokerRefusal("mode_changed", "Live orders were switched since this order was made. Make a new one.")
        connection = store.get_connection(context, (intent.account_id or "")[len(ACCOUNT_PREFIX):])
        if connection is None or connection.status != "active" or connection.environment != environment:
            raise BrokerRefusal("not_connected", "The IBKR connection for this order is gone. Make a new order.")
        client = _client(connection)
        kills = _kills(fstore, context, settings)
        orders_today = _orders_today(store, context, environment, now)
        recon_ok = settings.recon(environment).state == "ok"
        conid = int((intent.estimate or {}).get("conid") or 0)
        gates = live_gates(fstore, store, context, settings, now) if environment == Environment.LIVE else []
    if environment == Environment.LIVE and failed(gates):
        unmet = failed(gates)
        raise _refuse_intent(context, intent_id, "gates_unmet", " ".join(c.message for c in unmet), now, environment, {"unmet": [c.code for c in unmet]})
    # 2. Fresh IBKR data (no transaction open), then every hard limit at approval.
    try:
        facts, _ = _gather(client, conid, settings, kills, orders_today, recon_ok, now)
    except IbkrError as exc:
        raise BrokerRefusal(exc.code, exc.message, status=502) from None
    assert intent.symbol and intent.side and intent.quantity and intent.limit_price  # nosec B101 - a broker intent has them
    checks = order_checks(side=intent.side.value, quantity=intent.quantity, limit_price=intent.limit_price, symbol=intent.symbol, conid=conid,
                          settings=settings, facts=facts, now=now)
    if failed(checks):
        first = failed(checks)[0]
        raise _refuse_intent(context, intent_id, first.code, " ".join(c.message for c in failed(checks)), now, environment,
                             {"failed": [c.code for c in failed(checks)]})
    # 3. Claim once and write the order row in the same unit of work.
    with open_ibkr_stores(context) as (fstore, store):
        intent = fstore.get_intent(context, intent_id)
        if intent is None:
            raise BrokerRefusal("not_found", "Order request not found.", status=404)
        decision, version = _guardian(fstore, context, intent, intent.amount or Decimal(0), now,
                                      clearance(live_gates(fstore, store, context, _settings(store, context), now)) if environment == Environment.LIVE else None)
        if decision.outcome == "deny":
            intent.status, intent.decision, intent.guardian_version, intent.updated_at = IntentStatus.DENIED, decision.model_dump(mode="json"), version, now
            fstore.save_intent(intent)
            raise BrokerRefusal("guardian_denied", "Your rules block this now: " + " ".join(r.message for r in decision.reasons if r.outcome == "deny"))
        if not fstore.claim_intent(context, intent_id, now, context.user_id):
            raise BrokerRefusal("already_used", "This approval was used already. Nothing was sent twice.")
        order = BrokerOrder(
            id=intent.id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, connection_id=connection.id,
            environment=environment, symbol=intent.symbol or "", conid=conid, side=intent.side.value if intent.side else "buy",
            quantity=intent.quantity or Decimal(0), limit_price=intent.limit_price or Decimal(0), state=OrderState.SUBMITTING, created_at=now, updated_at=now,
        )
        if not store.add_order(order):
            raise BrokerRefusal("already_used", "This order exists already. Nothing was sent twice.")
        intent.decision, intent.guardian_version = decision.model_dump(mode="json"), version
        intent.status, intent.approved_at, intent.approved_by, intent.updated_at = IntentStatus.EXECUTING, now, context.user_id, now
        fstore.save_intent(intent)
    return send(context, order.id, now)


def _finish(context: TenantContext, order: BrokerOrder, now: datetime, *, intent_status: IntentStatus | None, event: tuple[str, str, str] | None) -> BrokerOrder:
    with open_ibkr_stores(context) as (fstore, store):
        before = store.get_order(context, order.id) or order
        store.save_order(order)
        intent = fstore.get_intent(context, order.id)
        if intent is not None and intent_status is not None and intent.status == IntentStatus.EXECUTING:
            intent.status, intent.updated_at = intent_status, now
            if intent_status in {IntentStatus.EXECUTED, IntentStatus.FAILED}:
                intent.executed_at = now
            intent.result = {"order_id": order.id, "order_state": order.state.value, "broker_order_id": order.broker_order_id, "simulated": False}
            fstore.save_intent(intent)
        if event is not None:
            kind, text, severity = event
            _event(store, context, kind, text, now=now, environment=order.environment, severity=severity, alert=True, ref=order.id)  # type: ignore[arg-type]
        else:
            _announce(store, context, before, order, now)
    return order


def send(context: TenantContext, order_id: str, now: datetime) -> dict[str, Any]:
    """Immediately before sending: everything is read and checked again, the id looked up, then one send."""
    with open_ibkr_stores(context) as (fstore, store):
        order = store.get_order(context, order_id)
        if order is None:
            raise BrokerRefusal("not_found", "Order not found.", status=404)
        if order.state not in {OrderState.SUBMITTING, OrderState.UNKNOWN}:
            raise BrokerRefusal("not_sendable", "This order was sent already or is finished. Nothing was sent twice.")
        settings = _settings(store, context)
        connection = store.get_connection(context, order.connection_id)
        if connection is None or connection.status != "active":
            raise BrokerRefusal("not_connected", "The IBKR connection for this order is gone.")
        client = _client(connection)
        kills = _kills(fstore, context, settings)
        orders_today = _orders_today(store, context, order.environment, now, exclude=order.id)
        recon_ok = settings.recon(order.environment).state == "ok"
        live_ok = order.environment == Environment.PAPER or not failed(live_gates(fstore, store, context, settings, now))
        mode_ok = settings.mode(now) == order.environment
    try:
        existing, fills = client.find_order(order.id)
    except IbkrError as exc:
        # Nothing is sent while ANUM cannot look the id up first.
        raise BrokerRefusal(exc.code, exc.message + " Nothing was sent. Try again with Send.", status=502) from None
    if existing is not None or fills:
        adopted = _order_update(order.model_copy(update={"broker_order_id": existing.order_id if existing else order.broker_order_id}), existing, fills, now)
        return {"order": _finish(context, adopted, now, intent_status=IntentStatus.EXECUTED, event=None).model_dump(mode="json")}
    not_sent = None
    if not live_ok or not mode_ok:
        not_sent = ("gates_unmet", "Live gates no longer hold.")
    else:
        try:
            facts, _ = _gather(client, order.conid, settings, kills, orders_today, recon_ok, now)
        except IbkrError as exc:
            not_sent = (exc.code, exc.message)
        else:
            checks = failed(order_checks(side=order.side, quantity=order.quantity, limit_price=order.limit_price, symbol=order.symbol,
                                         conid=order.conid, settings=settings, facts=facts, now=now))
            if checks:
                not_sent = (checks[0].code, " ".join(c.message for c in checks))
    if not_sent is not None:
        code, text = not_sent
        rejected = order.model_copy(update={"state": OrderState.REJECTED, "message": f"Not sent: {text}"[:300], "updated_at": now})
        _finish(context, rejected, now, intent_status=IntentStatus.FAILED,
                event=("limit_hit" if code in LIMIT_CODES else "refused", f"Not sent, checked again right before sending: {text}", "warning"))
        raise BrokerRefusal(code, f"Not sent. {text}")
    attempt = order.model_copy(update={"attempts": order.attempts + 1, "updated_at": now})
    with open_ibkr_stores(context) as (_, store):
        store.save_order(attempt)  # the attempt counts before the send, so a crash after it is looked up, not resent blind
    try:
        placed = client.place_limit_order(client_order_id=order.id, conid=order.conid, side=order.side.upper(), quantity=order.quantity,
                                          limit_price=order.limit_price)
    except IbkrError as exc:
        state = OrderState.UNKNOWN if exc.sent else OrderState.REJECTED
        failed_order = attempt.model_copy(update={"state": state, "updated_at": now, "submitted_at": now if exc.sent else None,
                                                  "message": exc.message if exc.sent else f"Not sent: {exc.message}"})
        _finish(context, failed_order, now, intent_status=None if exc.sent else IntentStatus.FAILED, event=None)
        raise BrokerRefusal(exc.code, exc.message if not exc.sent else
                            "IBKR did not answer. ANUM will look the order up by its id before anything else; it is never sent twice.", status=502) from None
    if placed.order_id is None:
        rejected = attempt.model_copy(update={"state": OrderState.REJECTED, "message": placed.message[:300], "updated_at": now, "submitted_at": now})
        return {"order": _finish(context, rejected, now, intent_status=IntentStatus.FAILED, event=None).model_dump(mode="json")}
    accepted = attempt.model_copy(update={"state": OrderState.FILLED if placed.status == "Filled" else OrderState.SUBMITTED,
                                          "broker_order_id": placed.order_id, "submitted_at": now, "updated_at": now, "message": placed.message or None})
    done = _finish(context, accepted, now, intent_status=IntentStatus.EXECUTED,
                   event=("order_sent", f"Sent to IBKR ({order.environment.value}): {order.side} {order.quantity} {order.symbol} at {order.limit_price}.", "info"))
    return {"order": done.model_dump(mode="json")}


def refresh_order(context: TenantContext, order_id: str, now: datetime) -> BrokerOrder:
    """Look one order up at IBKR by its client order id (used for unknown answers)."""
    with open_ibkr_stores(context) as (_, store):
        order = store.get_order(context, order_id)
        if order is None:
            raise BrokerRefusal("not_found", "Order not found.", status=404)
        connection = store.get_connection(context, order.connection_id)
        if connection is None or connection.status != "active":
            raise BrokerRefusal("not_connected", "The IBKR connection for this order is gone.")
        client = _client(connection)
    try:
        live, fills = client.find_order(order.id)
    except IbkrError as exc:
        raise BrokerRefusal(exc.code, exc.message, status=502) from None
    after = _order_update(order, live, fills, now)
    status = IntentStatus.EXECUTED if after.broker_order_id or fills else None
    return _finish(context, after, now, intent_status=status, event=None)


def cancel(context: TenantContext, order_id: str, now: datetime) -> BrokerOrder:
    with open_ibkr_stores(context) as (_, store):
        order = store.get_order(context, order_id)
        if order is None:
            raise BrokerRefusal("not_found", "Order not found.", status=404)
        if order.state not in OPEN_STATES or not order.broker_order_id:
            raise BrokerRefusal("not_open", "This order is not open at IBKR.")
        connection = store.get_connection(context, order.connection_id)
        if connection is None or connection.status != "active":
            raise BrokerRefusal("not_connected", "The IBKR connection for this order is gone.")
        client = _client(connection)
    results = _cancel_and_confirm(client, [order.broker_order_id])
    try:
        live, fills = client.find_order(order.id)
    except IbkrError as exc:
        raise BrokerRefusal(exc.code, exc.message, status=502) from None
    after = _order_update(order.model_copy(update={"state": OrderState.CANCEL_REQUESTED}), live, fills, now)
    if not results.get(order.broker_order_id, False):
        after = after.model_copy(update={"message": "IBKR has not confirmed the cancel yet. Check again in a moment."})
    return _finish(context, after, now, intent_status=None, event=None)


def _cancel_and_confirm(client: IbkrClient, order_ids: list[str]) -> dict[str, bool]:
    """Cancel each order, read IBKR again and confirm each is done; one retry for any that is not."""
    confirmed = {oid: False for oid in order_ids}
    for _ in range(2):
        for oid in [k for k, done in confirmed.items() if not done]:
            try:
                client.cancel_order(oid)
            except IbkrError:
                continue
        try:
            status = {o.order_id: o.status for o in client.live_orders()}
        except IbkrError:
            status = {}
        for oid in confirmed:
            confirmed[oid] = confirmed[oid] or status.get(oid) in DONE_STATUSES
        if all(confirmed.values()):
            break
    return confirmed


def kill(context: TenantContext, reason: str | None, now: datetime) -> dict[str, Any]:
    """Block every new order, then cancel every open order at IBKR and confirm each cancel."""
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        settings.kill_switch, settings.kill_switch_at = True, now
        _save_settings(store, context, settings, now)
        connections = [c for c in store.list_connections(context) if c.status == "active"]
        _event(store, context, "kill_switch", "Kill switch on: new orders are blocked and open orders at IBKR are being cancelled." +
               (f" Reason: {reason[:200]}" if reason else ""), now=now, severity="critical", alert=True)
        clients = [(c, _client(c)) for c in connections]
    report: list[dict[str, Any]] = []
    for connection, client in clients:
        try:
            open_ids = [o.order_id for o in client.live_orders() if o.is_open]
        except IbkrError as exc:
            report.append({"environment": connection.environment.value, "error": exc.message, "orders": []})
            continue
        confirmed = _cancel_and_confirm(client, open_ids) if open_ids else {}
        report.append({"environment": connection.environment.value, "orders": [{"order_id": k, "cancelled": v} for k, v in confirmed.items()]})
    with open_ibkr_stores(context) as (_, store):
        for item in report:
            env = Environment(item["environment"])
            stuck = [o for o in item["orders"] if not o["cancelled"]]
            if item.get("error"):
                _event(store, context, "kill_switch", f"Could not read open orders at IBKR ({env.value}): {item['error']} Cancel them in IBKR yourself.",
                       now=now, environment=env, severity="critical", alert=True)
            elif stuck:
                _event(store, context, "kill_switch", f"{len(stuck)} open orders at IBKR ({env.value}) are not confirmed cancelled. Check IBKR now.",
                       now=now, environment=env, severity="critical", alert=True)
            else:
                _event(store, context, "kill_switch", f"{len(item['orders'])} open orders at IBKR ({env.value}) cancelled and confirmed.",
                       now=now, environment=env, severity="warning", alert=True)
    for connection, _ in clients:
        try:
            sync(context, connection.environment, now)
        except BrokerRefusal:
            pass
    return {"kill_switch": True, "report": report}


def release_kill(context: TenantContext, now: datetime) -> BrokerSettings:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        settings.kill_switch, settings.kill_switch_at = False, None
        saved = _save_settings(store, context, settings, now)
        _event(store, context, "kill_switch_off", "Kill switch off: new orders can be made again (each still needs your approval).", now=now,
               alert=True)
        return saved


def accept_reconciliation(context: TenantContext, environment: Environment, note: str, now: datetime) -> BrokerSettings:
    """The owner explains a difference: IBKR's current positions become ANUM's starting point again."""
    with open_ibkr_stores(context) as (_, store):
        connection = _connection(store, context, environment)
        positions = {str(p["conid"]): Decimal(str(p["quantity"])) for p in connection.snapshot.get("positions", [])}
        if not connection.snapshot.get("synced_at"):
            raise BrokerRefusal("not_synced", "Read IBKR first (Sync now), then explain the difference.")
        settings = _settings(store, context)
        before = settings.recon(environment)
        after = new_baseline(before, positions, store.orders_for(context, environment), now, explained=before.episodes > before.explained)
        settings.reconciliation[environment.value] = after.model_copy(update={"episodes": before.episodes})
        saved = _save_settings(store, context, settings, now)
        _event(store, context, "mismatch_explained", f"You explained the difference: {note[:300]}", now=now, environment=environment)
        return saved


# -- buy plans (owner, 2 Nov 2026: long-term buy and hold, a small fixed amount on a schedule) -------


class PlanInput(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    symbol: str = Field(min_length=1, max_length=10)
    amount: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    day_of_month: int = Field(ge=1, le=28)
    active: bool = True


def save_plans(context: TenantContext, plans: list[PlanInput], version: int, now: datetime) -> BrokerSettings:
    """Replace the owner's buy plans. Each buys an allowed ETF for at most the per-order limit."""
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        if settings.version != version:
            raise BrokerRefusal("stale", "Your IBKR settings changed in another tab. Reload and try again.")
        if len(plans) > 10:
            raise BrokerRefusal("too_many", "At most 10 buy plans.", status=422)
        before = {p.id: p for p in settings.buy_plans}
        saved: list[BuyPlan] = []
        for item in plans:
            symbol = item.symbol.strip().upper()
            if settings.allowed_symbol(symbol) is None:
                raise BrokerRefusal("symbol_not_allowed", f"{symbol} is not on your allowed list of US ETFs.", status=422)
            if item.amount > settings.limits.max_order:
                raise BrokerRefusal("max_order", f"A plan buys at most your USD {settings.limits.max_order} per order.", status=422)
            old = before.get(item.id or "")
            saved.append(BuyPlan(id=old.id if old else new_id("fin_plan"), symbol=symbol, amount=item.amount, day_of_month=item.day_of_month,
                                 active=item.active, last_proposed=old.last_proposed if old else None))
        settings.buy_plans = saved
        result = _save_settings(store, context, settings, now)
        _event(store, context, "plans_changed", f"Your buy plans changed ({len(saved)} set). A plan only proposes a buy for your approval.", now=now)
        return result


def propose_due_plans(context: TenantContext, now: datetime) -> dict[str, Any]:
    """Each due plan PROPOSES one buy (a limit at IBKR's current price) into the approval queue. Nothing is sent."""
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        due = [p for p in settings.buy_plans if p.due(now)]
        if not due:
            return {"proposed": [], "skipped": []}
        environment = settings.mode(now)
        connection = store.active_connection(context, environment)
    skipped: list[dict[str, str]] = []
    market = us_session(now, settings.limits.session_buffer_minutes)
    if connection is None or connection.verified_at is None:
        return {"proposed": [], "skipped": [{"plan_id": p.id, "reason": "Connect and check IBKR first."} for p in due]}
    if not market.open:
        return {"proposed": [], "skipped": [{"plan_id": p.id, "reason": market.message} for p in due]}
    client = _client(connection)
    proposed: list[Intent] = []
    done: list[str] = []
    for plan in due:
        allowed = settings.allowed_symbol(plan.symbol)
        if allowed is None:
            skipped.append({"plan_id": plan.id, "reason": f"{plan.symbol} is no longer on your allowed list."})
            continue
        try:
            quote = client.quote(allowed.conid)
        except IbkrError as exc:
            skipped.append({"plan_id": plan.id, "reason": exc.message})
            continue
        if quote.last is None or quote.last <= 0 or quote.closing_price:
            skipped.append({"plan_id": plan.id, "reason": "IBKR has no current price for this ETF."})
            continue
        step = Decimal("0.0001") if settings.fractional_ok else Decimal(1)
        quantity = (plan.amount / quote.last).quantize(step, rounding=ROUND_DOWN)
        if quantity <= 0:
            done.append(plan.id)
            with open_ibkr_stores(context) as (_, store):
                _event(store, context, "plan_skipped", f"Buy plan for {plan.symbol}: USD {plan.amount} buys no whole share at {quote.last}. "
                       "Nothing was proposed this month.", now=now, environment=environment, severity="warning", alert=True)
            skipped.append({"plan_id": plan.id, "reason": "The amount buys less than one whole share at today's price."})
            continue
        body = OrderInput(symbol=plan.symbol, side="buy", quantity=quantity, limit_price=quote.last,
                          reason=f"Buy plan: USD {plan.amount} on day {plan.day_of_month}")
        try:
            proposed.append(create_order(context, body, now, proposer=f"buy_plan:{plan.id}"))
            done.append(plan.id)
        except BrokerRefusal as exc:
            skipped.append({"plan_id": plan.id, "reason": exc.message})
    if done:
        month = f"{now.astimezone(NEW_YORK):%Y-%m}"
        with open_ibkr_stores(context) as (_, store):
            settings = _settings(store, context)
            settings.buy_plans = [p.model_copy(update={"last_proposed": month}) if p.id in done else p for p in settings.buy_plans]
            _save_settings(store, context, settings, now)
    return {"proposed": proposed, "skipped": skipped}


def quote(context: TenantContext, symbol: str, now: datetime) -> dict[str, Any]:
    """IBKR's current price for an allowed ETF, labelled real time or delayed (for the order ticket)."""
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        allowed = settings.allowed_symbol(symbol.strip().upper())
        if allowed is None:
            raise BrokerRefusal("symbol_not_allowed", f"{symbol.strip().upper()} is not on your allowed list of US ETFs.", status=422)
        connection = _connection(store, context, settings.mode(now))
        client = _client(connection)
    try:
        found = client.quote(allowed.conid)
    except IbkrError as exc:
        raise BrokerRefusal(exc.code, exc.message, status=502) from None
    age = None if found.updated_ms is None else max(0.0, now.timestamp() - found.updated_ms / 1000)
    usable = found.last is not None and not found.frozen and not found.closing_price
    return {"symbol": allowed.symbol, "last": str(found.last) if usable else None, "bid": _text(found.bid), "ask": _text(found.ask),
            "delayed": found.delayed, "availability": found.availability[:1] or None, "age_seconds": age,
            "band_percent": str(settings.limits.price_band_percent)}
