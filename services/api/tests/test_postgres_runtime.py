from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.dependencies import repository_context, tenant_context as request_tenant_context
from anum_api.main import app
from anum_api.repository import AnumRepository
from anum_api.schemas import ApprovalStatus, TaskStatus, TenantContext

from conftest import APP_ROLE, TENANT_A, WORKSPACE_A, tenant_context


pytestmark = pytest.mark.database

HEADERS = {
    "x-tenant-id": TENANT_A,
    "x-workspace-id": WORKSPACE_A,
    "x-user-id": "user_test",
    "x-user-roles": "owner",
}


@pytest.fixture
def postgres_client(database_engine: Engine, seed_scopes: None) -> Iterator[TestClient]:
    def override_repository(
        context: TenantContext = Depends(request_tenant_context),
    ) -> Iterator[AnumRepository]:
        connection = database_engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(
                text("select set_config('anum.tenant_id', :tenant_id, true)"),
                {"tenant_id": context.tenant_id},
            )
            connection.execute(
                text("select set_config('anum.workspace_id', :workspace_id, true)"),
                {"workspace_id": context.workspace_id},
            )
            yield SqlAlchemyRepository(session, created_by_user_id=context.user_id)
            session.flush()
            transaction.commit()
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise
        finally:
            session.close()
            connection.close()

    app.dependency_overrides[repository_context] = override_repository
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(repository_context, None)


def create_waiting_approval(client: TestClient) -> tuple[str, str, str]:
    created = client.post(
        "/api/v1/tasks",
        headers=HEADERS,
        json={"title": "Publish update", "prompt": "Publish the final update"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    started = client.post(f"/api/v1/tasks/{task_id}/run", headers=HEADERS)
    assert started.status_code == 200
    payload = started.json()
    assert payload["task"]["status"] == TaskStatus.WAITING_APPROVAL
    return task_id, payload["run"]["id"], payload["approval"]["id"]


@pytest.mark.parametrize(
    ("decision", "approval_status", "task_status", "terminal_event"),
    [
        ("approve", ApprovalStatus.APPROVED, TaskStatus.COMPLETED, "agent_run.completed"),
        ("reject", ApprovalStatus.REJECTED, TaskStatus.FAILED, "agent_run.failed"),
    ],
)
def test_waiting_approval_survives_reload_and_resumes_durably(
    postgres_client: TestClient,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
    decision: str,
    approval_status: ApprovalStatus,
    task_status: TaskStatus,
    terminal_event: str,
) -> None:
    context = tenant_context()
    task_id, run_id, approval_id = create_waiting_approval(postgres_client)

    with repository_factory(context) as repository:
        assert repository.get_task(task_id, context).status == TaskStatus.WAITING_APPROVAL
        assert repository.get_run(run_id, context).status == TaskStatus.WAITING_APPROVAL
        assert repository.get_approval(approval_id, context).status == ApprovalStatus.PENDING
        assert [event.type for event in repository.list_events(context)] == [
            "task.created",
            "approval.requested",
        ]

    decided = postgres_client.post(
        f"/api/v1/approvals/{approval_id}/{decision}",
        headers=HEADERS,
    )
    assert decided.status_code == 200

    with repository_factory(context) as repository:
        assert repository.get_task(task_id, context).status == task_status
        assert repository.get_run(run_id, context).status == task_status
        approval = repository.get_approval(approval_id, context)
        assert approval.status == approval_status
        assert approval.decided_at is not None
        event_types = [event.type for event in repository.list_events(context)]
        assert event_types == [
            "task.created",
            "approval.requested",
            f"approval.{approval_status.value}",
            terminal_event,
        ]


def test_duplicate_approval_decision_is_a_conflict_and_emits_no_extra_events(
    postgres_client: TestClient,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    _, _, approval_id = create_waiting_approval(postgres_client)

    first = postgres_client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=HEADERS,
    )
    second = postgres_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=HEADERS,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    with repository_factory(context) as repository:
        events = repository.list_events(context)
        assert sum(event.type == "approval.approved" for event in events) == 1
        assert sum(event.type == "approval.rejected" for event in events) == 0
        assert sum(event.type == "agent_run.completed" for event in events) == 1


def test_cancelling_waiting_task_expires_approval_and_prevents_resume(
    postgres_client: TestClient,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    task_id, run_id, approval_id = create_waiting_approval(postgres_client)

    cancelled = postgres_client.post(f"/api/v1/tasks/{task_id}/cancel", headers=HEADERS)
    late_approval = postgres_client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=HEADERS,
    )

    assert cancelled.status_code == 200
    assert late_approval.status_code == 409
    with repository_factory(context) as repository:
        assert repository.get_task(task_id, context).status == TaskStatus.CANCELLED
        assert repository.get_run(run_id, context).status == TaskStatus.CANCELLED
        assert repository.get_approval(approval_id, context).status == ApprovalStatus.EXPIRED
        event_types = [event.type for event in repository.list_events(context)]
        assert event_types == ["task.created", "approval.requested", "task.cancelled"]
