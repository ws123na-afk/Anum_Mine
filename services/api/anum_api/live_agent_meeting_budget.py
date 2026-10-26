"""Workspace-wide monthly cost cap for live agent meetings (finding S-02, owner decision of 25 October 2026).

Every workspace may spend at most ``limit`` USD on agent meeting turns per calendar month
(UTC). The default is USD 50; a workspace owner (``workspace:manage``) can change it.

Metering. The cost of a turn is the figure the model gateway reported for it (the same
``estimated_cost_usd`` the per-meeting limit uses). It is added to
``agent_meeting_usage_monthly`` in the same transaction that stores the turn. The month's
usage is the larger of that ledger and the sum over the stored turns, so turns written
before the ledger existed count, and a deleted transcript (which removes turn rows) does
not hand the budget back.

Enforcement, concurrency. ``gate`` runs in the transaction that claims a turn. It first
takes a transaction-level advisory lock for the workspace, so two meetings cannot claim at
the same time and each claim sees the other's lease. A claim is refused when

    used + in_flight * reserve >= limit

where ``in_flight`` is the number of unexpired turn leases in the workspace and ``reserve``
is the largest cost among the workspace's last 20 turns (at least USD 0.10). So a turn only
starts while the budget still covers the turns already running at their typical size.
The turn that is allowed to start can end above the limit by what it costs beyond its own
reserve: the month's spend can pass the cap by about one turn, not by one turn per
meeting. The overshoot is never hidden: it is stored and shown.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from .live_agent_meeting_store import LiveMeetingStoreError, SqlLiveMeetingStore
from .schemas import TenantContext

DEFAULT_MONTHLY_CAP_USD = Decimal("50")
MAX_MONTHLY_CAP_USD = Decimal("100000")
RESERVE_FLOOR_USD = Decimal("0.10")
REACHED = "Monthly agent meeting budget reached"
_CENT = Decimal("0.01")


class MeetingBudgetReached(LiveMeetingStoreError):
    """Raised before a model call when the workspace's monthly meeting budget has no room left."""

    def __init__(self, state: BudgetState):
        super().__init__("budget", budget_message(state))
        self.state = state


@dataclass(frozen=True, slots=True)
class BudgetState:
    limit_usd: Decimal
    is_default: bool
    used_usd: Decimal
    turns: int
    in_flight: int
    reserve_usd: Decimal
    period_start: date
    resets_on: date
    updated_by: str | None
    updated_at: datetime | None

    @property
    def remaining_usd(self) -> Decimal:
        return max(Decimal(0), self.limit_usd - self.used_usd)

    @property
    def exceeded(self) -> bool:
        """True when no further turn may start, counting turns already running."""
        return self.used_usd + self.in_flight * self.reserve_usd >= self.limit_usd

    @property
    def percent(self) -> float:
        if self.limit_usd <= 0:
            return 100.0
        return float(min(Decimal(100), self.used_usd * 100 / self.limit_usd))

    def view(self) -> dict[str, Any]:
        # percent is cut, not rounded, so it never reads 100 while a turn can still start
        return {
            "limit_usd": float(self.limit_usd), "default_limit_usd": float(DEFAULT_MONTHLY_CAP_USD),
            "is_default": self.is_default, "used_usd": float(self.used_usd), "remaining_usd": float(self.remaining_usd),
            "percent": math.floor(self.percent * 10) / 10, "turns": self.turns, "exceeded": self.exceeded,
            "period_start": self.period_start.isoformat(), "resets_on": self.resets_on.isoformat(),
            "updated_by": self.updated_by, "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "message": budget_message(self) if self.exceeded else None,
        }


def _money(value: Decimal) -> str:
    return str(value.quantize(_CENT))


def budget_message(state: BudgetState) -> str:
    """One plain sentence; the Arabic form is in ``localization.py`` and must match this text."""
    return (f"{REACHED}: this workspace has used USD {_money(state.used_usd)} of its USD {_money(state.limit_usd)} "
            f"for agent meetings this month. It resets on {state.resets_on.isoformat()} (UTC); "
            "a workspace owner can raise the limit under Budgets.")


def period_bounds(now: datetime | None = None) -> tuple[date, date]:
    """First day of the current UTC month and of the next one."""
    day = (now or datetime.now(UTC)).astimezone(UTC).date()
    start = day.replace(day=1)
    following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, following


def _args(store: SqlLiveMeetingStore, context: TenantContext, **kw: Any) -> dict[str, Any]:
    return store.args(context, **kw)


