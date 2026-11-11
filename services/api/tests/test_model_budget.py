"""Per-tenant and per-workspace monthly model budgets (threat model G4), memory backend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from anum_api import model_budget, onboarding
from anum_api.main import app
from anum_api.model_budget import (
    BudgetedModelGateway,
    BudgetKind,
    BudgetScope,
    ModelBudgetExceededError,
    ModelBudgetLimits,
    UsageGroup,
    check_model_budget,
    feature_for_module,
    feature_scope,
    memory_model_budget_store,
    month_start,
    next_month_start,
    record_model_usage,
)
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.onboarding import _model_configs
from anum_api.schemas import TenantContext
from anum_api.store import store
from anum_api.telemetry import telemetry
from anum_api.voice import voice_store

client = TestClient(app)
TENANT = "tenant_budget"
WORKSPACE = "workspace_budget"
OCTOBER = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
NOVEMBER = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


def _context(workspace: str = WORKSPACE, role: str = "owner") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=workspace, user_id="user_budget", roles=[role])


def _headers(workspace: str = WORKSPACE, role: str = "owner") -> dict[str, str]:
    return {
        "x-tenant-id": TENANT,
        "x-workspace-id": workspace,
        "x-user-id": "user_budget",
        "x-user-roles": role,
    }


def _org_admin(workspace: str = WORKSPACE) -> dict[str, str]:
    """A workspace owner who is also the organization administrator (the tenant budget needs it)."""
    return _headers(workspace, role="owner,org_admin")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    memory_model_budget_store.clear()
    _model_configs.clear()
    onboarding._workspace_gateways.clear()
    voice_store.clear()
    yield
    memory_model_budget_store.clear()
    _model_configs.clear()
    onboarding._workspace_gateways.clear()


@pytest.fixture
def metrics() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    telemetry.bind(meter_provider=provider)
    try:
        yield reader
    finally:
        telemetry.bind()
        provider.shutdown()


def _points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict, float]]:
    data = reader.get_metrics_data()
    points = []
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    points.extend((dict(point.attributes), point.value) for point in metric.data.data_points)
    return points


class FixedGateway:
    provider = "openai-compatible"
    model = "gpt-4.1-mini"

    def __init__(self, input_tokens: int = 100, output_tokens: int = 50, cost: float | None = 0.004) -> None:
        self.usage = ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider,
            model=self.model,
            estimated_cost_usd=cost,
        )
        self.calls = 0

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text="ok", usage=self.usage)


def _set(scope: BudgetScope, context: TenantContext | None = None, **limits) -> None:
    memory_model_budget_store.set_budget(context or _context(), scope, ModelBudgetLimits(**limits), OCTOBER)


def _call(gateway: BudgetedModelGateway) -> None:
    asyncio.run(gateway.generate_text("hello"))


# --------------------------------------------------------------------------- periods


def test_usage_is_keyed_by_utc_calendar_month() -> None:
    assert month_start(datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)) == date(2026, 10, 1)
    # 01:00 on 1 November in UTC+2 is still October in UTC.
    from datetime import timedelta

    plus_two = timezone(timedelta(hours=2))
    assert month_start(datetime(2026, 11, 1, 1, 0, tzinfo=plus_two)) == date(2026, 10, 1)
    assert next_month_start(date(2026, 12, 1)) == date(2027, 1, 1)
    assert next_month_start(date(2026, 10, 1)) == date(2026, 11, 1)


# --------------------------------------------------------------------------- enforcement


def test_without_a_budget_calls_are_metered_but_never_refused() -> None:
    gateway = BudgetedModelGateway(FixedGateway(), _context(), clock=lambda: OCTOBER)
    for _ in range(5):
        _call(gateway)

    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (5, 500, 250)
    assert usage.estimated_cost_usd == pytest.approx(0.02)


def test_workspace_cost_budget_refuses_calls_once_used_up(metrics: InMemoryMetricReader, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="anum.model_budget")
    _set(BudgetScope.WORKSPACE, monthly_cost_limit_usd=0.01)
    inner = FixedGateway(cost=0.004)
    gateway = BudgetedModelGateway(inner, _context(), clock=lambda: OCTOBER)

    for _ in range(3):  # 0.004, 0.008 (80%), 0.012 (100%)
        _call(gateway)
    with pytest.raises(ModelBudgetExceededError) as raised:
        _call(gateway)

    assert inner.calls == 3  # the refused call never reached the provider
    assert raised.value.scope == BudgetScope.WORKSPACE
    assert raised.value.kind == BudgetKind.COST
    assert raised.value.resets_on == date(2026, 11, 1)
    assert "2026-11-01" in raised.value.message
    thresholds = _points(metrics, "anum.model.budget.thresholds")
    assert sorted((attrs["anum.budget.percent"], value) for attrs, value in thresholds) == [(80, 1), (100, 1)]
    assert all(attrs["anum.budget.scope"] == "workspace" and attrs["anum.budget.kind"] == "cost" for attrs, _ in thresholds)
    # Bounded metric labels: no tenant or workspace ids.
    assert TENANT not in str(thresholds) and WORKSPACE not in str(thresholds)
    assert _points(metrics, "anum.model.budget.rejections") == [
        ({"anum.budget.scope": "workspace", "anum.budget.kind": "cost"}, 1)
    ]
    messages = [record.getMessage() for record in caplog.records]
    assert any("model_budget_threshold" in message and "percent=80" in message for message in messages)
    assert any("model_budget_threshold" in message and "percent=100" in message for message in messages)
    assert any("model_budget_exceeded" in message for message in messages)
    assert not any("hello" in message for message in messages)  # never the prompt


def test_tenant_token_budget_spans_all_workspaces() -> None:
    _set(BudgetScope.TENANT, monthly_token_limit=400)
    first = BudgetedModelGateway(FixedGateway(100, 50), _context("workspace_one"), clock=lambda: OCTOBER)
    second = BudgetedModelGateway(FixedGateway(100, 50), _context("workspace_two"), clock=lambda: OCTOBER)

    _call(first)  # 150
    _call(second)  # 300
    _call(first)  # 450 >= 400
    with pytest.raises(ModelBudgetExceededError) as raised:
        _call(second)
    assert (raised.value.scope, raised.value.kind) == (BudgetScope.TENANT, BudgetKind.TOKENS)

    # Another tenant is unaffected.
    other = TenantContext(tenant_id="tenant_other", workspace_id="workspace_one", user_id="u", roles=["owner"])
    _call(BudgetedModelGateway(FixedGateway(), other, clock=lambda: OCTOBER))


def test_month_rollover_resets_usage_and_enforcement() -> None:
    _set(BudgetScope.WORKSPACE, monthly_token_limit=150)
    clock = {"now": OCTOBER}
    gateway = BudgetedModelGateway(FixedGateway(100, 50), _context(), clock=lambda: clock["now"])
    _call(gateway)
    with pytest.raises(ModelBudgetExceededError):
        _call(gateway)

    clock["now"] = NOVEMBER
    _call(gateway)

    assert memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1)).calls == 1
    assert memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 11, 1)).calls == 1
    with pytest.raises(ModelBudgetExceededError) as raised:
        _call(gateway)
    assert raised.value.resets_on == date(2026, 12, 1)


def test_unpriced_models_without_a_cost_limit_count_tokens_but_no_cost() -> None:
    _set(BudgetScope.WORKSPACE, monthly_token_limit=10_000)
    gateway = BudgetedModelGateway(FixedGateway(cost=None), _context(), clock=lambda: OCTOBER)
    for _ in range(3):
        _call(gateway)

    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert (usage.calls, usage.unpriced_calls, usage.estimated_cost_usd, usage.total_tokens) == (3, 3, 0, 450)


def test_an_unpriced_model_under_a_cost_limit_is_counted_at_the_worst_case_price(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A USD limit used to be ignored for a model with no price: its cost was None, counted as 0."""
    monkeypatch.setattr(model_budget, "_WARNED_UNPRICED", set())
    caplog.set_level(logging.WARNING, logger="anum.model_budget")
    _set(BudgetScope.WORKSPACE, monthly_cost_limit_usd=0.005)
    inner = FixedGateway(cost=None)  # 100 in, 50 out: 100 * $10/M + 50 * $50/M = $0.0035 a call
    gateway = BudgetedModelGateway(inner, _context(), clock=lambda: OCTOBER)
    _call(gateway)
    _call(gateway)
    with pytest.raises(ModelBudgetExceededError) as raised:
        _call(gateway)
    assert raised.value.kind == BudgetKind.COST and inner.calls == 2

    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert usage.calls == 2 and usage.unpriced_calls == 2
    assert usage.estimated_cost_usd == pytest.approx(0.007)
    warnings = [r for r in caplog.records if r.getMessage().startswith("model_budget_unpriced_model")]
    assert len(warnings) == 1, "once per model"
    assert "gpt-4.1-mini" in warnings[0].getMessage() and "ANUM_MODEL_UNPRICED_PRICE" in warnings[0].getMessage()


