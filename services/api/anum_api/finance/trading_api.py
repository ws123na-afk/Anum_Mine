"""Routes for Finance stages 6 to 8 under ``/api/v1/finance`` (docs/finance.md#api).

* Stage 6, paper and shadow trading (SIMULATED): recorded prices, portfolios with simulated
  cash, positions and P&L, order history, reset, and the shadow comparison.
* Stage 7, the execution gateway: intents, the person's approval bound to the payload hash,
  the kill switches.
* Stage 8, limited live: the person's live limits and opt-in (each change approved with the
  hash), and the preconditions. Live execution is not enabled: every live intent is refused.

``finance:read`` reads, ``finance:manage`` changes; the organisation's kill switch also needs
a workspace owner or an organisation administrator. Every record is the caller's own. Audit
records carry ids, modes, statuses and rule codes only: never an amount, a price, a
quantity, a symbol or a description.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import repository_context, require_owner_or_org_admin, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import gateway
from .api import _audit, _clean, _invalid, _manage, _not_found, _read
from .config import finance_config
from .execution import ExecMode, ExecSettings, GlobalSwitch, Intent, IntentStatus
from .gateway import GatewayRefusal, TradeSpec
from .live import LIVE_REFUSAL, LiveLimits, Precondition
from .money import MoneyError, normalize_currency, to_money
from .store import MAX_ORDERS, MAX_PAGE, MAX_PORTFOLIOS, MAX_PRICES, FinanceStore, open_finance_store
from .summary import settings_for, today_in
from .trading import (
    SIMULATED_NOTE,
    CashLine,
    InstrumentKind,
    MoneyRow,
    OrderStatus,
    OrderType,
    PaperOrder,
    Portfolio,
    PortfolioMode,
    PositionView,
    PriceRecord,
    ShadowEntry,
    Side,
    TradingError,
    check_price,
    check_quantity,
    compare_shadow,
    normalize_symbol,
    replay,
    value_book,
)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
GLOBAL_DENIED = "Only workspace owners and organisation administrators can change the organisation's kill switch."
OWNER_STEPS = [
    "A provider account with a licensed broker or bank in your name, and its API documentation.",
    "An API key for that provider with exactly the write permission needed (no withdrawals), stored encrypted, never shown to an assistant.",
    "A live executor built and reviewed against that provider's documented API, with its own tests.",
    "Your live limits: per action, per day, per month, currencies, and the instruments or accounts allowed.",
    "Your explicit written approval to switch on bounded live actions, recorded before the operator sets ANUM_FINANCE_LIVE_ENABLED.",
]


def _refused(exc: GatewayRefusal) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _audit_refusal(repository: AnumRepository, context: TenantContext, action: str, target: str, exc: GatewayRefusal) -> None:
    """A refused gateway step, recorded as denied: the code only, never what was asked."""
    meta: dict[str, Any] = {"code": exc.code}
    if exc.details.get("unmet"):
        meta["unmet"] = list(exc.details["unmet"])
    if exc.details.get("codes"):
        meta["guardian_codes"] = list(exc.details["codes"])
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id, action=action,
        target=target, outcome="denied", correlation_id=target, created_at=utc_now(), metadata=meta,
    ))


SCOPE = {"tenant_id", "workspace_id", "owner_user_id"}

# -- stage 6: recorded prices ---------------------------------------------------------------------------


class PriceView(BaseModel):
    id: str
    symbol: str
    currency: str
    price: Decimal
    as_of: date
    source: str
    recorded_at: datetime


class PriceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    currency: str = Field(min_length=3, max_length=3)
    price: str | int | Decimal
    as_of: date


class PriceSaved(BaseModel):
    price: PriceView
    orders_changed: int


def _price_view(item: PriceRecord) -> PriceView:
    return PriceView(**item.model_dump(exclude=SCOPE | {"connection_id"}))


@router.get("/prices", response_model=list[PriceView])
def list_prices(
    symbol: str | None = Query(default=None, max_length=20), limit: int = Query(default=100, ge=1, le=500),
    context: TenantContext = Depends(tenant_context),
) -> list[PriceView]:
    """Prices you recorded, newest first. ANUM has no market-price source: it never invents one."""
    _read(context)
    try:
        wanted = normalize_symbol(symbol) if symbol else None
    except TradingError as exc:
        raise _invalid(str(exc)) from None
    with open_finance_store(context) as store:
        return [_price_view(p) for p in store.list_prices(context, symbol=wanted, limit=limit)]


@router.post("/prices", response_model=PriceSaved, status_code=status.HTTP_201_CREATED)
def add_price(
    body: PriceInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> PriceSaved:
    """Record a price you saw (source ``manual``). Waiting paper and shadow orders are checked against it."""
    _manage(context)
    try:
        symbol, currency, price = normalize_symbol(body.symbol), normalize_currency(body.currency), check_price(body.price)
    except (TradingError, MoneyError) as exc:
        raise _invalid(str(exc)) from None
    now = utc_now()
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        if body.as_of > today or body.as_of < today - timedelta(days=366):
            raise _invalid("The price's date must be within the last year and not in the future.")
        if len(store.list_prices(context, symbol=None, limit=MAX_PRICES)) >= MAX_PRICES:
            raise HTTPException(status.HTTP_409_CONFLICT, "Too many prices are recorded. Delete old ones first.")
        item = PriceRecord(id=new_id("fin_price"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                           symbol=symbol, currency=currency, price=price, as_of=body.as_of, source="manual", recorded_at=now)
        if not store.save_price(item):
            raise HTTPException(status.HTTP_409_CONFLICT, "You already recorded a price for this symbol and currency on that date. Delete it first.")
        changed = sum(gateway.check_waiting(store, context, p, now) for p in store.list_portfolios(context))
    _audit(repository, context, "finance.price.create", item.id, {"orders_changed": changed})
    return PriceSaved(price=_price_view(item), orders_changed=changed)


@router.delete("/prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_price(price_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_finance_store(context) as store:
        if not store.delete_price(context, price_id):
            raise _not_found("Price")
    _audit(repository, context, "finance.price.delete", price_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- stage 6: portfolios ----------------------------------------------------------------------------------


class Assumptions(BaseModel):
    fee_bps: Decimal
    fee_min: Decimal
    slippage_bps: Decimal
    max_price_age_days: int


class PortfolioSummary(BaseModel):
    id: str
    name: str
    mode: PortfolioMode
    epoch: int
    assumptions: Assumptions
    starting_cash: list[CashLine]
    cash: list[MoneyRow]
    realised: list[MoneyRow]
    unrealised: list[MoneyRow]
    positions: int
    waiting: int
    created_at: datetime
    reset_at: datetime | None
    simulated: bool = True


class PortfolioView(PortfolioSummary):
    holdings: list[PositionView]
    fees: list[MoneyRow]
    slippage: list[MoneyRow]
    unpriced: list[str]
    shadow: list[ShadowEntry]
    shadow_totals: list[MoneyRow]
    note: str


class OrderView(BaseModel):
    id: str
    portfolio_id: str
    intent_id: str
    epoch: int
    mode: PortfolioMode
    instrument_kind: InstrumentKind
    symbol: str
    currency: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    status: OrderStatus
    reason_code: str | None
    reason: str | None
    requested_by: str
    market_price: Decimal | None
    fill_price: Decimal | None
    price_as_of: date | None
    price_source: str | None
    notional: Decimal | None
    fee: Decimal | None
    slippage: Decimal | None
    guardian_outcome: str | None
    guardian_codes: list[str]
    created_at: datetime
    filled_at: datetime | None
    simulated: bool = True


class OrderPage(BaseModel):
    items: list[OrderView]
    total: int
    limit: int
    offset: int


class CashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(min_length=3, max_length=3)
    amount: str | int | Decimal


class PortfolioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    mode: PortfolioMode = PortfolioMode.PAPER
    starting_cash: list[CashInput] = Field(default_factory=list, max_length=10)
    fee_bps: str | int | Decimal = "10"
    fee_min: str | int | Decimal = "0"
    slippage_bps: str | int | Decimal = "5"
    max_price_age_days: int = Field(default=3, ge=1, le=30)


class PortfolioPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    fee_bps: str | int | Decimal | None = None
    fee_min: str | int | Decimal | None = None
    slippage_bps: str | int | Decimal | None = None
    max_price_age_days: int | None = Field(default=None, ge=1, le=30)


class ResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starting_cash: list[CashInput] | None = Field(default=None, max_length=10)


def _bps(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001
        raise _invalid(f"{field}: enter a number of basis points, for example 10.") from None
    if not number.is_finite() or number < 0 or number > 1000 or number != number.quantize(Decimal("0.01")):
        raise _invalid(f"{field}: between 0 and 1000 basis points (10 percent), with at most 2 decimals.")
    return number.quantize(Decimal("0.01"))


def _fee_min(value: object) -> Decimal:
    try:
        amount = to_money(value, "AED")
    except MoneyError as exc:
        raise _invalid(f"Minimum fee: {exc}") from None
    if amount < 0 or amount > 10000:
        raise _invalid("Minimum fee: between 0 and 10,000 in the order's currency.")
    return amount


def _cash(rows: list[CashInput], mode: PortfolioMode) -> list[CashLine]:
    if mode == PortfolioMode.SHADOW:
        if rows:
            raise _invalid("A shadow portfolio has no cash: it records what orders would have done.")
        return []
    if not rows:
        raise _invalid("Starting cash: add at least one currency and amount, for example AED 10000.")
    found: dict[str, Decimal] = {}
    for row in rows:
        try:
            currency = normalize_currency(row.currency)
            amount = to_money(row.amount, currency)
        except MoneyError as exc:
            raise _invalid(f"Starting cash: {exc}") from None
        if currency in found:
            raise _invalid(f"Starting cash: {currency} is listed twice.")
        if amount <= 0 or amount > Decimal(1_000_000_000):
            raise _invalid("Starting cash: an amount above zero and at most 1,000,000,000.")
        found[currency] = amount
    return [CashLine(currency=c, amount=a) for c, a in sorted(found.items())]


def _own_portfolio(store: FinanceStore, context: TenantContext, portfolio_id: str) -> Portfolio:
    found = store.get_portfolio(context, portfolio_id)
    if found is None:
        raise _not_found("Portfolio")
    return found


def _assumptions(p: Portfolio) -> Assumptions:
    return Assumptions(fee_bps=p.fee_bps, fee_min=p.fee_min, slippage_bps=p.slippage_bps, max_price_age_days=p.max_price_age_days)


def portfolio_view(store: FinanceStore, context: TenantContext, portfolio: Portfolio, now: datetime) -> PortfolioView:
    orders = store.portfolio_orders(context, portfolio.id, portfolio.epoch)
    prices, rates = store.list_prices(context, symbol=None, limit=MAX_PRICES), store.list_fx_rates(context, limit=5000)
    today = today_in(settings_for(store, context), now)
    valuation = value_book(replay(portfolio, orders), prices, rates, today, portfolio.max_price_age_days)
    shadow, shadow_totals = ([], [])
    if portfolio.mode == PortfolioMode.SHADOW:
        shadow, shadow_totals = compare_shadow(orders, prices, rates, today, portfolio.max_price_age_days)
    return PortfolioView(
        id=portfolio.id, name=portfolio.name, mode=portfolio.mode, epoch=portfolio.epoch, assumptions=_assumptions(portfolio),
        starting_cash=portfolio.starting_cash, cash=valuation.cash, realised=valuation.realised, unrealised=valuation.unrealised,
        positions=len(valuation.positions), waiting=sum(1 for o in orders if o.status == OrderStatus.WAITING),
        created_at=portfolio.created_at, reset_at=portfolio.reset_at, holdings=valuation.positions, fees=valuation.fees,
        slippage=valuation.slippage, unpriced=valuation.unpriced, shadow=shadow[:200], shadow_totals=shadow_totals, note=SIMULATED_NOTE,
    )


def _summary(view: PortfolioView) -> PortfolioSummary:
    return PortfolioSummary(**view.model_dump(include=set(PortfolioSummary.model_fields)))


@router.get("/paper/portfolios", response_model=list[PortfolioSummary])
def list_portfolios(context: TenantContext = Depends(tenant_context)) -> list[PortfolioSummary]:
    """Your paper and shadow portfolios with their simulated cash and P&L. SIMULATED."""
    _read(context)
    now = utc_now()
    with open_finance_store(context) as store:
        return [_summary(portfolio_view(store, context, p, now)) for p in store.list_portfolios(context)]


@router.post("/paper/portfolios", response_model=PortfolioView, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    body: PortfolioInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> PortfolioView:
    _manage(context)
    now = utc_now()
    portfolio = Portfolio(
        id=new_id("fin_port"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        name=_clean(body.name, 80) or "Paper portfolio", mode=body.mode, starting_cash=_cash(body.starting_cash, body.mode),
        fee_bps=_bps(body.fee_bps, "Fee"), fee_min=_fee_min(body.fee_min), slippage_bps=_bps(body.slippage_bps, "Slippage"),
        max_price_age_days=body.max_price_age_days, created_at=now, updated_at=now,
    )
    with open_finance_store(context) as store:
        if len(store.list_portfolios(context)) >= MAX_PORTFOLIOS:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_PORTFOLIOS} portfolios. Delete one first.")
        store.save_portfolio(portfolio)
        view = portfolio_view(store, context, portfolio, now)
    _audit(repository, context, "finance.paper.portfolio.create", portfolio.id, {"mode": portfolio.mode.value})
    return view


@router.get("/paper/portfolios/{portfolio_id}", response_model=PortfolioView)
def get_portfolio(portfolio_id: str, context: TenantContext = Depends(tenant_context)) -> PortfolioView:
    _read(context)
    with open_finance_store(context) as store:
        return portfolio_view(store, context, _own_portfolio(store, context, portfolio_id), utc_now())


@router.patch("/paper/portfolios/{portfolio_id}", response_model=PortfolioView)
def update_portfolio(
    portfolio_id: str, body: PortfolioPatch, context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PortfolioView:
    """Rename it or change the fee and slippage assumptions (they apply to later fills only)."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        portfolio = _own_portfolio(store, context, portfolio_id)
        if body.name is not None:
            portfolio.name = _clean(body.name, 80) or portfolio.name
        if body.fee_bps is not None:
            portfolio.fee_bps = _bps(body.fee_bps, "Fee")
        if body.fee_min is not None:
            portfolio.fee_min = _fee_min(body.fee_min)
        if body.slippage_bps is not None:
            portfolio.slippage_bps = _bps(body.slippage_bps, "Slippage")
        if body.max_price_age_days is not None:
            portfolio.max_price_age_days = body.max_price_age_days
        portfolio.updated_at = now
        store.save_portfolio(portfolio)
        view = portfolio_view(store, context, portfolio, now)
    _audit(repository, context, "finance.paper.portfolio.update", portfolio_id, {"fields": sorted(body.model_dump(exclude_unset=True))})
    return view


