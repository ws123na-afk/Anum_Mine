"""Limited live execution, stage 8: the framework only, shipped OFF (docs/finance.md#limited-live).

Nothing in ANUM places a real order, payment or transfer. This module holds what a later,
separately approved change would need, so that its absence is explicit and tested:

* ``LiveExecutor``: the interface a live executor would implement. **There is no
  implementation and no provider adapter**: ``LIVE_EXECUTORS`` is empty by design, and the
  execution gateway refuses every live intent ("live execution is not enabled").
* ``LiveLimits`` and ``check_live_limits``: the person's bounds for live actions (per
  action, per day, per month, allowed instruments, accounts and currencies, a cooling-off
  period). Pure and deterministic, tested, used only once live execution exists.
* ``live_preconditions``: every condition that must hold before the gateway would even
  consider a live intent. ``ANUM_FINANCE_LIVE_ENABLED`` is refused at startup while no live
  executor exists (``config.py``, mirrored by the Helm chart), so the first precondition
  never holds in this release.

This module imports nothing from the rest of the finance package except pure models, so the
startup check in ``config.py`` can read ``LIVE_EXECUTORS`` without import cycles.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .guardian import AmountLimit
from .money import MoneyError, normalize_currency

MAX_LIST = 50


class LiveExecutor(Protocol):
    """What a live executor would implement. None exists: see ``LIVE_EXECUTORS``.

    A real implementation would need a write-scoped connector to a licensed provider, which
    ANUM does not have (every connector scope ends in ``:read``, enforced by the database).
    """

    name: str

    def execute(self, intent: Any, *, idempotency_key: str) -> dict[str, Any]: ...


# Empty by design: no live executor and no provider write integration is built. Stage 8
# could only be switched on by a later change that adds one, after the owner's written
# approval (docs/finance.md#what-the-owner-must-provide-before-live).
LIVE_EXECUTORS: dict[str, LiveExecutor] = {}


def live_executor_available() -> bool:
    return bool(LIVE_EXECUTORS)


class LiveLimits(BaseModel):
    """The person's bounds for live actions. Empty lists mean "nothing allowed" (not "no limit")."""

    model_config = ConfigDict(extra="forbid")

    max_per_action: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)
    max_per_day: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)
    max_per_month: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)
    instruments: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    accounts: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    currencies: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    cooling_off_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("currencies")
    @classmethod
    def _codes(cls, value: list[str]) -> list[str]:
        try:
            return sorted({normalize_currency(code) for code in value})
        except MoneyError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("instruments")
    @classmethod
    def _instruments(cls, value: list[str]) -> list[str]:
        cleaned = sorted({item.strip().upper() for item in value} - {""})
        if any(len(item) > 20 or not all(ch.isalnum() or ch in ".-" for ch in item) for item in cleaned):
            raise ValueError("An instrument is a symbol of up to 20 letters, digits, dots or hyphens.")
        return cleaned

    @field_validator("accounts")
    @classmethod
    def _accounts(cls, value: list[str]) -> list[str]:
        cleaned = sorted({item.strip() for item in value} - {""})
        if any(len(item) > 80 for item in cleaned):
            raise ValueError("An account id is at most 80 characters.")
        return cleaned

    @model_validator(mode="after")
    def _one_each(self) -> LiveLimits:
        for name in ("max_per_action", "max_per_day", "max_per_month"):
            items: list[AmountLimit] = getattr(self, name)
            if len({i.currency for i in items}) != len(items):
                raise ValueError(f"{name}: one limit per currency.")
            setattr(self, name, sorted(items, key=lambda i: i.currency))
        return self

    def is_set(self) -> bool:
        """At least one currency with a per-action, daily and monthly bound, and something allowed."""
        return bool(self.max_per_action and self.max_per_day and self.max_per_month and self.currencies
                    and (self.instruments or self.accounts))