def test_a_tenant_cost_limit_also_counts_unpriced_calls() -> None:
    _set(BudgetScope.TENANT, monthly_cost_limit_usd=0.003)
    gateway = BudgetedModelGateway(FixedGateway(cost=None), _context(), clock=lambda: OCTOBER)
    _call(gateway)
    with pytest.raises(ModelBudgetExceededError):
        _call(gateway)


def test_the_worst_case_price_is_configurable_and_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.settings import ModelPrice, Settings

    assert Settings(environment="test", model_unpriced_price=None).model_unpriced_price is None
    monkeypatch.setattr(model_budget.settings, "model_unpriced_price", ModelPrice(input_per_million=1.0, output_per_million=2.0))
    _set(BudgetScope.WORKSPACE, monthly_cost_limit_usd=1.0)
    gateway = BudgetedModelGateway(FixedGateway(cost=None), _context(), clock=lambda: OCTOBER)
    _call(gateway)
    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert usage.estimated_cost_usd == pytest.approx(0.0002)

    monkeypatch.setattr(model_budget.settings, "model_unpriced_price", None)
    _call(gateway)
    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert usage.estimated_cost_usd == pytest.approx(0.0002) and usage.unpriced_calls == 2


def test_a_delta_built_without_the_flag_is_unpriced_when_it_has_no_cost() -> None:
    from anum_api.model_budget import UsageDelta

    assert UsageDelta(10, 5, None).unpriced is True and UsageDelta(10, 5, 0.1).unpriced is False
    assumed = UsageDelta(10, 5, None).with_assumed_cost("some-model")
    assert assumed.unpriced is True and assumed.estimated_cost_usd is not None