@router.post("/paper/portfolios/{portfolio_id}/reset", response_model=PortfolioView)
def reset_portfolio(
    portfolio_id: str, body: ResetInput, context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PortfolioView:
    """Start a new run: cash back to the start (or new starting cash), no positions. History stays."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        portfolio = _own_portfolio(store, context, portfolio_id)
        if body.starting_cash is not None:
            portfolio.starting_cash = _cash(body.starting_cash, portfolio.mode)
        for order in store.portfolio_orders(context, portfolio.id, portfolio.epoch):
            if order.status == OrderStatus.WAITING:
                order.status, order.reason_code, order.reason, order.updated_at = OrderStatus.CANCELLED, "reset", "Cancelled by a reset.", now
                store.save_order(order)
        portfolio.epoch += 1
        portfolio.reset_at = portfolio.updated_at = now
        store.save_portfolio(portfolio)
        view = portfolio_view(store, context, portfolio, now)
    _audit(repository, context, "finance.paper.portfolio.reset", portfolio_id, {"epoch": portfolio.epoch})
    return view


@router.delete("/paper/portfolios/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(portfolio_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete the portfolio and its simulated orders. Requests made for it stay in the history."""
    _manage(context)
    with open_finance_store(context) as store:
        if not store.delete_portfolio(context, portfolio_id):
            raise _not_found("Portfolio")
    _audit(repository, context, "finance.paper.portfolio.delete", portfolio_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _order_view(order: PaperOrder) -> OrderView:
    return OrderView(**order.model_dump(exclude=SCOPE | {"updated_at"}))


@router.get("/paper/portfolios/{portfolio_id}/orders", response_model=OrderPage)
def list_orders(
    portfolio_id: str,
    status_: Literal["waiting", "filled", "refused", "cancelled"] | None = Query(default=None, alias="status"),
    run: Literal["current", "all"] = Query(default="current"),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=MAX_ORDERS),
    context: TenantContext = Depends(tenant_context),
) -> OrderPage:
    """The order history, newest first: this run, or every run since the portfolio was made."""
    _read(context)
    with open_finance_store(context) as store:
        portfolio = _own_portfolio(store, context, portfolio_id)
        items, total = store.list_orders(context, portfolio.id, epoch=portfolio.epoch if run == "current" else None, status=status_,
                                         limit=limit, offset=offset)
    return OrderPage(items=[_order_view(o) for o in items], total=total, limit=limit, offset=offset)


@router.post("/paper/portfolios/{portfolio_id}/check", response_model=PortfolioView)
def check_orders(
    portfolio_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> PortfolioView:
    """Try waiting limit orders again against the newest recorded prices."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        portfolio = _own_portfolio(store, context, portfolio_id)
        changed = gateway.check_waiting(store, context, portfolio, now)
        view = portfolio_view(store, context, portfolio, now)
    _audit(repository, context, "finance.paper.check", portfolio_id, {"orders_changed": changed})
    return view


@router.post("/paper/orders/{order_id}/cancel", response_model=OrderView)
def cancel_order(order_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OrderView:
    _manage(context)
    with open_finance_store(context) as store:
        order = store.get_order(context, order_id)
        if order is None:
            raise _not_found("Order")
        if order.status != OrderStatus.WAITING:
            raise HTTPException(status.HTTP_409_CONFLICT, f"This order is {order.status.value}: only a waiting order can be cancelled.")
        order.status, order.reason_code, order.reason, order.updated_at = OrderStatus.CANCELLED, "cancelled", "Cancelled by you.", utc_now()
        store.save_order(order)
    _audit(repository, context, "finance.paper.order.cancel", order_id, {"mode": order.mode.value})
    return _order_view(order)


# -- stage 7: the execution gateway ------------------------------------------------------------------------


class IntentView(BaseModel):
    id: str
    mode: ExecMode
    kind: str
    portfolio_id: str | None
    portfolio_name: str | None = None
    instrument_kind: InstrumentKind | None
    symbol: str | None
    currency: str | None
    side: Side | None
    order_type: OrderType | None
    quantity: Decimal | None
    limit_price: Decimal | None
    amount: Decimal | None
    account_id: str | None
    live_profile: dict[str, Any] | None
    requested_by: Literal["person", "agent"]
    reason: str | None
    payload_hash: str
    status: IntentStatus
    decision: dict[str, Any]
    guardian_version: int
    estimate: dict[str, Any] | None
    refusal_code: str | None
    refusal: str | None
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    executed_at: datetime | None
    result: dict[str, Any]
    simulated: bool


class IntentPage(BaseModel):
    items: list[IntentView]
    total: int
    limit: int
    offset: int


class IntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["paper", "shadow", "live"]
    kind: Literal["trade", "transfer", "spend", "bill_payment"] = "trade"
    portfolio_id: str | None = Field(default=None, max_length=80)
    instrument_kind: InstrumentKind = InstrumentKind.SECURITY
    symbol: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    side: Side | None = None
    order_type: OrderType = OrderType.MARKET
    quantity: str | int | Decimal | None = None
    limit_price: str | int | Decimal | None = None
    amount: str | int | Decimal | None = None
    account_id: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=300)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class ApproveInput(BaseModel):
    """The intent's ``payload_hash`` exactly as it was shown to you."""

    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def intent_view(intent: Intent, names: dict[str, str] | None = None) -> IntentView:
    return IntentView(
        **intent.model_dump(exclude=SCOPE | {"proposer", "idempotency_key", "approved_by", "updated_at"}),
        portfolio_name=(names or {}).get(intent.portfolio_id or ""), simulated=intent.mode in {ExecMode.PAPER, ExecMode.SHADOW},
    )


def build_trade_spec(store: FinanceStore, context: TenantContext, body: IntentInput) -> TradeSpec:
    if body.kind != "trade":
        raise _invalid("Paper and shadow requests are trades: transfers, spends and bill payments are never simulated here.")
    if not body.portfolio_id or not body.symbol or not body.side or body.quantity is None:
        raise _invalid("A trade needs a portfolio, a symbol, a side (buy or sell) and a quantity.")
    portfolio = store.get_portfolio(context, body.portfolio_id)
    if portfolio is None:
        raise _not_found("Portfolio")
    try:
        symbol = normalize_symbol(body.symbol)
        if body.instrument_kind == InstrumentKind.FX:
            symbol = normalize_currency(symbol)
        currency = normalize_currency(body.currency or (portfolio.starting_cash[0].currency if portfolio.starting_cash else settings_for(store, context).base_currency))
        quantity = check_quantity(body.quantity)
        limit = check_price(body.limit_price) if body.limit_price not in (None, "") else None
    except (TradingError, MoneyError) as exc:
        raise _invalid(str(exc)) from None
    return TradeSpec(mode=ExecMode(body.mode), portfolio_id=portfolio.id, instrument_kind=body.instrument_kind, symbol=symbol,
                     currency=currency, side=body.side, order_type=body.order_type, quantity=quantity, limit_price=limit,
                     reason=_clean(body.reason, 300) if body.reason else None, idempotency_key=body.idempotency_key)


def _intent_meta(intent: Intent) -> dict[str, Any]:
    return {
        "mode": intent.mode.value, "kind": intent.kind.value, "status": intent.status.value, "requested_by": intent.requested_by,
        "portfolio_id": intent.portfolio_id, "guardian": intent.decision.get("outcome"),
        "guardian_codes": [r.get("code") for r in intent.decision.get("reasons", [])], "refusal": intent.refusal_code,
    }


@router.post("/execution/intents", response_model=IntentView, status_code=status.HTTP_201_CREATED)
def create_intent(
    body: IntentInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> IntentView:
    """A request through the gateway. Paper and shadow: checked by your rules, then waiting for
    your approval. Live: refused, live execution is not enabled."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        try:
            if body.mode == "live":
                raise gateway.refuse_live(store, context)
            spec = build_trade_spec(store, context, body)
            intent = gateway.create_trade_intent(store, context, spec, requested_by="person", proposer=context.user_id, now=now)
        except GatewayRefusal as exc:
            refusal = exc
        else:
            refusal = None
            names = {p.id: p.name for p in store.list_portfolios(context)}
    if refusal is not None:
        _audit_refusal(repository, context, "finance.exec.intent.refused", body.portfolio_id or body.mode, refusal)
        raise _refused(refusal)
    _audit(repository, context, "finance.exec.intent.create", intent.id, _intent_meta(intent))
    return intent_view(intent, names)


@router.get("/execution/intents", response_model=IntentPage)
def list_intents(
    status_: Literal["pending_approval", "denied", "refused", "executing", "executed", "failed", "expired", "rejected"] | None = Query(default=None, alias="status"),
    mode: Literal["paper", "shadow", "live", "settings"] | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> IntentPage:
    _read(context)
    now = utc_now()
    with open_finance_store(context) as store:
        gateway.expire(store, store.list_intents(context, status=IntentStatus.PENDING.value, mode=None, limit=100, offset=0)[0], now)
        items, total = store.list_intents(context, status=status_, mode=mode, limit=limit, offset=offset)
        names = {p.id: p.name for p in store.list_portfolios(context)}
    return IntentPage(items=[intent_view(i, names) for i in items], total=total, limit=limit, offset=offset)


@router.get("/execution/intents/{intent_id}", response_model=IntentView)
def get_intent(intent_id: str, context: TenantContext = Depends(tenant_context)) -> IntentView:
    _read(context)
    with open_finance_store(context) as store:
        intent = store.get_intent(context, intent_id)
        if intent is None:
            raise _not_found("Request")
        gateway.expire(store, [intent], utc_now())
        names = {p.id: p.name for p in store.list_portfolios(context)}
    return intent_view(intent, names)


@router.post("/execution/intents/{intent_id}/approve", response_model=IntentView)
def approve_intent(
    intent_id: str, body: ApproveInput, context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> IntentView:
    """Your approval, with the code you were shown. Used once; nothing runs if anything changed."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        try:
            intent = gateway.approve(store, context, intent_id, body.payload_hash, now)
        except GatewayRefusal as exc:
            refusal = exc
        else:
            refusal = None
            names = {p.id: p.name for p in store.list_portfolios(context)}
    if refusal is not None:
        _audit_refusal(repository, context, "finance.exec.intent.approve", intent_id, refusal)
        raise _refused(refusal)
    _audit(repository, context, "finance.exec.intent.approve", intent.id,
           {**_intent_meta(intent), "order_status": intent.result.get("order_status")})
    return intent_view(intent, names)


@router.post("/execution/intents/{intent_id}/reject", response_model=IntentView)
def reject_intent(intent_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> IntentView:
    _manage(context)
    with open_finance_store(context) as store:
        try:
            intent = gateway.reject(store, context, intent_id, utc_now())
        except GatewayRefusal as exc:
            raise _refused(exc) from None
    _audit(repository, context, "finance.exec.intent.reject", intent.id, {"mode": intent.mode.value, "requested_by": intent.requested_by})
    return intent_view(intent)


class SwitchView(BaseModel):
    engaged: bool
    changed_at: datetime | None
    changed_by_you: bool
    reason: str | None
    can_change: bool


class ExecutorView(BaseModel):
    mode: str
    available: bool
    simulated: bool
    note: str


class StatusView(BaseModel):
    global_switch: SwitchView
    person_switch: SwitchView
    executors: list[ExecutorView]
    live_enabled: bool
    approval_minutes: int
    pending: int


class SwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["person", "global"]
    engaged: bool
    reason: str | None = Field(default=None, max_length=300)


def _can_global(context: TenantContext) -> bool:
    try:
        require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, GLOBAL_DENIED)
    except HTTPException:
        return False
    return True


def execution_status(store: FinanceStore, context: TenantContext) -> StatusView:
    halt = store.get_global_switch(context) or GlobalSwitch()
    mine = store.get_exec_settings(context) or ExecSettings()
    _, pending = store.list_intents(context, status=IntentStatus.PENDING.value, mode=None, limit=1, offset=0)
    return StatusView(
        global_switch=SwitchView(engaged=halt.engaged, changed_at=halt.changed_at, changed_by_you=halt.changed_by == context.user_id,
                                 reason=halt.reason, can_change=_can_global(context)),
        person_switch=SwitchView(engaged=mine.kill_switch, changed_at=mine.kill_switch_at, changed_by_you=True,
                                 reason=mine.kill_switch_reason, can_change=True),
        executors=[
            ExecutorView(mode="paper", available=True, simulated=True, note="Simulated cash and orders against prices you recorded."),
            ExecutorView(mode="shadow", available=True, simulated=True, note="Records what an order would have done, without cash, and compares it over time."),
            ExecutorView(mode="live", available=False, simulated=False, note=LIVE_REFUSAL),
        ],
        live_enabled=False, approval_minutes=finance_config.approval_minutes, pending=pending,
    )


@router.get("/execution/status", response_model=StatusView)
def get_status(context: TenantContext = Depends(tenant_context)) -> StatusView:
    _read(context)
    with open_finance_store(context) as store:
        return execution_status(store, context)


@router.put("/execution/kill-switch", response_model=StatusView)
def set_kill_switch(body: SwitchInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> StatusView:
    """Stop (or allow again) every executor: yours, or the whole organisation's."""
    _manage(context)
    if body.scope == "global":
        require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, GLOBAL_DENIED)
    now = utc_now()
    reason = _clean(body.reason, 300) if body.reason else None
    with open_finance_store(context) as store:
        if body.scope == "global":
            store.save_global_switch(context, GlobalSwitch(engaged=body.engaged, changed_by=context.user_id, changed_at=now, reason=reason))
        else:
            mine = store.get_exec_settings(context) or ExecSettings()
            mine.kill_switch, mine.kill_switch_at, mine.kill_switch_reason, mine.updated_at = body.engaged, now, reason, now
            store.save_exec_settings(context, mine)
        view = execution_status(store, context)
    _audit(repository, context, f"finance.exec.kill_switch.{body.scope}", context.tenant_id if body.scope == "global" else context.user_id,
           {"engaged": body.engaged})
    return view


# -- stage 8: limited live (framework only, off) ------------------------------------------------------------


class LiveView(BaseModel):
    enabled: bool
    opt_in: bool
    opt_in_at: datetime | None
    limits: LiveLimits
    version: int
    preconditions: list[Precondition]
    pending: list[IntentView]
    owner_steps: list[str]
    note: str


class LiveChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: dict[str, Any]
    opt_in: bool = False
    reason: str | None = Field(default=None, max_length=300)


def live_view(store: FinanceStore, context: TenantContext) -> LiveView:
    mine = store.get_exec_settings(context) or ExecSettings()
    pending, _ = store.list_intents(context, status=IntentStatus.PENDING.value, mode=ExecMode.SETTINGS.value, limit=20, offset=0)
    return LiveView(enabled=False, opt_in=mine.live_opt_in, opt_in_at=mine.live_opt_in_at, limits=mine.live_limits, version=mine.live_version,
                    preconditions=gateway.preconditions(store, context), pending=[intent_view(i) for i in pending], owner_steps=OWNER_STEPS,
                    note=LIVE_REFUSAL)


@router.get("/execution/live", response_model=LiveView)
def get_live(context: TenantContext = Depends(tenant_context)) -> LiveView:
    """Your live limits and opt-in, and every precondition. Live execution is not enabled."""
    _read(context)
    with open_finance_store(context) as store:
        gateway.expire(store, store.list_intents(context, status=IntentStatus.PENDING.value, mode=ExecMode.SETTINGS.value, limit=20, offset=0)[0], utc_now())
        return live_view(store, context)


@router.post("/execution/live/changes", response_model=IntentView, status_code=status.HTTP_201_CREATED)
def propose_live_change(
    body: LiveChangeInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> IntentView:
    """New live limits (and opt-in), applied only when you approve them with the code shown."""
    _manage(context)
    try:
        limits = LiveLimits.model_validate(body.limits)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "limits"
        raise _invalid(f"Live limits, {where}: {first.get('msg', 'not valid')}".replace("Value error, ", "")) from None
    with open_finance_store(context) as store:
        try:
            intent = gateway.create_live_profile_change(store, context, limits, body.opt_in, _clean(body.reason, 300) if body.reason else None, utc_now())
        except GatewayRefusal as exc:
            refusal = exc
        else:
            refusal = None
    if refusal is not None:
        _audit_refusal(repository, context, "finance.exec.live.change", context.user_id, refusal)
        raise _refused(refusal)
    _audit(repository, context, "finance.exec.live.change", intent.id, {"opt_in": body.opt_in})
    return intent_view(intent)


@router.post("/execution/live/opt-out", response_model=LiveView)
def opt_out(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LiveView:
    """Withdraw your live opt-in at once (no approval needed to stop)."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        mine = store.get_exec_settings(context) or ExecSettings()
        mine.live_opt_in, mine.live_opt_in_at, mine.updated_at = False, None, now
        mine.live_version += 1
        store.save_exec_settings(context, mine)
        view = live_view(store, context)
    _audit(repository, context, "finance.exec.live.opt_out", context.user_id)
    return view


__all__ = ["router", "intent_view", "portfolio_view"]
