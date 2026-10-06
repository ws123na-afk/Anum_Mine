"""PostgreSQL model budgets and monthly usage under RLS (threat model G4)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pytest
from conftest import APP_ROLE, FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.db.model_budget_repository import SqlAlchemyModelBudgetStore
from anum_api.main import app
from anum_api.model_budget import (
    BudgetedModelGateway,
    BudgetScope,
    ModelBudgetExceededError,
    ModelBudgetLimits,
    UsageDelta,
)
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.settings import settings

pytestmark = pytest.mark.database

OCTOBER = date(2026, 10, 1)
NOVEMBER = date(2026, 11, 1)


def _limits(**values) -> ModelBudgetLimits:
    return ModelBudgetLimits(**values)


def test_budgets_round_trip_per_tenant_and_workspace(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    context = tenant_context()
    with app_session(context, commit=True) as session:
        store = SqlAlchemyModelBudgetStore(session)
        store.set_budget(context, BudgetScope.TENANT, _limits(monthly_cost_limit_usd=100.5), FIXED_NOW)
        store.set_budget(context, BudgetScope.WORKSPACE, _limits(monthly_token_limit=5000), FIXED_NOW)
        # Updating replaces the limits in place.
        store.set_budget(context, BudgetScope.WORKSPACE, _limits(monthly_token_limit=6000), FIXED_NOW)

    sibling = tenant_context(TENANT_A, WORKSPACE_A2)
    with app_session(sibling) as session:
        store = SqlAlchemyModelBudgetStore(session)
        tenant = store.get_budget(sibling, BudgetScope.TENANT)
        assert tenant is not None and tenant.monthly_cost_limit_usd == 100.5 and tenant.monthly_token_limit is None
        assert store.get_budget(sibling, BudgetScope.WORKSPACE) is None

    with app_session(context) as session:
        workspace = SqlAlchemyModelBudgetStore(session).get_budget(context, BudgetScope.WORKSPACE)
        assert workspace is not None and workspace.monthly_token_limit == 6000
        assert workspace.updated_by == "user_test"
        assert session.execute(text("select count(*) from model_budgets")).scalar_one() == 2

    other_tenant = tenant_context(TENANT_B, WORKSPACE_B)
    with app_session(other_tenant) as session:
        assert SqlAlchemyModelBudgetStore(session).get_budget(other_tenant, BudgetScope.TENANT) is None
        assert session.execute(text("select count(*) from model_budgets")).scalar_one() == 0


def test_rls_limits_writes_to_the_current_workspace(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    context = tenant_context()
    with app_session(context, commit=True) as session:
        store = SqlAlchemyModelBudgetStore(session)
        store.set_budget(context, BudgetScope.WORKSPACE, _limits(monthly_token_limit=10), FIXED_NOW)
        store.record_usage(context, OCTOBER, UsageDelta(5, 5, 0.1), FIXED_NOW)

    sibling = tenant_context(TENANT_A, WORKSPACE_A2)
    with app_session(sibling) as session:
        # Another workspace of the same tenant can read but not change A's rows.
        changed = session.execute(
            text("update model_budgets set monthly_token_limit = 999999 where workspace_id = :ws"),
            {"ws": WORKSPACE_A},
        ).rowcount
        assert changed == 0
        changed = session.execute(
            text("update model_usage_monthly set input_tokens = 0 where workspace_id = :ws"), {"ws": WORKSPACE_A}
        ).rowcount
        assert changed == 0
        assert session.execute(text("delete from model_usage_monthly")).rowcount == 0

    for statement, params in (
        (
            "insert into model_usage_monthly (tenant_id, workspace_id, month) values (:t, :w, :m)",
            {"t": TENANT_A, "w": WORKSPACE_A, "m": OCTOBER},
        ),
        (
            "insert into model_budgets (tenant_id, workspace_id, monthly_token_limit, updated_by) "
            "values (:t, :w, 1, 'x')",
            {"t": TENANT_A, "w": WORKSPACE_A},
        ),
        (
            "insert into model_usage_monthly (tenant_id, workspace_id, month) values (:t, :w, :m)",
            {"t": TENANT_B, "w": WORKSPACE_B, "m": OCTOBER},
        ),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            with app_session(sibling) as session:
                session.execute(text(statement), params)

    with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
        assert session.execute(text("select count(*) from model_usage_monthly")).scalar_one() == 0


def test_usage_accumulates_per_month_and_sums_across_the_tenant(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    first = tenant_context()
    second = tenant_context(TENANT_A, WORKSPACE_A2)
    with app_session(first, commit=True) as session:
        store = SqlAlchemyModelBudgetStore(session)
        store.record_usage(first, OCTOBER, UsageDelta(100, 50, 0.25), FIXED_NOW)
        total = store.record_usage(first, OCTOBER, UsageDelta(10, 5, None), FIXED_NOW)
        store.record_usage(first, NOVEMBER, UsageDelta(1, 1, 0.01), FIXED_NOW)
    assert (total.calls, total.input_tokens, total.output_tokens, total.unpriced_calls) == (2, 110, 55, 1)
    assert total.estimated_cost_usd == pytest.approx(0.25)

    with app_session(second, commit=True) as session:
        SqlAlchemyModelBudgetStore(session).record_usage(second, OCTOBER, UsageDelta(1000, 0, 1.0), FIXED_NOW)

    with app_session(first) as session:
        store = SqlAlchemyModelBudgetStore(session)
        workspace = store.usage(first, BudgetScope.WORKSPACE, OCTOBER)
        tenant = store.usage(first, BudgetScope.TENANT, OCTOBER)
        november = store.usage(first, BudgetScope.TENANT, NOVEMBER)
        december = store.usage(first, BudgetScope.TENANT, date(2026, 12, 1))
    assert workspace.total_tokens == 165
    assert tenant.total_tokens == 1165 and tenant.calls == 3
    assert tenant.estimated_cost_usd == pytest.approx(1.25)
    assert (november.calls, december.calls) == (1, 0)

    with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
        assert SqlAlchemyModelBudgetStore(session).usage(
            tenant_context(TENANT_B, WORKSPACE_B), BudgetScope.TENANT, OCTOBER
        ).calls == 0


def test_usage_month_must_be_the_first_day(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    with pytest.raises(DBAPIError, match="ck_model_usage_monthly_month_start"):
        with app_session(tenant_context()) as session:
            SqlAlchemyModelBudgetStore(session).record_usage(
                tenant_context(), date(2026, 10, 15), UsageDelta(1, 1, 0), FIXED_NOW
            )


def test_concurrent_usage_increments_are_never_lost(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    context = tenant_context()

    def one_call(_: int) -> None:
        with app_session(context, commit=True) as session:
            SqlAlchemyModelBudgetStore(session).record_usage(context, OCTOBER, UsageDelta(3, 2, 0.5), FIXED_NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one_call, range(80)))

    with app_session(context) as session:
        usage = SqlAlchemyModelBudgetStore(session).usage(context, BudgetScope.WORKSPACE, OCTOBER)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (80, 240, 160)
    assert usage.estimated_cost_usd == pytest.approx(40.0)


@pytest.fixture
def postgres_backend(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Route the API's stores to the test database as the non-owner app role."""
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield


HEADERS = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}


class FixedGateway:
    provider = "openai-compatible"
    model = "gpt-4.1-mini"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        usage = ModelUsage(input_tokens=60, output_tokens=40, provider=self.provider, model=self.model, estimated_cost_usd=0.5)
        return ModelResponse(text="ok", usage=usage)


def test_api_sets_budgets_audits_changes_and_the_gateway_enforces_them(
    postgres_backend: None, database_engine: Engine
) -> None:
    import asyncio

    client = TestClient(app)
    saved = client.put("/api/v1/model-budgets/tenant", headers=HEADERS, json={"monthly_token_limit": 150})
    assert saved.status_code == 200, saved.text
    assert saved.json()["tenant"]["budget"]["monthly_token_limit"] == 150

    now = datetime.now(timezone.utc)
    inner = FixedGateway()
    gateway = BudgetedModelGateway(inner, tenant_context(), clock=lambda: now)
    asyncio.run(gateway.generate_text("hello"))  # 100 tokens
    sibling = BudgetedModelGateway(inner, tenant_context(TENANT_A, WORKSPACE_A2), clock=lambda: now)
    asyncio.run(sibling.generate_text("hello"))  # tenant: 200 >= 150
    with pytest.raises(ModelBudgetExceededError):
        asyncio.run(gateway.generate_text("hello"))
    assert inner.calls == 2

    overview = client.get("/api/v1/model-budgets", headers=HEADERS).json()
    assert overview["tenant"]["usage"]["calls"] == 2
    assert overview["tenant"]["exceeded"] is True
    assert overview["workspace"]["usage"]["calls"] == 1

    with database_engine.connect() as connection:
        audit = connection.execute(
            text("select actor, target, metadata from audit_records where action = 'model_budget.update'")
        ).one()
    assert audit.actor == "user_test"
    assert audit.target == f"model_budget:tenant:{TENANT_A}"
    assert audit.metadata["limits"] == "cost_usd=none tokens=150"

    refused = client.put(
        "/api/v1/model-budgets/workspace",
        headers={**HEADERS, "x-workspace-id": "workspace_not_onboarded"},
        json={"monthly_token_limit": 1},
    )
    assert refused.status_code == 409