def test_a_priced_call_is_never_repriced() -> None:
    _set(BudgetScope.WORKSPACE, monthly_cost_limit_usd=1.0)
    _call(BudgetedModelGateway(FixedGateway(cost=0.004), _context(), clock=lambda: OCTOBER))
    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert usage.estimated_cost_usd == pytest.approx(0.004) and usage.unpriced_calls == 0


def test_claude_sonnet_reads_its_cache_at_ten_cents() -> None:
    from anum_api.model_gateway import estimate_cost_usd

    assert estimate_cost_usd("anthropic", "claude-sonnet-5-5", 1_000_000, 0, cache_read_input_tokens=1_000_000) == pytest.approx(0.10)


def test_zero_limit_blocks_every_call_and_null_limits_clear_the_budget() -> None:
    _set(BudgetScope.WORKSPACE, monthly_token_limit=0)
    with pytest.raises(ModelBudgetExceededError):
        check_model_budget(_context(), now=OCTOBER)
    _set(BudgetScope.WORKSPACE)
    check_model_budget(_context(), now=OCTOBER)


def test_concurrent_usage_is_never_lost() -> None:
    def one_call(_: int) -> None:
        record_model_usage(
            _context(),
            ModelUsage(input_tokens=3, output_tokens=2, provider="p", model="m", estimated_cost_usd=0.5),
            now=OCTOBER,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(one_call, range(200)))

    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (200, 600, 400)
    assert usage.estimated_cost_usd == pytest.approx(100.0)


