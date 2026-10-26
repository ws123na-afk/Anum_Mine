"""Historical broker statement change report, calculated without any trade execution.

Only owner-provided snapshots can be inspected. Calculations are deterministic,
per currency, and treat missing prices as unknown. Cash changes are not profit;
position quantity changes are not necessarily fills (splits and corrections
exist); mark-to-market movement is not a reliable return without cash flows.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from .broker_reconciliation import BrokerDataError, BrokerSnapshot, PositionSide


class CashChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str
    previous_settled: Decimal | None
    current_settled: Decimal | None
    settled_change: Decimal | None
    previous_available: Decimal | None
    current_available: Decimal | None
    available_change: Decimal | None


class PositionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    currency: str
    side: PositionSide
    previous_quantity: Decimal | None
    current_quantity: Decimal | None
    quantity_change: Decimal | None
    previous_price: Decimal | None
    current_price: Decimal | None
    previous_market_value: Decimal | None
    current_market_value: Decimal | None
    valuation_change: Decimal | None
    remark: str


class CurrencyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str
    previous_priced_positions_value: Decimal
    current_priced_positions_value: Decimal
    previous_unpriced_count: int
    current_unpriced_count: int
    comparable_positions: int
    known_valuation_change: Decimal
    partial: bool


class StatementChangeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker_id: str
    account_id: str
    baseline_time: str
    observed_time: str
    cash_changes: list[CashChange]
    position_changes: list[PositionChange]
    currency_changes: list[CurrencyChange]
    warnings: list[str] = Field(default_factory=list)
    trades_executed: int = 0
    profit_calculated: bool = False
    source: str = "user_uploaded_statements"
    read_only: bool = True


def compare_broker_history(previous: BrokerSnapshot, current: BrokerSnapshot) -> StatementChangeReport:
    """Produce a bounded statement-to-statement comparison without guessing P&L.

    New and removed positions are listed but have no valuation delta: doing so
    would incorrectly treat deposits, purchases and sales as investment return.
    """
    if previous.broker_id != current.broker_id or previous.account_id != current.account_id:
        raise BrokerDataError("Historical statements must use the same broker and account")
    if current.as_of < previous.as_of:
        raise BrokerDataError("Latest historical statement cannot predate baseline")

    baseline_cash = {x.currency: x for x in previous.cash}
    latest_cash = {x.currency: x for x in current.cash}
    cash_changes = []
    for currency in sorted(baseline_cash.keys() | latest_cash.keys()):
        first, last = baseline_cash.get(currency), latest_cash.get(currency)
        cash_changes.append(CashChange(
            currency=currency,
            previous_settled=first.settled if first else None,
            current_settled=last.settled if last else None,
            settled_change=(last.settled - first.settled) if first and last else None,
            previous_available=first.available if first else None,
            current_available=last.available if last else None,
            available_change=(last.available - first.available) if first and last else None,
        ))

    def indexed(snapshot: BrokerSnapshot):
        return {(x.symbol, x.currency, x.side): x for x in snapshot.positions}

    first_positions, last_positions = indexed(previous), indexed(current)
    position_changes = []
    currency_rows = defaultdict(lambda: {
        "before": Decimal(0), "after": Decimal(0), "missing_before": 0,
        "missing_after": 0, "comparable": 0, "change": Decimal(0), "partial": False,
    })
    for key in sorted(first_positions.keys() | last_positions.keys()):
        old, new = first_positions.get(key), last_positions.get(key)
        currency = key[1]
        totals = currency_rows[currency]
        old_value = old.quantity * old.last_price if old and old.last_price is not None else None
        new_value = new.quantity * new.last_price if new and new.last_price is not None else None
        sign = Decimal(-1) if key[2] is PositionSide.SHORT else Decimal(1)
        if old:
            if old_value is None:
                totals["missing_before"] += 1
            else:
                totals["before"] += old_value * sign
        if new:
            if new_value is None:
                totals["missing_after"] += 1
            else:
                totals["after"] += new_value * sign

        if old is None:
            remark = "New position; value difference is not a trading return."
        elif new is None:
            remark = "Position absent from latest statement; a sale or corporate action is not verified."
        elif old_value is None or new_value is None:
            remark = "Price is missing; valuation difference cannot be calculated."
        elif old.quantity != new.quantity:
            remark = "Quantity changed; valuation movement mixes price and holding changes."
        else:
            remark = "Mark-to-market movement at unchanged quantity; excludes flows, fees and FX."

        comparable = old is not None and new is not None and old_value is not None and new_value is not None and old.quantity == new.quantity
        change = (new_value - old_value) * sign if comparable else None
        if comparable:
            totals["comparable"] += 1
            totals["change"] += change
        else:
            totals["partial"] = True
        position_changes.append(PositionChange(
            symbol=key[0], currency=currency, side=key[2],
            previous_quantity=old.quantity if old else None,
            current_quantity=new.quantity if new else None,
            quantity_change=new.quantity - old.quantity if old and new else None,
            previous_price=old.last_price if old else None,
            current_price=new.last_price if new else None,
            previous_market_value=old_value * sign if old_value is not None else None,
            current_market_value=new_value * sign if new_value is not None else None,
            valuation_change=change,
            remark=remark,
        ))
    currency_changes = [
        CurrencyChange(currency=currency,
                       previous_priced_positions_value=totals["before"],
                       current_priced_positions_value=totals["after"],
                       previous_unpriced_count=totals["missing_before"],
                       current_unpriced_count=totals["missing_after"],
                       comparable_positions=totals["comparable"],
                       known_valuation_change=totals["change"],
                       partial=totals["partial"])
        for currency, totals in sorted(currency_rows.items())
    ]
    warnings = [
        "Cash changes are not deposits, withdrawals or investment returns until transactions are reconciled.",
        "Valuation changes are not portfolio profit: fees, corporate actions and cash flows are unverified.",
        "Amounts from different currencies are never combined without a sourced FX rate.",
    ]
    if any(item.partial for item in currency_changes):
        warnings.append("Some instruments were added, removed, repriced without data or changed quantity; performance is partial.")
    return StatementChangeReport(
        broker_id=current.broker_id, account_id=current.account_id,
        baseline_time=previous.as_of.isoformat(), observed_time=current.as_of.isoformat(),
        cash_changes=cash_changes, position_changes=position_changes,
        currency_changes=currency_changes, warnings=warnings,
    )
