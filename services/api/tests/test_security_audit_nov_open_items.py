"""The four open items of the November 2026 security audit (docs/pending-work.md), memory backend.

1. A data-private task's approval of a call that touches only its person's own data is decided by
   that person after an explicit confirmation; anything reaching further is refused with "Run this
   as a shared task so a second person can approve it." (docs/approvals-and-risk.md#private-task-self-approval).
2. The emergency stop cancels other people's private tasks too, and the person who pulled it learns a
   count only (docs/agent-controls.md#emergency-stop).
3. A mailbox call naming another person's mailbox in ``owner_user_id`` is refused when planned,
   when its approval is decided and when it executes (docs/tasks.md#acting-for-someone-else).
4. An approved call runs as the person who asked, with their permissions, on the inline path as on
   the durable one (docs/approvals-and-risk.md#who-the-action-runs-as).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import agent_controls, main
from anum_api.agent_tools import (
    ACTS_FOR_SOMEONE_ELSE,
    ToolCall,
    ToolDefinition,
    ToolPolicyOutcome,
    ToolRegistry,
    ToolResult,
)
from anum_api.dependencies import memory_repository
from anum_api.durable_runs import AgentRunActivities, context_for, run_input_for
from anum_api.email_api import _pause_for_approval
from anum_api.mailbox import ai as mail_ai
from anum_api.mailbox import automation_tools, service
from anum_api.mailbox.service import mail_tool_registry
from anum_api.model_gateway import MockModelGateway
from anum_api.requester_identity import REQUESTER_GONE
from anum_api.runtime import AgentRuntime
from anum_api.schemas import RiskLevel, Task, TaskStatus, TenantContext, WorkspaceMembership, new_id, utc_now
from anum_api.store import store

TENANT = "tenant_secnov"
WORKSPACE = "workspace_secnov"
client = TestClient(main.app)

MAIL_TOOLS = (
    service.SEND_TOOL, service.DELETE_TOOL, mail_ai.ARCHIVE_TOOL, mail_ai.EXPORT_TOOL,
    automation_tools.SETTINGS_TOOL, automation_tools.RULE_TOOL,
)


def _context(user: str, role: str = "owner") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=[role])


def _headers(user: str, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


ALICE, OMAR, MONA = _headers("alice"), _headers("omar"), _headers("mona", "member")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records, store.approval_policies):
        collection.clear()
    for key in [key for key in store.memberships if key[0] == TENANT]:
        store.memberships.pop(key)
    monkeypatch.setattr(main, "run_dispatcher", None)
    yield
    for key in [key for key in store.memberships if key[0] == TENANT]:
        store.memberships.pop(key)
    store.approval_policies.clear()


def _recording_registry(seen: list[tuple[str, str, list[str]]]) -> ToolRegistry:
    """The mail tools' definitions and a probe tool, each recording who it ran as."""
    registry = ToolRegistry()
    real = mail_tool_registry()

    def recorder(name: str) -> Any:
        async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
            seen.append((name, context.user_id, list(context.roles)))
            return ToolResult(status="succeeded", summary=f"{name} done", output={})

        return handler

    for name in MAIL_TOOLS:
        definition = real.definition(name)
        assert definition is not None
        registry.register(definition, recorder(name))
    registry.register(
        ToolDefinition(name="probe.act", description="A high-risk probe.", risk_level=RiskLevel.HIGH,
                       required_roles=frozenset({"owner", "member"}), category="general", action_kind="other"),
        recorder("probe.act"),
    )
    return registry


def _waiting(context: TenantContext, call: ToolCall, registry: ToolRegistry, *, private: bool = False) -> tuple[Task, Any]:
    """A task whose run waits for an approval of ``call``, proposed by ``context``'s person."""
    task, approval = _pause_for_approval(memory_repository, context, call, "Probe", "Probe the action.", registry=registry)
    if private:
        task = store.tasks[task.id]
        task.private_to, task.private_kind = context.user_id, "data"
    return task, approval


def _approve(approval: Any, who: dict[str, str], **extra: Any) -> Any:
    return client.post(f"/api/v1/approvals/{approval.id}/approve", headers=who, json={"payload_hash": approval.payload_hash, **extra})


def _member(user: str, role: str, *, active: bool = True) -> None:
    now = utc_now()
    store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=active, created_at=now, updated_at=now,
    )


# ----------------------------------------------------------------------------- 3: another person's mailbox


@pytest.mark.parametrize("tool", MAIL_TOOLS)
def test_a_planned_mail_call_naming_another_persons_mailbox_is_blocked(tool: str) -> None:
    runtime = AgentRuntime(MockModelGateway(), memory_repository, tools=mail_tool_registry())
    mona = _context("mona", "member")
    theirs = runtime.evaluate(ToolCall(name=tool, arguments={"account_id": "mbx_1", "owner_user_id": "alice"}), mona)
    assert theirs.outcome == ToolPolicyOutcome.BLOCK and theirs.reason == ACTS_FOR_SOMEONE_ELSE
    own = runtime.evaluate(ToolCall(name=tool, arguments={"account_id": "mbx_1", "owner_user_id": "mona"}), mona)
    assert own.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL


