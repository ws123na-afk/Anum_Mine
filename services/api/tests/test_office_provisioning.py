"""Pure rules of governed agent provisioning: catalogue honesty, gaps, placement, hash binding."""
from __future__ import annotations

import fnmatch
from datetime import datetime, timedelta, timezone

from anum_api import agent_profiles as ap
from anum_api import main  # noqa: F401 (registers the runtime catalogue)
from anum_api import office_provisioning as op
from anum_api.executive_pa import TOOLS as PA_TOOLS
from anum_api.office_flow import FlowAgent, FlowDesk, FlowEvidence, FlowOrder, derive_flow

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


def test_every_template_names_registry_tools_and_consistent_skills() -> None:
    registry = ap.runtime_tools()
    skills = {m.id: m for m in ap.runtime_skills().manifests()}
    for t in op.TEMPLATES:
        assert t.name_en and t.name_ar and t.description_ar and t.work_kinds is not None
        for tool in t.tools:
            assert registry.definition(tool) is not None, f"{t.id}: {tool} is not a registered tool"
            assert registry.definition(tool).risk_level.value != "high", f"{t.id}: {tool} is high risk"
        for skill in t.skills:
            assert skill in skills, f"{t.id}: unknown skill {skill}"
            assert set(skills[skill].required_tools) <= set(t.tools) | {"anum.respond"}, f"{t.id}: {skill} needs more tools"
        assert t.monthly_cost_limit_usd > 0
        ids = [n.id for n in t.missing]
        assert len(ids) == len(set(ids))


def test_a_capability_called_missing_really_has_no_registered_tool() -> None:
    names = ap.runtime_tools().names
    for t in op.TEMPLATES:
        for need in t.missing:
            hits = [n for n in names for pattern in need.patterns if fnmatch.fnmatchcase(n, pattern)]
            assert not hits, f"{t.id}: {need.id} now exists as {hits}; attach it instead of calling it missing"


def test_templates_use_no_dashes_and_no_model_names_and_only_least_privilege() -> None:
    for t in op.TEMPLATES:
        text = " ".join([t.name_en, t.name_ar, t.description_en, t.description_ar, t.instructions])
        assert "–" not in text and "—" not in text
        assert not any(word in text.lower() for word in ("claude", "gpt", "opus", "sonnet", "haiku"))
        assert t.write_autonomy in ("ask", "off")
    fields = op.agent_fields(op.TEMPLATE_BY_ID["orders_desk"], op.Resolved(), "en")
    assert fields["autonomy"]["external"] == "off" and fields["sharing"] == "workspace"
    assert len(fields["template_id"]) <= 40 and ap.AgentProfileCreate(**fields)


def test_unavailable_tools_are_listed_missing_and_never_attached() -> None:
    t = op.TEMPLATE_BY_ID["orders_desk"]
    info = {"commerce.list_orders": {"risk": "low", "category": "read", "refusal": None},
            "commerce.get_order": {"risk": "low", "category": "read", "refusal": "Needs the owner role"}}
    skills = {"anum.commerce": {"name": "Commerce", "source": "builtin", "required_tools": ["commerce.sales_summary"]}}
    r = op.resolve_capabilities(t, tool_info=info, skill_info=skills)
    assert r.attach_tools == ["commerce.list_orders"]
    assert "anum.commerce" not in r.attach_skills  # its tool is not attached
    kinds = {(m["kind"], m["id"], m["reason"]) for m in r.missing}
    assert ("tool", "commerce.get_order", "not_permitted") in kinds and ("tool", "draft_reply", "not_registered") in kinds
    assert ("capability", "carrier_tracking", "no_tool_exists") in kinds and r.limited


def test_the_executive_assistant_has_no_tool_that_confirms_a_proposal() -> None:
    assert not [name for name in PA_TOOLS if "provision" in name or "agent" in name and "confirm" in name]
    assert not [n for n in ap.runtime_tools().names if "provision" in n]


def room(rid, dept=None, kind="office", capacity=2):
    return {"id": rid, "name": rid.upper(), "kind": kind, "capacity": capacity, "team_department": dept, "floor_id": "f", "building_id": "b"}


