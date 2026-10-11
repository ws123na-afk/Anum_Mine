"""PostgreSQL task list filters, summary, archive columns, run history and task events
(docs/tasks.md), under the app role with forced row-level security."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from anum_api.repository import UnknownCursorError
from anum_api.schemas import AgentRun, AgentRunStep, Approval, ApprovalStatus, DomainEvent, RiskLevel, Task, TaskStatus
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