@pytest.mark.parametrize("tool", MAIL_TOOLS)
def test_every_mail_handler_refuses_another_persons_mailbox_when_it_runs(tool: str) -> None:
    registry = mail_tool_registry()
    call = ToolCall(name=tool, arguments={"account_id": "mbx_1", "owner_user_id": "alice", "uids": [1], "folder": "INBOX"})
    mona = _context("mona", "member")
    # The registry refuses before the handler, and the handler refuses on its own as well.
    through_registry = asyncio.run(registry.execute(call, mona))
    assert through_registry.status == "blocked" and through_registry.summary == ACTS_FOR_SOMEONE_ELSE
    handler = registry.handler(tool)
    assert handler is not None
    direct = asyncio.run(handler(call, mona))
    assert direct.status == "failed" and ACTS_FOR_SOMEONE_ELSE in direct.summary


def test_an_approval_naming_another_persons_mailbox_is_not_run_after_an_owner_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    # Stored as it was before the plan-time check (an approval created by an older release).
    task, approval = _waiting(_context("mona", "member"), ToolCall(name="probe.act", arguments={}), registry)
    run = store.runs[approval.run_id]
    call = {"name": mail_ai.ARCHIVE_TOOL, "arguments": {"account_id": "mbx_1", "owner_user_id": "alice", "folder": "INBOX", "uids": [1]}}
    run.checkpoint.tool_call = call
    approval = approval.model_copy(update={"action": mail_ai.ARCHIVE_TOOL, "arguments": call["arguments"],
                                           "payload_hash": AgentRuntime(MockModelGateway(), memory_repository, tools=registry).checkpoint_payload_hash(task, run)})
    store.approvals[approval.id] = approval
    decided = _approve(approval, ALICE)
    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["status"] == "failed"
    assert seen == []  # alice's mailbox was never touched


# ----------------------------------------------------------------------------- 4: who the call runs as


def test_an_approved_call_runs_as_the_requester_not_the_approver_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    _, approval = _waiting(_context("mona", "member"), ToolCall(name="probe.act", arguments={}), registry)
    decided = _approve(approval, ALICE)
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval"]["decided_by"] == "alice"
    assert decided.json()["run"]["status"] == "completed"
    assert seen == [("probe.act", "mona", ["member"])]


def test_the_requesters_current_role_decides_not_the_approvers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    _, approval = _waiting(_context("mona", "member"), ToolCall(name="probe.act", arguments={}), registry)
    _member("mona", "viewer")  # mona became a viewer while the approval waited
    decided = _approve(approval, ALICE)
    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["status"] == "failed"
    assert seen == []


