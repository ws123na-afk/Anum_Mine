"""Staff my campus: roles, room rules and the pure planner (no database)."""
from __future__ import annotations

import fnmatch

from anum_api import agent_profiles as ap
from anum_api import main  # noqa: F401 (registers the runtime catalogue)
from anum_api import office_provisioning as op
from anum_api import office_staff_desks as sd
from anum_api import office_staff_templates as st
from anum_api import office_starter_campus as sc
from anum_api.executive_pa import TOOLS as PA_TOOLS


def test_every_role_names_registry_tools_and_consistent_skills() -> None:
    registry = ap.runtime_tools()
    skills = {m.id: m for m in ap.runtime_skills().manifests()}
    assert len(st.EXTRA_TEMPLATES) == 7 and len({t.id for t in st.STAFF_TEMPLATES}) == len(st.STAFF_TEMPLATES) == 18
    for t in st.STAFF_TEMPLATES:
        assert t.name_en and t.name_ar and t.description_ar
        for tool in t.tools:
            assert registry.definition(tool) is not None, f"{t.id}: {tool} is not a registered tool"
            assert registry.definition(tool).risk_level.value != "high", f"{t.id}: {tool} is high risk"
        for skill in t.skills:
            assert skill in skills, f"{t.id}: unknown skill {skill}"
            assert set(skills[skill].required_tools) <= set(t.tools) | {"anum.respond"}, f"{t.id}: {skill} needs more tools"
        assert t.write_autonomy in ("ask", "off") and t.monthly_cost_limit_usd > 0
        assert ap.AgentProfileCreate(**op.agent_fields(t, op.Resolved(), "en"))


def test_a_capability_called_missing_really_has_no_registered_tool() -> None:
    names = ap.runtime_tools().names
    for t in st.EXTRA_TEMPLATES:
        assert t.missing, f"{t.id} lists nothing missing"
        for need in t.missing:
            hits = [n for n in names for pattern in need.patterns if fnmatch.fnmatchcase(n, pattern)]
            assert not hits, f"{t.id}: {need.id} now exists as {hits}; attach it instead of calling it missing"


def test_role_text_has_no_dashes_or_model_names() -> None:
    for t in st.EXTRA_TEMPLATES:
        text = " ".join([t.name_en, t.name_ar, t.description_en, t.description_ar, t.instructions])
        assert "–" not in text and "—" not in text
        assert not any(word in text.lower() for word in ("claude", "gpt", "opus", "sonnet", "haiku"))


def test_no_tool_the_assistant_can_call_confirms_staffing() -> None:
    assert not [n for n in PA_TOOLS if "staff" in n or "provision" in n]
    assert not [n for n in ap.runtime_tools().names if "staff_desk" in n or "provision" in n]


def test_every_desk_of_the_starter_campus_gets_a_role_and_the_counts_are_stable() -> None:
    per_role: dict[str, int] = {}
    for b in sc.CAMPUS:
        for f in b.floors:
            for r in f.rooms:
                room = {"name": r.en, "kind": r.kind, "team_department": r.department}
                tid, source = st.template_for_room(room)
                if r.desks:
                    assert tid in st.STAFF_TEMPLATE_BY_ID, f"{r.en}: no role"
                    per_role[tid] = per_role.get(tid, 0) + r.desks
                if r.kind == "reception":
                    assert tid == "receptionist" and source == "kind"
    assert sum(per_role.values()) == 220
    assert per_role["orders_desk"] == 11 and per_role["support_triage"] == 20 and per_role["hr_people"] == 10
    assert per_role["fleet_maintenance"] == 21 and per_role["procurement"] == 8 and per_role["security_reviewer"] == 5


def test_the_stored_department_decides_and_an_unknown_room_gets_no_role() -> None:
    assert st.template_for_room({"name": "Back room", "kind": "office", "team_department": "finance"}) == ("finance_reconciliation", "department")
    assert st.template_for_room({"name": "Procurement", "kind": "office", "team_department": "operations"}) == ("procurement", "name")
    assert st.template_for_room({"name": "Zebra", "kind": "office", "team_department": None}) == (None, "none")


def test_names_are_role_room_and_number_never_a_person() -> None:
    orders = st.STAFF_TEMPLATE_BY_ID["orders_desk"]
    assert st.agent_name(orders, "Orders", "الطلبات", 2, "en") == "Orders Desk, Orders 2"
    assert st.agent_name(orders, "Orders", "الطلبات", 2, "ar") == "مكتب الطلبات، الطلبات 2"
    assert len(st.agent_name(orders, "R" * 200, None, 12, "en")) <= 80
    marker = st.desk_marker("orders_desk", "campus-02-commerce-orders-d1")
    assert marker == st.desk_marker("orders_desk", "campus-02-commerce-orders-d1") and len(marker) <= 40 and marker.count(":") == 2


