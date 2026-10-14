"""Wider live use, stage 9: the framework only, shipped OFF (docs/finance.md#wider-live).

Limited live (stage 8) could only ever widen when measured proof exists, one tier at a
time, within hard ceilings, after the owner's approval and a cooling-off period, and it
steps back down on any incident. None of it can run today: there is no live executor,
``ANUM_FINANCE_LIVE_ENABLED`` is refused at startup, ``ANUM_FINANCE_WIDER_LIVE_ENABLED``
needs it, and the execution gateway refuses every live intent whatever the tier.

* ``graduation``: the checklist, computed from the stage 7 and 8 records over a window:
  days since the live opt-in, executed live actions, no Guardian denial overridden, no
  failed execution (a failed reconciliation), no kill switch tripped, not frozen, and every
  stage 8 precondition met. Pure and deterministic.
* ``Widening``: a proposal (by the person or an assistant) of the next tier's limits and
  action types, bound to a payload hash the owner approves once before it expires. It moves
  exactly one tier, stays under the ceiling (each tier at most ``wider_step_percent`` of
  the stage 8 limits above the last, at most ``wider_max_tier`` tiers), adds at most one
  action type, and takes effect only after ``wider_cooling_hours``. An assistant can propose
  but never approve, and a widening never changes the Guardian rules: loosening a rule is
  only ever the person's own Guardian change, reviewed with its code.
* ``Incident``: a kill switch, a failed execution or repeated Guardian denials drop the tier
  back one step, cancel any approved widening still cooling off, and freeze widening until
  the owner reviews it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from .guardian import AmountLimit, canonical_json
from .live import LiveLimits
from .models import _Owned

LIVE_KINDS = ("trade", "transfer", "spend", "bill_payment")
WIDENING_TTL = timedelta(hours=24)
REPEATED_DENIALS = 3  # Guardian denials within a day that count as an incident


class Widening(_Owned):
    """A proposed step up one tier, waiting for the owner's approval with its payload hash."""

    id: str
    base_tier: int
    tier: int
    limits: LiveLimits
    kinds: list[str]
    proposed_by: Literal["person", "agent"]
    proposer: str
    reason: str | None = None
    payload_hash: str
    status: Literal["pending", "approved", "rejected", "expired", "superseded", "cancelled"] = "pending"
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    effective_at: datetime | None = None  # approved: takes effect after the cooling-off

    def lapsed(self, now: datetime) -> bool:
        return self.status == "pending" and now >= self.expires_at


class Incident(_Owned):
    id: str
    code: Literal["kill_switch_person", "kill_switch_global", "failed_execution", "repeated_denials"]
    tier_before: int
    tier_after: int
    created_at: datetime
    reviewed_at: datetime | None = None


def widening_hash(widening: Widening) -> str:
    document = {
        "base_tier": widening.base_tier, "id": widening.id, "kinds": sorted(widening.kinds), "limits": widening.limits.model_dump(mode="json"),
        "owner": widening.owner_user_id, "tier": widening.tier,
    }
    return hashlib.sha256(canonical_json(document)).hexdigest()


# -- graduation -------------------------------------------------------------------------------------


class Criterion(BaseModel):
    code: str
    met: bool
    value: str
    needed: str


class LiveRecord(BaseModel):
    """What graduation reads of one stage 7 or 8 request: never an amount or a symbol."""

    mode: str
    status: str
    outcome: str | None  # the Guardian decision's outcome
    created_at: datetime


