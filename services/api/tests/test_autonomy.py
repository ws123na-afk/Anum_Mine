"""Autonomy policy (docs/autonomy.md): evaluation, floors, thresholds, rules, proposals,
the activity feed and undo, through the pure evaluator, the runtime and the HTTP API
(in-memory backend; PostgreSQL and RLS are in ``test_postgres_autonomy.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_planning import AgentPlanner
from anum_api.agent_tools import (
    ToolCall,
    ToolDefinition,
    ToolPolicy,
    ToolPolicyOutcome,
    ToolResult,
    default_tool_registry,
)
from anum_api.autonomy import (
    ActionFilter,
    ActionKind,
    AutonomyFacts,
    AutonomyLevel,
    AutonomyMode,
    AutonomyRule,
    Guardrail,
    OrganizationGuardrails,
    QuietHours,
    RuleSource,
    ToolCategory,
    WorkspaceAutonomySettings,
    autonomy_memory_store,
    category_of,
    decide,
    effective,
    floor_violations,
    kind_of,
    profile_call,
)
from anum_api.governance import governance_store
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.store import store

TENANT = "tenant_autonomy"
WORKSPACE = "workspace_autonomy"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def headers(user: str = "owner_a", role: str = "owner", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = headers()
OTHER_OWNER = headers("owner_b")
MEMBER = headers("member_c", "member")
OTHER_MEMBER = headers("member_d", "member")
VIEWER = headers("viewer_e", "viewer")
ORG_ADMIN = headers("admin_f", "owner,org_admin")

client = TestClient(main.app)


# -- tools used by the runtime tests ---------------------------------------------------------

UNDONE: list[dict[str, Any]] = []
UNDO_FAILS = {"value": False}


async def _send(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(status="succeeded", summary=f"Sent {call.arguments.get('template', 'message')} to {call.arguments.get('to')}")


async def _archive(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(
        status="succeeded",
        summary=f"Archived email {call.arguments['message_id']}",
        output={"undo": {"message_id": call.arguments["message_id"]}},
    )


async def _unarchive(call: ToolCall, _: TenantContext) -> ToolResult:
    if UNDO_FAILS["value"]:
        return ToolResult(status="failed", summary="The mailbox refused the change.")
    UNDONE.append(dict(call.arguments))
    return ToolResult(status="succeeded", summary="Moved back to the inbox")


async def _price(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(status="succeeded", summary="Price changed")


def _registry():  # type: ignore[no-untyped-def]
    registry = default_tool_registry()
    registry.register(
        ToolDefinition(name="whatsapp.send_template", description="Send a WhatsApp template", risk_level=RiskLevel.HIGH,
                       category="whatsapp", action_kind="send", target="graph.facebook.com"),
        _send,
    )
    registry.register(
        ToolDefinition(name="email.archive", description="Archive an email", risk_level=RiskLevel.MEDIUM,
                       category="email", action_kind="archive", undo_tool="email.unarchive", undo_window_seconds=600),
        _archive,
    )
    registry.register(
        ToolDefinition(name="email.unarchive", description="Move an email back", risk_level=RiskLevel.MEDIUM,
                       category="email", action_kind="update"),
        _unarchive,
    )
    registry.register(
        ToolDefinition(name="commerce.update_price", description="Change a listing price", risk_level=RiskLevel.HIGH,
                       category="commerce", action_kind="price_change"),
        _price,
    )
    return registry


PLANNED: dict[str, ToolCall | None] = {"call": None}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records, store.approval_policies, store.approval_approvers):
        collection.clear()
    autonomy_memory_store.clear()
    governance_store.clear() if hasattr(governance_store, "clear") else None
    UNDONE.clear()
    UNDO_FAILS["value"] = False
    registry = _registry()
    monkeypatch.setattr(main, "tool_registry", registry)
    monkeypatch.setattr(main.app.state, "tool_registry", registry)
    original = AgentPlanner._select_tool

    def select(self, prompt, skills, response):  # type: ignore[no-untyped-def]
        return PLANNED["call"] or original(self, prompt, skills, response)

    monkeypatch.setattr(AgentPlanner, "_select_tool", select)
    PLANNED["call"] = None
    yield
    autonomy_memory_store.clear()
    store.approval_policies.clear()


def _run(call: ToolCall, who: dict[str, str] = OWNER) -> dict[str, Any]:
    PLANNED["call"] = call
    task = client.post("/api/v1/tasks", headers=who, json={"title": "Autonomy", "prompt": "Do the thing"}).json()
    response = client.post(f"/api/v1/tasks/{task['id']}/run", headers=who)
    assert response.status_code == 200, response.text
    return response.json()


def _approve(body: dict[str, Any], who: dict[str, str] = OWNER) -> dict[str, Any]:
    approval = body["approval"]
    response = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=who, json={"payload_hash": approval["payload_hash"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _settings(who: dict[str, str] = OWNER, **values: Any):  # type: ignore[no-untyped-def]
    return client.put("/api/v1/autonomy/settings", headers=who, json=values)


def _audits(action: str) -> list:
    return [record for record in store.audit_records if record.action == action]


def _events(kind: str) -> list:
    return [event for event in store.events if event.type == kind]


SEND = ToolCall(name="whatsapp.send_template", arguments={"to": "+966 50 000 0001", "template": "order_status", "order": "A1"})


# -- pure evaluation --------------------------------------------------------------------------


def _profile(name: str, risk: RiskLevel = RiskLevel.HIGH, *, category: str | None = None, kind: str | None = None, **arguments: Any):  # type: ignore[no-untyped-def]
    definition = ToolDefinition(name=name, description="t", risk_level=risk, category=category, action_kind=kind)
    return profile_call(ToolCall(name=name, arguments=arguments), definition)


def _decide(profile, settings: WorkspaceAutonomySettings | None = None, guardrails: OrganizationGuardrails | None = None, *,  # type: ignore[no-untyped-def]
            rules: list[AutonomyRule] | None = None, known: set[str] | None = None, now: datetime = NOW,
            done_today: int = 0, platform_allows: bool = False):
    cfg = effective(settings or WorkspaceAutonomySettings(approved_templates=["order_status"]), guardrails or OrganizationGuardrails())
    return decide(profile, cfg, rules or [], now=now, known=known or set(),
                  count_today=lambda **_: done_today, platform_allows=platform_allows)


def test_reading_and_drafts_run_silently_by_default() -> None:
    assert _decide(_profile("email.search", RiskLevel.LOW, category="email", kind="read")).mode == AutonomyMode.SILENT
    assert _decide(_profile("email.draft_reply", RiskLevel.LOW)).mode == AutonomyMode.SILENT


def test_silent_needs_low_risk_otherwise_it_notifies() -> None:
    decision = _decide(_profile("files.summarize", RiskLevel.MEDIUM, category="files", kind="read"))
    assert decision.mode == AutonomyMode.NOTIFY and decision.level == AutonomyLevel.ACT_AND_NOTIFY


def test_known_recipient_with_approved_template_acts_and_notifies() -> None:
    profile = _profile("whatsapp.send_template", category="whatsapp", kind="send", to="+966500000001", template="order_status")
    assert _decide(profile, known={"+966500000001"}).mode == AutonomyMode.NOTIFY


def test_first_contact_without_template_or_without_recipient_always_asks() -> None:
    first = _profile("whatsapp.send_template", category="whatsapp", kind="send", to="+966500000009", template="order_status")
    assert _decide(first).guardrail == Guardrail.FIRST_CONTACT
    free_text = _profile("email.reply", category="email", kind="reply", to="a@shop.example", body="hi")
    assert _decide(free_text, known={"a@shop.example"}).guardrail == Guardrail.TEMPLATE
    unknown = _profile("whatsapp.send_template", category="whatsapp", kind="send", template="order_status")
    assert _decide(unknown).mode == AutonomyMode.ASK
    unknown_reply = _profile("whatsapp.reply_template", category="whatsapp", kind="reply", template="order_status")
    assert _decide(unknown_reply).guardrail == Guardrail.FIRST_CONTACT


def _facts_decide(profile, facts: AutonomyFacts, settings: WorkspaceAutonomySettings | None = None,  # type: ignore[no-untyped-def]
                  rules: list[AutonomyRule] | None = None):
    cfg = effective(settings or WorkspaceAutonomySettings(), OrganizationGuardrails())
    return decide(profile, cfg, rules or [], now=NOW, known=set(), count_today=lambda **_: 0, facts=facts)


def test_connector_facts_make_who_wrote_first_known_and_own_the_category_templates() -> None:
    template = _profile("send_whatsapp_template", RiskLevel.MEDIUM, category="whatsapp", kind="reply", to="966500000001", template="order_update")
    listed = AutonomyFacts(known_recipients=frozenset({"+966500000001"}), approved_templates=("order_update",),
                           templates_source="WhatsApp's approved templates are the auto-send list.")
    assert _facts_decide(template, listed).mode == AutonomyMode.NOTIFY
    # Without the customer having written first, it is first contact.
    assert _facts_decide(template, listed.model_copy(update={"known_recipients": frozenset()})).guardrail == Guardrail.FIRST_CONTACT
    # The connector's list replaces the workspace list for its category: no second list to keep in sync.
    unlisted = listed.model_copy(update={"approved_templates": ()})
    decision = _facts_decide(template, unlisted, WorkspaceAutonomySettings(approved_templates=["order_update"]))
    assert decision.guardrail == Guardrail.TEMPLATE and "auto-send list" in decision.reason
    # Facts never lift the other checks: quiet hours, levels, blocked recipients.
    quiet = WorkspaceAutonomySettings(quiet_hours=QuietHours(start="11:00", end="13:00"))
    assert _facts_decide(template, listed, quiet).guardrail == Guardrail.QUIET_HOURS
    asks = WorkspaceAutonomySettings(category_levels={ToolCategory.WHATSAPP: AutonomyLevel.ASK_ALWAYS})
    assert _facts_decide(template, listed, asks).mode == AutonomyMode.ASK
    blocked = WorkspaceAutonomySettings(blocked_recipients=["+966500000001"])
    assert _facts_decide(template, listed, blocked).mode == AutonomyMode.BLOCK
    # The connector's list is the only way in: a rule cannot add a template beside it, and
    # approvals of WhatsApp templates are not counted towards such a rule.
    rule = AutonomyRule(id="r", tenant_id=TENANT, workspace_id=WORKSPACE, pattern_key="send_whatsapp_template|template=order_update",
                        tool="send_whatsapp_template", template="order_update", category=ToolCategory.WHATSAPP,
                        action_kind=ActionKind.REPLY, level=AutonomyLevel.ACT_AND_NOTIFY, source=RuleSource.PROPOSAL,
                        created_by="x", created_at=NOW)
    off_list = _facts_decide(template, unlisted, rules=[rule])
    assert off_list.mode == AutonomyMode.ASK and off_list.guardrail == Guardrail.TEMPLATE and not off_list.proposable
    assert _facts_decide(template, listed, quiet).proposable is False
    always_ask = rule.model_copy(update={"level": AutonomyLevel.ASK_ALWAYS})
    assert _facts_decide(template, listed, rules=[always_ask]).guardrail == Guardrail.RULE


def test_free_text_always_asks_even_with_a_rule_for_its_pattern() -> None:
    free_text = _profile("send_whatsapp", category="whatsapp", kind="reply", to="966500000001", text="hello")
    rule = AutonomyRule(id="r", tenant_id=TENANT, workspace_id=WORKSPACE, pattern_key="send_whatsapp", tool="send_whatsapp",
                        category=ToolCategory.WHATSAPP, action_kind=ActionKind.REPLY, level=AutonomyLevel.ACT_AND_NOTIFY,
                        source=RuleSource.MANUAL, created_by="x", created_at=NOW)
    facts = AutonomyFacts(known_recipients=frozenset({"+966500000001"}), approved_templates=())
    decision = _facts_decide(free_text, facts, rules=[rule])
    assert decision.mode == AutonomyMode.ASK and decision.guardrail == Guardrail.TEMPLATE and not decision.proposable


@pytest.mark.parametrize(
    ("name", "kind"),
    [("commerce.update_price", "price_change"), ("commerce.set_stock", "stock_change"), ("email.delete", "delete"),
     ("developer.merge", "merge_code"), ("commerce.refund", "payment")],
)
def test_money_prices_stock_deleting_and_merging_always_ask_even_with_a_rule(name: str, kind: str) -> None:
    profile = _profile(name, category="commerce", kind=kind)
    rule = AutonomyRule(id="r", tenant_id=TENANT, workspace_id=WORKSPACE, pattern_key=name, tool=name, category=ToolCategory.COMMERCE,
                        action_kind=ActionKind(kind), level=AutonomyLevel.ACT_SILENTLY, source=RuleSource.MANUAL, created_by="x", created_at=NOW)
    decision = _decide(profile, rules=[rule])
    assert decision.mode == AutonomyMode.ASK and decision.guardrail == Guardrail.PROTECTED_KIND


def test_bulk_amount_and_price_change_thresholds_ask() -> None:
    many = [f"c{i}@shop.example" for i in range(6)]
    bulk = _profile("email.send", category="email", kind="send", to=many, template="order_status")
    assert _decide(bulk, known=set(many)).guardrail == Guardrail.BULK
    settings = WorkspaceAutonomySettings(approved_templates=["order_status"], recipients_threshold=10, amount_threshold=100,
                                         price_change_percent_threshold=5)
    assert _decide(bulk, settings, known=set(many)).mode == AutonomyMode.NOTIFY
    coupon = _profile("commerce.create_coupon", category="commerce", kind="create", amount=150)
    assert _decide(coupon, settings).guardrail == Guardrail.AMOUNT
    assert _decide(_profile("commerce.create_coupon", category="commerce", kind="create", amount=50), settings).mode == AutonomyMode.NOTIFY
    promo = _profile("commerce.create_promotion", category="commerce", kind="create", old_price=100, new_price=90)
    assert _decide(promo, settings).guardrail == Guardrail.PRICE_CHANGE


def test_blocked_recipients_and_domains_and_the_spending_limit_block() -> None:
    guardrails = OrganizationGuardrails(blocked_domains=["@Competitor.example"], blocked_recipients=["+966 50 111 2222"], spending_limit=1000)
    to_domain = _profile("email.send", category="email", kind="send", to="sales@eu.competitor.example", template="order_status")
    assert _decide(to_domain, guardrails=guardrails).mode == AutonomyMode.BLOCK
    to_number = _profile("whatsapp.send_template", category="whatsapp", kind="send", to="+966501112222", template="order_status")
    assert _decide(to_number, guardrails=guardrails).guardrail == Guardrail.BLOCKED_RECIPIENT
    spend = _profile("commerce.buy_ads", category="commerce", kind="create", amount=1500)
    decision = _decide(spend, guardrails=guardrails)
    assert decision.mode == AutonomyMode.BLOCK and decision.guardrail == Guardrail.SPENDING_LIMIT


def test_quiet_hours_cross_midnight_and_hold_effects_but_not_reads() -> None:
    settings = WorkspaceAutonomySettings(quiet_hours=QuietHours(start="22:00", end="07:00", timezone="Asia/Riyadh"))
    archive = _profile("email.archive", RiskLevel.MEDIUM, category="email", kind="archive")
    night = datetime(2026, 10, 11, 20, 30, tzinfo=timezone.utc)  # 23:30 in Riyadh
    morning = datetime(2026, 10, 12, 3, 59, tzinfo=timezone.utc)  # 06:59
    day = datetime(2026, 10, 12, 4, 0, tzinfo=timezone.utc)  # 07:00
    assert _decide(archive, settings, now=night).guardrail == Guardrail.QUIET_HOURS
    assert _decide(archive, settings, now=morning).guardrail == Guardrail.QUIET_HOURS
    assert _decide(archive, settings, now=day).mode == AutonomyMode.NOTIFY
    read = _profile("email.search", RiskLevel.LOW, category="email", kind="read")
    assert _decide(read, settings, now=night).mode == AutonomyMode.SILENT


def test_daily_caps_per_category_and_tool() -> None:
    archive = _profile("email.archive", RiskLevel.MEDIUM, category="email", kind="archive")
    settings = WorkspaceAutonomySettings(daily_caps={"email.archive": 3})
    assert _decide(archive, settings, done_today=2).mode == AutonomyMode.NOTIFY
    assert _decide(archive, settings, done_today=3).guardrail == Guardrail.DAILY_CAP
    assert _decide(archive, done_today=200).guardrail == Guardrail.DAILY_CAP  # the default category cap


def test_category_levels_are_ceilings_and_rules_set_exact_patterns() -> None:
    archive = _profile("email.archive", RiskLevel.MEDIUM, category="email", kind="archive")
    ask = WorkspaceAutonomySettings(category_levels={ToolCategory.EMAIL: AutonomyLevel.ASK_ALWAYS})
    assert _decide(archive, ask).mode == AutonomyMode.ASK
    send = _profile("whatsapp.send_template", category="whatsapp", kind="send", to="+966500000001", template="shipping")
    assert _decide(send, known={"+966500000001"}).guardrail == Guardrail.TEMPLATE
    rule = AutonomyRule(id="r1", tenant_id=TENANT, workspace_id=WORKSPACE, pattern_key="whatsapp.send_template|template=shipping",
                        tool="whatsapp.send_template", template="shipping", category=ToolCategory.WHATSAPP, action_kind=ActionKind.SEND,
                        level=AutonomyLevel.ACT_AND_NOTIFY, source=RuleSource.PROPOSAL, created_by="x", created_at=NOW)
    assert _decide(send, rules=[rule], known={"+966500000001"}).mode == AutonomyMode.NOTIFY
    tight = rule.model_copy(update={"level": AutonomyLevel.ASK_ALWAYS})
    assert _decide(send, rules=[tight], known={"+966500000001"}).guardrail == Guardrail.RULE


def test_org_ceilings_and_floors_cap_the_workspace() -> None:
    guardrails = OrganizationGuardrails(category_ceilings={ToolCategory.EMAIL: AutonomyLevel.ASK_ALWAYS}, max_auto_recipients=2,
                                        max_auto_amount=50, daily_caps={"email": 5})
    settings = WorkspaceAutonomySettings(recipients_threshold=10, amount_threshold=500, daily_caps={"email": 9},
                                         category_levels={ToolCategory.EMAIL: AutonomyLevel.ACT_SILENTLY})
    cfg = effective(settings, guardrails)
    assert cfg.category_levels[ToolCategory.EMAIL] == AutonomyLevel.ASK_ALWAYS
    assert (cfg.recipients_threshold, cfg.amount_threshold, cfg.daily_caps["email"]) == (2, 50, 5)
    problems = floor_violations(settings, guardrails)
    assert len(problems) == 4 and any("email can be at most ask_always" in p for p in problems)


def test_names_are_classified_cautiously() -> None:
    assert category_of("gmail.send_message", None) == ToolCategory.EMAIL
    assert category_of("noon.update_price", None) == ToolCategory.COMMERCE
    assert kind_of("noon.update_price", None) == ActionKind.PRICE_CHANGE
    assert kind_of("amazon.set_inventory", None) == ActionKind.STOCK_CHANGE
    low = ToolDefinition(name="x.get_orders", description="", risk_level=RiskLevel.LOW)
    high = ToolDefinition(name="x.set_target", description="", risk_level=RiskLevel.HIGH)
    assert kind_of("x.get_orders", low) == ActionKind.READ
    # A medium or high-risk tool that only sounds like a read is "other" (always asks).
    assert kind_of("x.get_orders", high) == ActionKind.OTHER
    assert kind_of("x.set_target", high) == ActionKind.UPDATE


def test_autonomy_never_unblocks_and_policy_asks_stay() -> None:
    from anum_api.autonomy import apply_autonomy

    policy = ToolPolicy()
    definition = ToolDefinition(name="whatsapp.send_template", description="", risk_level=RiskLevel.HIGH, category="whatsapp", action_kind="send")
    call = ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "order_status"})
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="u", roles=["owner"])
    base = policy.evaluate(call, definition, context)
    assert base.lowerable is True
    profile = profile_call(call, definition)
    notify = _decide(profile, known={"+966500000001"})
    assert apply_autonomy(base, profile, notify).outcome == ToolPolicyOutcome.ALLOW
    from anum_api.schemas import WorkspaceApprovalPolicy

    two_person = policy.evaluate(call, definition, context, WorkspaceApprovalPolicy(two_person_rule=True))
    assert two_person.lowerable is False
    combined = apply_autonomy(two_person, profile, notify)
    assert combined.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL and combined.autonomy["guardrail"] == "policy"
    blocked = base.model_copy(update={"outcome": ToolPolicyOutcome.BLOCK})
    assert apply_autonomy(blocked, profile, notify).outcome == ToolPolicyOutcome.BLOCK


# -- runtime through the API ------------------------------------------------------------------


def test_reads_run_silently_and_are_listed_without_an_event() -> None:
    body = _run(ToolCall(name="anum.respond", arguments={"text": "Weekly summary"}))
    assert body["approval"] is None and body["task"]["status"] == "completed"
    page = client.get("/api/v1/autonomy/actions", headers=OWNER).json()
    (item,) = page["items"]
    assert (item["tool"], item["level"], item["undo_available"]) == ("anum.respond", "act_silently", False)
    assert not _events("autonomy.action_taken")
    assert _audits("autonomy.action_taken")[0].metadata["level"] == "act_silently"


def test_first_contact_asks_then_the_known_recipient_acts_and_notifies() -> None:
    assert _settings(approved_templates=["order_status"]).status_code == 200
    first = _run(SEND)
    assert first["approval"] is not None
    step = next(s for s in first["run"]["steps"] if s["type"] == "tool_proposal")
    assert step["metadata"]["autonomy_reason"] == "First contact with a new recipient always asks."
    _approve(first)
    second = _run(SEND)
    assert second["approval"] is None and second["task"]["status"] == "completed"
    (event,) = _events("autonomy.action_taken")
    assert event.payload["tool"] == "whatsapp.send_template" and event.payload["level"] == "act_and_notify"
    (audit,) = _audits("autonomy.action_taken")
    assert audit.metadata["pattern_key"] == "whatsapp.send_template|template=order_status"
    proposal = next(s for s in second["run"]["steps"] if s["type"] == "tool_proposal")
    assert proposal["metadata"]["policy_outcome"] == "allow" and proposal["metadata"]["autonomy_mode"] == "notify"


def test_a_blocked_recipient_is_refused_even_after_approval() -> None:
    first = _run(SEND)
    assert first["approval"] is not None
    assert client.put("/api/v1/autonomy/guardrails", headers=ORG_ADMIN, json={"blocked_recipients": ["+966500000001"]}).status_code == 200
    decided = _approve(first)
    assert decided["run"]["status"] == "failed"
    assert "blocked list" in decided["run"]["steps"][-1]["summary"]


def test_an_organization_approval_rule_keeps_asking() -> None:
    _settings(approved_templates=["order_status"])
    _approve(_run(SEND))  # the recipient becomes known
    rule = client.post(
        "/api/v1/organization/approval-rules",
        headers=ORG_ADMIN,
        json={"name": "WhatsApp needs a look", "action_pattern": "whatsapp.*", "minimum_approvers": 1, "required_roles": ["owner"]},
    )
    assert rule.status_code in {200, 201}, rule.text
    again = _run(SEND)
    assert again["approval"] is not None


def test_ask_once_remember_proposes_after_three_approvals_and_never_raises_silently() -> None:
    for _ in range(3):
        body = _run(ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "shipping"}))
        assert body["approval"] is not None  # template not approved: asks every time
        _approve(body)
    (proposal,) = client.get("/api/v1/autonomy/proposals", headers=OWNER).json()
    assert proposal["pattern_key"] == "whatsapp.send_template|template=shipping" and proposal["approvals"] == 3
    assert len(_events("autonomy.proposal_created")) == 1
    # Still asks until an owner accepts.
    assert _run(ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "shipping"}))["approval"]
    assert client.post(f"/api/v1/autonomy/proposals/{proposal['id']}/accept", headers=MEMBER).status_code == 403
    accepted = client.post(f"/api/v1/autonomy/proposals/{proposal['id']}/accept", headers=OWNER)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["level"] == "act_and_notify" and accepted.json()["source"] == "proposal"
    assert _run(ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "shipping"}))["approval"] is None
    assert client.post(f"/api/v1/autonomy/proposals/{proposal['id']}/accept", headers=OWNER).status_code == 409
    assert _audits("autonomy.proposal_accepted")


def test_a_declined_proposal_is_not_raised_again_and_protected_kinds_are_never_proposed() -> None:
    call = ToolCall(name="whatsapp.send_template", arguments={"to": "+966500000001", "template": "promo"})
    for _ in range(3):
        _approve(_run(call))
    (proposal,) = client.get("/api/v1/autonomy/proposals", headers=OWNER).json()
    declined = client.post(f"/api/v1/autonomy/proposals/{proposal['id']}/decline", headers=OWNER)
    assert declined.json()["status"] == "declined"
    for _ in range(3):
        _approve(_run(call))
    assert client.get("/api/v1/autonomy/proposals", headers=OWNER).json() == []
    for _ in range(4):
        _approve(_run(ToolCall(name="commerce.update_price", arguments={"sku": "T1", "old_price": 100, "new_price": 101})))
    assert not [p for p in autonomy_memory_store.proposals.values() if p.tool == "commerce.update_price"]


# -- undo, feed and tighten -----------------------------------------------------------------------


def _archived(who: dict[str, str] = OWNER, message_id: str = "m1") -> dict[str, Any]:
    body = _run(ToolCall(name="email.archive", arguments={"message_id": message_id}), who)
    assert body["approval"] is None
    page = client.get("/api/v1/autonomy/actions", headers=who).json()
    return next(item for item in page["items"] if item["tool"] == "email.archive")


def test_reversible_actions_can_be_undone_once_within_the_window() -> None:
    item = _archived()
    assert item["undo_available"] and item["undo_token"] and item["level"] == "act_and_notify"
    one = client.get(f"/api/v1/autonomy/actions/{item['id']}", headers=OWNER).json()
    assert one["undo_token"] == item["undo_token"]
    assert client.get("/api/v1/autonomy/actions/auto_missing", headers=OWNER).status_code == 404
    assert _events("autonomy.action_taken")[0].payload["undo_until"]
    wrong = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=OWNER, json={"undo_token": "undo_wrong_value"})
    assert wrong.status_code == 409
    done = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=OWNER, json={"undo_token": item["undo_token"]})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "undone" and done.json()["undone_by"] == "owner_a"
    assert UNDONE == [{"message_id": "m1"}]
    assert _events("autonomy.action_undone") and _audits("autonomy.action_undone")
    again = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=OWNER, json={"undo_token": item["undo_token"]})
    assert again.status_code == 409


def test_undo_after_the_window_is_gone_and_a_failed_undo_keeps_the_action() -> None:
    item = _archived()
    stored = autonomy_memory_store.actions[item["id"]]
    UNDO_FAILS["value"] = True
    failed = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=OWNER, json={"undo_token": item["undo_token"]})
    assert failed.status_code == 502 and "still in effect" in failed.json()["error"]["message"]
    assert autonomy_memory_store.actions[item["id"]].status == "done"
    assert _audits("autonomy.undo_failed")
    stored.undo_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    autonomy_memory_store.actions[item["id"]] = stored
    UNDO_FAILS["value"] = False
    late = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=OWNER, json={"undo_token": item["undo_token"]})
    assert late.status_code == 410


def test_only_owners_or_the_requester_can_undo() -> None:
    item = _archived(MEMBER)
    other = client.get("/api/v1/autonomy/actions", headers=OTHER_MEMBER).json()["items"][0]
    assert other["can_undo"] is False and other["undo_token"] is None
    refused = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=OTHER_MEMBER, json={"undo_token": item["undo_token"]})
    assert refused.status_code == 403 and "owner or the person" in refused.json()["error"]["message"]
    mine = client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=MEMBER, json={"undo_token": item["undo_token"]})
    assert mine.status_code == 200


def test_feed_filters_searches_pages_and_is_scoped_to_the_workspace() -> None:
    for index in range(5):
        _archived(message_id=f"m{index}")
    _run(ToolCall(name="anum.respond", arguments={"text": "Summary"}))
    first = client.get("/api/v1/autonomy/actions?limit=2&category=email", headers=OWNER).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = client.get(f"/api/v1/autonomy/actions?limit=10&category=email&cursor={first['next_cursor']}", headers=OWNER).json()
    assert len(rest["items"]) == 3 and rest["next_cursor"] is None
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in rest["items"]})
    assert len(client.get("/api/v1/autonomy/actions?level=act_silently", headers=OWNER).json()["items"]) == 1
    assert [i["summary"] for i in client.get("/api/v1/autonomy/actions?q=email%20m3", headers=OWNER).json()["items"]] == ["Archived email m3"]
    assert client.get("/api/v1/autonomy/actions?cursor=auto_missing", headers=OWNER).status_code == 400
    elsewhere = headers(workspace="workspace_autonomy_other")
    assert client.get("/api/v1/autonomy/actions", headers=elsewhere).json()["items"] == []
    item = first["items"][0]
    assert client.post(f"/api/v1/autonomy/actions/{item['id']}/undo", headers=elsewhere, json={"undo_token": "undo_whatever_x"}).status_code == 404


def test_always_ask_for_this_tightens_exactly_that_pattern() -> None:
    item = _archived()
    assert client.post(f"/api/v1/autonomy/actions/{item['id']}/tighten", headers=VIEWER).status_code == 403
    rule = client.post(f"/api/v1/autonomy/actions/{item['id']}/tighten", headers=MEMBER)
    assert rule.status_code == 201 and rule.json()["level"] == "ask_always" and rule.json()["source"] == "tighten"
    assert _run(ToolCall(name="email.archive", arguments={"message_id": "m9"}))["approval"] is not None
    listed = client.get("/api/v1/autonomy/actions", headers=OWNER).json()["items"][0]
    assert listed["pattern_level"] == "ask_always"
    # Only owners loosen again.
    assert client.delete(f"/api/v1/autonomy/rules/{rule.json()['id']}", headers=MEMBER).status_code == 403
    assert client.delete(f"/api/v1/autonomy/rules/{rule.json()['id']}", headers=OWNER).status_code == 204
    assert _audits("autonomy.rule_deleted")


# -- settings, guardrails and rules API --------------------------------------------------------------


def test_settings_defaults_permissions_floors_and_audit() -> None:
    overview = client.get("/api/v1/autonomy/settings", headers=VIEWER).json()
    assert overview["can_manage_workspace"] is False and overview["can_tighten"] is False
    assert {c["category"] for c in overview["categories"]} == {c.value for c in ToolCategory}
    assert next(r for r in overview["recommendations"] if r["kind"] == "price_change")["protected"] is True
    refused = _settings(MEMBER, recipients_threshold=3)
    assert refused.status_code == 403 and "workspace owner" in refused.json()["error"]["message"]
    assert client.put("/api/v1/autonomy/guardrails", headers=OWNER, json={"max_auto_recipients": 2}).status_code == 403
    assert client.put("/api/v1/autonomy/guardrails", headers=ORG_ADMIN, json={"max_auto_recipients": 2}).status_code == 200
    above = _settings(recipients_threshold=3)
    assert above.status_code == 422 and "at most 2" in above.json()["error"]["message"]
    saved = _settings(recipients_threshold=2, category_levels={"email": "act_and_notify"}, approved_templates=[" order_status ", "order_status"])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["settings"]["approved_templates"] == ["order_status"] and body["settings"]["updated_by"] == "owner_a"
    assert next(c for c in body["categories"] if c["category"] == "email")["effective"] == "act_and_notify"
    (audit,) = _audits("autonomy.settings_updated")
    assert audit.metadata["after"]["recipients_threshold"] == 2
    assert _audits("autonomy.guardrails_updated")[0].actor == "admin_f"
    assert _settings(quiet_hours={"start": "25:00", "end": "07:00", "timezone": "UTC"}).status_code == 422
    assert _settings(quiet_hours={"start": "22:00", "end": "07:00", "timezone": "Mars/Base"}).status_code == 422


def test_rules_api_refuses_what_must_always_ask() -> None:
    assert client.post("/api/v1/autonomy/rules", headers=MEMBER, json={"tool": "email.archive", "level": "act_and_notify"}).status_code == 403
    assert client.post("/api/v1/autonomy/rules", headers=MEMBER, json={"tool": "email.archive", "level": "ask_always"}).status_code == 201
    unknown = client.post("/api/v1/autonomy/rules", headers=OWNER, json={"tool": "nope.tool", "level": "act_and_notify"})
    assert unknown.status_code == 422
    price = client.post("/api/v1/autonomy/rules", headers=OWNER, json={"tool": "commerce.update_price", "level": "act_and_notify"})
    assert price.status_code == 422 and "always asks" in price.json()["error"]["message"]
    free_text = client.post("/api/v1/autonomy/rules", headers=OWNER, json={"tool": "whatsapp.send_template", "level": "act_and_notify"})
    assert free_text.status_code == 422
    silent = client.post("/api/v1/autonomy/rules", headers=OWNER, json={"tool": "email.archive", "level": "act_silently"})
    assert silent.status_code == 422 and "low-risk" in silent.json()["error"]["message"]
    ok = client.post("/api/v1/autonomy/rules", headers=OWNER, json={"tool": "whatsapp.send_template", "template": "eta", "level": "act_and_notify"})
    assert ok.status_code == 201
    rules = client.get("/api/v1/autonomy/rules", headers=VIEWER).json()
    assert {r["pattern_key"] for r in rules} == {"email.archive", "whatsapp.send_template|template=eta"}


def test_the_durable_path_re_evaluates_and_records_like_inline() -> None:
    """Temporal: plan, then begin_execution (a separate activity) re-checks autonomy."""
    import asyncio

    from anum_api.model_gateway import MockModelGateway
    from anum_api.repository import InMemoryRepository
    from anum_api.runtime import AgentRuntime
    from anum_api.schemas import RunPhase, Task, TaskStatus, new_id, utc_now

    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_a", roles=["owner"])
    repository = InMemoryRepository(store)
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_registry())
    PLANNED["call"] = ToolCall(name="email.archive", arguments={"message_id": "d1"})
    now = utc_now()
    task = Task(id=new_id("task"), tenant_id=TENANT, workspace_id=WORKSPACE, title="t", prompt="Archive it",
                status=TaskStatus.QUEUED, created_at=now, updated_at=now, created_by="owner_a")
    run = runtime.new_run(task, status=TaskStatus.QUEUED)
    assert asyncio.run(runtime.plan_run(task, run, context)) is None
    assert run.checkpoint.phase == RunPhase.TOOL_READY and run.checkpoint.autonomy["mode"] == "notify"
    # Quiet hours start between planning and execution: the worker's re-check asks instead.
    settings = WorkspaceAutonomySettings(quiet_hours=QuietHours(start="00:00", end="23:59", timezone="UTC"))
    autonomy_memory_store.save_settings(context, settings)
    assert runtime.begin_execution(task, run, context) is None
    assert run.checkpoint.phase == RunPhase.WAITING_APPROVAL and run.checkpoint.autonomy["guardrail"] == "quiet_hours"
    autonomy_memory_store.save_settings(context, WorkspaceAutonomySettings())
    run2 = runtime.new_run(task, status=TaskStatus.QUEUED)
    asyncio.run(runtime.plan_run(task, run2, context))
    call = runtime.begin_execution(task, run2, context)
    assert call is not None
    asyncio.run(runtime.finish_execution(task, run2, context, call))
    (action,) = autonomy_memory_store.list_actions(context, ActionFilter(), limit=5)
    assert action.tool == "email.archive" and action.undo_token and action.run_id == run2.id
