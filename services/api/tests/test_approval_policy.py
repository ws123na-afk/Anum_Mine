"""Approval decision reasons (A4), the integration target on approvals (G2), the
optional two-person rule (A6) and the optional medium-risk approval policy (A5).

See docs/approvals-and-risk.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_tools import (
    ToolCall,
    ToolDefinition,
    ToolPolicy,
    ToolPolicyOutcome,
    ToolRegistry,
    ToolResult,
    default_tool_registry,
)
from anum_api.integration_tools import RestToolAdapter
from anum_api.model_gateway import MockModelGateway
from anum_api.repository import InMemoryRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import (
    DECISION_REASON_MAX_CHARS,
    RiskLevel,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    WorkspaceApprovalPolicy,
    new_id,
    utc_now,
)
from anum_api.store import InMemoryStore, store

TENANT = "tenant_policy"
WORKSPACE = "workspace_policy"


def headers(user: str = "owner_a", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


OWNER_A = headers("owner_a")
OWNER_B = headers("owner_b")
MEMBER = headers("member_c", "member")
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_a", roles=["owner"])

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_store() -> Iterator[None]:
    for collection in (
        store.tasks,
        store.runs,
        store.approvals,
        store.events,
        store.audit_records,
        store.approval_policies,
    ):
        collection.clear()
    yield
    store.approval_policies.clear()


def _waiting(creator: dict[str, str] = OWNER_A, runner: dict[str, str] | None = None) -> dict:
    task = client.post(
        "/api/v1/tasks", headers=creator, json={"title": "Risky", "prompt": "Publish the final update"}
    ).json()
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=runner or creator)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["task"]["status"] == "waiting_approval"
    return body


def _decide(approval: dict, decision: str, who: dict[str, str], **extra) -> httpx.Response:
    body = {"payload_hash": approval["payload_hash"], **extra}
    return client.post(f"/api/v1/approvals/{approval['id']}/{decision}", headers=who, json=body)


def _audits(action: str) -> list:
    return [record for record in store.audit_records if record.action == action]


def _set_policy(who: dict[str, str] = OWNER_A, **values: bool) -> httpx.Response:
    body = {"two_person_rule": False, "medium_risk_requires_approval": False, **values}
    return client.put("/api/v1/approval-policy", headers=who, json=body)


# A4: decision reason -------------------------------------------------------------------


def test_approve_with_a_reason_stores_audits_and_emits_it() -> None:
    approval = _waiting()["approval"]
    response = _decide(approval, "approve", OWNER_A, reason="  Checked the recipients with legal.  ")
    assert response.status_code == 200, response.text
    decided = response.json()["approval"]
    assert decided["decision_reason"] == "Checked the recipients with legal."
    assert decided["decided_by"] == "owner_a"
    listed = client.get(f"/api/v1/approvals/{approval['id']}", headers=OWNER_A).json()
    assert listed["decision_reason"] == "Checked the recipients with legal."
    (audit,) = _audits("approval.approved")
    assert audit.metadata["reason"] == "Checked the recipients with legal."
    event = next(event for event in store.events if event.type == "approval.approved")
    assert event.payload["reason"] == "Checked the recipients with legal."


def test_reject_may_send_only_a_reason() -> None:
    approval = _waiting()["approval"]
    response = client.post(
        f"/api/v1/approvals/{approval['id']}/reject", headers=OWNER_A, json={"reason": "Wrong audience"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["approval"]["status"] == "rejected"
    assert response.json()["approval"]["decision_reason"] == "Wrong audience"
    assert _audits("approval.rejected")[0].metadata["reason"] == "Wrong audience"


def test_reason_is_optional_and_blank_means_none() -> None:
    approval = _waiting()["approval"]
    response = _decide(approval, "approve", OWNER_A, reason="   ")
    assert response.status_code == 200
    assert response.json()["approval"]["decision_reason"] is None
    event = next(event for event in store.events if event.type == "approval.approved")
    assert "reason" not in event.payload


@pytest.mark.parametrize(
    "reason",
    ["x" * (DECISION_REASON_MAX_CHARS + 1), "ok\rINFO forged log line", "bell\x07", "nul\x00"],
)
def test_overlong_or_control_character_reasons_are_refused(reason: str) -> None:
    approval = _waiting()["approval"]
    response = _decide(approval, "approve", OWNER_A, reason=reason)
    assert response.status_code == 422
    assert client.get(f"/api/v1/approvals/{approval['id']}", headers=OWNER_A).json()["status"] == "pending"


def test_multiline_reasons_are_kept() -> None:
    approval = _waiting()["approval"]
    response = _decide(approval, "reject", OWNER_A, reason="Two things:\r\n1. wrong list\n2. typo")
    assert response.json()["approval"]["decision_reason"] == "Two things:\n1. wrong list\n2. typo"


# A6: two-person rule -------------------------------------------------------------------


def test_policy_defaults_to_off_and_any_member_can_read_it() -> None:
    response = client.get("/api/v1/approval-policy", headers=MEMBER)
    assert response.status_code == 200
    assert response.json()["two_person_rule"] is False
    assert response.json()["medium_risk_requires_approval"] is False


def test_only_owners_change_the_policy_and_changes_are_audited() -> None:
    assert _set_policy(MEMBER, two_person_rule=True).status_code == 403
    assert client.get("/api/v1/approval-policy", headers=OWNER_A).json()["two_person_rule"] is False

    response = _set_policy(OWNER_A, two_person_rule=True)
    assert response.status_code == 200
    assert response.json()["two_person_rule"] is True
    assert response.json()["updated_by"] == "owner_a"
    (audit,) = _audits("approval_policy.updated")
    assert audit.actor == "owner_a"
    assert audit.metadata["before"]["two_person_rule"] is False
    assert audit.metadata["after"]["two_person_rule"] is True


def test_policy_is_scoped_to_its_workspace() -> None:
    _set_policy(OWNER_A, two_person_rule=True)
    other = {**OWNER_A, "x-workspace-id": "workspace_policy_other"}
    assert client.get("/api/v1/approval-policy", headers=other).json()["two_person_rule"] is False


def test_without_the_rule_the_creator_can_approve_their_own_task() -> None:
    approval = _waiting()["approval"]
    assert _decide(approval, "approve", OWNER_A).status_code == 200


def test_with_the_rule_the_creator_cannot_approve_but_another_owner_can() -> None:
    _set_policy(OWNER_A, two_person_rule=True)
    body = _waiting()
    approval = body["approval"]
    assert approval["requested_by"] == "owner_a"

    refused = _decide(approval, "approve", OWNER_A)
    assert refused.status_code == 403
    error = refused.json()["error"]
    assert error["code"] == "forbidden"
    assert "another owner must approve" in error["message"]
    assert client.get(f"/api/v1/approvals/{approval['id']}", headers=OWNER_A).json()["status"] == "pending"
    (denied,) = _audits("approval.self_approval_denied")
    assert denied.actor == "owner_a" and denied.outcome == "denied"

    approved = _decide(approval, "approve", OWNER_B, reason="Second pair of eyes")
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["decided_by"] == "owner_b"
    assert approved.json()["task"]["status"] == "completed"


def test_with_the_rule_the_creator_can_still_reject() -> None:
    _set_policy(OWNER_A, two_person_rule=True)
    approval = _waiting()["approval"]
    response = _decide(approval, "reject", OWNER_A, reason="Changed my mind")
    assert response.status_code == 200
    assert response.json()["approval"]["status"] == "rejected"


def test_with_the_rule_whoever_started_the_run_cannot_approve_either() -> None:
    _set_policy(OWNER_A, two_person_rule=True)
    approval = _waiting(creator=MEMBER, runner=OWNER_B)["approval"]
    assert approval["requested_by"] == "owner_b"
    assert _decide(approval, "approve", OWNER_B).status_code == 403
    assert _decide(approval, "approve", OWNER_A).status_code == 200


def test_tasks_record_their_creator() -> None:
    task = client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "hello"}).json()
    assert task["created_by"] == "member_c"


# G2: the integration target on the approval -------------------------------------------


@pytest.fixture
def webhook_registry(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    adapter = RestToolAdapter(
        endpoint="https://user:pw@hooks.example:8443/send?token=abc",
        allowed_hosts={"hooks.example"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(main, "tool_registry", default_tool_registry(adapter))
    return sent


def test_approval_shows_the_configured_target_host_only(webhook_registry: list[httpx.Request]) -> None:
    body = _waiting()
    approval = body["approval"]
    assert approval["target"] == "hooks.example"
    requested = next(event for event in store.events if event.type == "approval.requested")
    assert requested.payload["target"] == "hooks.example"
    assert requested.payload["risk_level"] == "high"

    assert _decide(approval, "approve", OWNER_A).status_code == 200
    assert len(webhook_registry) == 1
    assert _audits("approval.approved")[0].metadata["target"] == "hooks.example"


def test_internal_tools_have_no_target() -> None:
    assert _waiting()["approval"]["target"] is None


def test_a_changed_target_is_never_executed(
    webhook_registry: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = _waiting()["approval"]
    moved = RestToolAdapter(
        endpoint="https://elsewhere.example/send",
        allowed_hosts={"elsewhere.example"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))),
    )
    monkeypatch.setattr(main, "tool_registry", default_tool_registry(moved))

    response = _decide(approval, "approve", OWNER_A)
    assert response.status_code == 200
    assert response.json()["task"]["status"] == "failed"
    assert "integration target changed" in response.json()["run"]["steps"][-1]["summary"]
    (mismatch,) = _audits("approval.target_mismatch")
    assert mismatch.metadata["approved_target"] == "hooks.example"
    assert mismatch.metadata["current_target"] == "elsewhere.example"
    assert webhook_registry == []


# A5: medium risk -----------------------------------------------------------------------


async def _record(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(status="succeeded", summary=f"ran {call.name}")


def _medium_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="anum.respond", description="durable note", risk_level=RiskLevel.MEDIUM),
        _record,
    )
    registry.register(
        ToolDefinition(name="external.action", description="x", risk_level=RiskLevel.HIGH),
        _record,
    )
    return registry


def _medium_run(policy: WorkspaceApprovalPolicy):
    repository = InMemoryRepository(InMemoryStore())
    repository.save_approval_policy(policy, CONTEXT)
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_medium_registry())
    now = utc_now()
    task = Task(
        id=new_id("task"), title="t", prompt="Write a note", status=TaskStatus.CREATED,
        tenant_id=TENANT, workspace_id=WORKSPACE, created_at=now, updated_at=now, created_by="owner_a",
    )
    repository.create_task(task)
    return repository, runtime, task, asyncio.run(runtime.run_task(task, CONTEXT))


def test_medium_risk_runs_without_approval_by_default() -> None:
    _, _, task, (run, approval) = _medium_run(WorkspaceApprovalPolicy())
    assert approval is None
    assert run.status == TaskStatus.COMPLETED


def test_medium_risk_pauses_when_the_workspace_requires_it() -> None:
    repository, runtime, task, (run, approval) = _medium_run(
        WorkspaceApprovalPolicy(medium_risk_requires_approval=True)
    )
    assert approval is not None
    assert approval.risk_level == RiskLevel.MEDIUM
    assert run.checkpoint.phase == RunPhase.WAITING_APPROVAL
    assert "requires approval for medium-risk" in approval.reason

    approval.status = approval.status.APPROVED
    approval.decided_at = utc_now()
    approval.decided_by = "owner_a"
    asyncio.run(runtime.resume_after_approval(task, run, approval, CONTEXT))
    assert run.status == TaskStatus.COMPLETED


def test_policy_object_only_escalates_medium() -> None:
    registry = _medium_registry()
    policy = ToolPolicy(registry.names)
    strict = WorkspaceApprovalPolicy(medium_risk_requires_approval=True)
    medium = ToolCall(name="anum.respond")
    assert policy.evaluate(medium, registry.definition("anum.respond"), CONTEXT).outcome == ToolPolicyOutcome.ALLOW
    assert (
        policy.evaluate(medium, registry.definition("anum.respond"), CONTEXT, strict).outcome
        == ToolPolicyOutcome.REQUIRE_APPROVAL
    )
    low = default_tool_registry()
    assert (
        ToolPolicy(low.names).evaluate(medium, low.definition("anum.respond"), CONTEXT, strict).outcome
        == ToolPolicyOutcome.ALLOW
    )


def test_two_person_rule_does_not_apply_to_medium_risk_approvals() -> None:
    assert RiskLevel.MEDIUM not in main.TWO_PERSON_RISK_LEVELS
    assert RiskLevel.HIGH in main.TWO_PERSON_RISK_LEVELS
