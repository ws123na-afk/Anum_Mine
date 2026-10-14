"""The Guardian policy kernel, Stage 4 (docs/finance.md#guardian).

The person's own spending and risk rules, and one pure, deterministic evaluator:

    evaluate(action, rules, ledger_state) -> Decision(allow | needs_approval | deny, reasons)

The same inputs always give the same decision; nothing here reads a clock, a store or the
network. ``gate`` is the mandatory check every finance action passes before it may run:
it evaluates the rules and adds the kernel's own limits, which no rule can lift. Today the
first of them is absolute: **ANUM moves no money** (there is no execution gateway), so any
money-moving action is denied whatever the rules say. The future execution gateway
(Stage 7) must call ``gate`` and may run an action only when it returns ``allow`` (or
``needs_approval`` and the person approved that exact action).

Rules belong to the person. They are versioned (every change is a new version, nothing is
overwritten) and every change is a proposal confirmed by the person with the payload hash
they were shown (``payload_hash``, the same binding rule as approvals in
``approval_integrity.py``). An agent can read the rules and propose a stricter change; it
can never confirm one, and a proposal that loosens anything is refused for agents
(``loosening``): only the person loosens their own rules.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Category, _Owned
from .money import MoneyError, normalize_currency, to_money

MAX_LIST = 50
PROPOSAL_TTL = timedelta(hours=24)


class ActionKind(StrEnum):
    SPEND = "spend"  # paying a merchant
    BILL_PAYMENT = "bill_payment"
    TRANSFER = "transfer"
    TRADE = "trade"
    RECORD = "record"  # writing down money that already left (a ledger entry): never moves money


MONEY_MOVING = frozenset({ActionKind.SPEND, ActionKind.BILL_PAYMENT, ActionKind.TRANSFER, ActionKind.TRADE})
Outcome = Literal["allow", "needs_approval", "deny"]
SEVERITY: dict[str, int] = {"allow": 0, "needs_approval": 1, "deny": 2}


# -- rules ---------------------------------------------------------------------------------------


class AmountLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    amount: Decimal

    @model_validator(mode="before")
    @classmethod
    def _money(cls, value: Any) -> Any:
        if isinstance(value, dict) and "currency" in value:
            try:
                currency = normalize_currency(str(value["currency"]))
                amount = to_money(value.get("amount"), currency)
            except MoneyError as exc:
                raise ValueError(str(exc)) from None
            if amount <= 0:
                raise ValueError("A limit is an amount above zero.")
            return {**value, "currency": currency, "amount": amount}
        return value


class CategoryCap(AmountLimit):
    category: Category


class QuietHours(BaseModel):
    """From ``start`` to ``end`` (local time, may wrap past midnight) actions need approval."""

    model_config = ConfigDict(extra="forbid")

    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def _not_empty(self) -> QuietHours:
        if self.start == self.end:
            raise ValueError("Quiet hours need a different start and end.")
        return self

    def minutes(self) -> frozenset[int]:
        def at(text: str) -> int:
            hours, minutes = text.split(":")
            return int(hours) * 60 + int(minutes)

        start, end = at(self.start), at(self.end)
        span = range(start, end) if start < end else [*range(start, 1440), *range(0, end)]
        return frozenset(span)

    def contains(self, moment: time) -> bool:
        return moment.hour * 60 + moment.minute in self.minutes()


class CoolingOff(BaseModel):
    """An action at or above ``at_or_above`` waits ``hours`` after it was first asked for."""

    model_config = ConfigDict(extra="forbid")

    hours: int = Field(ge=1, le=168)
    at_or_above: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)


def _merchant(text: str) -> str:
    return " ".join(re.sub(r"[^\w&.' ]", " ", (text or "").lower()).split())


def merchant_matches(phrase: str, text: str) -> bool:
    """A blocked phrase as a whole word or phrase of the cleaned description."""
    merchant = _merchant(text)
    return bool(merchant) and bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", merchant))


class GuardianRules(BaseModel):
    """The person's rules. Empty lists mean "no rule of this kind"."""

    model_config = ConfigDict(extra="forbid")

    allowed_currencies: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    max_single: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)
    max_daily_total: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)
    category_caps: list[CategoryCap] = Field(default_factory=list, max_length=MAX_LIST)
    blocked_categories: list[Category] = Field(default_factory=list, max_length=MAX_LIST)
    blocked_merchants: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    approval_above: list[AmountLimit] = Field(default_factory=list, max_length=MAX_LIST)
    quiet_hours: QuietHours | None = None
    cooling_off: CoolingOff | None = None

    @field_validator("allowed_currencies")
    @classmethod
    def _codes(cls, value: list[str]) -> list[str]:
        try:
            return sorted({normalize_currency(code) for code in value})
        except MoneyError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("blocked_merchants")
    @classmethod
    def _merchants(cls, value: list[str]) -> list[str]:
        cleaned = sorted({_merchant(item)[:80] for item in value} - {""})
        if any(len(item) < 2 for item in cleaned):
            raise ValueError("A blocked merchant needs at least two letters.")
        return cleaned

    @field_validator("blocked_categories")
    @classmethod
    def _categories(cls, value: list[Category]) -> list[Category]:
        return sorted(set(value), key=lambda c: c.value)

    @model_validator(mode="after")
    def _one_limit_each(self) -> GuardianRules:
        for name in ("max_single", "max_daily_total", "approval_above"):
            items: list[AmountLimit] = getattr(self, name)
            if len({i.currency for i in items}) != len(items):
                raise ValueError(f"{name}: one limit per currency.")
            setattr(self, name, sorted(items, key=lambda i: i.currency))
        if len({(c.category, c.currency) for c in self.category_caps}) != len(self.category_caps):
            raise ValueError("category_caps: one cap per category and currency.")
        self.category_caps = sorted(self.category_caps, key=lambda c: (c.category.value, c.currency))
        if self.cooling_off is not None:
            limits = self.cooling_off.at_or_above
            if len({i.currency for i in limits}) != len(limits):
                raise ValueError("cooling_off: one amount per currency.")
            self.cooling_off.at_or_above = sorted(limits, key=lambda i: i.currency)
        return self

    def canonical(self) -> dict[str, Any]:
        return json.loads(self.model_dump_json())

    def is_empty(self) -> bool:
        return self == GuardianRules()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")