def desk(did, rid, agent=None, rev=1):
    return {"id": did, "room_id": rid, "label": did, "agent_id": agent, "revision": rev}


def test_placement_prefers_linked_vacant_desk_then_new_desk_then_nothing() -> None:
    t = op.TEMPLATE_BY_ID["finance_reconciliation"]
    plan = op.plan_placement(t, [room("r1", "finance"), room("r2")], [desk("d1", "r1", "a"), desk("d2", "r1")])
    assert (plan["mode"], plan["desk_id"], plan["room_id"]) == ("existing_desk", "d2", "r1")
    plan = op.plan_placement(t, [room("r1", "finance")], [desk("d1", "r1", "a")])
    assert plan["mode"] == "new_desk" and plan["desk_id"].endswith("-finance_reconciliation")
    plan = op.plan_placement(t, [room("r1", "finance", capacity=1)], [desk("d1", "r1", "a")])
    assert plan["mode"] == "none" and plan["reason"] == "linked_rooms_full"
    plan = op.plan_placement(t, [room("r1"), room("m", kind="meeting")], [])
    assert plan["mode"] == "none" and plan["reason"] == "no_linked_room"
    assert [c["room_id"] for c in plan["candidates"]] == ["r1"]  # meeting rooms never get permanent desks
    chosen = op.plan_placement(t, [room("r1"), room("r2")], [], "r2")
    assert chosen["mode"] == "new_desk" and chosen["room_id"] == "r2" and chosen["reason"] == "chosen_by_you"
    assert op.plan_placement(t, [room("m", kind="meeting")], [], "m")["mode"] == "invalid_room"


def test_hash_binds_tenant_workspace_user_and_content() -> None:
    a = op.proposal_hash("t", "w", "u", {"x": 1})
    assert a == op.proposal_hash("t", "w", "u", {"x": 1})
    assert len({a, op.proposal_hash("t2", "w", "u", {"x": 1}), op.proposal_hash("t", "w2", "u", {"x": 1}),
                op.proposal_hash("t", "w", "u2", {"x": 1}), op.proposal_hash("t", "w", "u", {"x": 2})}) == 5


def item(kind, stage="arrived", reason=None, stuck=None, location=None):
    return {"kind": kind, "stage": stage, "location": location, "location_reason": reason, "stuck": stuck}


def test_gaps_need_connected_sources_and_clear_with_a_handler() -> None:
    items = [item("order", reason="no_order_office", stuck={"code": "order_ship_overdue", "severity": "critical"}),
             item("lead", reason="no_owner"), item("order", stage="done", reason="no_order_office")]
    off = op.detect_gaps(items=items, sources={"orders": "not_connected", "leads": "no_access"}, rooms=[], agents=[], provisions=[])
    assert off["gaps"] == []
    on = op.detect_gaps(items=items, sources={"orders": "connected", "leads": "connected"}, rooms=[], agents=[], provisions=[])
    by = {g["template_id"]: g for g in on["gaps"]}
    assert by["orders_desk"]["severity"] == "critical" and by["orders_desk"]["reasons"][0]["count"] == 1  # done ones are not counted
    assert by["sales_followup"]["reasons"][0]["code"] == "leads_without_owner"
    agent = op.AgentFact("ag", "Orders", "paused", "operations", "hq:orders_desk")
    done = op.detect_gaps(items=items, sources={"orders": "connected", "leads": "connected"}, rooms=[], agents=[agent],
                          provisions=[{"template_id": "orders_desk", "status": "active", "agent_id": "ag"}])
    assert "orders_desk" not in {g["template_id"] for g in done["gaps"]}
    cov = {c["template_id"]: c for c in done["coverage"]}
    assert cov["orders_desk"]["state"] == "covered" and cov["orders_desk"]["handler"]["agent_status"] == "paused"
    ghost = op.detect_gaps(items=items, sources={"orders": "connected", "leads": "connected"}, rooms=[], agents=[],
                           provisions=[{"template_id": "orders_desk", "status": "active", "agent_id": "deleted"}])
    assert "orders_desk" in {g["template_id"] for g in ghost["gaps"]}  # a deleted handler is a gap again