def graduation(
    *, now: datetime, opted_in_at: datetime | None, records: list[LiveRecord], kill_switch_at: list[datetime], frozen: bool,
    preconditions_met: bool, window_days: int, min_days: int, min_actions: int,
) -> list[Criterion]:
    """The proof widening needs, every item measured over the window. Pure."""
    since = now - timedelta(days=window_days)
    recent = [r for r in records if r.created_at >= since]
    live = [r for r in recent if r.mode == "live"]
    executed = [r for r in live if r.status == "executed"]
    overridden = [r for r in recent if r.status == "executed" and r.outcome == "deny"]
    failed = [r for r in recent if r.status == "failed"]
    trips = [at for at in kill_switch_at if at >= since]
    days = (now - opted_in_at).days if opted_in_at else 0
    return [
        Criterion(code="stage8_ready", met=preconditions_met, value="met" if preconditions_met else "not met", needed="every stage 8 precondition"),
        Criterion(code="days_live", met=opted_in_at is not None and days >= min_days, value=str(days), needed=str(min_days)),
        Criterion(code="executed_actions", met=len(executed) >= min_actions, value=str(len(executed)), needed=str(min_actions)),
        Criterion(code="no_denials_overridden", met=not overridden, value=str(len(overridden)), needed="0"),
        Criterion(code="no_failed_executions", met=not failed, value=str(len(failed)), needed="0"),
        Criterion(code="no_kill_switch_trips", met=not trips, value=str(len(trips)), needed="0"),
        Criterion(code="not_frozen", met=not frozen, value="frozen" if frozen else "open", needed="open"),
    ]


def graduated(criteria: list[Criterion]) -> bool:
    return all(c.met for c in criteria)


# -- ceilings -----------------------------------------------------------------------------------------


def _limits(items: list[AmountLimit]) -> dict[str, Decimal]:
    return {i.currency: i.amount for i in items}


def ceiling_breaches(base: LiveLimits, proposed: LiveLimits, tier: int, *, max_tier: int, step_percent: int) -> list[str]:
    """Every way ``proposed`` goes past tier ``tier``'s hard ceiling over the stage 8 ``base``."""
    found: list[str] = []
    if tier < 1 or tier > max_tier:
        found.append("tier_out_of_range")
        return found
    factor = Decimal(1) + Decimal(step_percent) * tier / Decimal(100)
    for name in ("max_per_action", "max_per_day", "max_per_month"):
        before, after = _limits(getattr(base, name)), _limits(getattr(proposed, name))
        for currency, amount in after.items():
            if currency not in before or amount > before[currency] * factor:
                found.append(name)
                break
    if set(proposed.currencies) - set(base.currencies):
        found.append("currencies")
    if proposed.cooling_off_hours < base.cooling_off_hours:
        found.append("cooling_off_hours")
    return found


def added_kinds(current: list[str], proposed: list[str]) -> list[str]:
    return sorted(set(proposed) - set(current))


# -- incidents ----------------------------------------------------------------------------------------


def incident_codes(
    *, since: datetime, records: list[LiveRecord], person_switch_at: datetime | None, person_switch_on: bool,
    global_switch_at: datetime | None, global_switch_on: bool,
) -> list[str]:
    """New incidents since ``since`` (the last check). Pure."""
    codes: list[str] = []
    if person_switch_on and person_switch_at is not None and person_switch_at > since:
        codes.append("kill_switch_person")
    if global_switch_on and global_switch_at is not None and global_switch_at > since:
        codes.append("kill_switch_global")
    if any(r.status == "failed" and r.created_at > since for r in records):
        codes.append("failed_execution")
    denials = sorted(r.created_at for r in records if r.status == "denied" and r.created_at > since)
    if any(denials[i + REPEATED_DENIALS - 1] - denials[i] <= timedelta(days=1) for i in range(len(denials) - REPEATED_DENIALS + 1)):
        codes.append("repeated_denials")
    return codes


class WiderSettings(BaseModel):
    """The person's stage 9 state, kept with their execution settings."""

    tier: int = 0
    kinds: list[str] = Field(default_factory=lambda: ["trade"])
    frozen: bool = False
    frozen_at: datetime | None = None
    frozen_reason: str | None = None
    checked_at: datetime | None = None
    reviewed_at: datetime | None = None


def step_down(state: WiderSettings, code: str, now: datetime) -> tuple[int, int]:
    before = state.tier
    state.tier = max(0, state.tier - 1)
    state.frozen, state.frozen_at, state.frozen_reason = True, now, code
    return before, state.tier


def summary(state: WiderSettings) -> dict[str, Any]:
    return {"tier": state.tier, "kinds": state.kinds, "frozen": state.frozen}
