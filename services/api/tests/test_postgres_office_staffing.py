"""Office staffing on PostgreSQL as the non-owner app role with forced RLS: list, gaps, apply, idempotency, isolation."""
from __future__ import annotations

import json

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import EXEC, OWNER_A, _scoped, headers, hq, run_sql, seed_agent  # noqa: F401 (fixtures)
from test_postgres_office_provisioning import msg, run_rows

from anum_api import office_staffing_blueprint as bp

pytestmark = pytest.mark.database
BASE = "/api/v1/office/staffing"
BID = "campus-04-corporate-services"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def seed_corporate(engine: Engine, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> None:
    rows = [
        ("insert into office_buildings (id, tenant_id, workspace_id, created_by, name, timezone) values (:b, :t, :w, 'x', 'Corporate Services', 'Asia/Dubai')", {}),
        ("insert into office_floors (id, tenant_id, workspace_id, created_by, building_id, level, name) values ('cf0', :t, :w, 'x', :b, 0, 'Ground')", {}),
    ]
    for rid, name, dept in (("c-hr", "HR", "hr"), ("c-fin", "Finance Office", "finance"), ("c-open", "Open Office", None)):
        rows.append(("insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity, team_department) "
                     "values (:r, :t, :w, 'x', 'cf0', :n, 'office', 4, :d)", {"r": rid, "n": name, "d": dept}))
        rows.append(("insert into office_desks (id, tenant_id, workspace_id, created_by, room_id, label) values (:k, :t, :w, 'x', :r, 'D1')",
                     {"k": f"{rid}-d1", "r": rid}))
    for sql, params in rows:
        run_sql(engine, sql, t=tenant, w=workspace, b=BID, **params)


def seat(engine: Engine, desk: str, agent: str) -> None:
    run_sql(engine, "update office_desks set agent_id = :a where id = :d", a=agent, d=desk)


def office(hq: TestClient, h=None, lang="en") -> dict:
    r = hq.get(f"{BASE}/offices/{BID}", params={"lang": lang}, headers=h or OWNER())
    assert r.status_code == 200, r.text
    return r.json()


def apply(hq: TestClient, view: dict, ids: list[str], *, mode="click", h=None, lang="en"):
    picked = [{"id": a["id"], "hash": a["hash"]} for a in view["actions"] if a["id"] in ids]
    return hq.post(f"{BASE}/offices/{BID}/apply", headers=h or OWNER(), json={"input_mode": mode, "lang": lang, "actions": picked})


def test_list_shows_coverage_agents_and_unconnected_sources_from_records(hq: TestClient, database_engine: Engine) -> None:
    assert hq.get(f"{BASE}/offices", headers=OWNER()).json()["items"] == []  # nothing stored, nothing invented
    seed_corporate(database_engine)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "fin1")
    run_sql(database_engine, "update agent_profiles set department = 'Finance' where id = 'fin1'")
    seat(database_engine, "c-fin-d1", "fin1")
    body = hq.get(f"{BASE}/offices", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")).json()
    item = body["items"][0]
    assert item["kind"] == "corporate_services" and item["roles_total"] == 5 and item["roles_staffed"] == 1
    assert item["agents_total"] == 1 and "finance_clerk" not in item["missing_roles"]
    states = {s["id"]: s["state"] for s in item["sources"]}
    assert states["finance_ledger"] == "no_access" and states["files"] == "available"  # a viewer may not read finance
    assert body["can_apply"] is False
    owner = {s["id"]: s["state"] for s in hq.get(f"{BASE}/offices", headers=OWNER()).json()["items"][0]["sources"]}
    assert owner["finance_ledger"] == "not_connected"
    assert hq.get(f"{BASE}/offices", params={"q": "nothing like it"}, headers=OWNER()).json()["total"] == 0


def test_detail_lists_role_gaps_and_actions_and_never_shows_instructions(hq: TestClient, database_engine: Engine) -> None:
    seed_corporate(database_engine)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "fin1")
    run_sql(database_engine, "update agent_profiles set department = 'finance', allowed_tools = '[\"send_email\"]', "
            "instructions = 'secret words' where id = 'fin1'")
    seat(database_engine, "c-fin-d1", "fin1")
    view = office(hq)
    assert "secret words" not in json.dumps(view)
    fin = next(r for r in view["roles"] if r["key"] == "finance_clerk")
    member = fin["agents"][0]
    assert member["over_risk_tools"] == ["send_email"]
    assert set(member["missing_rules"]) == {"autonomy", "cost_cap", "never_list"}
    ids = {a["id"] for a in view["actions"]}
    assert "update:fin1" in ids and "create:hr_people" in ids and "create:finance_clerk" not in ids
    create = next(a for a in view["actions"] if a["id"] == "create:hr_people")
    assert create["content"]["placement"]["desk_id"] == "c-hr-d1" and create["content"]["status"] == "paused"
    assert create["content"]["agent"]["autonomy"]["external"] == "off"
    assert office(hq, lang="ar")["actions"][0]["hash"] != view["actions"][0]["hash"]


