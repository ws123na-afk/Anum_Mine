"""PostgreSQL task list filters, summary, archive columns, run history and task events
(docs/tasks.md), under the app role with forced row-level security."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from anum_api.repository import UnknownCursorError
from anum_api.schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    DomainEvent,
    RiskLevel,
    Task,
    TaskPriority,
    TaskStatus,
)
from anum_api.tasks import TaskFilters

from conftest import FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context

pytestmark = pytest.mark.database


def _task(task_id: str, *, title: str = "Task", prompt: str = "Do it", status: TaskStatus = TaskStatus.CREATED,
          created_by: str = "user_test", minutes: int = 0, tenant_id: str = TENANT_A,
          workspace_id: str = WORKSPACE_A) -> Task:
    at = FIXED_NOW + timedelta(minutes=minutes)
    return Task(id=task_id, title=title, prompt=prompt, status=status, tenant_id=tenant_id,
                workspace_id=workspace_id, created_at=at, updated_at=at, created_by=created_by)


def _run(run_id: str, task_id: str, minutes: int, status: TaskStatus = TaskStatus.FAILED) -> AgentRun:
    at = FIXED_NOW + timedelta(minutes=minutes)
    return AgentRun(id=run_id, task_id=task_id, status=status, created_at=at, updated_at=at,
                    steps=[AgentRunStep(id=f"step_{run_id}", type="model_call", summary="planned", created_at=at)])


def _event(event_id: str, task_id: str, seconds: int) -> DomainEvent:
    return DomainEvent(id=event_id, type="task.created", tenant_id=TENANT_A, workspace_id=WORKSPACE_A,
                       subject=task_id, correlation_id=task_id, created_at=FIXED_NOW + timedelta(seconds=seconds),
                       payload={"task_id": task_id})


def _ids(tasks: list[Task]) -> list[str]:
    return [task.id for task in tasks]


def test_filters_search_and_keyset_paging(seed_scopes: None, repository_factory: Callable[..., Any]) -> None:
    context = tenant_context()
    with repository_factory(context, commit=True) as repository:
        repository.create_task(_task("task_report_1", title="Quarterly report", minutes=1, status=TaskStatus.COMPLETED))
        repository.create_task(_task("task_report_2", title="Weekly report", minutes=2, created_by="user_member"))
        repository.create_task(_task("task_pct", title="100% plan_b", minutes=3))
        repository.create_task(_task("task_old", title="Old", minutes=-60 * 24 * 10))
    with repository_factory(tenant_context(TENANT_B, WORKSPACE_B), commit=True) as repository:
        repository.create_task(_task("task_other_tenant", title="Quarterly report", tenant_id=TENANT_B,
                                     workspace_id=WORKSPACE_B))

    with repository_factory(context) as repository:
        assert _ids(repository.list_tasks(context, filters=TaskFilters(query="REPORT"))) == ["task_report_2", "task_report_1"]
        assert _ids(repository.list_tasks(context, filters=TaskFilters(query="%"))) == ["task_pct"]
        assert _ids(repository.list_tasks(context, filters=TaskFilters(query="n_b"))) == ["task_pct"]
        assert _ids(repository.list_tasks(context, filters=TaskFilters(statuses=frozenset({TaskStatus.COMPLETED})))) == ["task_report_1"]
        assert _ids(repository.list_tasks(context, filters=TaskFilters(created_by="user_member"))) == ["task_report_2"]
        recent = TaskFilters(created_after=FIXED_NOW, created_before=FIXED_NOW + timedelta(minutes=3))
        assert _ids(repository.list_tasks(context, filters=recent)) == ["task_report_2", "task_report_1"]
        first = repository.list_tasks(context, limit=1, filters=TaskFilters(query="report"))
        rest = repository.list_tasks(context, limit=5, after=first[-1].id, filters=TaskFilters(query="report"))
        assert _ids(first + rest) == ["task_report_2", "task_report_1"]
        with pytest.raises(UnknownCursorError):
            repository.list_tasks(context, after="task_other_tenant", filters=TaskFilters())


def test_archive_columns_round_trip_and_hide_from_the_default_list(
    seed_scopes: None, repository_factory: Callable[..., Any]
) -> None:
    context = tenant_context()
    with repository_factory(context, commit=True) as repository:
        repository.create_task(_task("task_kept", minutes=1, status=TaskStatus.COMPLETED))
        archived = _task("task_archived", minutes=2, status=TaskStatus.FAILED)
        archived.archived_at = FIXED_NOW + timedelta(hours=1)
        archived.archived_by = "user_owner"
        repository.create_task(archived)
    other_workspace = tenant_context(TENANT_A, WORKSPACE_A2)
    with repository_factory(other_workspace, commit=True) as repository:
        repository.create_task(_task("task_other_ws", workspace_id=WORKSPACE_A2))  # invisible in WORKSPACE_A

    with repository_factory(context) as repository:
        stored = repository.get_task("task_archived", context)
        assert stored is not None
        assert stored.archived_at == FIXED_NOW + timedelta(hours=1)
        assert stored.archived_by == "user_owner"
        assert _ids(repository.list_tasks(context, filters=TaskFilters())) == ["task_kept"]
        assert _ids(repository.list_tasks(context, filters=TaskFilters(archived=True))) == ["task_archived"]
        # No filters: every task in scope, as before archiving existed.
        assert set(_ids(repository.list_tasks(context))) == {"task_kept", "task_archived"}
        summary = repository.summarize_tasks(context, TaskFilters(statuses=frozenset({TaskStatus.FAILED})))
        assert summary.by_status == {"completed": 1}
        assert summary.total == 1 and summary.archived == 1
        assert summary.creators == ["user_test"]


def test_priority_and_due_date_round_trip(seed_scopes: None, repository_factory: Callable[..., Any]) -> None:
    context = tenant_context()
    with repository_factory(context, commit=True) as repository:
        planned = _task("task_planned", minutes=1)
        planned.priority = TaskPriority.HIGH
        planned.due_at = FIXED_NOW + timedelta(days=2)
        repository.create_task(planned)
        repository.create_task(_task("task_unplanned", minutes=2))
    with repository_factory(context, commit=True) as repository:
        stored = repository.get_task("task_planned", context)
        assert stored is not None
        assert stored.priority == TaskPriority.HIGH and stored.due_at == FIXED_NOW + timedelta(days=2)
        unplanned = repository.get_task("task_unplanned", context)
        assert unplanned is not None and unplanned.priority == TaskPriority.MEDIUM and unplanned.due_at is None
        stored.priority = TaskPriority.LOW
        stored.due_at = None
        repository.save_task(stored)
    with repository_factory(context) as repository:
        again = repository.get_task("task_planned", context)
        assert again is not None and again.priority == TaskPriority.LOW and again.due_at is None


def test_runs_events_and_approvals_of_a_task(seed_scopes: None, repository_factory: Callable[..., Any]) -> None:
    context = tenant_context()
    with repository_factory(context, commit=True) as repository:
        repository.create_task(_task("task_runs"))
        repository.create_task(_task("task_other"))
        for index in range(3):
            repository.save_run(_run(f"run_{index}", "task_runs", minutes=index))
        repository.save_run(_run("run_other", "task_other", minutes=9))
        for index in range(3):
            repository.record_event(_event(f"evt_{index}", "task_runs", seconds=index))
        repository.record_event(_event("evt_other", "task_other", seconds=9))
        repository.save_approval(Approval(id="approval_runs", task_id="task_runs", action="external.action",
                                          risk_level=RiskLevel.HIGH, status=ApprovalStatus.PENDING, reason="r",
                                          created_at=FIXED_NOW, run_id="run_2"))

    with repository_factory(context) as repository:
        runs = repository.list_runs_for_task("task_runs", context)
        assert [run.id for run in runs] == ["run_2", "run_1", "run_0"]
        assert runs[0].steps[0].summary == "planned"
        page = repository.list_runs_for_task("task_runs", context, limit=2)
        rest = repository.list_runs_for_task("task_runs", context, limit=2, after=page[-1].id)
        assert [run.id for run in page + rest] == ["run_2", "run_1", "run_0"]
        with pytest.raises(UnknownCursorError):
            repository.list_runs_for_task("task_runs", context, after="run_other")
        found = repository.find_run_for_task("task_runs", context)
        assert found is not None and found.id == "run_2"

        events = repository.list_task_events("task_runs", context)
        assert [event.id for event in events] == ["evt_2", "evt_1", "evt_0"]
        assert [event.id for event in repository.list_task_events("task_runs", context, limit=1, after="evt_2")] == ["evt_1"]
        with pytest.raises(UnknownCursorError):
            repository.list_task_events("task_runs", context, after="evt_other")
        assert [approval.id for approval in repository.list_task_approvals("task_runs", context)] == ["approval_runs"]
        assert repository.list_task_approvals("task_other", context) == []

    other = tenant_context(TENANT_B, WORKSPACE_B)
    with repository_factory(other) as repository:
        assert repository.list_runs_for_task("task_runs", other) == []
        assert repository.list_task_events("task_runs", other) == []
        assert repository.list_task_approvals("task_runs", other) == []


def test_locking_a_tasks_approvals_leaves_other_tasks_approvals_free(
    seed_scopes: None, repository_factory: Callable[..., Any], database_engine: Any
) -> None:
    """Cancelling a task locks its own approvals only (it used to lock the workspace's)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    context = tenant_context()
    with repository_factory(context, commit=True) as repository:
        for name in ("lock_a", "lock_b"):
            repository.create_task(_task(f"task_{name}"))
            repository.save_approval(Approval(id=f"approval_{name}", task_id=f"task_{name}", action="external.action",
                                              risk_level=RiskLevel.HIGH, status=ApprovalStatus.PENDING, reason="r",
                                              created_at=FIXED_NOW))

    with repository_factory(context) as repository:
        held = repository.list_task_approvals("task_lock_a", context, for_update=True)
        assert [approval.id for approval in held] == ["approval_lock_a"]
        with database_engine.connect() as other:
            # Another transaction can lock the other task's approval at once...
            free = other.execute(text("select id from approvals where id = 'approval_lock_b' for update nowait")).all()
            assert free == [("approval_lock_b",)]
            other.rollback()
            # ...and not the one whose task is being cancelled.
            with pytest.raises(OperationalError, match="could not obtain lock"):
                other.execute(text("select id from approvals where id = 'approval_lock_a' for update nowait"))
            other.rollback()
        # Without for_update nothing is locked.
    with repository_factory(context) as repository:
        assert [a.id for a in repository.list_task_approvals("task_lock_a", context)] == ["approval_lock_a"]
        with database_engine.connect() as other:
            assert other.execute(text("select id from approvals where id = 'approval_lock_a' for update nowait")).all()
            other.rollback()


def test_pending_approvals_and_status_counts_come_from_page_and_count_queries(
    seed_scopes: None, repository_factory: Callable[..., Any]
) -> None:
    """``approval_paging.pending_now`` and the voice status counts on PostgreSQL (tests/test_bounded_reads.py
    covers the in-memory repository): more than one page of pending approvals, none repeated or skipped."""
    from anum_api.approval_paging import pending_now
    from anum_api.voice_assistant import snapshot, snapshot_from_counts

    context = tenant_context()
    now = FIXED_NOW + timedelta(days=1)
    with repository_factory(context, commit=True) as repository:
        repository.create_task(_task("task_wait", status=TaskStatus.WAITING_APPROVAL))
        repository.create_task(_task("task_run", status=TaskStatus.RUNNING))
        repository.create_task(_task("task_done", status=TaskStatus.COMPLETED).model_copy(update={"archived_at": FIXED_NOW, "archived_by": "user_test"}))
        for index in range(130):
            repository.save_approval(Approval(id=f"wait_{index:03d}", task_id="task_wait", action="external.action",
                                              risk_level=RiskLevel.HIGH, status=ApprovalStatus.PENDING, reason="r",
                                              created_at=FIXED_NOW + timedelta(seconds=index // 2), expires_at=now + timedelta(hours=1)))
        repository.save_approval(Approval(id="lapsed", task_id="task_wait", action="external.action", risk_level=RiskLevel.HIGH,
                                          status=ApprovalStatus.PENDING, reason="r", created_at=FIXED_NOW, expires_at=now - timedelta(minutes=1)))
        repository.save_approval(Approval(id="decided", task_id="task_run", action="external.action", risk_level=RiskLevel.HIGH,
                                          status=ApprovalStatus.APPROVED, reason="r", created_at=FIXED_NOW, decided_at=FIXED_NOW))

    with repository_factory(context) as repository:
        waiting = pending_now(repository, context, now)
        assert len(waiting) == 130 and {a.id for a in waiting} == {f"wait_{index:03d}" for index in range(130)}
        assert [(a.created_at, a.id) for a in waiting] == sorted(((a.created_at, a.id) for a in waiting), reverse=True)
        assert [a.id for a in pending_now(repository, context, now, limit=3)] == ["wait_129", "wait_128", "wait_127"]
        counts = snapshot_from_counts(repository.summarize_tasks(context, TaskFilters()), repository.summarize_approvals(context, now).pending)
        lists = snapshot(repository.list_tasks(context), [])
        assert (counts.tasks_total, counts.running, counts.waiting_approval) == (lists.tasks_total, lists.running, lists.waiting_approval) == (3, 1, 1)
        assert counts.pending_approvals == 130
    with repository_factory(tenant_context(TENANT_B, WORKSPACE_B)) as repository:
        assert pending_now(repository, tenant_context(TENANT_B, WORKSPACE_B), now) == []
