"""An approved action's tool runs with no transaction open (docs/realtime.md#event-loop).

``POST /approvals/{id}/approve`` (and ``POST /agent-runs/{id}/resume``) used to run the tool
inside the request's transaction, holding the task's and the approval's ``FOR UPDATE`` row
locks while the connected service answered. Now the decision commits in one short unit of
work, the tool runs with none open, and the outcome is written in a new unit that re-checks
the claim. The payload-hash binding, single use, expiry and audit stay exactly as they were.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_tools import ToolCall, ToolPolicyDecision, ToolPolicyOutcome, ToolResult
from anum_api.dependencies import _membership_unit_of_work, units_of_work
from anum_api.repository import AnumRepository
from anum_api.schemas import ApprovalStatus, RiskLevel, RunPhase, TaskStatus, TenantContext, utc_now
from anum_api.store import store

HEADERS = {
    "x-tenant-id": "tenant_offloop",
    "x-workspace-id": "workspace_offloop",
    "x-user-id": "owner_offloop",
    "x-user-roles": "owner",
}
CONTEXT = TenantContext(tenant_id="tenant_offloop", workspace_id="workspace_offloop", user_id="owner_offloop", roles=["owner"])

client = TestClient(main.app, raise_server_exceptions=False)


class Units:
    """``units_of_work`` that counts the units open right now."""

    def __init__(self) -> None:
        self.open = 0
        self.opened = 0

    def __call__(self, context: TenantContext) -> Any:
        @contextmanager
        def unit() -> Iterator[AnumRepository]:
            self.open += 1
            self.opened += 1
            try:
                with _membership_unit_of_work(context) as repository:
                    yield repository
            finally:
                self.open -= 1

        return unit()


class Tool:
    """Stands in for the tool registry's ``execute``: records what was open while it ran."""

    def __init__(self, units: Units, original: Any) -> None:
        self.units = units
        self.original = original
        self.calls: list[ToolCall] = []
        self.open_while_running: list[int] = []
        self.approval_status_while_running: list[ApprovalStatus] = []
        self.during: Any = None
        self.error: Exception | None = None

    async def execute(self, call: ToolCall, context: TenantContext) -> ToolResult:
        self.calls.append(call)
        self.open_while_running.append(self.units.open)
        approvals = [item for item in store.approvals.values() if item.action == call.name]
        self.approval_status_while_running += [item.status for item in approvals]
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        return await self.original(call, context)


@pytest.fixture
def units() -> Iterator[Units]:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    counting = Units()
    main.app.dependency_overrides[units_of_work] = lambda: counting
    yield counting
    main.app.dependency_overrides.pop(units_of_work, None)


@pytest.fixture
def tool(units: Units, monkeypatch: pytest.MonkeyPatch) -> Tool:
    fake = Tool(units, main.tool_registry.execute)
    monkeypatch.setattr(main.tool_registry, "execute", fake.execute)
    return fake


def _waiting() -> dict[str, Any]:
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Risky", "prompt": "Publish the final update"}).json()
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["task"]["status"] == "waiting_approval"
    return body


def _approve(approval: dict[str, Any], payload_hash: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=HEADERS,
        json={"payload_hash": payload_hash if payload_hash is not None else approval["payload_hash"]},
    )


def _audit_actions() -> list[str]:
    return [record.action for record in store.audit_records]


