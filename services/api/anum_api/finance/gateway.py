"""The execution gateway, stage 7 (docs/finance.md#execution-gateway).

The one governed path for any finance action that would move money or trade. Every intent
goes through the same pipeline, in this order, and stops at the first refusal:

    intent -> kill switches -> Guardian ``gate`` (deny stops) -> the person's approval bound
    to the payload hash -> single-use claim (idempotency key) -> executor -> result, audited

* **Executors.** Only ``paper`` and ``shadow`` are implemented (stage 6, simulated, no real
  account). A ``live`` executor is an interface with no implementation
  (``live.LIVE_EXECUTORS`` is empty): every live intent is refused with "live execution is
  not enabled", before anything else, and the database refuses a live intent in any
  approved or executed state.
* **Approvals.** ANUM's approval binding rule (``approval_integrity.py``): the person sees
  the intent and a SHA-256 hash of its canonical JSON (id, owner, mode, kind, portfolio,
  instrument, currency, side, order type, quantity, limit, amount, account, idempotency
  key); approving means sending that hash back. The gateway recomputes it from the stored
  intent, so any change invalidates the approval. Approvals expire
  (``ANUM_FINANCE_APPROVAL_MINUTES``), are used once (a compare-and-set claim), and only
  the intent's owner can approve: there is no agent tool for it, the route needs the
  person's own session, and a database check refuses an approver other than the owner.
  Finance approvals live in the owner-only ``finance_exec_intents`` table rather than the
  workspace approvals queue, because that queue is routed to workspace approvers and would
  show one person's private finance to others.
* **Agents** may create paper or shadow trade intents only (``requested_by = agent``),
  never live, never settings, and never approve (also database checks).
* **Kill switches.** The organisation's (``finance_exec_global``, owners and organisation
  administrators) and the person's own (``finance_exec_settings``). Either one stops every
  executor at once; both are audited.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from ..schemas import TenantContext, new_id
from .config import finance_config
from .connectors import PROVIDERS, is_read_only
from .guardian import Decision, GuardianAction, canonical_json, gate
from .execution import GUARDIAN_KIND, ExecMode, ExecSettings, GlobalSwitch, Intent, IntentKind, IntentStatus
from .live import LIVE_REFUSAL, LiveLimits, Precondition, live_preconditions
from .money import quantize
from .policy import current_rules, ledger_state
from .store import FinanceStore
from .summary import settings_for, zone
from .trading import (
    SIMULATED_NOTE,
    FeeModel,
    InstrumentKind,
    OrderStatus,
    OrderType,
    PaperOrder,
    Portfolio,
    PortfolioMode,
    Side,
    check_paper_fill,
    fill_at,
    limit_met,
    price_for,
    replay,
)

MAX_INTENTS_PENDING = 50


# -- the payload hash -------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def intent_payload(intent: Intent) -> dict[str, Any]:
    """Exactly what an approval binds to (amounts and quantities as exact decimal strings)."""
    return {
        "account_id": intent.account_id, "amount": _text(intent.amount), "currency": intent.currency, "id": intent.id,
        "idempotency_key": intent.idempotency_key, "instrument_kind": _text(intent.instrument_kind), "kind": intent.kind.value,
        "limit_price": _text(intent.limit_price), "live_profile": intent.live_profile, "mode": intent.mode.value,
        "order_type": _text(intent.order_type), "owner": intent.owner_user_id, "portfolio_id": intent.portfolio_id,
        "quantity": _text(intent.quantity), "side": _text(intent.side), "symbol": intent.symbol,
    }


def intent_hash(intent: Intent) -> str:
    return hashlib.sha256(canonical_json(intent_payload(intent))).hexdigest()


# -- refusals -------------------------------------------------------------------------------------


class GatewayRefusal(Exception):
    """The gateway did not run the intent. ``message`` is safe to show; ``code`` is stable."""

    def __init__(self, code: str, message: str, *, status: int = 409, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def write_connector_exists() -> bool:
    """True only if some connector could write. None can: every scope ends in ``:read``."""
    return any(not is_read_only(spec.scopes) for spec in PROVIDERS.values())


def preconditions(store: FinanceStore, context: TenantContext) -> list[Precondition]:
    mine = store.get_exec_settings(context) or ExecSettings()
    halt = store.get_global_switch(context) or GlobalSwitch()
    return live_preconditions(
        flag=finance_config.live_enabled, write_connector=write_connector_exists(), opted_in=mine.live_opt_in,
        limits_set=mine.live_limits.is_set(), global_halt=halt.engaged, person_halt=mine.kill_switch,
    )


def refuse_live(store: FinanceStore, context: TenantContext) -> GatewayRefusal:
    unmet = [p for p in preconditions(store, context) if not p.met]
    return GatewayRefusal("live_not_enabled", LIVE_REFUSAL, details={"unmet": [p.code for p in unmet]})


def check_switches(store: FinanceStore, context: TenantContext) -> None:
    halt = store.get_global_switch(context) or GlobalSwitch()
    if halt.engaged:
        raise GatewayRefusal("kill_switch_global", "Your organisation's kill switch is on: nothing runs until an owner turns it off.")
    mine = store.get_exec_settings(context) or ExecSettings()
    if mine.kill_switch:
        raise GatewayRefusal("kill_switch_person", "Your kill switch is on: nothing runs until you turn it off under Finance, Paper trading.")


# -- pricing and Guardian ---------------------------------------------------------------------------


def _today(store: FinanceStore, context: TenantContext, now: datetime) -> Any:
    return now.astimezone(zone(settings_for(store, context).timezone)).date()


def _point(store: FinanceStore, context: TenantContext, portfolio: Portfolio, kind: InstrumentKind, symbol: str, currency: str, now: datetime) -> Any:
    today = _today(store, context, now)
    return price_for(kind, symbol, currency, store.list_prices(context, symbol=None, limit=5000), store.list_fx_rates(context, limit=5000),
                     today, portfolio.max_price_age_days)


def estimate(store: FinanceStore, context: TenantContext, portfolio: Portfolio, intent: Intent, now: datetime) -> dict[str, Any] | None:
    """What the order would fill at now (recorded price, slippage, fee), or None with no price."""
    assert intent.instrument_kind and intent.symbol and intent.currency and intent.side and intent.quantity  # nosec B101 - a trade has them
    point = _point(store, context, portfolio, intent.instrument_kind, intent.symbol, intent.currency, now)
    if point is None:
        return None
    fill = fill_at(point, intent.side, intent.quantity, intent.currency, FeeModel.of(portfolio))
    return {
        "market_price": str(fill.market_price), "fill_price": str(fill.fill_price), "notional": str(fill.notional), "fee": str(fill.fee),
        "slippage": str(fill.slippage), "price_as_of": fill.as_of.isoformat(), "price_source": fill.source,
        "fee_bps": str(portfolio.fee_bps), "fee_min": str(portfolio.fee_min), "slippage_bps": str(portfolio.slippage_bps),
        "limit_met": limit_met(fill, intent.side, intent.limit_price), "simulated": True, "note": SIMULATED_NOTE,
    }


def guardian_decision(store: FinanceStore, context: TenantContext, intent: Intent, amount: Decimal, now: datetime) -> tuple[Decision, int]:
    rules, version = current_rules(store, context)
    action = GuardianAction(
        kind=GUARDIAN_KIND[intent.kind], amount=amount, currency=intent.currency or "AED", merchant=intent.symbol,
        at=now, first_requested_at=intent.created_at, requested_by=intent.requested_by,
    )
    state = ledger_state(store, context, _today(store, context, now))
    return gate(action, rules, state, simulated=intent.mode in {ExecMode.PAPER, ExecMode.SHADOW}), version


def _cooling_until(decision: Decision) -> datetime | None:
    for reason in decision.reasons:
        if reason.code == "cooling_off" and reason.params.get("until"):
            return datetime.fromisoformat(reason.params["until"])
    return None


def _decision_dump(decision: Decision) -> dict[str, Any]:
    return decision.model_dump(mode="json")


# -- creating an intent -------------------------------------------------------------------------------


class TradeSpec(BaseModel):
    """A checked trade request (the API and the agent tool build it)."""

    mode: ExecMode
    portfolio_id: str
    instrument_kind: InstrumentKind
    symbol: str
    currency: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    reason: str | None = None
    idempotency_key: str | None = None


def own_portfolio(store: FinanceStore, context: TenantContext, portfolio_id: str) -> Portfolio:
    found = store.get_portfolio(context, portfolio_id)
    if found is None:
        raise GatewayRefusal("not_found", "Portfolio not found. It may have been deleted.", status=404)
    return found


def _same_request(existing: Intent, spec: TradeSpec) -> bool:
    return (existing.mode, existing.portfolio_id, existing.instrument_kind, existing.symbol, existing.currency, existing.side,
            existing.order_type, existing.quantity, existing.limit_price) == (
        spec.mode, spec.portfolio_id, spec.instrument_kind, spec.symbol, spec.currency, spec.side, spec.order_type, spec.quantity,
        spec.limit_price)


def create_trade_intent(
    store: FinanceStore, context: TenantContext, spec: TradeSpec, *, requested_by: Literal["person", "agent"], proposer: str, now: datetime,
) -> Intent:
    """A paper or shadow trade intent: Guardian first, then waiting for the person's approval.

    A live intent is refused here, before anything else: live execution is not enabled.
    """
    if spec.mode == ExecMode.LIVE:
        raise refuse_live(store, context)
    if spec.mode not in {ExecMode.PAPER, ExecMode.SHADOW}:
        raise GatewayRefusal("invalid_mode", "A trade is paper, shadow or live.", status=422)
    portfolio = own_portfolio(store, context, spec.portfolio_id)
    if portfolio.mode.value != spec.mode.value:
        raise GatewayRefusal("mode_mismatch", f"This is a {portfolio.mode.value} portfolio: place {portfolio.mode.value} orders in it.", status=422)
    if spec.order_type == OrderType.LIMIT and spec.limit_price is None:
        raise GatewayRefusal("limit_required", "A limit order needs a limit price.", status=422)
    if spec.order_type == OrderType.MARKET and spec.limit_price is not None:
        raise GatewayRefusal("limit_not_allowed", "A market order has no limit price.", status=422)
    if spec.instrument_kind == InstrumentKind.FX and spec.symbol == spec.currency:
        raise GatewayRefusal("same_currency", "Buy or sell a currency with another one.", status=422)
    check_switches(store, context)
    key = spec.idempotency_key or new_id("fin_idem")
    existing = store.intent_by_key(context, key)
    if existing is not None:
        if not _same_request(existing, spec):
            raise GatewayRefusal("idempotency_conflict", "This idempotency key was used for a different request.")
        return existing
    _, waiting = store.list_intents(context, status=IntentStatus.PENDING.value, mode=None, limit=1, offset=0)
    if waiting >= MAX_INTENTS_PENDING:
        raise GatewayRefusal("too_many_pending", f"{MAX_INTENTS_PENDING} requests are waiting. Approve or reject some first.")
    window = timedelta(minutes=finance_config.approval_minutes)
    intent = Intent(
        id=new_id("fin_int"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        mode=spec.mode, kind=IntentKind.TRADE, portfolio_id=portfolio.id, instrument_kind=spec.instrument_kind, symbol=spec.symbol,
        currency=spec.currency, side=spec.side, order_type=spec.order_type, quantity=spec.quantity, limit_price=spec.limit_price,
        requested_by=requested_by, proposer=proposer[:160], reason=spec.reason, idempotency_key=key, payload_hash="0" * 64,
        status=IntentStatus.PENDING, created_at=now, expires_at=now + window, updated_at=now,
    )
    intent.estimate = estimate(store, context, portfolio, intent, now)
    if intent.estimate is not None:
        amount = Decimal(intent.estimate["notional"])
    elif spec.order_type == OrderType.LIMIT and spec.limit_price is not None:
        amount = quantize(spec.quantity * spec.limit_price, spec.currency)
    else:
        amount = None
    if amount is None or amount <= 0:
        intent.status, intent.refusal_code = IntentStatus.REFUSED, "no_price"
        intent.refusal = (f"No price for {spec.symbol} in {spec.currency} is recorded in the last {portfolio.max_price_age_days} days, "
                          "so a market order cannot be simulated. Record a price, or place a limit order that waits for one.")
    else:
        decision, version = guardian_decision(store, context, intent, amount, now)
        intent.decision, intent.guardian_version = _decision_dump(decision), version
        if decision.outcome == "deny":
            intent.status = IntentStatus.DENIED
        else:
            until = _cooling_until(decision)
            if until is not None:
                intent.expires_at = max(intent.expires_at, until + window)
    intent.payload_hash = intent_hash(intent)
    if not store.save_intent(intent):
        raise GatewayRefusal("idempotency_conflict", "This idempotency key was used already.")
    return intent


# -- approving, rejecting, expiring ----------------------------------------------------------------


def expire(store: FinanceStore, intents: list[Intent], now: datetime) -> list[Intent]:
    for intent in intents:
        if intent.lapsed(now):
            intent.status, intent.updated_at = IntentStatus.EXPIRED, now
            store.save_intent(intent)
    return intents


def own_intent(store: FinanceStore, context: TenantContext, intent_id: str) -> Intent:
    found = store.get_intent(context, intent_id)
    if found is None:
        raise GatewayRefusal("not_found", "Request not found. It may have expired and been removed.", status=404)
    return found


def approve(store: FinanceStore, context: TenantContext, intent_id: str, shown_hash: str, now: datetime) -> Intent:
    """The person's approval of one intent: checked in full, used once, then executed.

    Nothing runs unless every check passes: the intent is the caller's own and still
    pending, the hash is the one shown and still matches the stored intent, the approval
    has not expired, no kill switch is on, the Guardian gate does not deny it now, and the
    single-use claim succeeds. Live intents are refused whatever else holds.
    """
    intent = own_intent(store, context, intent_id)
    if intent.mode == ExecMode.LIVE:
        raise refuse_live(store, context)
    if intent.owner_user_id != context.user_id:  # the store already filters on the owner
        raise GatewayRefusal("not_owner", "Only the person who owns this request can approve it.", status=403)
    if intent.lapsed(now):
        intent.status, intent.updated_at = IntentStatus.EXPIRED, now
        store.save_intent(intent)
    if intent.status != IntentStatus.PENDING:
        raise GatewayRefusal("not_pending", f"This request is {intent.status.value.replace('_', ' ')}: it cannot be approved. Make a new one.")
    if shown_hash != intent.payload_hash or shown_hash != intent_hash(intent):
        raise GatewayRefusal("hash_mismatch", "The approval does not match the request shown. Reload it and approve again.")
    if intent.mode == ExecMode.SETTINGS:
        return _apply_live_profile(store, context, intent, now)
    check_switches(store, context)
    portfolio = own_portfolio(store, context, intent.portfolio_id or "")
    fresh = estimate(store, context, portfolio, intent, now)
    amount = Decimal(fresh["notional"]) if fresh else (
        quantize(intent.quantity * intent.limit_price, intent.currency or "AED")
        if intent.limit_price is not None and intent.quantity is not None else None)
    if amount is None:
        raise GatewayRefusal("no_price", "No recent price is recorded now. Record a price and approve again.")
    decision, version = guardian_decision(store, context, intent, amount, now)
    if decision.outcome == "deny":
        intent.status, intent.decision, intent.guardian_version, intent.updated_at = IntentStatus.DENIED, _decision_dump(decision), version, now
        store.save_intent(intent)
        raise GatewayRefusal("guardian_denied", "Your rules block this now: " + " ".join(r.message for r in decision.reasons if r.outcome == "deny"),
                             details={"codes": [r.code for r in decision.reasons if r.outcome == "deny"]})
    until = _cooling_until(decision)
    if until is not None and now < until:
        raise GatewayRefusal("cooling_off", f"Your cooling-off period ends at {until.isoformat()}. Approve it after that.")
    if not store.claim_intent(context, intent.id, now, context.user_id):
        raise GatewayRefusal("already_used", "This approval was used already. Nothing ran twice.")
    intent.status, intent.approved_at, intent.approved_by = IntentStatus.EXECUTING, now, context.user_id
    intent.decision, intent.guardian_version = _decision_dump(decision), version
    try:
        order = EXECUTORS[intent.mode].execute(store, context, intent, portfolio, now)
    except Exception:
        intent.status, intent.updated_at = IntentStatus.FAILED, now
        intent.result = {"error": "The simulation could not run. Nothing was bought, sold or sent."}
        store.save_intent(intent)
        raise
    intent.status, intent.executed_at, intent.updated_at = IntentStatus.EXECUTED, now, now
    intent.result = {"order_id": order.id, "order_status": order.status.value, "reason_code": order.reason_code, "simulated": True}
    store.save_intent(intent)
    return intent


def reject(store: FinanceStore, context: TenantContext, intent_id: str, now: datetime) -> Intent:
    intent = own_intent(store, context, intent_id)
    if intent.status != IntentStatus.PENDING:
        raise GatewayRefusal("not_pending", f"This request is {intent.status.value.replace('_', ' ')} already.")
    intent.status, intent.updated_at = IntentStatus.REJECTED, now
    store.save_intent(intent)
    return intent


# -- executors -------------------------------------------------------------------------------------


class SimulatedExecutor:
    """The paper and shadow executors: a simulated order against recorded prices. Nothing real."""

    def __init__(self, mode: PortfolioMode) -> None:
        self.mode = mode
        self.name = mode.value

    def execute(self, store: FinanceStore, context: TenantContext, intent: Intent, portfolio: Portfolio, now: datetime) -> PaperOrder:
        # The intent id is the idempotency key: one order per intent, ever (unique in the database).
        existing = store.order_for_intent(context, intent.id)
        if existing is not None:
            return existing
        assert intent.instrument_kind and intent.symbol and intent.currency and intent.side and intent.order_type and intent.quantity  # nosec B101
        order = PaperOrder(
            id=new_id("fin_pord"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            portfolio_id=portfolio.id, intent_id=intent.id, epoch=portfolio.epoch, mode=portfolio.mode, instrument_kind=intent.instrument_kind,
            symbol=intent.symbol, currency=intent.currency, side=intent.side, order_type=intent.order_type, quantity=intent.quantity,
            limit_price=intent.limit_price, status=OrderStatus.WAITING, requested_by=intent.requested_by,
            guardian_outcome=intent.decision.get("outcome"), guardian_codes=[r.get("code", "") for r in intent.decision.get("reasons", [])],
            created_at=now, updated_at=now,
        )
        try_fill(store, context, portfolio, order, now)
        if not store.add_order(order):
            found = store.order_for_intent(context, intent.id)
            if found is None:  # pragma: no cover - the unique key says it exists
                raise RuntimeError("order missing")
            return found
        return order


EXECUTORS: dict[ExecMode, SimulatedExecutor] = {
    ExecMode.PAPER: SimulatedExecutor(PortfolioMode.PAPER),
    ExecMode.SHADOW: SimulatedExecutor(PortfolioMode.SHADOW),
}


def try_fill(store: FinanceStore, context: TenantContext, portfolio: Portfolio, order: PaperOrder, now: datetime) -> PaperOrder:
    """Fill, keep waiting or refuse one order against the newest recorded price (mutates ``order``)."""
    point = _point(store, context, portfolio, order.instrument_kind, order.symbol, order.currency, now)
    order.updated_at = now
    if point is None:
        if order.order_type == OrderType.MARKET:
            order.status, order.reason_code = OrderStatus.REFUSED, "no_price"
            order.reason = f"No price for {order.symbol} in {order.currency} is recorded in the last {portfolio.max_price_age_days} days."
        else:
            order.reason_code, order.reason = "waiting_price", "Waiting for a recorded price."
        return order
    fill = fill_at(point, order.side, order.quantity, order.currency, FeeModel.of(portfolio))
    if not limit_met(fill, order.side, order.limit_price):
        order.reason_code, order.reason = "waiting_limit", "Waiting: the newest recorded price does not meet the limit."
        order.market_price, order.price_as_of, order.price_source = fill.market_price, fill.as_of, fill.source
        return order
    if portfolio.mode == PortfolioMode.PAPER:
        others = [o for o in store.portfolio_orders(context, portfolio.id, portfolio.epoch) if o.id != order.id]
        refusal = check_paper_fill(replay(portfolio, others), order, fill)
        if refusal is not None:
            order.status, order.reason_code, order.reason = OrderStatus.REFUSED, refusal.code, refusal.message
            return order
    order.status, order.reason_code, order.reason = OrderStatus.FILLED, None, None
    order.market_price, order.fill_price, order.notional, order.fee, order.slippage = (
        fill.market_price, fill.fill_price, fill.notional, fill.fee, fill.slippage)
    order.price_as_of, order.price_source, order.filled_at = fill.as_of, fill.source, now
    return order


def check_waiting(store: FinanceStore, context: TenantContext, portfolio: Portfolio, now: datetime) -> int:
    """Try every waiting order of the current run again. Returns how many filled or were refused."""
    changed = 0
    for order in store.portfolio_orders(context, portfolio.id, portfolio.epoch):
        if order.status != OrderStatus.WAITING:
            continue
        try_fill(store, context, portfolio, order, now)
        store.save_order(order)
        changed += int(order.status != OrderStatus.WAITING)
    return changed


# -- stage 8: the person's live limits and opt-in -------------------------------------------------


def create_live_profile_change(
    store: FinanceStore, context: TenantContext, limits: LiveLimits, opt_in: bool, reason: str | None, now: datetime,
) -> Intent:
    """A change of the person's live limits (and opt-in), waiting for their approval with the hash.

    Opting in is refused while live execution is not enabled on this deployment: the
    limits can be set now, the opt-in only once the owner's steps are done.
    """
    if opt_in and not finance_config.live_enabled:
        raise GatewayRefusal("live_not_enabled", "Live execution is not enabled on this deployment, so you cannot opt in. You can set your limits now.",
                             details={"unmet": [p.code for p in preconditions(store, context) if not p.met]})
    current = store.get_exec_settings(context) or ExecSettings()
    window = timedelta(minutes=finance_config.approval_minutes)
    profile = {"base_version": current.live_version, "limits": limits.model_dump(mode="json"), "opt_in": opt_in}
    intent = Intent(
        id=new_id("fin_int"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        mode=ExecMode.SETTINGS, kind=IntentKind.LIVE_PROFILE, live_profile=profile, requested_by="person", proposer=context.user_id,
        reason=reason, idempotency_key=new_id("fin_idem"), payload_hash="0" * 64, status=IntentStatus.PENDING,
        created_at=now, expires_at=now + window, updated_at=now,
    )
    intent.payload_hash = intent_hash(intent)
    store.save_intent(intent)
    return intent


def _apply_live_profile(store: FinanceStore, context: TenantContext, intent: Intent, now: datetime) -> Intent:
    profile = intent.live_profile or {}
    current = store.get_exec_settings(context) or ExecSettings()
    if profile.get("base_version") != current.live_version:
        intent.status, intent.updated_at = IntentStatus.REFUSED, now
        intent.refusal_code, intent.refusal = "stale", "Your live settings changed since this was made. Review it again."
        store.save_intent(intent)
        raise GatewayRefusal("stale", intent.refusal)
    if profile.get("opt_in") and not finance_config.live_enabled:
        intent.status, intent.updated_at = IntentStatus.REFUSED, now
        intent.refusal_code, intent.refusal = "live_not_enabled", LIVE_REFUSAL
        store.save_intent(intent)
        raise refuse_live(store, context)
    if not store.claim_intent(context, intent.id, now, context.user_id):
        raise GatewayRefusal("already_used", "This approval was used already.")
    current.live_limits = LiveLimits.model_validate(profile.get("limits") or {})
    current.live_opt_in = bool(profile.get("opt_in"))
    current.live_opt_in_at = now if current.live_opt_in else None
    current.live_version += 1
    current.updated_at = now
    store.save_exec_settings(context, current)
    intent.status, intent.approved_at, intent.approved_by, intent.executed_at, intent.updated_at = (
        IntentStatus.EXECUTED, now, context.user_id, now, now)
    intent.result = {"live_version": current.live_version}
    store.save_intent(intent)
    return intent
