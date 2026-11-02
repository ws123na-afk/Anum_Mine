"""Green Concrete agents kit (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md), memory backend: the skill and tool catalog,
risk ceilings, Arabic for every string, the planner, the read tools' sections, agent working memory (kind, source,
date, edit, retention, provenance, file sources the agent may read), knowledge collections as agent sources and the
automation step that runs as an agent."""
from __future__ import annotations

import asyncio
import fnmatch
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import green_concrete_kit as gk
from anum_api import green_concrete_ops as ops
from anum_api import office_provisioning as op
from anum_api import office_staff_templates as st
from anum_api import office_staffing_blueprint as bp
from anum_api.agent_planning import choose_tool_call
from anum_api.agent_profiles import AgentProfile, agent_store, configure_runtime_catalog, runtime_skills
from anum_api.agent_tools import ToolCall
from anum_api.governance import MemoryGovernance, governance_store
from anum_api.main import app, store, tool_registry
from anum_api.office_green_concrete import PEOPLE
from anum_api.schemas import RiskLevel, Task, TaskStatus, TenantContext, utc_now
from anum_api.tyre.model import (
    Checklist,
    EntityStats,
    HourReading,
    InspectionSchedule,
    OdometerReading,
    SyncStats,
    Tyre,
    TyreDataset,
    Vehicle,
    WorkOrder,
)

TENANT, WORKSPACE = "tenant_gc_kit", "workspace_gc_kit"
ARABIC = re.compile(r"[؀-ۿ]")
LONG_DASHES = re.compile("[–—]")


def headers(user: str = "user_owner", role: str = "owner", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def ctx(role: str = "owner", user: str = "user_owner") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=[role])


OWNER = headers()
MEMBER = headers("user_member", "member")
VIEWER = headers("user_viewer", "viewer")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    agent_store.clear()
    governance_store.clear()
    store.audit_records.clear()
    configure_runtime_catalog(tool_registry)
    yield
    agent_store.clear()
    governance_store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- catalog integrity


def test_every_skill_names_one_registered_low_risk_read_tool() -> None:
    assert len({s.id for s in gk.SKILLS}) == len(gk.SKILLS) and len(gk.TOOL_NAMES) == len(set(gk.TOOL_NAMES))
    used = {s.tool for s in gk.SKILLS}
    assert used == set(gk.TOOL_NAMES)  # no tool without a skill, no skill without its tool
    for name in gk.TOOL_NAMES:
        definition = tool_registry.definition(name)
        assert definition is not None, name
        assert definition.risk_level == RiskLevel.LOW and definition.action_kind == "read" and definition.idempotent
        assert definition.required_organization_permission is None and definition.delivery_channel is None


def test_skills_are_agent_only_runtime_skills_with_reviewed_instructions() -> None:
    registry = runtime_skills()
    for skill in gk.SKILLS:
        manifest = registry.get(skill.id)
        assert manifest is not None and manifest.agent_only and manifest.required_tools == frozenset({skill.tool})
        assert manifest.name_ar and manifest.description_ar
        text = manifest.instructions
        assert skill.tool in text and "Not connected" in text and "never grant tools" in text
        assert "Inputs:" in text and "Output:" in text and "Rules:" in text
        for source in skill.sources:
            assert source in bp.SOURCE_BY_ID, source  # every data source the skill needs is a known source
    # The plain Assistant never selects a role skill; an agent that allows one does.
    assert not [m for m in registry.select("prepare the daily brief", set(gk.TOOL_NAMES)) if m.id.startswith("gc.")]
    picked = registry.select("prepare the daily brief", set(gk.TOOL_NAMES), include_agent_only=True)
    assert "gc.daily-ops-brief" in {m.id for m in picked}


def test_no_tool_satisfies_a_need_the_templates_call_missing() -> None:
    patterns = [p for t in (*op.TEMPLATES, *st.EXTRA_TEMPLATES) for need in t.missing for p in need.patterns]
    for name in gk.TOOL_NAMES:
        assert not any(fnmatch.fnmatch(name, p) for p in patterns), name