def test_the_approved_tool_runs_with_no_unit_of_work_open(units: Units, tool: Tool) -> None:
    waiting = _waiting()
    approved = _approve(waiting["approval"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["approval"]["status"] == "approved"
    assert body["task"]["status"] == "completed"
    assert body["run"]["status"] == "completed"
    assert len(tool.calls) == 1
    # The decision committed before the tool ran, and nothing was open while it ran.
    assert tool.open_while_running == [0]
    assert tool.approval_status_while_running == [ApprovalStatus.APPROVED]
    assert "approval.approved" in _audit_actions()


def test_a_second_approve_is_refused_and_the_tool_runs_once(units: Units, tool: Tool) -> None:
    waiting = _waiting()
    assert _approve(waiting["approval"]).status_code == 200
    again = _approve(waiting["approval"])
    assert again.status_code == 409
    assert "already decided" in again.text
    assert len(tool.calls) == 1
    assert _audit_actions().count("approval.approved") == 1


def test_an_approve_during_the_tool_is_refused(units: Units, tool: Tool) -> None:
    """The approval is used once even while its tool still runs (no row lock is held then)."""
    waiting = _waiting()
    answers: list[int] = []

    def second_decider() -> None:
        with units(CONTEXT) as repository:
            current = repository.get_approval(waiting["approval"]["id"], CONTEXT)
            answers.append(0 if current is None else (409 if current.status != ApprovalStatus.PENDING else 200))

    tool.during = second_decider
    assert _approve(waiting["approval"]).status_code == 200
    assert answers == [409]
    assert len(tool.calls) == 1


def test_an_expired_approval_is_refused_and_the_tool_never_runs(units: Units, tool: Tool) -> None:
    waiting = _waiting()
    store.approvals[waiting["approval"]["id"]].expires_at = utc_now() - timedelta(seconds=1)
    refused = _approve(waiting["approval"])
    assert refused.status_code == 410
    assert tool.calls == []
    assert store.approvals[waiting["approval"]["id"]].status == ApprovalStatus.EXPIRED
    assert store.tasks[waiting["task"]["id"]].status == TaskStatus.FAILED
    assert "approval.expired" in _audit_actions()


def test_a_changed_payload_hash_is_refused_and_the_tool_never_runs(units: Units, tool: Tool) -> None:
    waiting = _waiting()
    refused = _approve(waiting["approval"], "0" * 64)
    assert refused.status_code == 409
    assert tool.calls == []
    assert store.approvals[waiting["approval"]["id"]].status == ApprovalStatus.PENDING


def test_a_tool_that_raises_fails_the_run_and_the_approval_stays_used(units: Units, tool: Tool) -> None:
    waiting = _waiting()
    tool.error = RuntimeError("the connected service broke")
    failed = _approve(waiting["approval"])
    assert failed.status_code == 500
    assert len(tool.calls) == 1
    assert tool.open_while_running == [0]
    run = store.runs[waiting["run"]["id"]]
    assert run.status == TaskStatus.FAILED
    assert store.tasks[waiting["task"]["id"]].status == TaskStatus.FAILED
    assert "may not have finished" in (run.steps[-1].summary if run.steps else "")
    # Single use: the action may have happened, so the approval cannot be approved again.
    assert store.approvals[waiting["approval"]["id"]].status == ApprovalStatus.APPROVED
    again = _approve(waiting["approval"])
    assert again.status_code == 409
    assert len(tool.calls) == 1


def test_a_cancel_while_the_tool_runs_is_kept(units: Units, tool: Tool) -> None:
    waiting = _waiting()

    def cancel() -> None:
        with units(CONTEXT) as repository:
            main.cancel_task_record(waiting["task"]["id"], CONTEXT, repository)

    tool.during = cancel
    answered = _approve(waiting["approval"])
    assert answered.status_code == 409
    assert "cancelled while its action ran" in answered.text
    assert store.tasks[waiting["task"]["id"]].status == TaskStatus.CANCELLED
    assert store.runs[waiting["run"]["id"]].checkpoint.phase == RunPhase.CANCELLED


def test_reject_runs_no_tool(units: Units, tool: Tool) -> None:
    waiting = _waiting()
    rejected = client.post(
        f"/api/v1/approvals/{waiting['approval']['id']}/reject", headers=HEADERS,
        json={"payload_hash": waiting["approval"]["payload_hash"], "reason": "Not now"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["task"]["status"] == "failed"
    assert tool.calls == []
    assert "approval.rejected" in _audit_actions()


def test_resume_runs_the_tool_with_no_unit_of_work_open(units: Units, tool: Tool, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting = _waiting()
    run = store.runs[waiting["run"]["id"]]
    task = store.tasks[waiting["task"]["id"]]
    # A run stranded at an executable checkpoint that policy now allows on its own (the
    # process stopped before executing): resume picks it up.
    run.status = task.status = TaskStatus.RUNNING
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.approval_id = None
    store.approvals.clear()
    allow = ToolPolicyDecision(outcome=ToolPolicyOutcome.ALLOW, reason="Allowed in this test.", risk_level=RiskLevel.LOW)
    monkeypatch.setattr(main.AgentRuntime, "evaluate", lambda self, *args, **kwargs: allow)
    resumed = client.post(f"/api/v1/agent-runs/{run.id}/resume", headers=HEADERS)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run"]["status"] == "completed"
    assert len(tool.calls) == 1
    assert tool.open_while_running == [0]
    assert "agent_run.resumed" in [event.type for event in store.events]
    # Resumed once: the run is no longer at an executable checkpoint.
    assert client.post(f"/api/v1/agent-runs/{run.id}/resume", headers=HEADERS).status_code == 409
    assert len(tool.calls) == 1


def test_the_routes_this_change_fixed_stay_off_the_baseline() -> None:
    """Approvals, run resume and the routes moved with them never go back on the frozen list."""
    from pathlib import Path

    baseline = (Path(__file__).with_name("request_unit_across_await_baseline.txt")).read_text()
    for route in (
        "/api/v1/approvals/", "/api/v1/agent-runs/", "/api/v1/mail/", "/api/v1/marketing/", "/api/v1/developer/",
        "/api/v1/tyre/", "/api/v1/whatsapp/", "/request-whatsapp-delivery", "/request-approval", "/share/whatsapp",
        "/api/v1/reports/{report_id}/files", "/api/v1/integrations/setup/", "/api/v1/tasks/",
    ):
        assert route not in baseline, route
