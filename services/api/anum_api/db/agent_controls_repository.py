"""PostgreSQL store for the owner agent controls (migration ``0080_agent_controls``).

``agent_emergency_stops`` is tenant-scoped; ``agent_cost_caps`` and ``agent_usage_monthly``
are workspace-scoped. The session must already carry the RLS context
(``set_tenant_context``); every statement also filters on the scope explicitly. Usage is
added with one ``insert ... on conflict do update`` so concurrent calls never lose an
increment.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from anum_api.agent_controls import AgentCap, EmergencyStop
from anum_api.model_budget import ModelUsageTotals, UsageDelta
from anum_api.schemas import TenantContext

from .scope import require_tenant, require_workspace

def _totals(row) -> ModelUsageTotals:  # type: ignore[no-untyped-def]
    if row is None:
        return ModelUsageTotals()
    return ModelUsageTotals(
        input_tokens=int(row.input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
        estimated_cost_usd=float(row.estimated_cost_usd or 0),
        calls=int(row.calls or 0),
        unpriced_calls=int(row.unpriced_calls or 0),
    )


def _cap(row) -> AgentCap:  # type: ignore[no-untyped-def]
    return AgentCap(
        agent_id=row.agent_id,
        monthly_cost_limit_usd=None if row.monthly_cost_limit_usd is None else float(row.monthly_cost_limit_usd),
        monthly_token_limit=None if row.monthly_token_limit is None else int(row.monthly_token_limit),
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def stopped_in_session(session: Session, tenant_id: str) -> bool:
    """Whether ``tenant_id``'s emergency stop is on, read through an existing tenant session."""
    return bool(
        session.execute(
            text("select engaged from agent_emergency_stops where tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).scalar()
    )


class SqlAlchemyAgentControlsStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- emergency stop ------------------------------------------------------------------

    def get_stop(self, context: TenantContext) -> EmergencyStop | None:
        row = self.session.execute(
            text(
                "select engaged, reason, changed_by, changed_at, engaged_at, version "
                "from agent_emergency_stops where tenant_id = :tenant_id"
            ),
            {"tenant_id": context.tenant_id},
        ).one_or_none()
        if row is None:
            return None
        return EmergencyStop(
            engaged=bool(row.engaged),
            reason=row.reason,
            changed_by=row.changed_by,
            changed_at=row.changed_at,
            engaged_at=row.engaged_at,
            version=int(row.version),
        )

    def get_stop_for_update(self, context: TenantContext) -> EmergencyStop | None:
        row = self.session.execute(
            text("select tenant_id from agent_emergency_stops where tenant_id = :tenant_id for update"),
            {"tenant_id": context.tenant_id},
        ).one_or_none()
        return None if row is None else self.get_stop(context)

    def save_stop(self, context: TenantContext, stop: EmergencyStop) -> EmergencyStop:
        require_tenant(self.session, context.tenant_id)
        self.session.execute(
            text(
                """
                insert into agent_emergency_stops as stop (
                    tenant_id, engaged, reason, changed_by, changed_at, engaged_at, version
                )
                values (:tenant_id, :engaged, :reason, :changed_by, :changed_at, :engaged_at, :version)
                on conflict (tenant_id) do update set
                    engaged = excluded.engaged,
                    reason = excluded.reason,
                    changed_by = excluded.changed_by,
                    changed_at = excluded.changed_at,
                    engaged_at = excluded.engaged_at,
                    version = excluded.version
                """
            ),
            {
                "tenant_id": context.tenant_id,
                "engaged": stop.engaged,
                "reason": stop.reason,
                "changed_by": stop.changed_by or context.user_id,
                "changed_at": stop.changed_at,
                "engaged_at": stop.engaged_at,
                "version": max(stop.version, 1),
            },
        )
        return stop

    # -- caps ----------------------------------------------------------------------------

    def list_caps(self, context: TenantContext) -> list[AgentCap]:
        rows = self.session.execute(
            text(
                "select agent_id, monthly_cost_limit_usd, monthly_token_limit, updated_by, updated_at from agent_cost_caps "
                "where tenant_id = :tenant_id and workspace_id = :workspace_id"
            ),
            {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id},
        ).all()
        return [_cap(row) for row in rows]

    def get_cap(self, context: TenantContext, agent_id: str) -> AgentCap | None:
        row = self.session.execute(
            text(
                "select agent_id, monthly_cost_limit_usd, monthly_token_limit, updated_by, updated_at from agent_cost_caps "
                "where tenant_id = :tenant_id and workspace_id = :workspace_id and agent_id = :agent_id"
            ),
            {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "agent_id": agent_id},
        ).one_or_none()
        return None if row is None else _cap(row)

    def set_cap(
        self, context: TenantContext, agent_id: str, cost: float | None, tokens: int | None, now: datetime
    ) -> AgentCap | None:
        scope = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "agent_id": agent_id}
        if cost is None and tokens is None:
            self.session.execute(
                text(
                    "delete from agent_cost_caps where tenant_id = :tenant_id "
                    "and workspace_id = :workspace_id and agent_id = :agent_id"
                ),
                scope,
            )
            return None
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(
            text(
                """
                insert into agent_cost_caps (
                    tenant_id, workspace_id, agent_id, monthly_cost_limit_usd, monthly_token_limit, updated_by, updated_at
                )
                values (:tenant_id, :workspace_id, :agent_id, :cost, :tokens, :updated_by, :now)
                on conflict (tenant_id, workspace_id, agent_id) do update set
                    monthly_cost_limit_usd = excluded.monthly_cost_limit_usd,
                    monthly_token_limit = excluded.monthly_token_limit,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """
            ),
            {**scope, "cost": cost, "tokens": tokens, "updated_by": context.user_id, "now": now},
        )
        return AgentCap(
            agent_id=agent_id, monthly_cost_limit_usd=cost, monthly_token_limit=tokens, updated_by=context.user_id, updated_at=now
        )

    # -- usage ---------------------------------------------------------------------------

    def agent_usage(self, context: TenantContext, agent_id: str, month: date) -> ModelUsageTotals:
        row = self.session.execute(
            text(
                "select input_tokens, output_tokens, estimated_cost_usd, calls, unpriced_calls from agent_usage_monthly where tenant_id = :tenant_id "
                "and workspace_id = :workspace_id and agent_id = :agent_id and month = :month"
            ),
            {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "agent_id": agent_id, "month": month},
        ).one_or_none()
        return _totals(row)

    def usage_by_agent(self, context: TenantContext, month: date) -> dict[str, ModelUsageTotals]:
        rows = self.session.execute(
            text(
                "select agent_id, input_tokens, output_tokens, estimated_cost_usd, calls, unpriced_calls from agent_usage_monthly where tenant_id = :tenant_id "
                "and workspace_id = :workspace_id and month = :month"
            ),
            {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "month": month},
        ).all()
        return {row.agent_id: _totals(row) for row in rows}

    def record_agent_usage(
        self, context: TenantContext, agent_id: str, month: date, delta: UsageDelta, now: datetime
    ) -> ModelUsageTotals:
        row = self.session.execute(
            text(
                """
                insert into agent_usage_monthly as usage (
                    tenant_id, workspace_id, agent_id, month, input_tokens, output_tokens,
                    estimated_cost_usd, calls, unpriced_calls, updated_at
                )
                values (
                    :tenant_id, :workspace_id, :agent_id, :month, :input_tokens, :output_tokens,
                    :cost, 1, :unpriced, :now
                )
                on conflict (tenant_id, workspace_id, agent_id, month) do update set
                    input_tokens = usage.input_tokens + excluded.input_tokens,
                    output_tokens = usage.output_tokens + excluded.output_tokens,
                    estimated_cost_usd = usage.estimated_cost_usd + excluded.estimated_cost_usd,
                    calls = usage.calls + 1,
                    unpriced_calls = usage.unpriced_calls + excluded.unpriced_calls,
                    updated_at = excluded.updated_at
                returning input_tokens, output_tokens, estimated_cost_usd, calls, unpriced_calls
                """
            ),
            {
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "agent_id": agent_id,
                "month": month,
                "input_tokens": delta.input_tokens,
                "output_tokens": delta.output_tokens,
                "cost": delta.estimated_cost_usd or 0,
                "unpriced": 1 if delta.estimated_cost_usd is None else 0,
                "now": now,
            },
        ).one()
        return _totals(row)