def test_concurrent_calls_cross_each_threshold_once(metrics: InMemoryMetricReader) -> None:
    _set(BudgetScope.WORKSPACE, monthly_token_limit=1000)

    def one_call(_: int) -> None:
        record_model_usage(
            _context(), ModelUsage(input_tokens=10, output_tokens=0, provider="p", model="m"), now=OCTOBER
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(one_call, range(150)))

    thresholds = _points(metrics, "anum.model.budget.thresholds")
    assert sorted((attrs["anum.budget.percent"], value) for attrs, value in thresholds) == [(80, 1), (100, 1)]


def test_a_failed_usage_write_never_fails_the_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(memory_model_budget_store, "record_usage", broken)
    response = asyncio.run(BudgetedModelGateway(FixedGateway(), _context()).generate_text("hello"))
    assert response.text == "ok"


def test_the_wrapper_exposes_the_inner_gateway_attributes() -> None:
    gateway = BudgetedModelGateway(FixedGateway(), _context())
    assert gateway.provider == "openai-compatible"
    assert gateway.model == "gpt-4.1-mini"


# --------------------------------------------------------------------------- API


def test_owners_set_and_read_budgets_and_every_change_is_audited() -> None:
    before = len(store.audit_records)
    workspace = client.put(
        "/api/v1/model-budgets/workspace",
        headers=_headers(),
        json={"monthly_cost_limit_usd": 25.5, "monthly_token_limit": 2_000_000},
    )
    tenant = client.put(
        "/api/v1/model-budgets/tenant", headers=_org_admin(), json={"monthly_cost_limit_usd": 100}
    )
    record_model_usage(
        _context(), ModelUsage(input_tokens=10, output_tokens=5, provider="p", model="m", estimated_cost_usd=0.25)
    )
    overview = client.get("/api/v1/model-budgets", headers=_headers())

    assert workspace.status_code == 200, workspace.text
    assert tenant.status_code == 200, tenant.text
    body = overview.json()
    assert body["workspace"]["budget"]["monthly_cost_limit_usd"] == 25.5
    assert body["workspace"]["budget"]["monthly_token_limit"] == 2_000_000
    assert body["tenant"]["budget"]["monthly_cost_limit_usd"] == 100
    assert body["tenant"]["budget"]["monthly_token_limit"] is None
    assert body["workspace"]["usage"]["estimated_cost_usd"] == 0.25
    assert body["workspace"]["total_tokens"] == 15
    assert body["workspace"]["exceeded"] is False
    assert body["resets_on"] > body["period_start"]
    # A workspace owner without org_admin reads the overview but is told it cannot change
    # the organization budget.
    assert body["can_manage_tenant_budget"] is False
    assert client.get("/api/v1/model-budgets", headers=_org_admin()).json()["can_manage_tenant_budget"] is True

    audits = [record for record in store.audit_records[before:] if record.action == "model_budget.update"]
    assert [record.target for record in audits] == [
        f"model_budget:workspace:{WORKSPACE}",
        f"model_budget:tenant:{TENANT}",
    ]
    assert audits[0].actor == "user_budget"
    assert audits[0].metadata["previous_limits"] == "none"
    assert audits[0].metadata["limits"] == "cost_usd=25.5 tokens=2000000 alert_percent=none"


def test_budget_api_is_owner_only_and_validated() -> None:
    member = _headers(role="member")
    assert client.get("/api/v1/model-budgets", headers=member).status_code == 403
    assert client.put("/api/v1/model-budgets/workspace", headers=member, json={"monthly_token_limit": 1}).status_code == 403
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_token_limit": -1}).status_code == 422
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_cost_limit_usd": -0.5}).status_code == 422
    assert client.put("/api/v1/model-budgets/galaxy", headers=_headers(), json={}).status_code == 422