def read_state(store: SqlLiveMeetingStore, context: TenantContext, now: datetime | None = None) -> BudgetState:
    start, following = period_bounds(now)
    args = _args(store, context, start=datetime(start.year, start.month, start.day, tzinfo=UTC),
                 following=datetime(following.year, following.month, following.day, tzinfo=UTC), day=start)
    run = store.session.execute
    setting = run(text("""
        select monthly_cost_limit_usd, updated_by, updated_at from agent_meeting_budgets
        where tenant_id=:tenant and workspace_id=:workspace"""), args).mappings().first()
    ledger = run(text("""
        select cost_usd, turns from agent_meeting_usage_monthly
        where tenant_id=:tenant and workspace_id=:workspace and period_start=:day"""), args).mappings().first()
    stored = run(text("""
        select coalesce(sum(estimated_cost_usd),0) as cost, count(*) as turns from agent_meeting_turns
        where tenant_id=:tenant and workspace_id=:workspace and role='agent'
          and created_at >= :start and created_at < :following"""), args).mappings().one()
    in_flight = run(text("""
        select count(*) from agent_meeting_turn_leases
        where tenant_id=:tenant and workspace_id=:workspace and expires_at > now()"""), args).scalar_one()
    largest = run(text("""
        select coalesce(max(estimated_cost_usd),0) from (
          select estimated_cost_usd from agent_meeting_turns
          where tenant_id=:tenant and workspace_id=:workspace and role='agent'
          order by created_at desc limit 20) recent"""), args).scalar_one()
    used = max(Decimal(ledger["cost_usd"]) if ledger else Decimal(0), Decimal(stored["cost"]))
    turns = max(int(ledger["turns"]) if ledger else 0, int(stored["turns"]))
    return BudgetState(
        limit_usd=Decimal(setting["monthly_cost_limit_usd"]) if setting else DEFAULT_MONTHLY_CAP_USD,
        is_default=setting is None, used_usd=used, turns=turns, in_flight=int(in_flight),
        reserve_usd=max(RESERVE_FLOOR_USD, Decimal(largest)), period_start=start, resets_on=following,
        updated_by=setting["updated_by"] if setting else None, updated_at=setting["updated_at"] if setting else None,
    )


def lock_workspace(store: SqlLiveMeetingStore, context: TenantContext) -> None:
    """Serialise budget decisions for this workspace until the transaction ends."""
    store.session.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                          {"key": f"{context.tenant_id}/{context.workspace_id}/agent_meeting_budget"})


def gate(store: SqlLiveMeetingStore, context: TenantContext, now: datetime | None = None) -> BudgetState:
    """Call in the transaction that inserts the turn lease. Raises ``MeetingBudgetReached`` when blocked."""
    lock_workspace(store, context)
    state = read_state(store, context, now)
    if state.exceeded:
        raise MeetingBudgetReached(state)
    return state


def record_usage(store: SqlLiveMeetingStore, context: TenantContext, cost: Decimal, now: datetime | None = None) -> BudgetState:
    """Add a committed turn's cost to the month's ledger (same transaction as the turn) and return the new state."""
    start, _ = period_bounds(now)
    store.session.execute(text("""
        insert into agent_meeting_usage_monthly (tenant_id, workspace_id, period_start, cost_usd, turns)
        values (:tenant, :workspace, :day, :cost, 1)
        on conflict (tenant_id, workspace_id, period_start)
        do update set cost_usd = agent_meeting_usage_monthly.cost_usd + excluded.cost_usd,
                      turns = agent_meeting_usage_monthly.turns + 1, updated_at = now()"""),
        _args(store, context, day=start, cost=cost))
    return read_state(store, context, now)


def set_limit(store: SqlLiveMeetingStore, context: TenantContext, limit: Decimal | None,
              now: datetime | None = None) -> tuple[BudgetState, BudgetState]:
    """Change the workspace's cap (``None`` returns to the default). Returns (before, after)."""
    lock_workspace(store, context)
    before = read_state(store, context, now)
    if limit is None:
        store.session.execute(text("delete from agent_meeting_budgets where tenant_id=:tenant and workspace_id=:workspace"),
                              _args(store, context))
    else:
        store.session.execute(text("""
            insert into agent_meeting_budgets (tenant_id, workspace_id, monthly_cost_limit_usd, updated_by)
            values (:tenant, :workspace, :limit, :user)
            on conflict (tenant_id, workspace_id)
            do update set monthly_cost_limit_usd = excluded.monthly_cost_limit_usd,
                          updated_by = excluded.updated_by, updated_at = now()"""),
            _args(store, context, limit=limit, user=context.user_id))
    return before, read_state(store, context, now)