def test_empty_department_counts_only_active_agents_of_that_department() -> None:
    rooms = [room("r1", "sales"), room("r2", "finance")]
    agents = [op.AgentFact("a", "Sam", "active", "Sales", None), op.AgentFact("b", "Fin", "paused", "finance", None)]
    gaps = op.detect_gaps(items=[], sources={}, rooms=rooms, agents=agents, provisions=[])["gaps"]
    assert [g["template_id"] for g in gaps] == ["finance_reconciliation"]


def test_flow_seats_orders_in_the_handlers_office_only_when_a_handler_exists() -> None:
    order = FlowOrder("o1", "E1", "unshipped", NOW - timedelta(hours=2), None, NOW)
    base = dict(as_of=NOW, orders=[order], sources={"orders": "connected"})
    agent = FlowAgent("ag", "Orders", "active")
    d = FlowDesk("d1", "r1", "Ops", "f", "b", "ag")
    none = derive_flow(FlowEvidence(agents=[agent], desks=[d], **base))["items"][0]
    assert none["location"] is None and none["location_reason"] == "no_order_office"
    placed = derive_flow(FlowEvidence(agents=[agent], desks=[d], handlers={"orders": "ag"}, **base))["items"][0]
    assert placed["location"]["room_id"] == "r1" and placed["location"]["via"] == "handler" and placed["stage"] == "in_office"
    nodesk = derive_flow(FlowEvidence(agents=[agent], desks=[], handlers={"orders": "ag"}, **base))["items"][0]
    assert nodesk["location"] is None and nodesk["location_reason"] == "agent_has_no_desk"
    paused = derive_flow(FlowEvidence(agents=[FlowAgent("ag", "Orders", "paused")], desks=[d], handlers={"orders": "ag"}, **base))["items"][0]
    assert paused["stuck"]["code"] == "agent_paused"


def test_every_new_department_has_a_least_privilege_template_with_a_cost_cap() -> None:
    from anum_api import office_layout as layout
    wanted = {"marketing": "marketing_specialist", "technology": "technology_coordinator", "research": "research_analyst",
              "legal": "legal_reviewer", "compliance": "compliance_reviewer"}
    for department, template_id in wanted.items():
        t = op.TEMPLATE_BY_ID[template_id]
        assert t.department == department and t.room_department == department
        assert department in layout.TEAM_DEPARTMENTS, "a room can be linked to this department"
        assert 0 < t.monthly_cost_limit_usd <= 10
        assert t.write_autonomy in ("ask", "off") and t.tools and t.skills
        assert "You never:" in t.instructions and "What you hand back:" in t.instructions
    for t in op.TEMPLATES:
        assert t.room_department is None or t.room_department in layout.TEAM_DEPARTMENTS
        assert t.description_en.endswith(".") and t.description_ar


def test_roles_state_what_they_cannot_do_and_hold_no_outward_tool() -> None:
    registry = ap.runtime_tools()
    for t in op.TEMPLATES:
        for tool in t.tools:
            assert registry.definition(tool).risk_level.value in ("low", "medium"), f"{t.id}: {tool}"
            assert not tool.startswith(("send", "brevo.send", "marketing.schedule", "marketing.enable", "developer.merge"))
    # Capabilities the platform really lacks are listed (so the role shows as Limited), never faked with a tool.
    for template_id in ("technology_coordinator", "research_analyst", "legal_reviewer", "marketing_specialist"):
        assert op.resolve_capabilities(op.TEMPLATE_BY_ID[template_id], tool_info={}, skill_info={}).limited
    skills = {m.id for m in ap.runtime_skills().manifests()}
    live = {n: {"risk": "low", "category": "x", "refusal": None} for n in ap.runtime_tools().names}
    info = {m.id: {"name": m.name, "source": "builtin", "required_tools": list(m.required_tools)} for m in ap.runtime_skills().manifests()}
    assert skills
    for template_id in ("marketing_specialist", "technology_coordinator", "research_analyst", "legal_reviewer"):
        r = op.resolve_capabilities(op.TEMPLATE_BY_ID[template_id], tool_info=live, skill_info=info)
        assert r.attach_tools and r.attach_skills, template_id
        assert not [m for m in r.missing if m["kind"] in ("tool", "skill")], f"{template_id}: {r.missing}"