def test_only_an_org_admin_changes_the_organization_budget() -> None:
    before = len(store.audit_records)

    refused = client.put("/api/v1/model-budgets/tenant", headers=_headers(), json={"monthly_cost_limit_usd": 1})
    member_admin = client.put(
        "/api/v1/model-budgets/tenant", headers=_headers(role="member,org_admin"), json={"monthly_token_limit": 5}
    )

    assert refused.status_code == 403
    assert refused.json()["error"]["message"].startswith("Organization administrator role required")
    # The organization role decides, not the workspace role: a member who is org admin may.
    assert member_admin.status_code == 200, member_admin.text
    assert member_admin.json()["tenant"]["budget"]["monthly_token_limit"] == 5
    assert member_admin.json()["tenant"]["budget"]["monthly_cost_limit_usd"] is None
    # The refused write left no budget change and no audit row behind.
    targets = [r.target for r in store.audit_records[before:] if r.action == "model_budget.update"]
    assert targets == [f"model_budget:tenant:{TENANT}"]
    # The workspace budget stays with the workspace owner; org_admin alone does not grant it.
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(role="member,org_admin"), json={"monthly_token_limit": 1}).status_code == 403
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_token_limit": 1}).status_code == 200


def test_task_runs_are_refused_with_402_when_the_budget_is_used_up() -> None:
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_token_limit": 0}).status_code == 200
    task = client.post("/api/v1/tasks", headers=_headers(), json={"title": "Summarize", "prompt": "Summarize the week"}).json()

    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=_headers())

    assert run.status_code == 402
    assert run.json()["error"]["code"] == "model_budget_exceeded"
    assert "monthly model budget" in run.json()["error"]["message"]
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=_headers()).json()["status"] == "created"


def test_task_runs_record_usage_against_the_budget() -> None:
    task = client.post("/api/v1/tasks", headers=_headers(), json={"title": "Plan", "prompt": "Plan the launch"}).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=_headers()).status_code == 200

    usage = client.get("/api/v1/model-budgets", headers=_headers()).json()["workspace"]["usage"]
    assert usage["calls"] == 1
    assert usage["input_tokens"] > 0
    # The same call is attributed to the feature (a task run), the caller and the default assistant.
    by_feature = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "feature"}).json()
    assert [(row["key"], row["calls"]) for row in by_feature["rows"]] == [("tasks", 1)]
    by_agent = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "agent"}).json()
    assert [(row["key"], row["label"]) for row in by_agent["rows"]] == [("assistant", "ANUM assistant")]
    by_user = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "user"}).json()
    assert [row["key"] for row in by_user["rows"]] == ["user_budget"]


def test_voice_says_the_budget_is_used_up_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"model": "llama3.2", "choices": [{"message": {"content": "Sure."}, "finish_reason": "stop"}]})

    monkeypatch.setattr(onboarding, "_test_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    client.put(
        "/api/v1/model-config",
        headers=_headers(),
        json={"provider": "ollama", "model": "llama3.2", "base_url": "http://localhost:11434/v1"},
    )
    client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_token_limit": 0})

    for locale, expected in (("en-US", "model budget for the month"), ("ar-SA", "حد استخدام النموذج")):
        session_id = client.post("/api/v1/voice/sessions", headers=_headers(), json={"locale": locale}).json()["id"]
        segment_id = client.post(
            f"/api/v1/voice/sessions/{session_id}/transcript",
            headers=_headers(),
            json={"text": "What should I focus on today?", "is_final": True, "client_sequence": 0},
        ).json()["id"]
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/ask", headers=_headers(), json={"transcript_segment_id": segment_id}
        )
        assert response.status_code == 200, response.text
        assert expected in response.json()["reply"]

    assert seen == []  # the model was never called


def test_durable_worker_fails_the_run_when_the_budget_is_used_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import main
    from anum_api.durable_runs import AgentRunActivities, RunDispatcher
    from anum_api.runtime import AgentRuntime
    from anum_api.temporal_workflow import AgentRunInput

    class Dispatcher(RunDispatcher):
        def __init__(self) -> None:
            super().__init__(target="unused:7233", namespace="default", task_queue="test")
            self.started: list[AgentRunInput] = []

        async def start(self, request: AgentRunInput) -> str:
            self.started.append(request)
            return request.task_id

    dispatcher = Dispatcher()
    monkeypatch.setattr(main, "run_dispatcher", dispatcher)
    task = client.post("/api/v1/tasks", headers=_headers(), json={"title": "Durable", "prompt": "Plan the launch"}).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=_headers()).status_code == 200
    # The budget runs out between queueing and the worker picking the run up.
    _set(BudgetScope.WORKSPACE, monthly_token_limit=0)
    inner = FixedGateway()
    activities = AgentRunActivities(
        lambda context, repository: AgentRuntime(BudgetedModelGateway(inner, context), repository)
    )

    state = asyncio.run(activities.advance(_context(), dispatcher.started[-1]))

    assert (state.phase, state.status) == ("failed", "failed")
    assert inner.calls == 0
    run = store.runs[dispatcher.started[-1].run_id]
    assert "monthly model budget" in run.steps[-1].summary
    assert store.tasks[task["id"]].status == "failed"