def payload_hash(*, change_id: str, owner_user_id: str, base_version: int, rules: GuardianRules) -> str:
    """SHA-256 of exactly what the person confirms: this change, on this version, these rules."""
    document = {"base_version": base_version, "change_id": change_id, "owner": owner_user_id, "rules": rules.canonical()}
    return hashlib.sha256(canonical_json(document)).hexdigest()


# -- loosening -----------------------------------------------------------------------------------


def _limits(items: list[AmountLimit]) -> dict[str, Decimal]:
    return {i.currency: i.amount for i in items}


def loosening(old: GuardianRules, new: GuardianRules) -> list[str]:
    """Every way ``new`` allows something ``old`` did not. Empty: ``new`` is as strict or stricter."""
    found: list[str] = []
    if old.allowed_currencies and (not new.allowed_currencies or set(new.allowed_currencies) - set(old.allowed_currencies)):
        found.append("allowed_currencies")
    for name in ("max_single", "max_daily_total", "approval_above"):
        before, after = _limits(getattr(old, name)), _limits(getattr(new, name))
        if any(cur not in after or after[cur] > amount for cur, amount in before.items()):
            found.append(name)
    caps_before = {(c.category, c.currency): c.amount for c in old.category_caps}
    caps_after = {(c.category, c.currency): c.amount for c in new.category_caps}
    if any(key not in caps_after or caps_after[key] > amount for key, amount in caps_before.items()):
        found.append("category_caps")
    if set(old.blocked_categories) - set(new.blocked_categories):
        found.append("blocked_categories")
    if set(old.blocked_merchants) - set(new.blocked_merchants):
        found.append("blocked_merchants")
    if old.quiet_hours is not None and (new.quiet_hours is None or not old.quiet_hours.minutes() <= new.quiet_hours.minutes()):
        found.append("quiet_hours")
    if old.cooling_off is not None:
        new_cool = new.cooling_off
        if new_cool is None or new_cool.hours < old.cooling_off.hours:
            found.append("cooling_off")
        else:
            before, after = _limits(old.cooling_off.at_or_above), _limits(new_cool.at_or_above)
            if any(cur not in after or after[cur] > amount for cur, amount in before.items()):
                found.append("cooling_off")
    return found


