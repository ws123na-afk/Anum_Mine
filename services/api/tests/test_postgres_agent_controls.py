"""Owner agent controls in PostgreSQL (migration ``0080_agent_controls``), as the non-owner app role.

The emergency stop is one row per tenant under forced RLS: tenant B can neither read nor
change tenant A's switch. Agent caps and usage are scoped by tenant and workspace.
``anum_maintenance`` reads only ``tenant_id`` and ``engaged`` (discovery of due sends), never
the reason or who changed it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone
from typing import Any

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.agent_controls import AgentsStoppedError, check_agent_cap, open_agent_controls_store, record_agent_usage
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.model_budget import check_model_budget
from anum_api.model_gateway import ModelUsage
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
client = TestClient(app)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user: str = "owner_test", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


def ctx(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id="owner_test", roles=["owner"])


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield


def _scoped(connection: Any, tenant_id: str, workspace_id: str) -> None:
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
    connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})


def test_the_stop_is_per_tenant_and_tenant_b_cannot_read_or_flip_it(db: None, database_engine: Engine) -> None:
    on = client.put(
        "/api/v1/agent-controls/emergency-stop", headers=headers(), json={"engaged": True, "reason": "Runaway agent"}
    )
    assert on.status_code == 200, on.text

    assert client.get("/api/v1/agent-controls", headers=headers(TENANT_B, WORKSPACE_B)).json()["emergency_stop"]["engaged"] is False
    with pytest.raises(AgentsStoppedError):
        check_model_budget(ctx())
    check_model_budget(ctx(TENANT_B, WORKSPACE_B))
    # Every workspace of tenant A is stopped, not only the one that turned it on.
    with pytest.raises(AgentsStoppedError):
        check_model_budget(ctx(TENANT_A, WORKSPACE_A2))

    with database_engine.connect() as connection:
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            assert connection.execute(text("select count(*) from agent_emergency_stops")).scalar_one() == 0
            changed = connection.execute(
                text("update agent_emergency_stops set engaged = false where tenant_id = :t"), {"t": TENANT_A}
            )
            assert changed.rowcount == 0
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("insert into agent_emergency_stops (tenant_id, engaged, changed_by) values (:t, false, 'x')"),
                    {"t": TENANT_A},
                )
    with pytest.raises(AgentsStoppedError):
        check_model_budget(ctx())


def test_maintenance_reads_only_the_tenant_and_whether_it_is_engaged(db: None, database_engine: Engine) -> None:
    assert client.put(
        "/api/v1/agent-controls/emergency-stop", headers=headers(), json={"engaged": True, "reason": "Runaway agent"}
    ).status_code == 200
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select tenant_id, engaged from agent_emergency_stops")).all()
            assert [(row.tenant_id, row.engaged) for row in rows] == [(TENANT_A, True)]
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            with pytest.raises(DBAPIError):
                connection.execute(text("select reason from agent_emergency_stops"))


def test_caps_and_usage_stay_in_their_workspace(db: None, database_engine: Engine) -> None:
    with open_agent_controls_store(ctx()) as store:
        store.set_cap(ctx(), "agent_one", 1.0, None, NOW)
    usage = ModelUsage(input_tokens=10, output_tokens=5, provider="p", model="m", estimated_cost_usd=0.75)
    record_agent_usage(ctx(), "agent_one", usage, now=NOW)
    record_agent_usage(ctx(), "agent_one", usage, now=NOW)

    with open_agent_controls_store(ctx()) as store:
        totals = store.agent_usage(ctx(), "agent_one", date(2026, 10, 1))
    assert totals.calls == 2 and totals.estimated_cost_usd == pytest.approx(1.5)
    with pytest.raises(Exception, match="monthly cost cap"):
        check_agent_cap(ctx(), "agent_one", now=NOW)
    # The other workspace and the other tenant see no cap and no usage.
    for other in (ctx(TENANT_A, WORKSPACE_A2), ctx(TENANT_B, WORKSPACE_B)):
        check_agent_cap(other, "agent_one", now=NOW)
        with open_agent_controls_store(other) as store:
            assert store.list_caps(other) == []
            assert store.usage_by_agent(other, date(2026, 10, 1)) == {}
    with database_engine.connect() as connection:
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            for table in ("agent_cost_caps", "agent_usage_monthly"):
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table