# ---------------------------------------------------------------------------- spend breakdown


def _usage(cost: float, tokens: int = 10, model: str = "m") -> ModelUsage:
    return ModelUsage(input_tokens=tokens, output_tokens=tokens, provider="p", model=model, estimated_cost_usd=cost)


def test_usage_breakdown_groups_the_recorded_spend_by_day_person_agent_feature_and_month() -> None:
    day1 = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 10, 4, 23, 30, tzinfo=timezone.utc)
    september = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    other_workspace = _context(workspace="workspace_budget_2")
    record_model_usage(_context(), _usage(1.0), now=day1, agent_id="assistant", feature="tasks")
    record_model_usage(_context(), _usage(2.0), now=day2, agent_id="agent_x", feature="voice")
    record_model_usage(
        TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_two", roles=["member"]),
        _usage(0.5), now=day2, agent_id="assistant", feature="tasks",
    )
    record_model_usage(other_workspace, _usage(4.0), now=day2, agent_id="assistant", feature="email")
    record_model_usage(_context(), _usage(7.0), now=september, agent_id="assistant", feature="tasks")
    params = {"from": "2026-10-01", "to": "2026-10-31"}

    by_day = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "day"}).json()
    assert [(row["key"], row["calls"], row["estimated_cost_usd"]) for row in by_day["rows"]] == [
        ("2026-10-03", 1, 1.0), ("2026-10-04", 2, 2.5),
    ]
    assert by_day["total"] == {"input_tokens": 30, "output_tokens": 20 + 10, "estimated_cost_usd": 3.5, "calls": 3, "unpriced_calls": 0}
    assert by_day["count"] == 2 and by_day["truncated"] is False and by_day["scope"] == "workspace"
    assert by_day["from_day"] == "2026-10-01" and by_day["to_day"] == "2026-10-31" and by_day["recorded_since"] is None

    # People, agents and features are ranked by cost (then tokens, then key).
    by_user = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "user"}).json()
    assert [row["key"] for row in by_user["rows"]] == ["user_budget", "user_two"]
    by_agent = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "agent"}).json()
    assert [(row["key"], row["label"], row["estimated_cost_usd"]) for row in by_agent["rows"]] == [
        ("agent_x", None, 2.0), ("assistant", "ANUM assistant", 1.5),
    ]
    by_feature = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "feature"}).json()
    assert [(row["key"], row["total_tokens"]) for row in by_feature["rows"]] == [("voice", 20), ("tasks", 40)]

    # The organization scope sums every workspace; the month view reads the exact monthly rows.
    tenant = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "feature", "scope": "tenant"}).json()
    assert [(row["key"], row["estimated_cost_usd"]) for row in tenant["rows"]] == [("email", 4.0), ("voice", 2.0), ("tasks", 1.5)]
    months = client.get(
        "/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "month", "from": "2026-09-01", "to": "2026-10-31"}
    ).json()
    assert [(row["key"], row["estimated_cost_usd"]) for row in months["rows"]] == [("2026-09-01", 7.0), ("2026-10-01", 3.5)]

    # Paging: the second page of one, with the truncation flag on the first.
    first = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "day", "limit": 1}).json()
    second = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={**params, "group_by": "day", "limit": 1, "offset": 1}).json()
    assert first["truncated"] is True and [row["key"] for row in first["rows"]] == ["2026-10-03"]
    assert second["truncated"] is False and [row["key"] for row in second["rows"]] == ["2026-10-04"] and second["count"] == 2

    # A range that starts before the first recorded day says so.
    early = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "day", "from": "2026-08-01", "to": "2026-10-31"}).json()
    assert early["recorded_since"] == "2026-09-20"