def test_a_requester_who_left_the_workspace_is_never_stood_in_for(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    _, approval = _waiting(_context("mona", "member"), ToolCall(name="probe.act", arguments={}), registry)
    _member("mona", "member", active=False)
    decided = _approve(approval, ALICE)
    assert decided.status_code == 200, decided.text
    run = decided.json()["run"]
    assert run["status"] == "failed" and run["steps"][-1]["summary"] == REQUESTER_GONE
    assert seen == []


def test_the_durable_path_runs_the_approved_call_as_the_requester_too(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)

    class Signals:
        async def approval_decided(self, context: TenantContext, task_id: str, approval_id: str) -> bool:
            return True

    mona = _context("mona", "member")
    task, approval = _waiting(mona, ToolCall(name="probe.act", arguments={}), registry)
    monkeypatch.setattr(main, "run_dispatcher", Signals())
    decided = _approve(approval, ALICE)
    assert decided.status_code == 200 and decided.json()["run"]["status"] == "waiting_approval", decided.text
    assert seen == []  # the worker, not the API, runs it
    activities = AgentRunActivities(lambda context, repository: AgentRuntime(MockModelGateway(), repository, tools=registry))
    request = run_input_for(mona, task.id, approval.run_id)  # the workflow carries whoever started the run
    state = asyncio.run(activities.advance(context_for(request), request))
    assert (state.phase, state.status) == ("completed", "completed")
    assert seen == [("probe.act", "mona", ["member"])]


# ----------------------------------------------------------------------------- 1: private task self-approval


def test_own_data_on_a_private_task_is_self_approved_after_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    assert client.put("/api/v1/approval-policy", headers=ALICE, json={"two_person_rule": True, "medium_risk_requires_approval": False}).status_code == 200
    call = ToolCall(name=mail_ai.ARCHIVE_TOOL, arguments={"account_id": "mbx_1", "owner_user_id": "alice", "folder": "INBOX", "uids": [1, 2]})
    _, approval = _waiting(_context("alice"), call, registry, private=True)

    listed = next(item for item in client.get("/api/v1/approvals", headers=ALICE).json() if item["id"] == approval.id)
    assert listed["routing"]["can_approve"] is True and listed["routing"]["confirm_own"] is True
    unconfirmed = _approve(approval, ALICE)
    assert unconfirmed.status_code == 428 and "self-approval under the private-task exception" in unconfirmed.text
    assert seen == []
    confirmed = _approve(approval, ALICE, confirm_own_request=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["run"]["status"] == "completed"
    assert seen == [(mail_ai.ARCHIVE_TOOL, "alice", ["owner"])]
    (audit,) = [row for row in store.audit_records if row.action == "approval.approved" and row.target == approval.id]
    assert audit.metadata["private_self_decision"] is True and audit.metadata["private_exception"] == "own_data"
    assert audit.metadata["confirmed"] is True
    # Nobody else could have decided it: they do not see it.
    assert client.get(f"/api/v1/approvals/{approval.id}", headers=OMAR).status_code == 404


def test_a_member_confirms_their_own_data_action_on_their_private_task(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    call = ToolCall(name=mail_ai.EXPORT_TOOL, arguments={"account_id": "mbx_1", "owner_user_id": "mona", "folder": "INBOX", "uids": [3]})
    _, approval = _waiting(_context("mona", "member"), call, registry, private=True)
    assert _approve(approval, MONA).status_code == 428
    confirmed = _approve(approval, MONA, confirm_own_request=True)
    assert confirmed.status_code == 200 and confirmed.json()["run"]["status"] == "completed", confirmed.text
    assert seen == [(mail_ai.EXPORT_TOOL, "mona", ["member"])]


@pytest.mark.parametrize("tool", [service.SEND_TOOL, automation_tools.RULE_TOOL, "probe.act"])
def test_a_private_task_reaching_beyond_its_persons_data_must_be_run_as_shared(tool: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, list[str]]] = []
    registry = _recording_registry(seen)
    monkeypatch.setattr(main, "tool_registry", registry)
    assert client.put("/api/v1/approval-policy", headers=ALICE, json={"two_person_rule": True, "medium_risk_requires_approval": False}).status_code == 200
    arguments = {"account_id": "mbx_1", "owner_user_id": "alice"} if tool != "probe.act" else {}
    _, approval = _waiting(_context("alice"), ToolCall(name=tool, arguments=arguments), registry, private=True)
    listed = next(item for item in client.get("/api/v1/approvals", headers=ALICE).json() if item["id"] == approval.id)
    assert listed["routing"]["can_approve"] is False and listed["routing"]["refusal"] == "run_as_shared"
    assert listed["routing"]["summary"] == main.RUN_AS_SHARED
    for extra in ({}, {"confirm_own_request": True}):
        refused = _approve(approval, ALICE, **extra)
        assert refused.status_code == 403 and main.RUN_AS_SHARED in refused.text
    assert seen == []
    assert client.post(f"/api/v1/approvals/{approval.id}/reject", headers=ALICE, json={}).status_code == 200


# ----------------------------------------------------------------------------- 2: the emergency stop


def test_the_emergency_stop_cancels_other_peoples_private_tasks_and_reports_a_count_only() -> None:
    now = utc_now()
    _member("alice", "owner")
    _member("mona", "member")
    _member("gone", "member", active=False)
    secrets = {"mona": "Mona's salary negotiation", "gone": "Gone's medical leave"}
    ids: dict[str, str] = {}
    for owner, title in (*secrets.items(), ("alice", "Alice's own private task"), (None, "Shared work")):
        task = Task(
            id=new_id("task"), tenant_id=TENANT, workspace_id=WORKSPACE, title=title, prompt=title, status=TaskStatus.RUNNING,
            created_at=now, updated_at=now, created_by=owner or "omar", private_to=owner, private_kind="data" if owner else None,
        )
        store.tasks[task.id] = task
        ids[title] = task.id
    # Before: alice cannot see the others' private tasks at all.
    assert {item["id"] for item in client.get("/api/v1/tasks", headers=ALICE).json()} == {ids["Alice's own private task"], ids["Shared work"]}

    engaged = client.put("/api/v1/agent-controls/emergency-stop", headers=ALICE, json={"engaged": True, "reason": "Stop everything"})
    assert engaged.status_code == 200, engaged.text
    assert engaged.json()["cancelled_tasks"] == 4
    assert all(store.tasks[task_id].status == TaskStatus.CANCELLED for task_id in ids.values())
    for secret in secrets.values():
        assert secret not in engaged.text
    (audit,) = [row for row in store.audit_records if row.action == "agent_controls.emergency_stop.cancelled_tasks"]
    assert audit.metadata["tasks"] == 4 and audit.metadata["private_tasks_of_others"] == 2
    assert not any(secret in str(row.metadata) for row in store.audit_records for secret in secrets.values())
    assert not any(ids[secret] in str(row.metadata) for row in store.audit_records for secret in secrets.values())
    # Still hidden from alice afterwards; each person sees their own task cancelled.
    assert client.get(f"/api/v1/tasks/{ids[secrets['mona']]}", headers=ALICE).status_code == 404
    assert client.get(f"/api/v1/tasks/{ids[secrets['mona']]}", headers=MONA).json()["status"] == "cancelled"
    agent_controls.memory_agent_controls_store.clear()
