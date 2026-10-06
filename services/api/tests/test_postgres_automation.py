"""Automation engine and scheduler in PostgreSQL (migration 0009).

The engine runs as the non-owner ``anum_test_app`` role. Scheduler tests run several
engines at once, as separate API replicas would, and check that a fire time produces
exactly one run, that discovery as ``anum_maintenance`` sees ids only, and that the
background loop leaves no connection or transaction behind.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import automation as automation_module
from anum_api.automation import (
    AutomationScheduler,
    RunStatus,
    ScheduleCreate,
    ScheduleUpdate,
    WorkflowCreate,
    WorkflowStep,
    schedule_fire_key,
)
from anum_api.db import session as db_session
from anum_api.db.automation_repository import PostgresAutomationEngine
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": "user_test", "x-user-roles": "owner"}


A = headers()


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(automation_module, "_postgres_engine", None)
    yield factory


@pytest.fixture
def engine(app_factory: sessionmaker[Session]) -> PostgresAutomationEngine:
    return PostgresAutomationEngine(app_factory)


def workflow(action: str = "notify") -> WorkflowCreate:
    return WorkflowCreate(name="Morning operations", steps=[WorkflowStep(id="check", name="Check", action=action)])


def _make_due(database_engine: Engine, schedule_id: str) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            text("update automation_schedules set next_run_at = date_trunc('minute', now()) - interval '1 hour' where id = :id"),
            {"id": schedule_id},
        )


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return connection.execute(text(f"select count(*) from {table}")).scalar_one()  # nosec B608 - fixed table names


def test_api_persists_workflows_schedules_and_runs(app_factory: sessionmaker[Session], database_engine: Engine) -> None:
    client = TestClient(app)
    created = client.post("/api/v1/automation/workflows", headers=A, json={
        "name": "Approval wait", "steps": [{"id": "wait", "name": "Wait", "action": "pause"}]})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    schedule = client.post("/api/v1/automation/schedules", headers=A, json={
        "workflow_id": workflow_id, "name": "Daily", "cron": "0 8 * * *", "timezone": "Asia/Riyadh"})
    assert schedule.status_code == 201, schedule.text
    assert schedule.json()["next_run_at"].endswith("05:00:00Z")

    run = client.post(f"/api/v1/automation/workflows/{workflow_id}/runs", headers={**A, "Idempotency-Key": "wait-1"})
    again = client.post(f"/api/v1/automation/workflows/{workflow_id}/runs", headers={**A, "Idempotency-Key": "wait-1"})
    assert run.status_code == 201 and run.json()["status"] == "paused"
    assert again.json()["id"] == run.json()["id"]
    resumed = client.post(f"/api/v1/automation/runs/{run.json()['id']}/resume", headers=A)
    assert resumed.status_code == 200 and resumed.json()["status"] == "completed"
    assert client.post(f"/api/v1/automation/runs/{run.json()['id']}/cancel", headers=A).status_code == 409

    # Nothing lives in the process: the local SQLite engine was never used.
    assert (_count(database_engine, "automation_workflows"), _count(database_engine, "automation_schedules"),
            _count(database_engine, "automation_runs")) == (1, 1, 1)
    with database_engine.connect() as connection:
        steps = connection.execute(text("select steps from automation_runs")).scalar_one()
    assert steps[0]["output"] == {"resumed": True}

    disabled = client.post(f"/api/v1/automation/schedules/{schedule.json()['id']}/disable", headers=A)
    assert disabled.json()["enabled"] is False and disabled.json()["next_run_at"] is None
    assert client.delete(f"/api/v1/automation/schedules/{schedule.json()['id']}", headers=A).status_code == 204
    assert client.get(f"/api/v1/automation/schedules/{schedule.json()['id']}", headers=A).status_code == 404


def test_automation_is_isolated_by_tenant_and_workspace(app_factory: sessionmaker[Session]) -> None:
    client = TestClient(app)
    workflow_id = client.post("/api/v1/automation/workflows", headers=A, json={
        "name": "Mine", "steps": [{"id": "one", "name": "One", "action": "notify"}]}).json()["id"]
    run_id = client.post(f"/api/v1/automation/workflows/{workflow_id}/runs", headers=A).json()["id"]

    for other in (headers(workspace_id=WORKSPACE_A2), headers(TENANT_B, WORKSPACE_B)):
        assert client.get("/api/v1/automation/workflows", headers=other).json() == []
        assert client.get("/api/v1/automation/runs", headers=other).json() == []
        assert client.post(f"/api/v1/automation/workflows/{workflow_id}/runs", headers=other).status_code == 404
        assert client.post(f"/api/v1/automation/runs/{run_id}/cancel", headers=other).status_code == 404
        assert client.post("/api/v1/automation/schedules", headers=other, json={
            "workflow_id": workflow_id, "name": "Steal", "cron": "* * * * *"}).status_code == 404
    assert client.post("/api/v1/automation/workflows", headers=headers(workspace_id="workspace_not_onboarded"), json={
        "name": "Early", "steps": [{"id": "one", "name": "One", "action": "notify"}]}).status_code == 409


def test_concurrent_starts_with_one_idempotency_key_make_one_run(engine: PostgresAutomationEngine) -> None:
    created = engine.create_workflow(tenant_context(), workflow())
    barrier = threading.Barrier(6)

    def start(_: int) -> str:
        barrier.wait()
        return engine.start(tenant_context(), created.id, "same-key").id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = set(pool.map(start, range(6)))
    assert len(ids) == 1
    assert len(engine.list_runs(tenant_context())) == 1


def test_due_schedule_fires_exactly_once_across_replicas(
    app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    replicas = [PostgresAutomationEngine(app_factory) for _ in range(4)]
    schedules = []
    for context in (tenant_context(), tenant_context(TENANT_A, WORKSPACE_A2), tenant_context(TENANT_B, WORKSPACE_B)):
        created = replicas[0].create_workflow(context, workflow())
        schedule = replicas[0].create_schedule(context, ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
        _make_due(database_engine, schedule.id)
        schedules.append((context, schedule.id))
    not_due = replicas[0].create_schedule(
        tenant_context(), ScheduleCreate(workflow_id=replicas[0].list_workflows(tenant_context())[0].id, name="Later", cron="0 * * * *")
    )
    barrier = threading.Barrier(len(replicas))

    def run(replica: PostgresAutomationEngine) -> list[str]:
        barrier.wait()
        return [item.id for item in replica.run_due_schedules()]

    with ThreadPoolExecutor(max_workers=len(replicas)) as pool:
        fired = [run_id for ids in pool.map(run, replicas) for run_id in ids]

    assert len(fired) == len(set(fired)) == 3
    for context, schedule_id in schedules:
        [scheduled_run] = replicas[0].list_runs(context)
        assert scheduled_run.schedule_id == schedule_id and scheduled_run.status == RunStatus.COMPLETED
        assert scheduled_run.created_by == "user_test"
        schedule = replicas[0].get_schedule(context, schedule_id)
        assert schedule.next_run_at is not None and schedule.last_run_at is not None
        assert schedule.next_run_at > schedule.last_run_at
    assert replicas[0].get_schedule(tenant_context(), not_due.id).last_run_at is None
    # Nothing is due any more: further passes do nothing.
    assert [replica.run_due_schedules() for replica in replicas] == [[], [], [], []]


def test_a_fire_time_that_already_ran_is_not_run_again(
    engine: PostgresAutomationEngine, database_engine: Engine
) -> None:
    created = engine.create_workflow(tenant_context(), workflow())
    schedule = engine.create_schedule(tenant_context(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    _make_due(database_engine, schedule.id)
    with database_engine.connect() as connection:
        fire_at = connection.execute(text("select next_run_at from automation_schedules")).scalar_one()
    # Simulates a replica that recorded the run, then crashed before advancing the schedule.
    engine.start(tenant_context(), created.id, schedule_fire_key(schedule.id, fire_at))

    assert engine.run_due_schedules() == []
    assert len(engine.list_runs(tenant_context())) == 1
    assert engine.get_schedule(tenant_context(), schedule.id).next_run_at > fire_at


def test_disabled_and_updated_schedules(engine: PostgresAutomationEngine, database_engine: Engine) -> None:
    created = engine.create_workflow(tenant_context(), workflow())
    schedule = engine.create_schedule(tenant_context(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    _make_due(database_engine, schedule.id)
    engine.update_schedule(tenant_context(), schedule.id, ScheduleUpdate(enabled=False))
    assert engine.run_due_schedules() == []

    enabled = engine.update_schedule(tenant_context(), schedule.id, ScheduleUpdate(enabled=True, cron="30 6 * * *"))
    assert enabled.next_run_at is not None and (enabled.next_run_at.hour, enabled.next_run_at.minute) == (6, 30)


def test_maintenance_role_discovers_due_schedule_ids_only(
    engine: PostgresAutomationEngine, database_engine: Engine
) -> None:
    created = engine.create_workflow(tenant_context(), workflow())
    due = engine.create_schedule(tenant_context(), ScheduleCreate(workflow_id=created.id, name="Due", cron="0 * * * *"))
    engine.create_schedule(tenant_context(), ScheduleCreate(workflow_id=created.id, name="Later", cron="0 * * * *"))
    off = engine.create_schedule(tenant_context(), ScheduleCreate(workflow_id=created.id, name="Off", cron="0 * * * *", enabled=False))
    _make_due(database_engine, due.id)
    with database_engine.begin() as connection:
        connection.execute(text("update automation_schedules set next_run_at = now() - interval '1 day' where id = :id"), {"id": off.id})

    assert engine.due_schedule_scopes() == [(due.id, TENANT_A, WORKSPACE_A)]
    with database_engine.connect() as connection:
        for statement in (
            "select cron from automation_schedules",
            "select steps from automation_workflows",
            "select id from automation_runs",
            "update automation_schedules set next_run_at = now() where id = :id",
            "insert into automation_runs (id) values (:id)",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement), {"id": due.id})


def test_scheduler_loop_on_postgres_stops_without_leaving_a_transaction(
    app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    engine = PostgresAutomationEngine(app_factory)
    created = engine.create_workflow(tenant_context(), workflow())
    schedule = engine.create_schedule(tenant_context(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    _make_due(database_engine, schedule.id)

    async def scenario() -> int:
        scheduler = AutomationScheduler(lambda: engine, poll_interval=0.01)
        scheduler.start()
        for _ in range(500):
            if scheduler.fired:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop(timeout=10)
        return scheduler.fired

    assert asyncio.run(scenario()) == 1
    with database_engine.connect() as connection:
        lingering = connection.execute(
            text(
                """
                select count(*) from pg_stat_activity
                where datname = current_database() and pid <> pg_backend_pid() and backend_type = 'client backend'
                  and (state like 'idle in transaction%' or state = 'active')
                """
            )
        ).scalar_one()
    assert lingering == 0