# -- evaluation ----------------------------------------------------------------------------------


class GuardianAction(BaseModel):
    """One finance action to check. ``amount`` is positive; ``at`` is when it would happen."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    amount: Decimal
    currency: str
    category: Category | None = None
    merchant: str | None = Field(default=None, max_length=500)
    at: datetime
    first_requested_at: datetime | None = None
    requested_by: Literal["person", "agent"] = "person"


class SpendRow(BaseModel):
    currency: str
    amount: Decimal
    category: Category | None = None


class LedgerState(BaseModel):
    """What the ledger already holds: this month's spend per category and today's per currency."""

    timezone: str = "Asia/Dubai"
    month_spend: list[SpendRow] = Field(default_factory=list)
    day_spend: list[SpendRow] = Field(default_factory=list)

    def month(self, category: Category, currency: str) -> Decimal:
        return sum((r.amount for r in self.month_spend if r.category == category and r.currency == currency), Decimal(0))

    def day(self, currency: str) -> Decimal:
        return sum((r.amount for r in self.day_spend if r.currency == currency), Decimal(0))


class Reason(BaseModel):
    code: str
    outcome: Outcome
    message: str
    rule: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


class Decision(BaseModel):
    outcome: Outcome
    reasons: list[Reason]


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Dubai")


def _decide(reasons: list[Reason]) -> Decision:
    outcome: Outcome = "allow"
    for reason in reasons:
        if SEVERITY[reason.outcome] > SEVERITY[outcome]:
            outcome = reason.outcome
    return Decision(outcome=outcome, reasons=reasons)


def evaluate(action: GuardianAction, rules: GuardianRules, state: LedgerState) -> Decision:
    """The rules' verdict on one action. Pure and deterministic; reasons in a fixed order."""
    reasons: list[Reason] = []

    def add(code: str, outcome: Outcome, message: str, rule: str | None = None, **params: str) -> None:
        reasons.append(Reason(code=code, outcome=outcome, message=message, rule=rule, params=params))

    if action.at.tzinfo is None or (action.first_requested_at is not None and action.first_requested_at.tzinfo is None):
        add("invalid_time", "deny", "The action's time has no time zone.")
        return _decide(reasons)
    try:
        currency = normalize_currency(action.currency)
        amount = to_money(action.amount, currency)
    except MoneyError as exc:
        add("invalid_amount", "deny", str(exc))
        return _decide(reasons)
    if amount <= 0:
        add("invalid_amount", "deny", "An action's amount is above zero.")
        return _decide(reasons)
    shown = f"{currency} {amount}"

    if rules.allowed_currencies and currency not in rules.allowed_currencies:
        add("currency_not_allowed", "deny", f"{currency} is not one of your allowed currencies.", "allowed_currencies", currency=currency)
    if action.category is not None and action.category in rules.blocked_categories:
        add("category_blocked", "deny", f"You blocked the category {action.category.value}.", "blocked_categories", category=action.category.value)
    for phrase in rules.blocked_merchants:
        if merchant_matches(phrase, action.merchant or ""):
            add("merchant_blocked", "deny", f"You blocked the merchant {phrase}.", "blocked_merchants", merchant=phrase)
            break
    single = _limits(rules.max_single).get(currency)
    if single is not None and amount > single:
        add("over_single_limit", "deny", f"{shown} is above your single limit of {currency} {single}.", "max_single",
            limit=str(single), currency=currency)
    daily = _limits(rules.max_daily_total).get(currency)
    if daily is not None:
        spent = state.day(currency)
        if spent + amount > daily:
            add("over_daily_total", "deny", f"With {currency} {spent} already today, {shown} goes over your daily total of {currency} {daily}.",
                "max_daily_total", limit=str(daily), spent=str(spent), currency=currency)
    if action.category is not None:
        for cap in rules.category_caps:
            if cap.category == action.category and cap.currency == currency:
                spent = state.month(cap.category, currency)
                if spent + amount > cap.amount:
                    add("over_category_cap", "deny",
                        f"With {currency} {spent} on {cap.category.value} this month, {shown} goes over your cap of {currency} {cap.amount}.",
                        "category_caps", limit=str(cap.amount), spent=str(spent), currency=currency, category=cap.category.value)
    if action.kind == ActionKind.RECORD:
        # Money already gone: approval and the time rules (quiet hours, cooling off) do not apply.
        return _decide(reasons)
    approval = _limits(rules.approval_above).get(currency)
    if approval is not None and amount >= approval:
        add("above_approval_amount", "needs_approval", f"{shown} is at or above {currency} {approval}: you approve it first.",
            "approval_above", limit=str(approval), currency=currency)
    local = action.at.astimezone(_zone(state.timezone))
    if rules.quiet_hours is not None and rules.quiet_hours.contains(local.time()):
        add("quiet_hours", "needs_approval", f"It is within your quiet hours ({rules.quiet_hours.start} to {rules.quiet_hours.end}).",
            "quiet_hours", start=rules.quiet_hours.start, end=rules.quiet_hours.end)
    if rules.cooling_off is not None:
        threshold = _limits(rules.cooling_off.at_or_above).get(currency)
        if threshold is not None and amount >= threshold:
            first = action.first_requested_at or action.at
            ready = first + timedelta(hours=rules.cooling_off.hours)
            if action.at < ready:
                until = ready.astimezone(_zone(state.timezone)).replace(microsecond=0).isoformat()
                add("cooling_off", "needs_approval", f"Your cooling-off period of {rules.cooling_off.hours} hours ends at {until}.",
                    "cooling_off", hours=str(rules.cooling_off.hours), until=until)
    if action.requested_by == "agent" and action.kind in MONEY_MOVING:
        add("agent_request", "needs_approval", "An assistant asked for this: you approve it yourself.")
    return _decide(reasons)


