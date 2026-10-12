"""Week 1 pilot checks (docs/setup-plan.md#pilot-checks), memory backend.

Two parts: the checklist people record (per tenant, audited, owner or org_admin only), and
the cases ANUM can verify on its own governance, encoded as tests here instead of a button
that runs against live data:

- CC05 / OWN02: a refund of AED 300 never runs on its own; it waits for an approval bound to
  the exact payload, whatever the autonomy level, and no plan agent holds a payment tool.
- OWN03: a send to 20 recipients always asks (bulk), even with an approved template.
- OWN01: "Show all COD orders awaiting remittance..." plans read-only steps that need no approval.
- CC04: ANUM has no opt-out suppression, and the plan says so instead of claiming it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api.agent_tools import ToolCall, ToolDefinition, ToolPolicy, ToolPolicyOutcome
from anum_api.autonomy import (
    AutonomyLevel,
    AutonomyMode,
    Guardrail,
    OrganizationGuardrails,
    ToolCategory,
    WorkspaceAutonomySettings,
    decide,
    effective,
    profile_call,
)
from anum_api.main import store
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.setup_plan_checks import memory_check_store
from anum_api.setup_plans import load_blueprints

import test_setup_plans as plan_tests
from test_setup_plans import ADMIN, MEMBER, OWNER, PLAN, client, h  # noqa: F401 (client is the shared fixture)

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_checks() -> Iterator[None]:
    memory_check_store.clear()
    yield
    memory_check_store.clear()


# ---------------------------------------------------------------------------- the checklist


def test_the_blueprint_holds_the_eighteen_cases_and_the_role_cards() -> None:
    blueprint = load_blueprints()[PLAN]
    ids = [check.id for check in blueprint.pilot_checks]
    assert ids == [f"CC{i:02d}" for i in range(1, 11)] + [f"COD{i:02d}" for i in range(1, 6)] + ["OWN01", "OWN02", "OWN03"]
    for check in blueprint.pilot_checks:
        assert check.input and check.expected and check.approval_gate and check.pass_criteria and check.anum
        if check.verified_by:
            file_name, test_name = check.verified_by.split("::")
            assert test_name in Path(__file__).with_name(file_name).read_text(encoding="utf-8")
    cards = {t.key: t for t in blueprint.role_templates}
    assert any("AED 250" in line for line in cards["r_ops"].may_approve)
    assert any("AED 25.01 to 250" in line for line in cards["r_finance"].must_escalate)
    assert cards["r_gov"].must_escalate and "pilot" in blueprint.pilot_gate.lower()


def test_recording_checks_counts_passes_and_opens_the_week_one_gate(client: TestClient) -> None:
    empty = client.get(f"/api/v1/setup-plans/{PLAN}/checks", headers=ADMIN).json()
    assert (empty["total"], empty["passed"], empty["not_run"], empty["done"]) == (18, 0, 18, False)
    assert all(check["result"] is None for check in empty["checks"])

    body = {"status": "fail", "note": "Invented an ETA", "evidence": "task_123"}
    response = client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC01", headers=ADMIN, json=body)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["failed"] == 1 and view["passed"] == 0
    result = next(c for c in view["checks"] if c["id"] == "CC01")["result"]
    assert result["status"] == "fail" and result["updated_by"] == "owner_1" and result["evidence"] == "task_123"

    for check in empty["checks"]:
        assert client.put(f"/api/v1/setup-plans/{PLAN}/checks/{check['id']}", headers=OWNER, json={"status": "pass"}).status_code == 200
    done = client.get(f"/api/v1/setup-plans/{PLAN}/checks", headers=ADMIN).json()
    assert (done["passed"], done["failed"], done["done"]) == (18, 0, True)
    assert next(c for c in done["checks"] if c["id"] == "CC01")["result"]["updated_by"] == "owner_2"

    audits = [r for r in store.audit_records if r.action == "setup_plan.check_recorded"]
    assert len(audits) == 19
    first = audits[0]
    assert first.target == f"setup_plan:{PLAN}:CC01" and first.metadata == {"status": "fail", "has_note": True, "has_evidence": True}
    assert "Invented" not in str(first.metadata)


def test_checks_refuse_members_bad_input_and_unknown_cases(client: TestClient) -> None:
    assert client.get(f"/api/v1/setup-plans/{PLAN}/checks", headers=MEMBER).status_code == 403
    assert client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC01", headers=MEMBER, json={"status": "pass"}).status_code == 403
    assert client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC99", headers=ADMIN, json={"status": "pass"}).status_code == 404
    assert client.put("/api/v1/setup-plans/nope/checks/CC01", headers=ADMIN, json={"status": "pass"}).status_code == 404
    for bad in ({"status": "maybe"}, {"status": "pass", "evidence": "http://example.test/x"}, {"status": "pass", "note": "a\x00b"},
                {"status": "pass", "note": "x" * 1001}, {"status": "pass", "extra": 1}):
        assert client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC01", headers=ADMIN, json=bad).status_code == 422, bad
    assert client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC01", headers=ADMIN, json={"status": "pass", "evidence": "https://example.test/x"}).status_code == 200
    assert not memory_check_store.rows.get(("tenant_setup", PLAN, "CC99"))


def test_checks_belong_to_one_tenant(client: TestClient) -> None:
    assert client.put(f"/api/v1/setup-plans/{PLAN}/checks/CC01", headers=ADMIN, json={"status": "pass"}).status_code == 200
    other = h("owner_b", "owner,org_admin", workspace="workspace_b", tenant="tenant_b")
    assert client.post("/api/v1/tenants", headers=other, json={"name": "Other"}).status_code == 201
    assert client.post("/api/v1/workspaces", headers=other, json={"name": "B"}).status_code == 201
    assert client.post("/api/v1/workspace-memberships/current", headers=other).status_code == 201
    assert client.get(f"/api/v1/setup-plans/{PLAN}/checks", headers=other).json()["passed"] == 0
    # Another workspace of the same tenant sees the organization's pilot results.
    second = h("owner_1", "owner,org_admin", workspace="workspace_setup_two")
    assert client.post("/api/v1/workspaces", headers=second, json={"name": "Two"}).status_code == 201
    assert client.post("/api/v1/workspace-memberships/current", headers=second).status_code == 201
    assert client.get(f"/api/v1/setup-plans/{PLAN}/checks", headers=second).json()["passed"] == 1


# ---------------------------------------------------------------------------- cases ANUM checks on its own governance


def _decide(call: ToolCall, definition: ToolDefinition, settings: WorkspaceAutonomySettings, *, known: set[str] | None = None):  # type: ignore[no-untyped-def]
    cfg = effective(settings, OrganizationGuardrails())
    return decide(profile_call(call, definition), cfg, [], now=NOW, known=known or set(), count_today=lambda **_: 0, platform_allows=False)


def test_cc05_own02_a_refund_always_waits_for_approval_and_never_runs(client: TestClient) -> None:
    refund = ToolDefinition(name="commerce.own_store.refund_order", description="Refund an order", risk_level=RiskLevel.HIGH,
                            category="commerce", action_kind="payment", required_roles=frozenset({"owner"}))
    call = ToolCall(name=refund.name, arguments={"order": "TEST-ORDER-003", "amount": 300, "currency": "AED"})
    # The platform: high risk always needs an approval before the call (and only that payload) can run.
    context = TenantContext(tenant_id="tenant_setup", workspace_id="workspace_setup", user_id="owner_1", roles=["owner"])
    decision = ToolPolicy({refund.name}).evaluate(call, refund, context)
    assert decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
    # Autonomy can never let a payment act on its own, even at the most permissive level.
    permissive = WorkspaceAutonomySettings(category_levels={ToolCategory.COMMERCE: AutonomyLevel.ACT_AND_NOTIFY}, amount_threshold=1000)
    assert _decide(call, refund, permissive).mode == AutonomyMode.ASK
    # And after applying the whole plan no agent holds a payment, price or stock tool at all.
    plan_tests.apply(client, "all")
    from anum_api.agent_profiles import runtime_tools
    from anum_api.autonomy import ActionKind, kind_of

    tools = runtime_tools()
    for agent in plan_tests.agents(client).values():
        for name in agent["allowed_tools"]:
            assert kind_of(name, tools.definition(name)) not in {ActionKind.PAYMENT, ActionKind.PRICE_CHANGE, ActionKind.STOCK_CHANGE}, (agent["name"], name)


def test_own03_a_send_to_twenty_recipients_always_asks() -> None:
    template_send = ToolDefinition(name="send_whatsapp_template", description="Send a template", risk_level=RiskLevel.MEDIUM,
                                   category="whatsapp", action_kind="reply")
    recipients = [f"+97150000{i:04d}" for i in range(20)]
    call = ToolCall(name=template_send.name, arguments={"to": recipients, "template": "new_collection"})
    settings = WorkspaceAutonomySettings(category_levels={ToolCategory.WHATSAPP: AutonomyLevel.ACT_AND_NOTIFY}, approved_templates=["new_collection"])
    decision = _decide(call, template_send, settings, known=set(recipients))
    assert decision.mode == AutonomyMode.ASK and decision.guardrail == Guardrail.BULK
    # The plan's bulk line is "more than 10 in 24 hours"; ANUM's default (5) is stricter.
    assert WorkspaceAutonomySettings().recipients_threshold <= 10


def test_own01_the_cod_summary_command_plans_read_only_steps(client: TestClient) -> None:
    response = client.post("/api/v1/commands/preview", headers=ADMIN,
                           json={"text": "Show all COD orders awaiting remittance and group them by variance.", "locale": "en"})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["approvals_required"] == 0 and plan["blocked"] == 0
    for step in plan["steps"]:
        assert step["approval"] == "none"
        assert step["tool"]["action_kind"] in {"read", None} or step["tool"]["name"] == "anum.respond", step["tool"]


def test_cc04_opt_out_is_reported_as_not_supported(client: TestClient) -> None:
    preview = plan_tests.preview(client)
    assert any(item["key"] == "u_optout" and item["action"] == "unsupported" for item in preview["items"])
    opt_out = next(check for check in load_blueprints()[PLAN].pilot_checks if check.id == "CC04")
    assert opt_out.support == "unsupported" and "cannot pass" in opt_out.anum
