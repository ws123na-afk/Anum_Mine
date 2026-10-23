"""Pure, read-only broker statement analytics.

Only aggregates values where prices and currency match; never invents exchange
rates, suggests trades, or authorizes execution. Intended for explicitly
uploaded account statements, not a verified live brokerage connection.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

from .broker_reconciliation import BrokerSnapshot, PositionSide


class ExposureRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str
    long_market_value: Decimal
    short_market_value: Decimal
    gross_market_value: Decimal
    net_market_value: Decimal
    top_position_symbol: str | None
    top_position_share_percent: Decimal | None
    unpriced_count: int
    priced_count: int


class StatementAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker_id: str
    account_id: str
    as_of: str
    position_count: int
    cash_count: int
    exposure: list[ExposureRow]
    warnings: list[str] = Field(default_factory=list)
    simulation_only: bool = False
    read_only: bool = True
    live_prices_verified: bool = False


def analyze_broker_statement(snapshot: BrokerSnapshot) -> StatementAnalytics:
    """Return neutral diagnostics; never sum unlike currencies.

    A concentration share uses gross priced exposure as the denominator. A
    missing price prevents complete concentration analysis and is flagged.
    """
    grouped: dict[str, list] = defaultdict(list)
    for p in snapshot.positions:
        grouped[p.currency].append(p)
    rows: list[ExposureRow] = []
    warnings: list[str] = []
    for currency, positions in sorted(grouped.items()):
        long_value = Decimal("0")
        short_value = Decimal("0")
        largest_symbol: str | None = None
        largest = Decimal("0")
        priced = 0
        missing = 0
        for p in positions:
            if p.last_price is None:
                missing += 1
                continue
            amount = p.quantity * p.last_price
            priced += 1
            if p.side is PositionSide.SHORT:
                short_value += amount
            else:
                long_value += amount
            if amount > largest:
                largest = amount
                largest_symbol = p.symbol
        gross = long_value + short_value
        pct = largest / gross * 100 if gross > 0 else None
        if missing:
            warnings.append(f"{currency}: {missing} position(s) have no price; exposure and concentration are incomplete.")
        if pct is not None and pct > 50:
            warnings.append(f"{currency}: the largest priced position is over 50% of gross priced exposure.")
        rows.append(ExposureRow(
            currency=currency,
            long_market_value=long_value,
            short_market_value=short_value,
            gross_market_value=gross,
            net_market_value=long_value - short_value,
            top_position_symbol=largest_symbol,
            top_position_share_percent=pct,
            unpriced_count=missing,
            priced_count=priced,
        ))
    for cash in snapshot.cash:
        if cash.available < 0:
            warnings.append(f"{cash.currency}: reported available cash is negative.")
        if cash.available > cash.settled:
            warnings.append(f"{cash.currency}: available cash exceeds settled cash; verify broker accounting.")
    if not snapshot.positions and not snapshot.cash:
        warnings.append("The uploaded statement has no cash or position rows.")
    warnings.append("Values are derived from user-supplied statements, not independently verified broker quotes.")
    return StatementAnalytics(
        broker_id=snapshot.broker_id,
        account_id=snapshot.account_id,
        as_of=snapshot.as_of.isoformat(),
        position_count=len(snapshot.positions),
        cash_count=len(snapshot.cash),
        exposure=rows,
        warnings=warnings,
    )