def test_usage_breakdown_exports_csv_and_checks_its_input() -> None:
    record_model_usage(_context(), _usage(1.25), now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc), agent_id="assistant", feature="=tasks")
    response = client.get(
        "/api/v1/model-budgets/usage", headers=_headers(),
        params={"group_by": "feature", "from": "2026-10-01", "to": "2026-10-31", "format": "csv", "limit": 1, "offset": 5},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="model-usage-workspace-feature-2026-10-01-2026-10-31.csv"' in response.headers["content-disposition"]
    lines = response.text.splitlines()
    assert lines[0] == "feature,label,calls,input_tokens,output_tokens,total_tokens,estimated_cost_usd,unpriced_calls"
    # The whole range, not the page; a key a spreadsheet would run as a formula is quoted.
    assert lines[1] == "'=tasks,,1,10,10,20,1.250000,0"

    assert client.get("/api/v1/model-budgets/usage", headers=_headers(role="member")).status_code == 403
    assert client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "planet"}).status_code == 422
    assert client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"from": "2026-10-05", "to": "2026-10-01"}).status_code == 422
    assert client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"from": "2024-01-01", "to": "2026-10-01"}).status_code == 422
    assert client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"format": "xml"}).status_code == 422
    empty = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "user", "from": "2027-01-01", "to": "2027-01-31"}).json()
    assert empty["rows"] == [] and empty["count"] == 0 and empty["total"]["calls"] == 0


def test_calls_are_attributed_to_the_feature_of_the_module_that_asked_for_the_gateway() -> None:
    assert feature_for_module("anum_api.voice") == "voice"
    assert feature_for_module("anum_api.main") == "tasks"
    assert feature_for_module("anum_api.worker") == "tasks"
    assert feature_for_module("anum_api.finance.sms_ai") == "finance"
    assert feature_for_module("anum_api.marketing.assist") == "marketing"
    assert feature_for_module("anum_api.email_inbox") == "email"
    assert feature_for_module("anum_api.knowledge_base") == "knowledge"
    assert feature_for_module(None) == "other" and feature_for_module("") == "other"

    class _Inner:
        provider = "mock"
        model = "m"

        async def generate_text(self, prompt: str) -> ModelResponse:
            return ModelResponse(text="ok", usage=_usage(0.1))

    # The gateway's own feature wins; without one, the feature scope in force; else "other".
    asyncio.run(BudgetedModelGateway(_Inner(), _context(), agent_id="assistant", feature="meetings").generate_text("x"))
    with feature_scope("business"):
        asyncio.run(BudgetedModelGateway(_Inner(), _context(), agent_id="assistant").generate_text("x"))
    asyncio.run(BudgetedModelGateway(_Inner(), _context(), agent_id="assistant").generate_text("x"))
    # The budgeted gateway built for a module keeps that module's feature through for_agent.
    asyncio.run(onboarding.budgeted_model_gateway(_context(), _Inner(), feature="calls").for_agent("agent_y").generate_text("x"))
    gateway = onboarding.budgeted_model_gateway(_context(), _Inner())
    assert gateway.feature == feature_for_module(__name__)
    rows = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "feature"}).json()["rows"]
    assert sorted(row["key"] for row in rows) == ["business", "calls", "meetings", "other"]
    agents = client.get("/api/v1/model-budgets/usage", headers=_headers(), params={"group_by": "agent"}).json()["rows"]
    assert {row["key"]: row["calls"] for row in agents} == {"assistant": 3, "agent_y": 1}


def test_an_owner_sets_an_alert_percent_with_the_limits() -> None:
    saved = client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_cost_limit_usd": 10, "alert_percent": 20})
    assert saved.status_code == 200, saved.text
    assert saved.json()["workspace"]["budget"]["alert_percent"] == 20
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"alert_percent": 100}).status_code == 422
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"alert_percent": 0}).status_code == 422
    cleared = client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_cost_limit_usd": 10})
    assert cleared.json()["workspace"]["budget"]["alert_percent"] is None
