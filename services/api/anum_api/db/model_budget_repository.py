"""PostgreSQL model budgets and monthly usage (``model_budgets``, ``model_usage_monthly``).

The session must already carry the tenant and workspace RLS context
(``set_tenant_context``); every statement also filters on the scope explicitly. Usage is
added with one ``insert ... on conflict do update`` so concurrent calls never lose an
increment.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from anum_api.model_budget import (
    BudgetScope,
    ModelBudget,
    ModelBudgetLimits,
    ModelUsageTotals,
    UsageBreakdownRow,
    UsageDelta,
    UsageGroup,
    UsageKey,
)
from anum_api.schemas import TenantContext

from .scope import require_workspace

_USAGE_SUMS = (
    "select sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, "
    "sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, "
    "sum(unpriced_calls) as unpriced_calls from model_usage_monthly "
)
_TENANT_USAGE = text(_USAGE_SUMS + "where tenant_id = :tenant_id and month = :month")
_WORKSPACE_USAGE = text(
    _USAGE_SUMS + "where tenant_id = :tenant_id and workspace_id = :workspace_id and month = :month"
)

# The spend breakdown (docs/model-gateway.md#spend-breakdown): one literal statement per group and
# scope, so no column name is ever built from input. ``:start`` inclusive, ``:end`` exclusive.
_BREAKDOWN_STATEMENTS = {
    (UsageGroup.MONTH, BudgetScope.TENANT): text(
        "select to_char(month, 'YYYY-MM-DD') as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_monthly where tenant_id = :tenant_id and month >= :start and month < :end group by month"
    ),
    (UsageGroup.MONTH, BudgetScope.WORKSPACE): text(
        "select to_char(month, 'YYYY-MM-DD') as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_monthly where tenant_id = :tenant_id and workspace_id = :workspace_id and month >= :start and month < :end group by month"
    ),
    (UsageGroup.DAY, BudgetScope.TENANT): text(
        "select to_char(day, 'YYYY-MM-DD') as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and day >= :start and day < :end group by day"
    ),
    (UsageGroup.DAY, BudgetScope.WORKSPACE): text(
        "select to_char(day, 'YYYY-MM-DD') as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and workspace_id = :workspace_id and day >= :start and day < :end group by day"
    ),
    (UsageGroup.USER, BudgetScope.TENANT): text(
        "select user_id as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and day >= :start and day < :end group by user_id"
    ),
    (UsageGroup.USER, BudgetScope.WORKSPACE): text(
        "select user_id as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and workspace_id = :workspace_id and day >= :start and day < :end group by user_id"
    ),
    (UsageGroup.AGENT, BudgetScope.TENANT): text(
        "select agent_id as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and day >= :start and day < :end group by agent_id"
    ),
    (UsageGroup.AGENT, BudgetScope.WORKSPACE): text(
        "select agent_id as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and workspace_id = :workspace_id and day >= :start and day < :end group by agent_id"
    ),
    (UsageGroup.FEATURE, BudgetScope.TENANT): text(
        "select feature as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and day >= :start and day < :end group by feature"
    ),
    (UsageGroup.FEATURE, BudgetScope.WORKSPACE): text(
        "select feature as key, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens, sum(estimated_cost_usd) as estimated_cost_usd, sum(calls) as calls, sum(unpriced_calls) as unpriced_calls from model_usage_daily where tenant_id = :tenant_id and workspace_id = :workspace_id and day >= :start and day < :end group by feature"
    ),
}
_FIRST_DAY = text("select min(day) as day from model_usage_daily where tenant_id = :tenant_id")


def _scope_workspace(context: TenantContext, scope: BudgetScope) -> str | None:
    return context.workspace_id if scope == BudgetScope.WORKSPACE else None


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


class SqlAlchemyModelBudgetStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_budget(self, context: TenantContext, scope: BudgetScope) -> ModelBudget | None:
        row = self.session.execute(
            text(
                "select monthly_cost_limit_usd, monthly_token_limit, alert_percent, updated_by, updated_at "
                "from model_budgets where tenant_id = :tenant_id "
                "and coalesce(workspace_id, '') = coalesce(:workspace_id, '')"
            ),
            {"tenant_id": context.tenant_id, "workspace_id": _scope_workspace(context, scope)},
        ).one_or_none()
        if row is None:
            return None
        return ModelBudget(
            scope=scope,
            monthly_cost_limit_usd=(
                None if row.monthly_cost_limit_usd is None else float(row.monthly_cost_limit_usd)
            ),
            monthly_token_limit=row.monthly_token_limit,
            alert_percent=row.alert_percent,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
        )

    def set_budget(
        self, context: TenantContext, scope: BudgetScope, limits: ModelBudgetLimits, now: datetime
    ) -> ModelBudget:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        values = {
            "tenant_id": context.tenant_id,
            "workspace_id": _scope_workspace(context, scope),
            "cost": limits.monthly_cost_limit_usd,
            "tokens": limits.monthly_token_limit,
            "alert": limits.alert_percent,
            "updated_by": context.user_id,
            "now": now,
        }
        updated = self.session.execute(
            text(
                "update model_budgets set monthly_cost_limit_usd = :cost, "
                "monthly_token_limit = :tokens, alert_percent = :alert, updated_by = :updated_by, updated_at = :now "
                "where tenant_id = :tenant_id "
                "and coalesce(workspace_id, '') = coalesce(:workspace_id, '')"
            ),
            values,
        )
        if updated.rowcount == 0:
            self.session.execute(
                text(
                    "insert into model_budgets (tenant_id, workspace_id, monthly_cost_limit_usd, "
                    "monthly_token_limit, alert_percent, updated_by, created_at, updated_at) "
                    "values (:tenant_id, :workspace_id, :cost, :tokens, :alert, :updated_by, :now, :now)"
                ),
                values,
            )
        return ModelBudget(scope=scope, updated_at=now, updated_by=context.user_id, **limits.model_dump())

    def usage(self, context: TenantContext, scope: BudgetScope, month: date) -> ModelUsageTotals:
        params = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "month": month}
        query = _WORKSPACE_USAGE if scope == BudgetScope.WORKSPACE else _TENANT_USAGE
        row = self.session.execute(query, params).one_or_none()
        return _totals(row)

    def record_usage(
        self, context: TenantContext, month: date, delta: UsageDelta, now: datetime
    ) -> ModelUsageTotals:
        row = self.session.execute(
            text(
                """
                insert into model_usage_monthly as usage (
                    tenant_id, workspace_id, month, input_tokens, output_tokens,
                    estimated_cost_usd, calls, unpriced_calls, updated_at
                )
                values (
                    :tenant_id, :workspace_id, :month, :input_tokens, :output_tokens,
                    :cost, 1, :unpriced, :now
                )
                on conflict (tenant_id, workspace_id, month) do update set
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
                "month": month,
                "input_tokens": delta.input_tokens,
                "output_tokens": delta.output_tokens,
                "cost": delta.estimated_cost_usd or 0,
                "unpriced": 1 if delta.unpriced else 0,
                "now": now,
            },
        ).one()
        return _totals(row)

    def record_usage_detail(self, context: TenantContext, key: UsageKey, delta: UsageDelta, now: datetime) -> None:
        self.session.execute(
            text(
                """
                insert into model_usage_daily as usage (
                    tenant_id, workspace_id, day, user_id, agent_id, feature, input_tokens, output_tokens,
                    estimated_cost_usd, calls, unpriced_calls, updated_at
                )
                values (
                    :tenant_id, :workspace_id, :day, :user_id, :agent_id, :feature, :input_tokens, :output_tokens,
                    :cost, 1, :unpriced, :now
                )
                on conflict (tenant_id, workspace_id, day, user_id, agent_id, feature) do update set
                    input_tokens = usage.input_tokens + excluded.input_tokens,
                    output_tokens = usage.output_tokens + excluded.output_tokens,
                    estimated_cost_usd = usage.estimated_cost_usd + excluded.estimated_cost_usd,
                    calls = usage.calls + 1,
                    unpriced_calls = usage.unpriced_calls + excluded.unpriced_calls,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "day": key.day,
                "user_id": key.user_id,
                "agent_id": key.agent_id,
                "feature": key.feature,
                "input_tokens": delta.input_tokens,
                "output_tokens": delta.output_tokens,
                "cost": delta.estimated_cost_usd or 0,
                "unpriced": 1 if delta.unpriced else 0,
                "now": now,
            },
        )

    def usage_breakdown(
        self, context: TenantContext, scope: BudgetScope, group_by: UsageGroup, start: date, end: date
    ) -> list[UsageBreakdownRow]:
        rows = self.session.execute(
            _BREAKDOWN_STATEMENTS[(group_by, scope)],
            {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "start": start, "end": end},
        ).all()
        return [UsageBreakdownRow.of(str(row.key), _totals(row)) for row in rows]

    def first_recorded_day(self, context: TenantContext) -> date | None:
        row = self.session.execute(_FIRST_DAY, {"tenant_id": context.tenant_id}).one_or_none()
        return None if row is None or row.day is None else row.day