class LiveAttempt(BaseModel):
    """One live action to check against the person's limits."""

    amount: Decimal
    currency: str
    instrument: str | None = None
    account_id: str | None = None
    requested_at: datetime
    at: datetime


class LiveSpent(BaseModel):
    """Live amounts already executed today and this month, per currency."""

    day: dict[str, Decimal] = Field(default_factory=dict)
    month: dict[str, Decimal] = Field(default_factory=dict)


class LiveReason(BaseModel):
    code: str
    message: str
    params: dict[str, str] = Field(default_factory=dict)


def check_live_limits(attempt: LiveAttempt, limits: LiveLimits, spent: LiveSpent) -> list[LiveReason]:
    """Every way ``attempt`` breaks the person's live limits. Empty: within them. Pure."""
    reasons: list[LiveReason] = []

    def add(code: str, message: str, **params: str) -> None:
        reasons.append(LiveReason(code=code, message=message, params=params))

    currency = attempt.currency
    if attempt.amount <= 0:
        add("invalid_amount", "A live action's amount is above zero.")
        return reasons
    if currency not in limits.currencies:
        add("currency_not_allowed", f"{currency} is not one of your live currencies.", currency=currency)
    if attempt.instrument is not None and attempt.instrument.upper() not in limits.instruments:
        add("instrument_not_allowed", "This instrument is not on your live list.")
    if attempt.account_id is not None and attempt.account_id not in limits.accounts:
        add("account_not_allowed", "This account is not on your live list.")
    if attempt.instrument is None and attempt.account_id is None:
        add("no_target", "A live action names an allowed instrument or account.")
    bounds: Iterable[tuple[str, list[AmountLimit], Decimal]] = (
        ("over_live_action", limits.max_per_action, Decimal(0)),
        ("over_live_day", limits.max_per_day, spent.day.get(currency, Decimal(0))),
        ("over_live_month", limits.max_per_month, spent.month.get(currency, Decimal(0))),
    )
    for code, items, already in bounds:
        limit = next((i.amount for i in items if i.currency == currency), None)
        if limit is None:
            add(code, f"There is no live limit for {currency}: nothing is allowed.", currency=currency)
        elif already + attempt.amount > limit:
            add(code, f"Over your live limit of {currency} {limit}.", currency=currency, limit=str(limit), spent=str(already))
    ready = attempt.requested_at + timedelta(hours=limits.cooling_off_hours)
    if attempt.at < ready:
        add("live_cooling_off", f"Your live cooling-off period ends at {ready.isoformat()}.", until=ready.isoformat())
    return reasons


class Precondition(BaseModel):
    code: str
    met: bool
    message: str


def live_preconditions(
    *, flag: bool, write_connector: bool, opted_in: bool, limits_set: bool, global_halt: bool, person_halt: bool,
) -> list[Precondition]:
    """Every condition live execution needs, in order. All must hold; today the first three never do."""
    return [
        Precondition(code="deployment_flag", met=flag,
                     message="The operator setting ANUM_FINANCE_LIVE_ENABLED is on (it is off, and the API refuses it while no live executor exists)."),
        Precondition(code="live_executor", met=live_executor_available(),
                     message="A live executor for a licensed provider is built and reviewed (none is built)."),
        Precondition(code="write_connector", met=write_connector,
                     message="A connector with a write scope exists (every connector is read only)."),
        Precondition(code="opt_in", met=opted_in, message="You opted in to limited live actions, confirmed with the code shown."),
        Precondition(code="limits", met=limits_set, message="You set live limits: per action, per day, per month, currencies and allowed instruments or accounts."),
        Precondition(code="kill_switch_global", met=not global_halt, message="The organisation's kill switch is off."),
        Precondition(code="kill_switch_person", met=not person_halt, message="Your own kill switch is off."),
    ]


LIVE_REFUSAL = "Live execution is not enabled. ANUM places no real order, payment or transfer; nothing was sent."
