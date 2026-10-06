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
    UsageDelta,
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
                "select monthly_cost_limit_usd, monthly_token_limit, updated_by, updated_at "
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
            "updated_by": context.user_id,
            "now": now,
        }
        updated = self.session.execute(
            text(
                "update model_budgets set monthly_cost_limit_usd = :cost, "
                "monthly_token_limit = :tokens, updated_by = :updated_by, updated_at = :now "
                "where tenant_id = :tenant_id "
                "and coalesce(workspace_id, '') = coalesce(:workspace_id, '')"
            ),
            values,
        )
        if updated.rowcount == 0:
            self.session.execute(
                text(
                    "insert into model_budgets (tenant_id, workspace_id, monthly_cost_limit_usd, "
                    "monthly_token_limit, updated_by, created_at, updated_at) "
                    "values (:tenant_id, :workspace_id, :cost, :tokens, :updated_by, :now, :now)"
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
                "unpriced": 1 if delta.estimated_cost_usd is None else 0,
                "now": now,
            },
        ).one()
        return _totals(row)
