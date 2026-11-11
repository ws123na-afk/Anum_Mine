"""Live agent meeting turns go through the workspace's budgeted model gateway
(docs/ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md#the-workspace-model-budget-counts-them-too).

Memory backend: the wrapping and the metering of one call. The full turn (PostgreSQL only) is in
``test_postgres_live_agent_meeting_security.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date

import pytest

from anum_api import onboarding
from anum_api.agent_controls import DEFAULT_ASSISTANT_ID
from anum_api.meeting_execution import MEETING_FEATURE, _budgeted_for, meeting_gateway
from anum_api.model_budget import (
    BudgetedModelGateway,
    BudgetScope,
    ModelBudgetExceededError,
    ModelBudgetLimits,
    UsageGroup,
    memory_model_budget_store,
    month_start,
    utc_now,
)
from anum_api.model_gateway import ModelResponse, ModelRoute, ModelUsage, route_gateway
from anum_api.onboarding import _model_configs
from anum_api.schemas import TenantContext

CONTEXT = TenantContext(tenant_id="tenant_meetbudget", workspace_id="workspace_meetbudget", user_id="owner_mb", roles=["owner"])


class FakeModel:
    provider = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text="A contribution.", usage=ModelUsage(
            input_tokens=10, output_tokens=20, provider="fake", model="fake-1", estimated_cost_usd=0.25))


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    memory_model_budget_store.clear()
    _model_configs.clear()
    onboarding._workspace_gateways.clear()
    yield
    memory_model_budget_store.clear()


def _month() -> date:
    return month_start(utc_now())


def test_the_meeting_gateway_is_the_workspace_budgeted_gateway_for_meetings() -> None:
    inner = FakeModel()
    gateway = meeting_gateway(CONTEXT, inner)
    assert isinstance(gateway, BudgetedModelGateway)
    assert gateway.feature == MEETING_FEATURE == "meetings" and gateway.agent_id == DEFAULT_ASSISTANT_ID
    # Without a model saved in Settings the server default answers, behind the budget.
    assert gateway.inner is inner and gateway.provider == "fake"


def test_a_budgeted_gateway_is_rebound_to_the_agent_and_never_wrapped_twice() -> None:
    budgeted = meeting_gateway(CONTEXT, FakeModel())
    rebound = _budgeted_for(CONTEXT, budgeted, "agent_planner")
    assert isinstance(rebound, BudgetedModelGateway) and not isinstance(rebound.inner, BudgetedModelGateway)
    assert rebound.agent_id == "agent_planner" and rebound.feature == "meetings"
    # A plain gateway (a test double, an older caller) is wrapped here, so no turn goes unmetered.
    wrapped = _budgeted_for(CONTEXT, FakeModel(), "agent_planner")
    assert isinstance(wrapped, BudgetedModelGateway) and wrapped.agent_id == "agent_planner" and wrapped.feature == "meetings"
    # Routing a persona to the fast or capable model keeps the budget.
    assert isinstance(route_gateway(rebound, ModelRoute.FAST), BudgetedModelGateway)


def test_one_turns_model_call_is_counted_once_in_the_workspace_budget_under_meetings() -> None:
    inner = FakeModel()
    model = route_gateway(_budgeted_for(CONTEXT, meeting_gateway(CONTEXT, inner), "agent_planner"), ModelRoute.CAPABLE)
    asyncio.run(model.generate_text("{}"))
    assert inner.calls == 1
    totals = memory_model_budget_store.usage(CONTEXT, BudgetScope.WORKSPACE, _month())
    assert totals.calls == 1 and totals.input_tokens == 10 and totals.output_tokens == 20 and totals.estimated_cost_usd == pytest.approx(0.25)
    by_feature = memory_model_budget_store.usage_breakdown(CONTEXT, BudgetScope.WORKSPACE, UsageGroup.FEATURE, _month(), date(9999, 1, 1))
    assert [(row.key, row.calls) for row in by_feature] == [("meetings", 1)]
    by_agent = memory_model_budget_store.usage_breakdown(CONTEXT, BudgetScope.WORKSPACE, UsageGroup.AGENT, _month(), date(9999, 1, 1))
    assert [(row.key, row.calls) for row in by_agent] == [("agent_planner", 1)]


def test_a_workspace_at_its_model_budget_refuses_the_call_before_the_provider() -> None:
    memory_model_budget_store.set_budget(CONTEXT, BudgetScope.WORKSPACE, ModelBudgetLimits(monthly_cost_limit_usd=0.2), utc_now())
    inner = FakeModel()
    model = _budgeted_for(CONTEXT, meeting_gateway(CONTEXT, inner), "agent_planner")
    asyncio.run(model.generate_text("{}"))  # under the limit: answers and is counted
    with pytest.raises(ModelBudgetExceededError):
        asyncio.run(model.generate_text("{}"))
    assert inner.calls == 1
    assert memory_model_budget_store.usage(CONTEXT, BudgetScope.WORKSPACE, _month()).calls == 1
