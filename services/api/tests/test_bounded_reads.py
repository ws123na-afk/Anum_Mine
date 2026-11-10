"""Hot paths count and page in the database instead of loading whole tables (reliability audit, November 2026).

Six places read every approval (and one every task) of the workspace to count, or to pick
the pending ones in Python: the voice ``ask``, Home's approvals focus, the assistant's
approvals tool, the calendar's next steps, the emergency stop's held items and the mail
domain changes. ``GET /api/v1/approvals`` and ``GET /api/v1/events`` returned every row the
workspace ever had. They now use the count and page queries, and the two lists are bounded.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import agent_controls, home_sources, voice_grounding
from anum_api.approval_paging import PENDING_READ_LIMIT, pending_now
from anum_api.events import CanonicalEventName, create_event
from anum_api.home import HomeScope
from anum_api.mailbox import domains_api
from anum_api.main import EVENT_LIST_DEFAULT_LIMIT, app, store
from anum_api.repository import InMemoryRepository
from anum_api.schemas import Approval, ApprovalStatus, RiskLevel, Task, TaskStatus, TenantContext
from anum_api.store import InMemoryStore
from anum_api.tasks import TaskFilters
from anum_api.voice_assistant import snapshot, snapshot_from_counts

NOW = datetime.now(UTC).replace(microsecond=0)  # the stop and the assistant tool read the clock themselves
CONTEXT = TenantContext(tenant_id="tenant_bounded", workspace_id="workspace_bounded", user_id="owner_bounded", roles=["owner"])
HEADERS = {"x-tenant-id": "tenant_bounded", "x-workspace-id": "workspace_bounded", "x-user-id": "owner_bounded", "x-user-roles": "owner"}
API = Path(__file__).parents[1] / "anum_api"


class NoWholeTableReads:
    """The repository, refusing the reads that load a whole table. The in-memory repository's
    own page and count helpers still reach its lists: only the caller under test is held to it."""

    def __init__(self, inner: InMemoryRepository) -> None:
        self._inner = inner

    def list_approvals(self, *_: Any, **__: Any) -> list[Approval]:
        raise AssertionError("list_approvals loads every approval of the workspace")

    def list_tasks(self, context: TenantContext, *, limit: int | None = None, **rest: Any) -> list[Task]:
        assert limit is not None, "list_tasks without a limit loads every task of the workspace"
        return self._inner.list_tasks(context, limit=limit, **rest)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _task(task_id: str, status: TaskStatus = TaskStatus.WAITING_APPROVAL, *, archived: bool = False) -> Task:
    return Task(id=task_id, title=f"Task {task_id}", prompt="Do it", status=status, tenant_id=CONTEXT.tenant_id,
                workspace_id=CONTEXT.workspace_id, created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=2),
                created_by=CONTEXT.user_id, archived_at=NOW - timedelta(days=1) if archived else None)


def _approval(approval_id: str, task_id: str, minutes_ago: int, status: ApprovalStatus = ApprovalStatus.PENDING, *,
              expired: bool = False, action: str = "send_email", risk: RiskLevel = RiskLevel.HIGH, **more: Any) -> Approval:
    created = NOW - timedelta(minutes=minutes_ago)
    return Approval(id=approval_id, task_id=task_id, action=action, risk_level=risk, status=status, reason="Needs a person",
                    created_at=created, expires_at=NOW - timedelta(seconds=1) if expired else NOW + timedelta(hours=1),
                    decided_at=None if status == ApprovalStatus.PENDING else created + timedelta(minutes=1), **more)


@pytest.fixture
def repository() -> NoWholeTableReads:
    inner = InMemoryRepository(InMemoryStore())
    inner.create_task(_task("task_1"))
    inner.create_task(_task("task_2", TaskStatus.RUNNING))
    inner.create_task(_task("task_3", TaskStatus.COMPLETED, archived=True))
    inner.save_approval(_approval("pending_old", "task_1", 50))
    inner.save_approval(_approval("pending_new", "task_1", 5, risk=RiskLevel.MEDIUM))
    inner.save_approval(_approval("lapsed", "task_1", 40, expired=True))
    inner.save_approval(_approval("approved", "task_2", 30, ApprovalStatus.APPROVED))
    inner.save_approval(_approval("rejected", "task_2", 20, ApprovalStatus.REJECTED))
    inner.save_approval(_approval("domain", "task_2", 10, action=domains_api.DOMAIN_TOOL,
                                  arguments={"operation": "add_domain", "domain": "example.com"}))
    return NoWholeTableReads(inner)


def test_pending_now_reads_only_what_is_waiting_newest_first_in_pages(repository: NoWholeTableReads) -> None:
    assert [a.id for a in pending_now(repository, CONTEXT, NOW)] == ["pending_new", "domain", "pending_old"]
    assert [a.id for a in pending_now(repository, CONTEXT, NOW, limit=2)] == ["pending_new", "domain"]
    assert PENDING_READ_LIMIT == 500

    # More than one page (100 a page): every pending approval once, in order, up to the limit.
    for index in range(230):
        repository.save_approval(_approval(f"bulk_{index:03d}", "task_1", 100 + index))
    pages: list[int] = []
    real = repository._inner.list_approvals_page

    def counted(context: TenantContext, **arguments: Any) -> Any:
        pages.append(arguments["limit"])
        return real(context, **arguments)

    repository._inner.list_approvals_page = counted  # type: ignore[method-assign]
    everything = pending_now(repository, CONTEXT, NOW)
    assert len(everything) == 233 and len({a.id for a in everything}) == 233
    assert [a.created_at for a in everything] == sorted((a.created_at for a in everything), reverse=True)
    assert pages == [100, 100, 100]
    assert len(pending_now(repository, CONTEXT, NOW, limit=150)) == 150


def test_voice_status_counts_match_the_lists_they_replace(repository: NoWholeTableReads) -> None:
    inner = repository._inner
    from_lists = snapshot(inner.list_tasks(CONTEXT), [a.status for a in inner.list_approvals(CONTEXT)])
    from_counts = snapshot_from_counts(
        repository.summarize_tasks(CONTEXT, TaskFilters()), repository.summarize_approvals(CONTEXT, NOW).pending
    )
    # Tasks: the same numbers, archived tasks included in the total as before.
    assert (from_counts.tasks_total, from_counts.running, from_counts.waiting_approval) == (3, 1, 1)
    assert (from_lists.tasks_total, from_lists.running, from_lists.waiting_approval) == (3, 1, 1)
    # Approvals: the count no longer includes one that lapsed and can no longer be decided.
    assert from_lists.pending_approvals == 4 and from_counts.pending_approvals == 3


def test_home_assistant_stop_and_mail_domain_reads_never_load_every_approval(
    repository: NoWholeTableReads, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = HomeScope(context=CONTEXT, repository=repository, state=None, now=NOW)  # type: ignore[arg-type]
    assert [a.id for a in home_sources._pending_approvals(scope)] == ["pending_old", "domain", "pending_new"]
    focus = home_sources.focus_approvals(scope)
    assert focus.count == 3 and focus.items[0].severity == "high"

    held = [item for item in agent_controls._held_items(CONTEXT, repository) if item.kind == "approval"]
    assert [item.id for item in held] == ["pending_old", "domain", "pending_new"]  # oldest first, as before

    changes = domains_api._pending(repository, CONTEXT)
    assert [(change.approval.id, change.operation, change.domain) for change in changes] == [("domain", "add_domain", "example.com")]

    class Unit:
        def __enter__(self) -> NoWholeTableReads:
            return repository

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(voice_grounding, "_units", lambda _context: Unit())
    result = voice_grounding._read_approvals(CONTEXT)
    assert result.status == "succeeded", result.summary
    listed = result.output["approvals"] if isinstance(result.output, dict) else []
    assert [item["action"] for item in listed] == ["send_email", domains_api.DOMAIN_TOOL, "send_email"]
    assert [item["task"] for item in listed] == ["Task task_1", "Task task_2", "Task task_1"]


def test_no_module_loads_every_approval_of_a_workspace_any_more() -> None:
    """``list_approvals`` stays for the in-memory backend's own helpers; a request path must use
    ``list_approvals_page``, ``summarize_approvals`` or ``approval_paging.pending_now``."""
    allowed = {
        "repository.py",  # the in-memory repository's page and count helpers
        "privacy.py",  # the in-memory privacy export (memory backend only)
        "approval_reminders.py",  # the in-memory reminder scan (memory backend only)
    }
    callers = {
        str(path.relative_to(API))
        for path in API.rglob("*.py")
        if re.search(r"\.list_approvals\(", path.read_text(encoding="utf-8"))
    }
    assert callers - {"db/repository.py"} <= allowed, sorted(callers - allowed)


# ----------------------------------------------------------------------------- the two plain lists


@pytest.fixture
def client() -> Iterator[TestClient]:
    saved = (dict(store.tasks), dict(store.approvals), list(store.events))
    store.tasks.clear()
    store.approvals.clear()
    store.events.clear()
    yield TestClient(app)
    store.tasks.clear()
    store.approvals.clear()
    store.events.clear()
    store.tasks.update(saved[0])
    store.approvals.update(saved[1])
    store.events.extend(saved[2])


def _seed_approvals(decided: int, pending: int) -> None:
    repository = InMemoryRepository(store)
    repository.create_task(_task("task_list"))
    now = datetime.now(UTC)
    for index in range(decided):
        created = now - timedelta(days=30, minutes=-index)
        repository.save_approval(Approval(id=f"decided_{index:04d}", task_id="task_list", action="send_email", risk_level=RiskLevel.HIGH,
                                          status=ApprovalStatus.APPROVED, reason="r", created_at=created, decided_at=created))
    for index in range(pending):
        repository.save_approval(Approval(id=f"waiting_{index:02d}", task_id="task_list", action="send_email", risk_level=RiskLevel.HIGH,
                                          status=ApprovalStatus.PENDING, reason="r", created_at=now - timedelta(days=60, minutes=-index),
                                          expires_at=now + timedelta(hours=1)))


def test_the_plain_approvals_list_is_bounded_keeps_its_order_and_never_drops_a_pending_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_approvals(decided=520, pending=3)

    everything = client.get("/api/v1/approvals", headers=HEADERS)
    assert everything.status_code == 200, everything.text
    rows = everything.json()
    assert len(rows) == 500  # it used to be all 523
    # Oldest first, the original shape, for the web org chart and the Android client.
    assert [row["created_at"] for row in rows] == sorted(row["created_at"] for row in rows)
    # Every pending approval is there although they are the oldest rows; the oldest decided ones are not.
    assert [row["id"] for row in rows[:3]] == ["waiting_00", "waiting_01", "waiting_02"]
    assert "decided_0000" not in {row["id"] for row in rows} and rows[-1]["id"] == "decided_0519"

    # No routing rule matches here, so the list reads no task's approvals one task at a time.
    sibling_reads: list[str] = []
    real = InMemoryRepository.list_task_approvals
    monkeypatch.setattr(
        InMemoryRepository, "list_task_approvals",
        lambda self, task_id, context, **more: sibling_reads.append(task_id) or real(self, task_id, context, **more),
    )
    assert len(client.get("/api/v1/approvals", headers=HEADERS).json()) == 500
    assert sibling_reads == []

    # The narrowed forms are unchanged: pending first and newest first, limit 1 to 500.
    shell = client.get("/api/v1/approvals", headers=HEADERS, params={"status": "pending", "limit": 200}).json()
    assert [row["id"] for row in shell] == ["waiting_02", "waiting_01", "waiting_00"]
    assert len(client.get("/api/v1/approvals", headers=HEADERS, params={"limit": 5}).json()) == 5
    assert client.get("/api/v1/approvals", headers=HEADERS, params={"limit": 501}).status_code == 422
    assert client.get("/api/v1/approvals", headers=HEADERS, params={"limit": 0}).status_code == 422


def test_the_plain_events_list_returns_the_newest_events_oldest_first_within_a_limit(client: TestClient) -> None:
    for index in range(EVENT_LIST_DEFAULT_LIMIT + 20):
        store.events.append(create_event(CanonicalEventName.TASK_CREATED, CONTEXT, f"task_{index}", {"task_id": f"task_{index}"},
                                         event_id=f"event_{index:04d}", created_at=NOW + timedelta(seconds=index)).event)

    rows = client.get("/api/v1/events", headers=HEADERS).json()
    assert len(rows) == EVENT_LIST_DEFAULT_LIMIT == 500
    assert rows[0]["id"] == "event_0020" and rows[-1]["id"] == "event_0519"
    assert [row["id"] for row in client.get("/api/v1/events", headers=HEADERS, params={"limit": 2}).json()] == ["event_0518", "event_0519"]
    assert len(client.get("/api/v1/events", headers=HEADERS, params={"limit": 1000}).json()) == 520
    assert client.get("/api/v1/events", headers=HEADERS, params={"limit": 1001}).status_code == 422
    assert client.get("/api/v1/events", headers=HEADERS, params={"limit": 0}).status_code == 422
