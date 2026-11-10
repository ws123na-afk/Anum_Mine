"""Unrealised revaluation of personal foreign-currency balances (docs/finance.md#foreign-currency-balances). Pure: no store.

For each bank, cash or investment account in a currency other than the person's base currency:

* **Value now**: the balance in the account's own currency times the newest stored rate to the base currency (``fx.find_rate``
  within the person's rate age limit). No rate: "rate not available"; a rate is never guessed.
* **Value when it came in** (the cost): average cost. Each money-in movement (the opening balance on the account's first
  day, then every credit) is valued at the stored rate of its own date; money out takes its share of the cost at the
  running average, so it needs no rate. A money-in movement without a rate of its day leaves the cost "rate not
  available" and names the dates.
* **Unrealised gain or loss**: value now less the cost, in the base currency.

Display only: never posted, never counted as spending or income, never added to the ledger. Cards and loans (money owed)
are not revalued, and an account at or below zero has nothing to revalue. An account whose history is longer than the API
reads at once is not valued at all ("history_too_long"): a partial history would give a wrong balance and cost.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from . import fx
from .models import Account, AccountKind, FxRate, Transaction
from .money import quantize

REVALUED_KINDS = (AccountKind.BANK, AccountKind.CASH, AccountKind.INVESTMENT)
MAX_MISSING_DATES = 5

Status = Literal["ok", "rate_not_available", "cost_rate_not_available", "nothing_to_revalue", "history_too_long"]


@dataclass
class Revaluation:
    account_id: str
    name: str
    currency: str
    base_currency: str
    balance: Decimal
    status: Status
    value: Decimal | None = None
    rate: Decimal | None = None
    rate_as_of: date | None = None
    rate_source: str | None = None
    cost: Decimal | None = None
    unrealised: Decimal | None = None
    missing_rate_dates: list[date] = field(default_factory=list)


def _movements(account: Account, rows: Iterable[Transaction]) -> list[tuple[date, Decimal]]:
    mine = sorted(((t.posted_on, t.amount) for t in rows if t.account_id == account.id and t.currency == account.currency and t.amount != 0),
                  key=lambda m: (m[0], -m[1]))  # on one day, money in before money out
    first = min([account.created_at.date(), *(m[0] for m in mine)])
    return ([(first, account.opening_balance)] if account.opening_balance != 0 else []) + mine


def revalue(account: Account, rows: Iterable[Transaction], base: str, rates: list[FxRate], today: date, max_age_days: int) -> Revaluation:
    """One account's revaluation (the account is in a currency other than ``base``)."""
    moves = _movements(account, rows)
    balance = quantize(sum((m[1] for m in moves), Decimal(0)), account.currency)
    out = Revaluation(account_id=account.id, name=account.name, currency=account.currency, base_currency=base, balance=balance,
                      status="nothing_to_revalue")
    if balance <= 0:
        return out
    now = fx.find_rate(rates, account.currency, base, today, max_age_days)
    if now is not None:
        out.rate, out.rate_as_of, out.rate_source = now.rate, now.as_of, now.source
        out.value = quantize(balance * now.rate, base)
    units, cost, missing = Decimal(0), Decimal(0), []
    for on, amount in moves:
        if amount > 0:
            covered = min(amount, -units) if units < 0 else Decimal(0)  # money that only brings an overdrawn balance back to zero
            units += covered
            fresh = amount - covered
            if fresh > 0:
                path = fx.find_rate(rates, account.currency, base, on, max_age_days)
                if path is None:
                    missing.append(on)
                else:
                    cost += fresh * path.rate
                units += fresh
        else:
            if units > 0:
                taken = min(-amount, units)
                cost -= cost * taken / units
            units += amount
            if units <= 0:
                # Nothing of the earlier money is left, so neither its cost nor a missing rate for it counts any more.
                cost, missing = Decimal(0), []
    if missing:
        out.missing_rate_dates = sorted(set(missing))[:MAX_MISSING_DATES]
    else:
        out.cost = quantize(cost, base)
    if out.value is None:
        out.status = "rate_not_available"
    elif out.cost is None:
        out.status = "cost_rate_not_available"
    else:
        out.status = "ok"
        out.unrealised = out.value - out.cost
    return out


def revalued_accounts(accounts: Iterable[Account], base: str) -> list[Account]:
    """Every open bank, cash or investment account in another currency than ``base``, by name."""
    return [a for a in sorted(accounts, key=lambda a: (a.name.casefold(), a.id)) if not a.archived and a.kind in REVALUED_KINDS and a.currency != base]


def too_long(account: Account, base: str, balance: Decimal) -> Revaluation:
    """An account whose whole history could not be read: its balance (from the stored totals) and no valuation."""
    return Revaluation(account_id=account.id, name=account.name, currency=account.currency, base_currency=base,
                       balance=quantize(balance, account.currency), status="history_too_long")


def revalue_all(accounts: Iterable[Account], rows: list[Transaction], base: str, rates: list[FxRate], today: date, max_age_days: int) -> list[Revaluation]:
    """Every revalued account (``revalued_accounts``) from one complete list of rows."""
    return [revalue(a, rows, base, rates, today, max_age_days) for a in revalued_accounts(accounts, base)]


__all__ = ["REVALUED_KINDS", "Revaluation", "revalue", "revalue_all"]