def gate(action: GuardianAction, rules: GuardianRules, state: LedgerState) -> Decision:
    """The mandatory check before any finance action: the rules plus the kernel's own limits.

    No rule can lift a kernel limit. Today ANUM moves no money, so every money-moving
    action is denied here; a ledger record (``record``) is only checked against the rules.
    """
    decision = evaluate(action, rules, state)
    reasons = list(decision.reasons)
    if action.kind in MONEY_MOVING:
        reasons.append(Reason(
            code="execution_not_available", outcome="deny",
            message="ANUM does not move money: there is no payment, transfer or trading path. Nothing was sent.",
        ))
    return _decide(reasons)


# -- stored versions and proposals ---------------------------------------------------------------


class GuardianPolicy(_Owned):
    """One version of the person's rules. A change adds a version; none is overwritten."""

    id: str
    version: int
    rules: GuardianRules
    change_id: str
    confirmed_by: str
    created_at: datetime


ChangeStatus = Literal["pending", "confirmed", "rejected", "expired", "superseded"]


class GuardianChange(_Owned):
    """A proposed set of rules, waiting for the person to confirm it with its payload hash."""

    id: str
    base_version: int
    rules: GuardianRules
    proposed_by: Literal["person", "agent"]
    proposer: str
    reason: str | None = None
    payload_hash: str
    loosens: list[str] = Field(default_factory=list)
    status: ChangeStatus = "pending"
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None

    def lapsed(self, now: datetime) -> bool:
        return self.status == "pending" and now >= self.expires_at