def test_role_kits_only_add_and_stay_under_each_role_risk_ceiling() -> None:
    gc_roles = set(bp.UNIT_BY_ID["green_concrete"].roles)
    assert set(gk.ROLE_KIT) == gc_roles
    for key, (skills, tools) in gk.ROLE_KIT.items():
        role = bp.ROLE_BY_KEY[key]
        base = st.STAFF_TEMPLATE_BY_ID[role.template_id]
        assert set(base.tools) <= set(role.template.tools) and set(base.skills) <= set(role.template.skills)
        assert set(tools) <= set(role.template.tools) and set(skills) <= set(role.template.skills)
        for skill in skills:
            assert gk.SKILL_BY_ID[skill].tool in tools  # a skill always comes with its tool
        for name in tools:
            risk = tool_registry.definition(name).risk_level.value
            assert bp.RISK_ORDER[risk] <= bp.RISK_ORDER[role.max_risk]
        assert role.knowledge_collection
    # Roles outside Green Concrete are untouched.
    assert bp.ROLE_BY_KEY["fleet_maintenance"].template is st.STAFF_TEMPLATE_BY_ID["fleet_maintenance"]


def test_role_view_attaches_the_kit_where_the_registry_has_it() -> None:
    from anum_api.office_staffing_api import _capabilities

    tool_info, skill_info = _capabilities(ctx())
    view = bp.role_view(bp.ROLE_BY_KEY["gc_pmv_manager"], tool_info, skill_info)
    assert {"gc.ops_brief", "gc.fleet_health"} <= set(view["attach_tools"])
    assert {"gc.daily-ops-brief", "gc.pmv-fleet-health"} <= set(view["attach_skills"])
    assert all(t["state"] != "over_risk" for t in view["tools"])


def test_every_string_has_arabic_and_no_long_dashes() -> None:
    texts = list(gk.strings())
    assert texts
    for text in texts:
        assert text.en.strip() and ARABIC.search(text.ar), text
        assert not LONG_DASHES.search(text.en + text.ar), text
    for skill in gk.SKILLS:
        assert not LONG_DASHES.search(skill.instructions)
    for tool in gk.TOOLS:
        assert not LONG_DASHES.search(tool.description)


def test_routines_name_a_kit_role_and_skill_and_a_valid_schedule() -> None:
    from anum_api import cron

    for routine in gk.ROUTINES:
        assert routine.skill in gk.ROLE_KIT[routine.role][0]
        cron.validate(routine.cron, "Asia/Dubai")
        assert "Not connected" in routine.prompt


# --------------------------------------------------------------------------- planner


