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
    check_model_budget,
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


def test_unpriced_models_count_tokens_but_no_cost() -> None:
    _set(BudgetScope.WORKSPACE, monthly_cost_limit_usd=0.001)
    gateway = BudgetedModelGateway(FixedGateway(cost=None), _context(), clock=lambda: OCTOBER)
    for _ in range(3):
        _call(gateway)  # never refused on cost: there is no estimate

    usage = memory_model_budget_store.usage(_context(), BudgetScope.WORKSPACE, date(2026, 10, 1))
    assert (usage.calls, usage.unpriced_calls, usage.estimated_cost_usd, usage.total_tokens) == (3, 3, 0, 450)


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
        "/api/v1/model-budgets/tenant", headers=_headers(), json={"monthly_cost_limit_usd": 100}
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

    audits = [record for record in store.audit_records[before:] if record.action == "model_budget.update"]
    assert [record.target for record in audits] == [
        f"model_budget:workspace:{WORKSPACE}",
        f"model_budget:tenant:{TENANT}",
    ]
    assert audits[0].actor == "user_budget"
    assert audits[0].metadata["previous_limits"] == "none"
    assert audits[0].metadata["limits"] == "cost_usd=25.5 tokens=2000000"


def test_budget_api_is_owner_only_and_validated() -> None:
    member = _headers(role="member")
    assert client.get("/api/v1/model-budgets", headers=member).status_code == 403
    assert client.put("/api/v1/model-budgets/workspace", headers=member, json={"monthly_token_limit": 1}).status_code == 403
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_token_limit": -1}).status_code == 422
    assert client.put("/api/v1/model-budgets/workspace", headers=_headers(), json={"monthly_cost_limit_usd": -0.5}).status_code == 422
    assert client.put("/api/v1/model-budgets/galaxy", headers=_headers(), json={}).status_code == 422


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
