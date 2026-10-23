"""Pure paper-only mixed-market simulator for a SAR 500 research budget.

No broker network calls, credentials, real funds, order creation, persistence
or live execution. The user must supply manually reviewed prices in SAR; the
API never guesses conversion rates or obtains market data as a hidden side
effect. Deterministic results make offline Guardian checks reproducible.
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Cash = Annotated[Decimal, Field(max_digits=18, decimal_places=4)]
MAX_STARTING_SAR = Decimal("500")
MAX_ALLOCATION = Decimal("0.20")
MAX_PAPER_LOSS = Decimal("0.02")


class Market(StrEnum):
    STOCK = "stock"
    CRYPTO = "crypto"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperAsset(Strict):
    ticker: str = Field(min_length=1, max_length=25)
    market: Market
    currency: str = "SAR"
    # User-verified quote, denominated in SAR per unit. No automatic FX.
    reference_price_sar: Cash
    source: str = Field(min_length=3, max_length=150)

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def price_rules(self):
        if self.currency != "SAR":
            raise ValueError("Only explicitly SAR-denominated quotes are supported")
        if self.reference_price_sar <= 0:
            raise ValueError("Price must be positive")
        return self


class PaperHolding(Strict):
    ticker: str
    market: Market
    quantity: Cash
    average_cost_sar: Cash

    @model_validator(mode="after")
    def quantity_rules(self):
        if self.quantity < 0 or self.average_cost_sar < 0:
            raise ValueError("Paper holdings cannot be negative")
        return self


class PaperTrade(Strict):
    ticker: str
    market: Market
    action: Action
    quantity: Cash

    @model_validator(mode="after")
    def valid_qty(self):
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive")
        return self


class PaperPortfolio(Strict):
    starting_capital_sar: Cash = MAX_STARTING_SAR
    cash_sar: Cash = MAX_STARTING_SAR
    holdings: list[PaperHolding] = Field(default_factory=list, max_length=100)
    realised_pnl_sar: Cash = Decimal("0")
    trading_halted: bool = False

    @model_validator(mode="after")
    def validate_budget(self):
        if not 0 < self.starting_capital_sar <= MAX_STARTING_SAR:
            raise ValueError("Initial research budget must be 0-500 SAR")
        if self.cash_sar < 0:
            raise ValueError("Paper cash cannot be negative")
        keys = [(h.ticker, h.market) for h in self.holdings]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate paper holding")
        return self


class PaperSimulationRequest(Strict):
    portfolio: PaperPortfolio
    quote: PaperAsset
    trade: PaperTrade
    estimated_fee_sar: Cash = Decimal("0")
    # Client-entered account-level daily P&L, a measured value, not a forecast.
    daily_realised_pnl_sar: Cash = Decimal("0")


class PaperSimulationResult(Strict):
    accepted: bool
    reason: str
    before: PaperPortfolio
    after: PaperPortfolio
    simulated_notional_sar: Decimal
    estimated_fee_sar: Decimal
    max_starting_budget_sar: Decimal = MAX_STARTING_SAR
    live_trading_enabled: bool = False
    real_orders_sent: int = 0
    money_transferred: bool = False


def _reject(request: PaperSimulationRequest, reason: str, notional: Decimal = Decimal("0")) -> PaperSimulationResult:
    return PaperSimulationResult(
        accepted=False, reason=reason, before=request.portfolio,
        after=request.portfolio, simulated_notional_sar=notional,
        estimated_fee_sar=request.estimated_fee_sar,
    )


def simulate_paper_trade(request: PaperSimulationRequest) -> PaperSimulationResult:
    """Fail-closed paper trade; never place a brokerage order."""
    portfolio, quote, trade = request.portfolio, request.quote, request.trade
    notional = trade.quantity * quote.reference_price_sar
    fee = request.estimated_fee_sar
    if trade.ticker.strip().upper() != quote.ticker or trade.market != quote.market:
        return _reject(request, "Symbol and market must match the reviewed quote", notional)
    if fee < 0:
        return _reject(request, "Estimated fee must be nonnegative", notional)
    if portfolio.trading_halted:
        return _reject(request, "Paper kill switch is engaged", notional)
    if request.daily_realised_pnl_sar <= -portfolio.starting_capital_sar * MAX_PAPER_LOSS:
        return _reject(request, "Paper daily-loss stop reached", notional)
    if request.daily_realised_pnl_sar > portfolio.starting_capital_sar:
        return _reject(request, "Daily P&L requires verification", notional)
    prior = next((h for h in portfolio.holdings if h.ticker == quote.ticker and h.market == quote.market), None)
    owned = prior.quantity if prior else Decimal("0")
    if trade.action == Action.BUY:
        if notional > portfolio.starting_capital_sar * MAX_ALLOCATION:
            return _reject(request, "Single paper order exceeds 20% of starting budget", notional)
        if prior and owned * quote.reference_price_sar + notional > portfolio.starting_capital_sar * MAX_ALLOCATION:
            return _reject(request, "Single asset exposure exceeds 20% of starting budget", notional)
        if notional + fee > portfolio.cash_sar:
            return _reject(request, "Insufficient simulated cash; no borrowing or leverage", notional)
        quantity = owned + trade.quantity
        basis = (owned * prior.average_cost_sar if prior else Decimal("0")) + notional + fee
        replacement = PaperHolding(
            ticker=quote.ticker, market=quote.market, quantity=quantity,
            average_cost_sar=(basis / quantity).quantize(Decimal('0.0001')),
        )
        new_cash = portfolio.cash_sar - notional - fee
        realised = portfolio.realised_pnl_sar
    else:
        if trade.quantity > owned:
            return _reject(request, "Cannot sell more than the simulated position", notional)
        replacement = PaperHolding(
            ticker=quote.ticker, market=quote.market,
            quantity=owned - trade.quantity, average_cost_sar=prior.average_cost_sar,
        )
        new_cash = portfolio.cash_sar + notional - fee
        realised = portfolio.realised_pnl_sar + (quote.reference_price_sar - prior.average_cost_sar) * trade.quantity - fee
        if new_cash < 0:
            return _reject(request, "Fee exceeds available simulated proceeds", notional)
    positions = [h for h in portfolio.holdings if (h.ticker, h.market) != (quote.ticker, quote.market)]
    if replacement.quantity > 0:
        positions.append(replacement)
    updated = portfolio.model_copy(update={
        "cash_sar": new_cash,
        "holdings": sorted(positions, key=lambda h: (h.market.value, h.ticker)),
        "realised_pnl_sar": realised,
    })
    return PaperSimulationResult(
        accepted=True,
        reason="Simulation calculated only; no broker order or transfer occurred",
        before=portfolio, after=updated,
        simulated_notional_sar=notional, estimated_fee_sar=fee,
    )
