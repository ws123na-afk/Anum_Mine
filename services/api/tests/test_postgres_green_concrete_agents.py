"""Green Concrete agents kit on PostgreSQL as the non-owner app role with forced RLS: migration 0128's columns and
checks, agent memory CRUD with kind, source, date and retention, the kit view, setup (the shared knowledge
collection and the routines, created turned off), the routine switch, the staffing Apply adding the kit and the
collection only, the shift handover tool from the real seats, and tenant isolation."""
from __future__ import annotations

import asyncio

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import EXEC, OWNER_A, headers, hq, run_sql, seed_agent  # noqa: F401 (fixtures)

from anum_api import green_concrete_kit as gk
from anum_api.agent_tools import ToolCall
from anum_api.main import tool_registry
from anum_api.office_green_concrete import PEOPLE
from anum_api.schemas import TenantContext

pytestmark = pytest.mark.database
KIT = "/api/v1/office/green-concrete/kit"
STAFF = "/api/v1/office/staffing/offices/gc-green-concrete"
BID = "gc-green-concrete"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def VIEWER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")


def seed_green_concrete(engine: Engine, people=PEOPLE, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    """The building, room and seats with each person's own hours, like the Green Concrete confirm writes them."""
    run_sql(engine, "insert into office_buildings (id, tenant_id, workspace_id, created_by, name, timezone) "
                    "values (:b, :t, :w, 'x', 'Green Concrete', 'Asia/Dubai')", b=BID, t=tenant, w=workspace)
    run_sql(engine, "insert into office_floors (id, tenant_id, workspace_id, created_by, building_id, level, name) "
                    "values ('gc-l0', :t, :w, 'x', :b, 0, 'Ground floor')", b=BID, t=tenant, w=workspace)
    run_sql(engine, "insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity) "
                    "values ('gc-cc', :t, :w, 'x', 'gc-l0', 'Command Center', 'command', 12)", t=tenant, w=workspace)
    agents: dict[str, str] = {}
    for index, person in enumerate(people, 1):
        agent = f"gc_{person.key.replace('-', '_')}_{workspace[-2:]}"
        seed_agent(engine, tenant, workspace, agent, status="paused")
        run_sql(engine, "update agent_profiles set name = :n, department = :d, autonomy = cast(:a as jsonb) where id = :i",
                n=person.name, d=person.role_en, i=agent, a='{"read": "auto", "write": "off", "external": "off"}')
        start, end = person.hours
        run_sql(engine, "insert into office_desks (id, tenant_id, workspace_id, created_by, room_id, label, agent_id, work_start, work_end) "
                        "values (:k, :t, :w, 'x', 'gc-cc', :l, :a, :s, :e)",
                k=f"gc-cc-d{index}", t=tenant, w=workspace, l=f"Seat {index}", a=agent, s=start, e=end)
        agents[person.key] = agent
    return agents


def kit(client: TestClient, h=None) -> dict:
    r = client.get(KIT, headers=h or OWNER())
    assert r.status_code == 200, r.text
    return r.json()


def setup(client: TestClient, view: dict, ids: list[str], mode: str = "click", h=None):
    items = [{"id": i["id"], "hash": i["hash"]} for i in view["items"] if i["id"] in ids]
    return client.post(f"{KIT}/setup", headers=h or OWNER(), json={"input_mode": mode, "items": items})


def test_migration_columns_and_checks(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "m1")
    base = ("insert into agent_memories (id, tenant_id, workspace_id, agent_id, content, source, created_by, created_at, kind, source_file_id) "
            "values (:i, :t, :w, 'm1', 'x', :s, 'u', now(), :k, :f)")
    run_sql(database_engine, base, i="ok1", t=TENANT_A, w=WORKSPACE_A, s="file", k="decision", f="file_1")
    for bad in ({"s": "file", "k": "fact", "f": None}, {"s": "manual", "k": "bogus", "f": None}, {"s": "other", "k": None, "f": None}):
        with pytest.raises(DBAPIError):
            run_sql(database_engine, base, i="bad", t=TENANT_A, w=WORKSPACE_A, **bad)


def test_memory_crud_with_kind_source_date_and_isolation(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "mem1")
    seed_agent(database_engine, TENANT_B, WORKSPACE_B, "mem_b")
    url = "/api/v1/agents/mem1/memories"
    r = hq.post(url, headers=OWNER(), json={"content": "Rotate at 6 mm", "kind": "decision", "source_label": "SOP tyres",
                                            "source_date": "2026-10-30"})
    assert r.status_code == 201, r.text
    memory = r.json()
    patched = hq.patch(f"{url}/{memory['id']}", headers=OWNER(), json={"content": "Rotate at 5 mm"})
    assert patched.status_code == 200 and patched.json()["kind"] == "decision" and patched.json()["updated_at"]
    row = None
    with database_engine.connect() as c:
        row = c.execute(text("select kind, source_label, source_date, content, updated_by from agent_memories where id = :i"),
                        {"i": memory["id"]}).one()
    assert row.kind == "decision" and row.source_label == "SOP tyres" and str(row.source_date) == "2026-10-30"
    assert row.content == "Rotate at 5 mm" and row.updated_by == OWNER_A
    other = headers(TENANT_B, WORKSPACE_B, EXEC)
    assert hq.get(url, headers=other).status_code == 404
    assert hq.patch(f"{url}/{memory['id']}", headers=other, json={"content": "x"}).status_code == 404
    assert hq.patch(f"{url}/{memory['id']}", headers=VIEWER(), json={"content": "x"}).status_code == 403
    # Expired rows are hidden, then removed by the next change.
    run_sql(database_engine, "update agent_memories set expires_at = now() - interval '1 minute' where id = :i", i=memory["id"])
    assert hq.get(url, headers=OWNER()).json()["total"] == 0
    hq.post(url, headers=OWNER(), json={"content": "Next"})
    with database_engine.connect() as c:
        assert c.execute(text("select count(*) from agent_memories where agent_id = 'mem1'")).scalar() == 1
    actions = []
    with database_engine.connect() as c:
        actions = [r[0] for r in c.execute(text("select action from audit_records where target like 'agent_memory%' order by created_at"))]
    assert "agent_memory.updated" in actions


def test_kit_view_lists_real_state_and_setup_builds_collection_and_routines_off_once(hq: TestClient, database_engine: Engine) -> None:
    empty = kit(hq)
    assert empty["building"] is None and empty["agents"] == [] and [i["id"] for i in empty["items"]] == ["knowledge"]
    assert all(r["state"] == "needs_agent" for r in empty["routines"])
    agents = seed_green_concrete(database_engine)
    view = kit(hq)
    assert view["building"]["id"] == BID and len(view["agents"]) == len(PEOPLE)
    states = {s["id"]: s["state"] for s in view["skills"]}
    assert states["gc.tyre-plan"] == "not_connected"  # no Tyre Pulse connection here: never a made up figure
    assert {g["id"] for g in view["gaps"]} >= {"fuel", "service_intervals"}
    assert {i["id"] for i in view["items"]} == {"knowledge", "routine:daily_ops_brief", "routine:weekly_pmv_health", "routine:morning_handover"}
    assert view["can_manage"] is True and kit(hq, VIEWER())["can_manage"] is False
    # Only a click by an owner; a stale hash does nothing.
    assert setup(hq, view, ["knowledge"], mode="typed").status_code == 403
    assert setup(hq, view, ["knowledge"], h=VIEWER()).status_code == 403
    stale = hq.post(f"{KIT}/setup", headers=OWNER(), json={"input_mode": "click", "items": [{"id": "knowledge", "hash": "0" * 64}]})
    assert stale.status_code == 409
    r = setup(hq, view, [i["id"] for i in view["items"]])
    assert r.status_code == 200, r.text
    assert {x["outcome"] for x in r.json()["results"]} == {"created"}
    after = kit(hq)
    assert after["items"] == [] and after["knowledge"]["state"] == "empty"
    for routine in after["routines"]:
        assert routine["state"] == "off" and routine["timezone"] == "Asia/Dubai" and routine["workflow_id"]
    brief = next(r for r in after["routines"] if r["key"] == "daily_ops_brief")
    assert brief["agent"]["id"] == agents["ahmed-elbendari"]
    with database_engine.connect() as c:
        wf = c.execute(text("select status, steps from automation_workflows where id = :i"), {"i": brief["workflow_id"]}).one()
        sched = c.execute(text("select enabled, cron, timezone from automation_schedules where workflow_id = :i"),
                          {"i": brief["workflow_id"]}).one()
        collections = c.execute(text("select count(*) from kb_collections where name = :n and owner_user_id is null"),
                                {"n": gk.KNOWLEDGE_NAME}).scalar()
    assert wf.status == "disabled" and wf.steps[0]["input"]["agent_id"] == agents["ahmed-elbendari"]
    assert sched.enabled is False and sched.cron == "0 6 * * *" and sched.timezone == "Asia/Dubai"
    assert collections == 1
    again = setup(hq, view, [i["id"] for i in view["items"]])
    assert {x["outcome"] for x in again.json()["results"]} == {"already_done"}
    with database_engine.connect() as c:
        assert c.execute(text("select count(*) from kb_collections where name = :n"), {"n": gk.KNOWLEDGE_NAME}).scalar() == 1
    # The owner switches a routine on and off with a click.
    on = hq.post(f"{KIT}/routines/daily_ops_brief/state", headers=OWNER(), json={"input_mode": "click", "enabled": True})
    assert on.status_code == 200, on.text
    assert next(r for r in kit(hq)["routines"] if r["key"] == "daily_ops_brief")["state"] == "on"
    assert hq.post(f"{KIT}/routines/daily_ops_brief/state", headers=OWNER(), json={"input_mode": "voice", "enabled": False}).status_code == 403
    hq.post(f"{KIT}/routines/daily_ops_brief/state", headers=OWNER(), json={"input_mode": "click", "enabled": False})
    assert next(r for r in kit(hq)["routines"] if r["key"] == "daily_ops_brief")["state"] == "off"
    assert hq.post(f"{KIT}/routines/nothing_here/state", headers=OWNER(), json={"input_mode": "click", "enabled": True}).status_code == 404


def test_staffing_apply_adds_the_kit_and_the_collection_never_removes(hq: TestClient, database_engine: Engine) -> None:
    agents = seed_green_concrete(database_engine)
    run_sql(database_engine, "update agent_profiles set allowed_tools = '[\"anum.respond\"]' where id = :i", i=agents["shahzeb"])
    setup(hq, kit(hq), ["knowledge"])
    office = hq.get(STAFF, headers=OWNER()).json()
    planner = next(r for r in office["roles"] if r["key"] == "gc_tyre_planner")
    member = planner["agents"][0]
    assert {"gc.tyre_plan", "gc.fleet_watch"} <= set(member["missing_tools"]) and member["missing_knowledge"] is True
    action = next(a for a in office["actions"] if a["id"] == f"update:{agents['shahzeb']}")
    assert action["content"]["add_collection"]
    r = hq.post(f"{STAFF}/apply", headers=OWNER(), json={"input_mode": "click", "lang": "en",
                                                         "actions": [{"id": action["id"], "hash": action["hash"]}]})
    assert r.status_code == 200, r.text
    with database_engine.connect() as c:
        row = c.execute(text("select allowed_tools, allowed_skills, knowledge_mode, knowledge_sources, status, autonomy "
                             "from agent_profiles where id = :i"), {"i": agents["shahzeb"]}).one()
    assert "anum.respond" in row.allowed_tools and {"gc.tyre_plan", "gc.fleet_watch"} <= set(row.allowed_tools)
    assert {"gc.tyre-plan", "gc.fleet-alerts-watch"} <= set(row.allowed_skills)
    assert row.knowledge_mode == "selected" and row.knowledge_sources[0]["type"] == "collection"
    assert row.status == "paused" and row.autonomy["external"] == "off"  # Apply never turns an agent on or widens it
    view = kit(hq)
    shahzeb = next(a for a in view["agents"] if a["id"] == agents["shahzeb"])
    assert shahzeb["reads_collection"] and "gc.tyre-plan" in shahzeb["skills"] and shahzeb["memories"] == 0


def test_shift_handover_tool_reads_the_real_seats(hq: TestClient, database_engine: Engine) -> None:
    seed_green_concrete(database_engine)
    context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=OWNER_A, roles=["owner"])
    result = asyncio.run(tool_registry.execute(ToolCall(name="gc.shift_handover"), context))
    team = next(s for s in result.output["sections"] if s["id"] == "team")
    assert team["state"] == "ok" and team["data"]["total"] == len(PEOPLE) and team["data"]["timezone"] == "Asia/Dubai"
    assert {p["name"] for p in team["data"]["people"]} == {p.name for p in PEOPLE}


def test_another_tenant_sees_none_of_it(hq: TestClient, database_engine: Engine) -> None:
    seed_green_concrete(database_engine)
    setup(hq, kit(hq), ["knowledge"])
    other = kit(hq, headers(TENANT_B, WORKSPACE_B, EXEC))
    assert other["building"] is None and other["agents"] == [] and other["knowledge"]["state"] == "missing"
    assert {r["state"] for r in other["routines"]} == {"needs_agent"}
