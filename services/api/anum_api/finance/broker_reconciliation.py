"""Provider-neutral broker statement reconciliation for ANUM's finance module.

An offline, read-only reconciliation of supplied brokerage snapshots. It has no
network client, credentials, order placement, account funding or withdrawals.
It deliberately never mutates a trading portfolio. A caller must separately
authenticate and authorize the owner before passing in any account data.

Money is Decimal. Missing prices and exchange rates are not invented. Cross-
currency totals are intentionally not produced.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Money = Annotated[Decimal, Field(max_digits=24, decimal_places=8)]
MAX_ROWS = 2000
MAX_ID = 100


class BrokerDataError(ValueError):
    """Broker data cannot safely be reconciled."""


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


def _text(value: str) -> str:
    result = value.strip()
    if not result or len(result) > MAX_ID:
        raise ValueError("A non-empty identifier of at most 100 characters is required")
    return result


def _currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise ValueError("Currency must be a three-letter ISO-style code")
    return code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrokerCash(_Strict):
    currency: str
    settled: Money
    available: Money

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        return _currency(value)


class BrokerPosition(_Strict):
    symbol: str
    currency: str
    quantity: Money
    side: PositionSide = PositionSide.LONG
    last_price: Money | None = None

    @field_validator("symbol")
    @classmethod
    def valid_symbol(cls, value: str) -> str:
        return _text(value).upper()

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        return _currency(value)

    @model_validator(mode="after")
    def valid_quantities(self) -> "BrokerPosition":
        if self.quantity <= 0:
            raise ValueError("Position quantity must be positive; use side=short instead")
        if self.last_price is not None and self.last_price < 0:
            raise ValueError("A recorded market price cannot be negative")
        return self


class BrokerSnapshot(_Strict):
    broker_id: str
    account_id: str
    as_of: datetime
    cash: list[BrokerCash] = Field(default_factory=list, max_length=MAX_ROWS)
    positions: list[BrokerPosition] = Field(default_factory=list, max_length=MAX_ROWS)
    source_reference: str

    @field_validator("broker_id", "account_id", "source_reference")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _text(value)

    @field_validator("as_of")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must be timezone aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def no_duplicate_items(self) -> "BrokerSnapshot":
        currencies = [row.currency for row in self.cash]
        if len(set(currencies)) != len(currencies):
            raise ValueError("Duplicate cash currency")
        keys = [(row.symbol, row.currency, row.side) for row in self.positions]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate position; aggregate only with verified broker rules")
        return self


class ReconciliationTolerance(_Strict):
    quantity: Money = Decimal("0")
    cash: Money = Decimal("0")

    @model_validator(mode="after")
    def nonnegative(self) -> "ReconciliationTolerance":
        if self.quantity < 0 or self.cash < 0:
            raise ValueError("Reconciliation tolerances must be nonnegative")
        return self


class Discrepancy(_Strict):
    kind: str
    key: str
    expected: Decimal | None
    actual: Decimal | None
    difference: Decimal | None
    currency: str | None = None


class BrokerReconciliation(_Strict):
    account_id: str
    broker_id: str
    baseline_time: datetime
    observed_time: datetime
    source_reference: str
    matches: bool
    discrepancies: list[Discrepancy]
    currency_exposure: dict[str, Decimal]
    unpriced_positions: list[str]
    stale: bool
    read_only: bool = True
    trades_executed: int = 0


def reconcile_snapshots(
    expected: BrokerSnapshot,
    observed: BrokerSnapshot,
    *,
    tolerance: ReconciliationTolerance | None = None,
    max_snapshot_age_seconds: int = 900,
    now: datetime | None = None,
) -> BrokerReconciliation:
    """Compare a recorded baseline against a newly supplied external snapshot.

    Raises on mismatched owners/accounts, untrusted future timestamps or an
    observed snapshot older than the baseline. Does not claim that an unchanged
    book proves broker fills, pending orders or corporate actions are complete.
    """
    if expected.account_id != observed.account_id or expected.broker_id != observed.broker_id:
        raise BrokerDataError("Snapshots must describe the same broker and account")
    if observed.as_of < expected.as_of:
        raise BrokerDataError("The observed snapshot predates the baseline")
    if max_snapshot_age_seconds < 0:
        raise ValueError("Snapshot age threshold must be nonnegative")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Current time must be timezone aware")
    current = current.astimezone(timezone.utc)
    if observed.as_of > current:
        raise BrokerDataError("Broker snapshot timestamp is in the future")
    limits = tolerance or ReconciliationTolerance()
    discrepancies: list[Discrepancy] = []

    baseline_cash = {row.currency: row for row in expected.cash}
    current_cash = {row.currency: row for row in observed.cash}
    for currency in sorted(baseline_cash.keys() | current_cash.keys()):
        previous = baseline_cash.get(currency)
        actual = current_cash.get(currency)
        for attribute in ("settled", "available"):
            first = getattr(previous, attribute) if previous else None
            last = getattr(actual, attribute) if actual else None
            difference = last - first if first is not None and last is not None else None
            if first is None or last is None or abs(difference) > limits.cash:
                discrepancies.append(Discrepancy(
                    kind="cash_" + attribute, key=currency, expected=first,
                    actual=last, difference=difference, currency=currency,
                ))

    def keyed(snapshot: BrokerSnapshot) -> dict[tuple[str, str, PositionSide], BrokerPosition]:
        return {(p.symbol, p.currency, p.side): p for p in snapshot.positions}

    before, after = keyed(expected), keyed(observed)
    for key in sorted(before.keys() | after.keys()):
        previous, actual = before.get(key), after.get(key)
        first = previous.quantity if previous else None
        last = actual.quantity if actual else None
        difference = last - first if first is not None and last is not None else None
        if first is None or last is None or abs(difference) > limits.quantity:
            discrepancies.append(Discrepancy(
                kind="position_quantity", key=f"{key[0]}:{key[2].value}",
                expected=first, actual=last, difference=difference, currency=key[1],
            ))
    exposure: dict[str, Decimal] = defaultdict(Decimal)
    unpriced = []
    for position in observed.positions:
        if position.last_price is None:
            unpriced.append(f"{position.symbol}:{position.currency}:{position.side.value}")
        else:
            direction = -1 if position.side is PositionSide.SHORT else 1
            exposure[position.currency] += position.quantity * position.last_price * direction

    return BrokerReconciliation(
        account_id=observed.account_id,
        broker_id=observed.broker_id,
        baseline_time=expected.as_of,
        observed_time=observed.as_of,
        source_reference=observed.source_reference,
        matches=not discrepancies,
        discrepancies=discrepancies,
        currency_exposure=dict(sorted(exposure.items())),
        unpriced_positions=sorted(unpriced),
        stale=(current - observed.as_of).total_seconds() > max_snapshot_age_seconds,
    )