def test_planner_picks_the_skill_the_users_words_call_for_with_the_period() -> None:
    skills = [s for s in runtime_skills().manifests() if s.id.startswith("gc.")]
    call = choose_tool_call("Prepare today's daily operations brief", skills, "")
    assert call.name == "gc.ops_brief"
    call = choose_tool_call("Write the shift handover note", skills, "")
    assert call.name == "gc.shift_handover"
    call = choose_tool_call("maintenance cost between 2026-10-01 and 2026-10-31", skills, "")
    assert call.name == "gc.maintenance_costs" and call.arguments == {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    call = gk.choose_call("tyre purchase plan for 6 months", ["gc.tyre-plan"], date(2026, 11, 2))
    assert call.name == "gc.tyre_plan" and call.arguments["months"] == 6
    assert gk.choose_call("anything", ["anum.email"]) is None


# --------------------------------------------------------------------------- sections (pure)

TODAY = date(2026, 11, 2)


def dataset(**over) -> TyreDataset:  # type: ignore[no-untyped-def]
    base = {
        "vehicles": [Vehicle(id="V1", status="active", site="North"), Vehicle(id="V2", status="workshop", site="North")],
        "work_orders": [
            WorkOrder(id="W1", opened_at=TODAY - timedelta(days=10), vehicle_id="V1", work_type="Breakdown repair", total_cost=500),
            WorkOrder(id="W2", opened_at=TODAY - timedelta(days=2), vehicle_id="V2", work_type="Service", status="closed",
                      completed_at=TODAY - timedelta(days=1)),
            WorkOrder(id="W3", opened_at=TODAY - timedelta(days=3), work_type="Tyre change", total_cost=200),
        ],
        "inspection_schedules": [InspectionSchedule(id="S1", scheduled_for=TODAY - timedelta(days=1), vehicle_id="V1"),
                                 InspectionSchedule(id="S2", scheduled_for=TODAY + timedelta(days=5), vehicle_id="V2")],
        "odometer_readings": [OdometerReading(vehicle_id="V1", read_at=TODAY - timedelta(days=20), odometer_km=1000),
                              OdometerReading(vehicle_id="V1", read_at=TODAY - timedelta(days=1), odometer_km=1800)],
        "hour_readings": [HourReading(vehicle_id="V2", read_at=TODAY - timedelta(days=9), hours=10),
                          HourReading(vehicle_id="V2", read_at=TODAY - timedelta(days=2), hours=40)],
        "checklists": [Checklist(id="C1", submitted_at=TODAY - timedelta(days=2), vehicle_id="V2", passed=False)],
        "tyres": [Tyre(id="T1", vehicle_id="V1")],
        "currency": "AED",
    }
    return TyreDataset(**(base | over))


def _states(sections) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {s["id"]: s["state"] for s in sections}


def test_fleet_health_counts_only_what_the_records_say() -> None:
    sections = ops.fleet_health_sections(dataset(), TODAY, TODAY - timedelta(days=29), TODAY)
    by = {s["id"]: s for s in sections}
    assert by["maintenance_due"]["data"]["open_work_orders"] == 2 and by["maintenance_due"]["data"]["open_over_7_days"] == 1
    assert by["maintenance_due"]["data"]["inspections_overdue"] == 1 and by["maintenance_due"]["data"]["inspections_next_14_days"] == 1
    assert by["breakdowns"]["data"]["breakdowns"] == 1 and by["breakdowns"]["data"]["by_vehicle"] == {"V1": 1}
    assert by["use"]["data"]["total_km"] == 800 and by["use"]["data"]["total_hours"] == 30
    assert by["cost"]["data"]["total"] == 700 and by["cost"]["data"]["currency"] == "AED"


def test_fleet_health_says_not_connected_instead_of_zero() -> None:
    bare = dataset(work_orders=[WorkOrder(id="W1", opened_at=TODAY)], odometer_readings=[], hour_readings=[], inspection_schedules=[])
    states = _states(ops.fleet_health_sections(bare, TODAY, TODAY - timedelta(days=29), TODAY))
    assert states["breakdowns"] == "not_connected" and states["use"] == "not_connected"
    empty = TyreDataset()
    states = _states(ops.fleet_health_sections(empty, TODAY, TODAY - timedelta(days=29), TODAY))
    assert states["maintenance_due"] == "not_connected" and states["cost"] == "not_connected" and states["fleet"] == "empty"


def test_data_quality_lists_missing_records_and_a_stale_sync() -> None:
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    stats = SyncStats(synced_at=now - timedelta(hours=30),
                      entities={"tyres": EntityStats(read=10, kept=8, skipped=2, issues={"missing serial": 2})})
    by = {s["id"]: s for s in ops.data_quality_sections(stats, dataset(), now)}
    assert by["sync"]["data"]["stale"] is True and "tyres (2)" in by["sync"]["summary"]
    missing = by["missing"]["data"]
    assert missing["vehicles_not_inspected_30_days"]["count"] == 2  # no inspection visits or readings in the data
    assert missing["fitted_tyres_without_tread"]["count"] == 1
    assert missing["work_orders_without_vehicle"]["count"] == 1 and missing["closed_work_orders_without_cost"]["count"] == 1
    assert missing["vehicles_without_readings_30_days"]["count"] == 0


def test_cost_review_has_fuel_not_connected() -> None:
    sections = ops.maintenance_cost_sections(dataset(), TODAY - timedelta(days=29), TODAY)
    by = {s["id"]: s for s in sections}
    assert by["fuel"]["state"] == "not_connected" and by["work_orders"]["data"]["total"] == 700
    assert by["work_orders"]["data"]["without_cost"] == 1


def test_fleet_watch_lists_overdue_and_failed_checklists() -> None:
    by = {s["id"]: s for s in ops.fleet_watch_sections(dataset(), TODAY)}
    assert by["inspections"]["data"]["overdue"] == 1 and by["checklists"]["data"]["failed_7_days"] == 1


def _seats() -> list[dict]:
    return [{"desk_id": f"d{i}", "label": f"Seat {i}", "work_start": p.hours[0], "work_end": p.hours[1], "shift": None,
             "timezone": "Asia/Dubai", "agent_name": p.name, "department": p.role_en} for i, p in enumerate(PEOPLE, 1)]


@pytest.mark.parametrize(("hour", "on", "ending"), [
    (7, {"Ijaz", "Ali Zeehan", "Rana"}, {"Ijaz", "Ali Zeehan", "Rana"}),
    (12, {p.name for p in PEOPLE} - {"Amjad"}, {"Ijaz", "Ali Zeehan", "Rana"}),
    (21, {"Amjad"}, {"Amjad"}),
])
def test_team_follows_each_persons_own_hours(hour: int, on: set[str], ending: set[str]) -> None:
    now = datetime(2026, 10, 26, hour - 4, tzinfo=UTC)  # Dubai is UTC+4
    team, began = ops.team_section(_seats(), now)
    assert {p["name"] for p in team["data"]["people"] if p["on_shift"]} == on
    assert set(team["data"]["ending_next"]) == ending
    assert began is not None and team["data"]["timezone"] == "Asia/Dubai"


def test_team_without_seats_is_empty_not_invented() -> None:
    team, began = ops.team_section([], utc_now())
    assert team["state"] == "empty" and began is None


def test_tasks_overdue_and_changed_this_shift() -> None:
    now = datetime(2026, 11, 2, 10, tzinfo=UTC)

    def task(i: int, due: datetime | None, updated: datetime) -> Task:
        return Task(id=f"t{i}", title=f"Task {i}", prompt="p", status=TaskStatus.CREATED, tenant_id=TENANT, workspace_id=WORKSPACE,
                    created_at=updated, updated_at=updated, due_at=due)

    sections = ops.task_sections({"created": 2, "waiting_approval": 1}, [task(1, now - timedelta(hours=1), now - timedelta(hours=5)),
                                                                          task(2, None, now - timedelta(minutes=10))],
                                 now, now - timedelta(hours=2))
    by = {s["id"]: s for s in sections}
    assert by["tasks"]["data"]["overdue"] == 1 and by["tasks"]["data"]["waiting_approval"] == 1
    assert [t["id"] for t in by["tasks_this_shift"]["data"]["changed"]] == ["t2"]


def test_tools_say_not_connected_or_no_access_without_data() -> None:
    result = asyncio.run(tool_registry.execute(ToolCall(name="gc.fleet_health"), ctx("member")))
    assert result.status == "succeeded"
    assert result.output["sections"][0]["state"] == "not_connected"
    assert result.audit_metadata == {"outcome": "answered", "sections": {"fleet": "not_connected"}}
    denied = asyncio.run(tool_registry.execute(ToolCall(name="gc.data_quality"), ctx("guest")))
    assert denied.output["sections"][0]["state"] == "no_access"
    brief = asyncio.run(tool_registry.execute(ToolCall(name="gc.ops_brief"), ctx("member")))
    states = _states(brief.output["sections"])
    assert states["team"] == "not_connected"  # seats live in the office, PostgreSQL only
    assert states["alerts"] == "not_connected" and states["tasks"] in {"ok", "empty"}


# --------------------------------------------------------------------------- agent memory


def _agent(client: TestClient, hdrs=OWNER, **fields) -> dict:  # type: ignore[no-untyped-def]
    body = {"name": "PMV manager", "category": "work", "sharing": "workspace"} | fields
    r = client.post("/api/v1/agents", headers=hdrs, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_memory_has_kind_source_date_and_can_be_edited_and_deleted(client: TestClient) -> None:
    agent = _agent(client)
    url = f"/api/v1/agents/{agent['id']}/memories"
    r = client.post(url, headers=OWNER, json={"content": "Tyres are bought from the approved supplier list only",
                                              "kind": "decision", "source_label": "Owner, weekly meeting", "source_date": "2026-11-01"})
    assert r.status_code == 201, r.text
    memory = r.json()
    assert memory["kind"] == "decision" and memory["source_label"] == "Owner, weekly meeting" and memory["source_date"] == "2026-11-01"
    assert memory["expires_at"] is None and memory["updated_at"] is None
    edited = client.patch(f"{url}/{memory['id']}", headers=OWNER, json={"content": "Updated rule", "kind": "fact"})
    assert edited.status_code == 200 and edited.json()["content"] == "Updated rule" and edited.json()["updated_by"] == "user_owner"
    assert client.patch(f"{url}/{memory['id']}", headers=OWNER, json={}).status_code == 422
    listed = client.get(url, headers=OWNER, params={"kind": "fact"}).json()
    assert listed["total"] == 1 and listed["items"][0]["content"] == "Updated rule"
    assert client.get(url, headers=OWNER, params={"kind": "decision"}).json()["total"] == 0
    assert client.get(url, headers=OWNER, params={"q": "weekly"}).json()["total"] == 1  # the source is searchable
    actions = [a.action for a in store.audit_records if a.tenant_id == TENANT]
    assert "agent_memory.updated" in actions
    assert all("Updated rule" not in str(a.metadata) for a in store.audit_records)
    assert client.delete(f"{url}/{memory['id']}", headers=OWNER).status_code == 204
    assert client.get(url, headers=OWNER).json()["total"] == 0


def test_memory_permissions_and_isolation(client: TestClient) -> None:
    agent = _agent(client)
    url = f"/api/v1/agents/{agent['id']}/memories"
    memory = client.post(url, headers=OWNER, json={"content": "Fact"}).json()
    assert client.post(url, headers=VIEWER, json={"content": "x"}).status_code == 403
    assert client.patch(f"{url}/{memory['id']}", headers=VIEWER, json={"content": "x"}).status_code == 403
    # A member may read a shared agent's memories but only its creator or a workspace owner edits them.
    assert client.get(url, headers=MEMBER).status_code == 200
    assert client.patch(f"{url}/{memory['id']}", headers=MEMBER, json={"content": "x"}).status_code == 403
    other = headers(workspace="workspace_other")
    assert client.get(url, headers=other).status_code == 404
    assert client.patch(f"{url}/{memory['id']}", headers=other, json={"content": "x"}).status_code == 404
    assert client.patch(f"{url}/missing", headers=OWNER, json={"content": "x"}).status_code == 404


def _governance(**fields) -> None:  # type: ignore[no-untyped-def]
    value = MemoryGovernance(tenant_id=TENANT, updated_by="user_owner", updated_at=utc_now(),
                             **({"default_retention_days": 30, "allow_permanent_retention": False, "require_provenance": False} | fields))
    governance_store.save_memory_governance(ctx(), value)


def test_memory_retention_follows_memory_governance_and_expired_ones_are_hidden_then_removed(client: TestClient) -> None:
    _governance()
    agent = _agent(client)
    url = f"/api/v1/agents/{agent['id']}/memories"
    memory = client.post(url, headers=OWNER, json={"content": "Kept 30 days"}).json()
    expires = datetime.fromisoformat(memory["expires_at"])
    assert timedelta(days=29) < expires - utc_now() <= timedelta(days=30)
    # Expire it in the store: reads hide it at once, runs never see it, the next change removes it.
    agent_store.memories[agent["id"]][0].expires_at = utc_now() - timedelta(seconds=1)
    assert client.get(url, headers=OWNER).json()["total"] == 0
    from anum_api.agent_profiles import agent_memories_for_run

    assert agent_memories_for_run(ctx(), agent["id"]) == []
    client.post(url, headers=OWNER, json={"content": "New"})
    assert [m.content for m in agent_store.memories[agent["id"]]] == ["New"]


def test_provenance_required_by_governance(client: TestClient) -> None:
    _governance(allow_permanent_retention=True, require_provenance=True)
    agent = _agent(client)
    url = f"/api/v1/agents/{agent['id']}/memories"
    refused = client.post(url, headers=OWNER, json={"content": "No source"})
    assert refused.status_code == 422 and "where it comes from" in refused.text
    ok = client.post(url, headers=OWNER, json={"content": "With source", "source_label": "SOP 4"}).json()
    assert ok["expires_at"] is None
    assert client.patch(f"{url}/{ok['id']}", headers=OWNER, json={"source_label": ""}).status_code == 422


def test_memory_from_a_file_only_when_the_agent_may_read_it(client: TestClient) -> None:
    agent = _agent(client)  # knowledge none
    url = f"/api/v1/agents/{agent['id']}/memories"
    r = client.post(url, headers=OWNER, json={"content": "From SOP", "file_id": "file_missing"})
    assert r.status_code == 422 and "reads no workspace knowledge" in r.text
    wide = _agent(client, name="Analyst", knowledge_mode="workspace")
    r = client.post(f"/api/v1/agents/{wide['id']}/memories", headers=OWNER, json={"content": "From SOP", "file_id": "file_missing"})
    assert r.status_code == 422 and "indexed documents" in r.text
    assert client.post(url, headers=OWNER, json={"content": "x", "file_id": "f", "task_id": "t"}).status_code == 422


def test_memory_reaches_a_run_with_its_kind_source_and_date() -> None:
    from anum_api.agent_profiles import AgentMemory, AgentMemoryKind, AgentMemorySource

    memory = AgentMemory(id="m", tenant_id=TENANT, workspace_id=WORKSPACE, agent_id="a", content="Rotate at 6 mm",
                         source=AgentMemorySource.MANUAL, created_by="u", created_at=datetime(2026, 11, 1, tzinfo=UTC),
                         kind=AgentMemoryKind.DECISION, source_label="SOP tyres v3", source_date=date(2026, 10, 30))
    assert memory.for_prompt() == "[Decision, source: SOP tyres v3, as of 2026-10-30] Rotate at 6 mm"


# --------------------------------------------------------------------------- knowledge collections


def test_agent_reads_a_shared_collection_never_a_private_or_unknown_one(client: TestClient) -> None:
    shared = client.post("/api/v1/knowledge/collections", headers=OWNER, json={"name": "Green Concrete knowledge"}).json()
    private = client.post("/api/v1/knowledge/collections", headers=OWNER, json={"name": "Mine", "visibility": "private"}).json()
    note = client.post(f"/api/v1/knowledge/collections/{shared['id']}/notes", headers=OWNER,
                       json={"title": "Tyre SOP", "content": "Rotate tyres every 20,000 km."})
    assert note.status_code == 201, note.text
    for bad in (private["id"], "kbc_missing"):
        r = client.post("/api/v1/agents", headers=OWNER, json={"name": "x", "knowledge_mode": "selected",
                                                               "knowledge_sources": [{"type": "collection", "id": bad}]})
        assert r.status_code == 422 and "not shared" in r.text
    agent = _agent(client, knowledge_mode="selected", knowledge_sources=[{"type": "collection", "id": shared["id"]}])
    from anum_api.agent_profiles import load_agent
    from anum_api.agent_skill_loadout import agent_run_profile

    profile: AgentProfile | None = load_agent(ctx(), agent["id"])
    assert profile is not None
    snapshot = agent_run_profile(ctx(), profile)
    note_id = note.json()["source_id"]
    assert ("memory", note_id) in snapshot.knowledge_filter()
    assert all(t != "collection" for t, _ in snapshot.knowledge_filter())


def test_role_skill_instructions_load_into_an_agents_run(client: TestClient) -> None:
    agent = _agent(client, allowed_tools=["gc.ops_brief"], allowed_skills=["gc.daily-ops-brief"])
    loadout = client.get(f"/api/v1/agents/{agent['id']}/skills", headers=OWNER).json()
    item = next(i for i in loadout["items"] if i["id"] == "gc.daily-ops-brief")
    assert item["state"] == "ready" and item["loads_instructions"] and item["activation"] == "always"
    from anum_api.agent_profiles import load_agent
    from anum_api.agent_skill_loadout import agent_run_profile

    snapshot = agent_run_profile(ctx(), load_agent(ctx(), agent["id"]))  # type: ignore[arg-type]
    assert [s.id for s in snapshot.loaded_skills] == ["gc.daily-ops-brief"]
    assert "Not connected" in snapshot.persona()
    # Without its tool the skill cannot be attached at all.
    r = client.post("/api/v1/agents", headers=OWNER, json={"name": "x", "allowed_skills": ["gc.tyre-plan"]})
    assert r.status_code == 422 and "gc.tyre_plan" in r.text


# --------------------------------------------------------------------------- kit routes and automation step


def test_kit_routes_need_postgres_and_the_right_role(client: TestClient) -> None:
    assert client.get("/api/v1/office/green-concrete/kit", headers=OWNER).status_code == 503
    assert client.get("/api/v1/office/green-concrete/kit", headers=headers(role="guest")).status_code == 403
    body = {"input_mode": "click", "items": [{"id": "knowledge", "hash": "0" * 64}]}
    assert client.post("/api/v1/office/green-concrete/kit/setup", headers=MEMBER, json=body).status_code == 403
    assert client.post("/api/v1/office/green-concrete/kit/setup", headers=OWNER, json=body).status_code == 503
    state = {"input_mode": "click", "enabled": True}
    assert client.post("/api/v1/office/green-concrete/kit/routines/daily_ops_brief/state", headers=MEMBER, json=state).status_code == 403


def test_automation_task_step_may_name_an_agent() -> None:
    from anum_api import automation as auto

    good = auto.WorkflowStep(id="t", name="Brief", action="task.create", input={"title": "Brief", "prompt": "p", "agent_id": "agent_1"})
    auto.WorkflowCreate(name="x", steps=[good])
    bad = auto.WorkflowStep(id="t", name="Brief", action="task.create", input={"title": "Brief", "prompt": "p", "agent_id": "a b"})
    with pytest.raises(ValueError, match="agent_id"):
        auto.WorkflowCreate(name="x", steps=[bad])

