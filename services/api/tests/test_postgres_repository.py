from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    DomainEvent,
    RiskLevel,
    Task,
    TaskStatus,
)

from conftest import (
    FIXED_NOW,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    tenant_context,
)


pytestmark = pytest.mark.database


def make_task(
    task_id: str = "task_repo_a",
    tenant_id: str = TENANT_A,
    workspace_id: str = WORKSPACE_A,
) -> Task:
    return Task(
        id=task_id,
        title="Persist repository state",
        prompt="Store this task",
        status=TaskStatus.CREATED,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )


def make_run(task_id: str = "task_repo_a") -> AgentRun:
    return AgentRun(
        id="run_repo_a",
        task_id=task_id,
        status=TaskStatus.WAITING_APPROVAL,
        steps=[
            AgentRunStep(
                id="step_repo_a",
                type="model_call",
                summary="Deterministic model response",
                created_at=FIXED_NOW + timedelta(seconds=1),
                metadata={"tokens": 7},
            )
        ],
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW + timedelta(seconds=1),
    )


def make_approval(task_id: str = "task_repo_a") -> Approval:
    return Approval(
        id="approval_repo_a",
        task_id=task_id,
        action="publish",
        risk_level=RiskLevel.HIGH,
        status=ApprovalStatus.PENDING,
        reason="Publishing requires approval",
        created_at=FIXED_NOW + timedelta(seconds=2),
    )


def make_event(
    event_id: str = "event_repo_a",
    tenant_id: str = TENANT_A,
    workspace_id: str = WORKSPACE_A,
) -> DomainEvent:
    return DomainEvent(
        id=event_id,
        type="approval.requested",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        subject="approval_repo_a",
        correlation_id="task_repo_a",
        created_at=FIXED_NOW + timedelta(seconds=3),
        payload={"task_id": "task_repo_a"},
    )


def test_repository_round_trip_survives_new_sessions(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    task = make_task()
    run = make_run()
    approval = make_approval()
    event = make_event()

    with repository_factory(context, commit=True) as repository:
        repository.create_task(task)
        repository.save_run(run)
        repository.save_approval(approval)
        repository.record_event(event)

    with repository_factory(context) as reloaded:
        assert reloaded.get_task(task.id, context) == task
        assert reloaded.get_run(run.id, context) == run
        assert reloaded.find_run_for_task(task.id, context) == run
        assert reloaded.get_approval(approval.id, context) == approval
        assert reloaded.list_approvals(context) == [approval]
        assert reloaded.list_events(context) == [event]


def test_updates_are_persisted_without_duplicate_rows(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    task = make_task()
    run = make_run()
    approval = make_approval()

    with repository_factory(context, commit=True) as repository:
        repository.create_task(task)
        repository.save_run(run)
        repository.save_approval(approval)

    task.status = TaskStatus.COMPLETED
    task.updated_at = FIXED_NOW + timedelta(minutes=1)
    run.status = TaskStatus.COMPLETED
    run.result = "Approved action completed"
    run.updated_at = task.updated_at
    approval.status = ApprovalStatus.APPROVED
    approval.decided_at = task.updated_at

    with repository_factory(context, commit=True) as repository:
        repository.save_task(task)
        repository.save_run(run)
        repository.save_approval(approval)

    with repository_factory(context) as reloaded:
        assert reloaded.get_task(task.id, context) == task
        assert reloaded.get_run(run.id, context) == run
        assert reloaded.list_approvals(context) == [approval]


def test_reads_are_isolated_by_tenant_and_workspace(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    task = make_task()
    run = make_run()
    approval = make_approval()
    event = make_event()

    with repository_factory(context, commit=True) as repository:
        repository.create_task(task)
        repository.save_run(run)
        repository.save_approval(approval)
        repository.record_event(event)

    hidden_contexts = (
        tenant_context(TENANT_B, WORKSPACE_B),
        tenant_context(TENANT_A, WORKSPACE_A2),
    )
    for hidden_context in hidden_contexts:
        with repository_factory(hidden_context) as repository:
            assert repository.get_task(task.id, hidden_context) is None
            assert repository.get_run(run.id, hidden_context) is None
            assert repository.find_run_for_task(task.id, hidden_context) is None
            assert repository.get_approval(approval.id, hidden_context) is None
            assert repository.list_approvals(hidden_context) == []
            assert repository.list_events(hidden_context) == []


def test_list_tasks_is_ordered_and_scoped_by_tenant_and_workspace(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    first = make_task("task_repo_a")
    second = make_task("task_repo_b")
    second.created_at = first.created_at + timedelta(seconds=1)
    second.updated_at = second.created_at

    with repository_factory(context, commit=True) as repository:
        repository.create_task(second)
        repository.create_task(first)

    with repository_factory(context) as repository:
        tasks = repository.list_tasks(context)
        assert [task.id for task in tasks] == [first.id, second.id]

    hidden_contexts = (
        tenant_context(TENANT_B, WORKSPACE_B),
        tenant_context(TENANT_A, WORKSPACE_A2),
    )
    for hidden_context in hidden_contexts:
        with repository_factory(hidden_context) as repository:
            assert repository.list_tasks(hidden_context) == []


def test_events_are_deterministically_ordered_and_workspace_scoped(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    first = make_event("event_01")
    second = make_event("event_02")
    second.created_at = first.created_at + timedelta(seconds=1)

    with repository_factory(context, commit=True) as repository:
        repository.record_event(second)
        repository.record_event(first)

    with repository_factory(context) as repository:
        events = repository.list_events(context)
        assert [event.id for event in events] == [first.id, second.id]
        assert {event.correlation_id for event in events} == {"task_repo_a"}

    with repository_factory(tenant_context(TENANT_A, WORKSPACE_A2)) as repository:
        assert repository.list_events(tenant_context(TENANT_A, WORKSPACE_A2)) == []