def test_apply_creates_a_paused_capped_seated_agent_once(hq: TestClient, database_engine: Engine) -> None:
    seed_corporate(database_engine)
    view = office(hq)
    r = apply(hq, view, ["create:hr_people"])
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["outcome"] == "created" and res["seated"] is True and res["desk_id"] == "c-hr-d1"
    agent = hq.get(f"/api/v1/agents/{res['agent_id']}", headers=OWNER()).json()
    assert agent["status"] == "paused" and agent["template_id"] == bp.marker(BID, "hr_people")
    assert agent["autonomy"]["external"] == "off" and "send_email" not in agent["allowed_tools"]
    caps = hq.get("/api/v1/agent-controls/agent-caps", headers=OWNER()).json()["items"]
    assert any(c["agent_id"] == res["agent_id"] and c["monthly_cost_limit_usd"] == 5.0 for c in caps)
    # The same click again is idempotent: no second agent.
    again = apply(hq, view, ["create:hr_people"])
    assert again.status_code == 200 and again.json()["results"][0]["outcome"] == "already_done"
    assert run_rows(database_engine, "select count(*) from agent_profiles where template_id like 'bp:%'") == [(1,)]
    assert run_rows(database_engine, "select status, agent_id from office_agent_provisions") == [("active", res["agent_id"])]
    # The role now counts as staffed.
    after = office(hq)
    assert "hr_people" not in after["missing_roles"]
    # One summary audit record with ids only.
    meta = run_rows(database_engine, "select metadata from audit_records where action = 'office.staffing.applied' order by created_at")
    assert meta and meta[0][0]["results"][0]["agent_id"] == res["agent_id"] and meta[0][0]["input_mode"] == "click"
    events = run_rows(database_engine, "select count(*) from domain_events where type = 'office.agent_provisioned' and payload->>'role' = 'hr_people'")
    assert events[0][0] >= 1


def test_apply_upgrades_an_existing_agent_without_widening_it(hq: TestClient, database_engine: Engine) -> None:
    seed_corporate(database_engine)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "fin1")
    run_sql(database_engine, "update agent_profiles set department = 'finance', "
            "autonomy = '{\"read\": \"auto\", \"write\": \"auto\", \"external\": \"ask\"}' where id = 'fin1'")
    seat(database_engine, "c-fin-d1", "fin1")
    view = office(hq)
    r = apply(hq, view, ["update:fin1"])
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["outcome"] == "updated"
    agent = hq.get("/api/v1/agents/fin1", headers=OWNER()).json()
    assert "finance.transactions.search" in agent["allowed_tools"] and "anum.finance" in agent["allowed_skills"]
    assert agent["autonomy"] == {"read": "auto", "write": "off", "external": "off"} and agent["status"] == "active"
    assert run_rows(database_engine, "select position(:m in instructions) > 0 from agent_profiles where id = 'fin1'", m=bp.RULES_MARKER) == [(True,)]
    fin = next(r for r in office(hq)["roles"] if r["key"] == "finance_clerk")
    assert fin["agents"][0]["complete"] is True and fin["state"] == "staffed"
    assert apply(hq, view, ["update:fin1"]).json()["results"][0]["outcome"] == "already_done"


def test_apply_refuses_non_owners_typed_input_and_stale_hashes(hq: TestClient, database_engine: Engine) -> None:
    seed_corporate(database_engine)
    view = office(hq)
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert apply(hq, view, ["create:hr_people"], h=viewer).status_code == 403
    assert office(hq, h=viewer)["can_apply"] is False
    assert apply(hq, view, ["create:hr_people"], mode="voice").status_code == 403
    assert apply(hq, view, ["create:hr_people"], mode="typed").status_code == 403
    bad = hq.post(f"{BASE}/offices/{BID}/apply", headers=OWNER(),
                  json={"input_mode": "click", "actions": [{"id": "create:hr_people", "hash": "0" * 64}]})
    assert bad.status_code == 409 and "Review" in msg(bad)
    assert run_rows(database_engine, "select count(*) from agent_profiles") == [(0,)]


def test_offices_never_cross_tenants_or_workspaces(hq: TestClient, database_engine: Engine) -> None:
    seed_corporate(database_engine)
    assert apply(hq, office(hq), ["create:hr_people"]).status_code == 200
    other = headers(TENANT_B, WORKSPACE_B, EXEC)
    assert hq.get(f"{BASE}/offices", headers=other).json()["items"] == []
    assert hq.get(f"{BASE}/offices/{BID}", headers=other).status_code == 404
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_B, WORKSPACE_B)
        assert connection.execute(text("select count(*) from office_agent_provisions")).scalar() == 0
        tx.rollback()


def test_private_agents_of_others_stay_invisible(hq: TestClient, database_engine: Engine) -> None:
    seed_corporate(database_engine)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "priv", sharing="private", owner="someone_else")
    run_sql(database_engine, "update agent_profiles set department = 'hr' where id = 'priv'")
    seat(database_engine, "c-hr-d1", "priv")
    view = office(hq)
    assert view["agents_total"] == 0 and "hr_people" in view["missing_roles"]
    assert "priv" not in json.dumps(view["roles"])