def world(desks_per_room=(3, 2), occupied=(), ledger=None, agent_count=0, selected=None, exclude=(), cap=None):
    buildings = [{"id": "b1", "name": "B1"}, {"id": "b2", "name": "B2"}]
    floors = [{"id": "f1", "building_id": "b1", "level": 0, "name": "G"}, {"id": "f2", "building_id": "b2", "level": 0, "name": "G"}]
    rooms = [{"id": "r1", "floor_id": "f1", "name": "Orders", "name_ar": None, "kind": "office", "capacity": 6, "team_department": "operations"},
             {"id": "r2", "floor_id": "f2", "name": "Zebra", "name_ar": None, "kind": "office", "capacity": 6, "team_department": None}]
    desks = [{"id": f"r1-d{i}", "room_id": "r1", "label": f"D{i}", "agent_id": "a-occ" if f"r1-d{i}" in occupied else None}
             for i in range(1, desks_per_room[0] + 1)]
    desks += [{"id": f"r2-d{i}", "room_id": "r2", "label": f"D{i}", "agent_id": None} for i in range(1, desks_per_room[1] + 1)]
    return sd.plan_staffing(buildings=buildings, floors=floors, rooms=rooms, desks=desks, ledger=ledger or {}, live_agents=set(),
                            markers={}, agent_count=agent_count, selected=selected, exclude_rooms=set(exclude), max_agents=cap, lang="en")


def states(plan):
    return {d["id"]: d["state"] for b in plan["buildings"] for r in b["rooms"] for d in r["desks"]}


def test_planner_never_touches_occupied_or_keep_empty_desks_and_names_by_position() -> None:
    plan = world(occupied=("r1-d1",), ledger={"r1-d2": {"status": "keep_empty", "agent_id": None}})
    assert states(plan) == {"r1-d1": "occupied", "r1-d2": "keep_empty", "r1-d3": "plan", "r2-d1": "no_role", "r2-d2": "no_role"}
    item = sd.batch_items(plan["buildings"][0])[0]
    assert item["name"] == "Orders Desk, Orders 3"  # the number is the desk position, not a counter


def test_planner_respects_the_200_limit_and_the_run_cap_in_a_fixed_order() -> None:
    assert [d["state"] for d in world(agent_count=198)["buildings"][0]["rooms"][0]["desks"]] == ["plan", "plan", "over_limit"]
    plan = world(cap=1)
    assert [d["state"] for d in plan["buildings"][0]["rooms"][0]["desks"]] == ["plan", "over_cap", "over_cap"]
    assert world(agent_count=200)["free_slots_after"] == 0


def test_unticked_buildings_and_rooms_are_excluded() -> None:
    assert set(states(world(selected={"b2"})).values()) == {"excluded", "no_role"}
    assert set(states(world(exclude=("r1",))).values()) == {"excluded", "no_role"}


def test_an_agent_an_interrupted_run_built_is_resumed_not_created_again() -> None:
    ledger = {"r1-d1": {"status": "claimed", "agent_id": "agent_x", "template_id": "operations_dispatcher"}}
    buildings = [{"id": "b1", "name": "B1"}]
    plan = sd.plan_staffing(
        buildings=buildings, floors=[{"id": "f1", "building_id": "b1", "level": 0, "name": "G"}],
        rooms=[{"id": "r1", "floor_id": "f1", "name": "Orders", "kind": "office", "capacity": 4, "team_department": None}],
        desks=[{"id": "r1-d1", "room_id": "r1", "label": "D1", "agent_id": None}], ledger=ledger, live_agents={"agent_x"},
        markers={}, agent_count=200, selected=None, exclude_rooms=set(), max_agents=None, lang="en")
    desk = plan["buildings"][0]["rooms"][0]["desks"][0]
    assert desk["state"] == "resume" and desk["agent"]["existing_agent_id"] == "agent_x"  # even at the limit: nothing new is created


def test_the_hash_binds_tenant_workspace_user_building_and_every_item() -> None:
    items = sd.batch_items(world()["buildings"][0])
    base = sd.batch_hash("t", "w", "u", "b1", "en", items)
    assert base == sd.batch_hash("t", "w", "u", "b1", "en", items)
    for other in (sd.batch_hash("t2", "w", "u", "b1", "en", items), sd.batch_hash("t", "w2", "u", "b1", "en", items),
                  sd.batch_hash("t", "w", "u2", "b1", "en", items), sd.batch_hash("t", "w", "u", "b2", "en", items),
                  sd.batch_hash("t", "w", "u", "b1", "ar", items), sd.batch_hash("t", "w", "u", "b1", "en", items[:-1])):
        assert other != base


def test_cost_is_the_sum_of_caps() -> None:
    cost = sd.cost_summary(world()["buildings"])
    assert cost["agents"] == 3 and cost["max_monthly_usd"] == 15.0
