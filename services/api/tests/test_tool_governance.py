"""Governance approval rules and policy packs in the runtime's tool policy (threat model A4).

See docs/approvals-and-risk.md#organization-approval-rules-and-policy-packs.
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
)
from anum_api.governance import governance_store
from anum_api.model_gateway import MockModelGateway
from anum_api.repository import InMemoryRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import (
    AgentRun,
    RiskLevel,
    RunCheckpoint,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from anum_api.store import InMemoryStore, store
from anum_api.tool_governance import (
    GovernanceApprovalRule,
    GovernancePolicyRule,
    ToolGovernance,
    decision_requirements,
    match_governance,
    pattern_matches,
    required_approvals,
)

TENANT = "tenant_govern"
WORKSPACE = "workspace_govern"
OTHER_TENANT = "tenant_govern_other"


def headers(user: str = "owner_a", role: str = "owner", tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


OWNER_A = headers("owner_a")
OWNER_B = headers("owner_b")
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_a", roles=["owner"])

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    governance_store.clear()
    for collection in (
        store.tasks, store.runs, store.approvals, store.events, store.audit_records, store.approval_approvers
    ):
        collection.clear()
    yield
    governance_store.clear()


def _approval_rule(pattern: str, *, who: dict[str, str] = OWNER_A, **extra: object) -> httpx.Response:
    body = {"name": f"rule {pattern}", "action_pattern": pattern, **extra}
    response = client.post("/api/v1/organization/approval-rules", headers=who, json=body)
    assert response.status_code == 201, response.text
    return response


def _policy_pack(*rules: dict, name: str = "Tools", who: dict[str, str] = OWNER_A) -> dict:
    response = client.post(
        "/api/v1/policy-packs", headers=who, json={"name": name, "description": "", "rules": list(rules)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run(prompt: str, who: dict[str, str] = OWNER_A) -> dict:
    task = client.post("/api/v1/tasks", headers=who, json={"title": "t", "prompt": prompt})
    assert task.status_code == 201, task.text
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=who)
    assert started.status_code == 200, started.text
    return started.json()


def _decide(approval: dict, who: dict[str, str], decision: str = "approve") -> httpx.Response:
    return client.post(
        f"/api/v1/approvals/{approval['id']}/{decision}",
        headers=who,
        json={"payload_hash": approval["payload_hash"]},
    )


def _audits(action: str) -> list:
    return [record for record in store.audit_records if record.action == action]


# Matching ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "tool", "target", "risk", "expected"),
    [
        ("anum.respond", "anum.respond", None, RiskLevel.LOW, True),
        ("ANUM.*", "anum.respond", None, RiskLevel.LOW, True),
        ("tool:external.*", "anum.respond", None, RiskLevel.LOW, False),
        ("tool:external.*", "external.action", "hooks.example", RiskLevel.HIGH, True),
        ("target:*.example", "external.action", "hooks.example", RiskLevel.HIGH, True),
        ("integration:hooks.example", "external.action", "hooks.example", RiskLevel.HIGH, True),
        ("target:*", "anum.respond", None, RiskLevel.LOW, False),
        ("risk:medium", "anum.respond", None, RiskLevel.LOW, False),
        ("risk:medium", "anum.respond", None, RiskLevel.MEDIUM, True),
        ("risk:medium", "external.action", None, RiskLevel.HIGH, True),
        ("risk:low", "anum.respond", None, RiskLevel.LOW, True),
        ("risk:severe", "anum.respond", None, RiskLevel.LOW, True),  # unknown level: fail closed
        ("finance.*", "anum.respond", None, RiskLevel.LOW, False),
    ],
)
def test_pattern_matching(pattern: str, tool: str, target: str | None, risk: RiskLevel, expected: bool) -> None:
    assert pattern_matches(pattern, tool=tool, target=target, risk_level=risk) is expected


def _rule(action: str, effect: str, **conditions: object) -> GovernancePolicyRule:
    return GovernancePolicyRule(
        pack_id="policy_1", pack_name="Tools", pack_version=3, action=action, effect=effect, conditions=conditions
    )


def test_conditions_narrow_rules_and_unknown_conditions_fail_closed() -> None:
    governance = ToolGovernance(
        policy_rules=[
            _rule("*", "deny", risk_level="high"),
            _rule("*", "require_approval", target=["*.internal"]),
            _rule("anum.*", "require_approval", region="eu"),
        ]
    )
    high = match_governance(governance, tool="external.action", target="hooks.example", risk_level=RiskLevel.HIGH)
    assert [rule.action for rule in high.deny] == ["*"]
    assert high.require_approval == []

    internal = match_governance(governance, tool="external.action", target="db.internal", risk_level=RiskLevel.MEDIUM)
    assert internal.deny == [] and len(internal.require_approval) == 1

    unknown = match_governance(governance, tool="anum.respond", target=None, risk_level=RiskLevel.LOW)
    assert [rule.conditions for rule in unknown.require_approval] == [{"region": "eu"}]


def test_unknown_effect_is_treated_as_deny() -> None:
    match = match_governance(
        ToolGovernance(policy_rules=[_rule("*", "quarantine")]),
        tool="anum.respond",
        target=None,
        risk_level=RiskLevel.LOW,
    )
    assert len(match.deny) == 1


def test_decision_requirements_combine_matching_rules() -> None:
    governance = ToolGovernance(
        approval_rules=[
            GovernanceApprovalRule(id="a", name="a", action_pattern="*", minimum_approvers=2, required_roles=["owner", "admin"]),
            GovernanceApprovalRule(id="b", name="b", action_pattern="*", required_roles=["Owner"]),
        ]
    )
    match = match_governance(governance, tool="anum.respond", target=None, risk_level=RiskLevel.LOW)
    assert decision_requirements(match) == (2, {"owner"})
    assert decision_requirements(match_governance(None, tool="x", target=None, risk_level=RiskLevel.LOW)) == (1, None)


# ToolPolicy ----------------------------------------------------------------------------


async def _record(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(status="succeeded", summary=f"ran {call.name}")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="anum.respond", description="note", risk_level=RiskLevel.LOW, idempotent=True), _record
    )
    registry.register(
        ToolDefinition(name="external.action", description="send", risk_level=RiskLevel.HIGH, target="hooks.example"),
        _record,
    )
    registry.register(
        ToolDefinition(name="anum.forbidden", description="never", risk_level=RiskLevel.BLOCKED), _record
    )
    return registry


def _evaluate(name: str, governance: ToolGovernance | None):
    registry = _registry()
    return ToolPolicy(registry.names).evaluate(
        ToolCall(name=name), registry.definition(name), CONTEXT, None, governance
    )


def test_policy_without_governance_is_unchanged() -> None:
    assert _evaluate("anum.respond", None).outcome == ToolPolicyOutcome.ALLOW
    assert _evaluate("anum.respond", ToolGovernance()).outcome == ToolPolicyOutcome.ALLOW
    assert _evaluate("external.action", ToolGovernance()).outcome == ToolPolicyOutcome.REQUIRE_APPROVAL


def test_an_approval_rule_escalates_a_low_risk_tool() -> None:
    governance = ToolGovernance(
        approval_rules=[GovernanceApprovalRule(id="r", name="Notes need review", action_pattern="anum.*")]
    )
    decision = _evaluate("anum.respond", governance)
    assert decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
    assert decision.risk_level == RiskLevel.LOW
    assert "organization approval rule" in decision.reason
    assert decision.governance_rules == ["approval_rule:Notes need review"]


def test_a_deny_rule_wins_over_approval_and_allow() -> None:
    governance = ToolGovernance(
        approval_rules=[GovernanceApprovalRule(id="r", name="r", action_pattern="*")],
        policy_rules=[_rule("external.*", "allow"), _rule("target:hooks.*", "deny")],
    )
    decision = _evaluate("external.action", governance)
    assert decision.outcome == ToolPolicyOutcome.BLOCK
    assert decision.governance_rules == ["policy_pack:Tools@v3:target:hooks.*"]


def test_allow_never_weakens_platform_policy() -> None:
    governance = ToolGovernance(policy_rules=[_rule("*", "allow")])
    assert _evaluate("external.action", governance).outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
    assert _evaluate("anum.forbidden", governance).outcome == ToolPolicyOutcome.BLOCK


def test_require_approval_policy_rule_pauses() -> None:
    governance = ToolGovernance(policy_rules=[_rule("risk:low", "require_approval")])
    decision = _evaluate("anum.respond", governance)
    assert decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
    assert decision.governance_rules == ["policy_pack:Tools@v3:risk:low"]


# Runtime: planning, execution and recovery --------------------------------------------


def test_planning_pauses_for_a_matching_approval_rule_and_records_it() -> None:
    _approval_rule("anum.respond")
    body = _run("Write a short note")
    assert body["task"]["status"] == "waiting_approval"
    approval = body["approval"]
    assert approval["action"] == "anum.respond"
    assert approval["risk_level"] == "low"
    assert "organization approval rule" in approval["reason"]
    proposal = next(step for step in body["run"]["steps"] if step["type"] == "tool_proposal")
    assert proposal["metadata"]["governance_rules"] == ["approval_rule:rule anum.respond"]

    approved = _decide(approval, OWNER_A)
    assert approved.status_code == 200, approved.text
    assert approved.json()["task"]["status"] == "completed"


def test_rules_are_tenant_scoped() -> None:
    _approval_rule("*")
    other = headers(tenant=OTHER_TENANT)
    assert _run("Write a short note", other)["task"]["status"] == "completed"


def test_disabled_rules_and_inactive_packs_do_not_apply() -> None:
    _approval_rule("anum.*")
    governance_store.approval_rules[TENANT][0].enabled = False
    pack = _policy_pack({"action": "anum.*", "effect": "deny"})
    assert client.post(f"/api/v1/policy-packs/{pack['id']}/archive", headers=OWNER_A).status_code == 200
    assert _run("Write a short note")["task"]["status"] == "completed"


def test_an_active_deny_pack_fails_the_run_at_planning() -> None:
    _policy_pack({"action": "anum.*", "effect": "deny"})
    body = _run("Write a short note")
    assert body["task"]["status"] == "failed"
    assert body["approval"] is None
    assert "policy pack denies" in body["run"]["steps"][-1]["summary"]


def test_only_the_active_pack_version_applies() -> None:
    _policy_pack({"action": "anum.*", "effect": "deny"})
    _policy_pack({"action": "anum.*", "effect": "allow"})  # version 2 replaces version 1
    assert _run("Write a short note")["task"]["status"] == "completed"


def _runtime() -> tuple[InMemoryRepository, AgentRuntime, Task]:
    repository = InMemoryRepository(InMemoryStore())
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_registry())
    now = utc_now()
    task = Task(
        id=new_id("task"), title="t", prompt="Write a note", status=TaskStatus.CREATED,
        tenant_id=TENANT, workspace_id=WORKSPACE, created_at=now, updated_at=now, created_by="owner_a",
    )
    repository.create_task(task)
    return repository, runtime, task


def test_a_rule_added_after_planning_pauses_at_execution() -> None:
    _, runtime, task = _runtime()
    run = runtime.new_run(task)
    assert asyncio.run(runtime.plan_run(task, run, CONTEXT)) is None
    assert run.checkpoint.phase == RunPhase.TOOL_READY

    _approval_rule("anum.respond")
    assert runtime.begin_execution(task, run, CONTEXT) is None
    assert run.checkpoint.phase == RunPhase.WAITING_APPROVAL
    assert run.status == TaskStatus.WAITING_APPROVAL


def test_a_deny_added_after_approval_blocks_the_approved_call() -> None:
    _approval_rule("anum.respond")
    _, runtime, task = _runtime()
    run, approval = asyncio.run(runtime.run_task(task, CONTEXT))
    assert approval is not None

    _policy_pack({"action": "anum.respond", "effect": "deny"})
    approval.status = approval.status.APPROVED
    approval.decided_at = utc_now()
    approval.decided_by = "owner_b"
    asyncio.run(runtime.resume_after_approval(task, run, approval, CONTEXT))
    assert run.status == TaskStatus.FAILED
    assert "blocked during policy re-evaluation" in run.steps[-1].summary


def test_recovery_never_repeats_a_call_a_rule_now_guards() -> None:
    _, runtime, task = _runtime()
    run = runtime.new_run(task)
    asyncio.run(runtime.plan_run(task, run, CONTEXT))
    assert runtime.begin_execution(task, run, CONTEXT) is not None  # worker "crashes" here
    assert run.checkpoint.phase == RunPhase.EXECUTING

    _approval_rule("risk:low")
    asyncio.run(runtime.recover_interrupted_execution(task, run, CONTEXT))
    assert run.status == TaskStatus.FAILED
    assert "not repeated automatically" in run.steps[-1].summary


# Decision-time requirements -----------------------------------------------------------


def test_two_approvers_rule_refuses_the_requester_but_not_another_owner() -> None:
    _approval_rule("external.*", minimum_approvers=2)
    approval = _run("Publish the final update")["approval"]
    refused = _decide(approval, OWNER_A)
    assert refused.status_code == 403
    assert "requires two people" in refused.json()["error"]["message"]
    (denied,) = _audits("approval.self_approval_denied")
    assert list(denied.metadata["approval_rules"]) == ["rule external.*"]
    assert client.get(f"/api/v1/approvals/{approval['id']}", headers=OWNER_A).json()["status"] == "pending"

    rejected = _decide(approval, OWNER_A, "reject")  # rejecting your own task stays possible
    assert rejected.status_code == 200
    assert rejected.json()["approval"]["status"] == "rejected"


def test_two_approvers_rule_lets_another_owner_approve() -> None:
    _approval_rule("external.*", minimum_approvers=2)
    approval = _run("Publish the final update")["approval"]
    approved = _decide(approval, OWNER_B)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["decided_by"] == "owner_b"


OWNER_C = headers("owner_c")
OWNER_D = headers("owner_d")


def _events(event_type: str) -> list:
    return [event for event in store.events if event.type == event_type]


def test_required_approvals_counts_distinct_approvers_only_above_two() -> None:
    assert [required_approvals(minimum) for minimum in (1, 2, 3, 5)] == [1, 1, 3, 5]


def test_three_approvers_are_collected_before_the_run_resumes() -> None:
    _approval_rule("external.*", minimum_approvers=3)
    started = _run("Publish the final update")
    approval = started["approval"]
    listed = client.get(f"/api/v1/approvals/{approval['id']}", headers=OWNER_A).json()
    assert (listed["required_approvals"], listed["approvers"]) == (3, [])

    # The requester never counts, whatever the chain length.
    refused = _decide(approval, OWNER_A)
    assert refused.status_code == 403
    assert "3 approvers other than" in refused.json()["error"]["message"]
    assert _audits("approval.self_approval_denied")

    first = _decide(approval, OWNER_B)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["approval"]["status"] == "pending"
    assert body["approval"]["required_approvals"] == 3
    assert [item["user_id"] for item in body["approval"]["approvers"]] == ["owner_b"]
    assert body["run"]["status"] == "waiting_approval"
    assert body["task"]["status"] == "waiting_approval"

    again = _decide(approval, OWNER_B)
    assert again.status_code == 409
    assert "already approved" in again.json()["error"]["message"]

    second = _decide(approval, OWNER_C)
    assert second.status_code == 200
    assert second.json()["approval"]["status"] == "pending"
    assert len(second.json()["approval"]["approvers"]) == 2

    partial_audits = _audits("approval.partially_approved")
    assert [(record.actor, record.metadata["approvals"], record.metadata["required_approvals"])
            for record in partial_audits] == [("owner_b", 1, 3), ("owner_c", 2, 3)]
    assert partial_audits[0].metadata["payload_hash"] == approval["payload_hash"]
    assert list(partial_audits[0].metadata["approval_rules"]) == ["rule external.*"]
    partial_events = _events("approval.partially_approved")
    assert [event.payload["approvals"] for event in partial_events] == [1, 2]
    assert all(event.subject == approval["id"] for event in partial_events)
    assert _events("approval.approved") == []

    last = _decide(approval, OWNER_D)
    assert last.status_code == 200, last.text
    final = last.json()
    assert final["approval"]["status"] == "approved"
    assert final["approval"]["decided_by"] == "owner_d"
    assert [item["user_id"] for item in final["approval"]["approvers"]] == ["owner_b", "owner_c", "owner_d"]
    assert final["approval"]["required_approvals"] == 3
    assert final["run"]["status"] == "completed"
    (approved_audit,) = _audits("approval.approved")
    assert list(approved_audit.metadata["approvers"]) == ["owner_b", "owner_c", "owner_d"]
    (approved_event,) = _events("approval.approved")
    assert approved_event.payload["approvals"] == 3 and approved_event.payload["required_approvals"] == 3

    listed = client.get("/api/v1/approvals", headers=OWNER_B).json()
    assert [len(item["approvers"]) for item in listed] == [3]


def test_run_and_resume_responses_show_chain_progress_for_the_approval_they_create() -> None:
    _approval_rule("external.*", minimum_approvers=3)
    started = _run("Publish the final update")
    assert started["run"]["status"] == "waiting_approval"
    assert (started["approval"]["required_approvals"], started["approval"]["approvers"]) == (3, [])

    # A checkpointed call that a rule now guards pauses on resume; that response too.
    _approval_rule("anum.respond", minimum_approvers=4)
    task = client.post("/api/v1/tasks", headers=OWNER_A, json={"title": "t", "prompt": "Summarize notes"}).json()
    stored = store.tasks[task["id"]]
    stored.status = TaskStatus.RUNNING
    now = utc_now()
    run = AgentRun(
        id=new_id("run"),
        task_id=stored.id,
        status=TaskStatus.RUNNING,
        checkpoint=RunCheckpoint(
            phase=RunPhase.TOOL_READY,
            version=2,
            selected_skills=["anum.task-planning"],
            tool_call={"name": "anum.respond", "arguments": {"content": "Recovered"}},
        ),
        created_at=now,
        updated_at=now,
    )
    store.runs[run.id] = run
    resumed = client.post(f"/api/v1/agent-runs/{run.id}/resume", headers=OWNER_A)
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["run"]["status"] == "waiting_approval"
    assert (body["approval"]["required_approvals"], body["approval"]["approvers"]) == (4, [])


def test_any_reject_ends_an_approval_chain() -> None:
    _approval_rule("external.*", minimum_approvers=3)
    approval = _run("Publish the final update")["approval"]
    assert _decide(approval, OWNER_B).status_code == 200
    rejected = client.post(
        f"/api/v1/approvals/{approval['id']}/reject", headers=OWNER_C, json={"reason": "Not this week"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["approval"]["status"] == "rejected"
    assert rejected.json()["run"]["status"] == "failed"
    assert _decide(approval, OWNER_D).status_code == 409
    shown = client.get(f"/api/v1/approvals/{approval['id']}", headers=OWNER_A).json()
    assert [item["user_id"] for item in shown["approvers"]] == ["owner_b"]


def test_each_approval_in_a_chain_checks_role_and_payload_hash() -> None:
    _approval_rule("external.*", minimum_approvers=3, required_roles=["security"])
    approval = _run("Publish the final update")["approval"]
    refused = _decide(approval, OWNER_B)
    assert refused.status_code == 403 and "security" in refused.json()["error"]["message"]

    security_b = {**OWNER_B, "x-user-roles": "owner,security"}
    stale = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=security_b, json={"payload_hash": "0" * 64}
    )
    assert stale.status_code == 409
    assert _audits("approval.partially_approved") == []
    assert store.approval_approvers.get(approval["id"]) is None

    assert _decide(approval, security_b).status_code == 200
    assert len(store.approval_approvers[approval["id"]]) == 1


def test_an_expired_chain_cannot_be_completed() -> None:
    _approval_rule("external.*", minimum_approvers=3)
    approval = _run("Publish the final update")["approval"]
    assert _decide(approval, OWNER_B).status_code == 200
    stored = store.approvals[approval["id"]]
    stored.expires_at = utc_now().replace(year=2000)
    expired = _decide(approval, OWNER_C)
    assert expired.status_code == 410
    assert store.approvals[approval["id"]].status.value == "expired"
    assert len(store.approval_approvers[approval["id"]]) == 1


def test_rules_are_reread_so_dropping_the_chain_rule_lets_the_next_approval_complete_it() -> None:
    _approval_rule("external.*", minimum_approvers=3)
    approval = _run("Publish the final update")["approval"]
    assert _decide(approval, OWNER_B).json()["approval"]["status"] == "pending"
    governance_store.clear()  # the organization drops the rule while the approval waits
    done = _decide(approval, OWNER_C)
    assert done.json()["approval"]["status"] == "approved"
    assert [item["user_id"] for item in done.json()["approval"]["approvers"]] == ["owner_b", "owner_c"]


def test_a_decider_must_hold_a_role_the_rule_requires() -> None:
    _approval_rule("external.*", required_roles=["security"])
    approval = _run("Publish the final update")["approval"]
    refused = _decide(approval, OWNER_B)
    assert refused.status_code == 403
    assert "security" in refused.json()["error"]["message"]
    assert _audits("approval.role_denied")

    security_owner = {**OWNER_B, "x-user-roles": "owner,security"}
    assert _decide(approval, security_owner).status_code == 200
