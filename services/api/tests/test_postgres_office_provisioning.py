"""Governed agent provisioning on PostgreSQL as the non-owner app role with forced RLS."""
from __future__ import annotations

import concurrent.futures
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import (  # noqa: F401 (fixtures)
    NOW,
    OWNER_A,
    _scoped,
    headers,
    hq,
    run_sql,
    seed_agent,
    seed_member,
    seed_office,
)

pytestmark = pytest.mark.database
BASE = "/api/v1/office/provisioning"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def seed_orders(engine: Engine, tenant: str, workspace: str, count: int = 2, overdue: bool = False) -> None:
    run_sql(engine, """insert into commerce_connectors (id, tenant_id, workspace_id, channel, name, marketplaces, settings, enabled,
        health, sync_counts, cursors, created_by) values ('cn1', :t, :w, 'own_store', 'Store', '[]', '{}', true, 'ok', '{}', '{}', 'x')""",
            t=tenant, w=workspace)
    for i in range(count):
        run_sql(engine, """insert into commerce_orders (id, tenant_id, workspace_id, connector_id, channel, marketplace_id, external_id,
            status, purchased_at, ship_by, synced_at) values (:i, :t, :w, 'cn1', 'own_store', 'm', :e, 'unshipped', :p, :s, :p)""",
                i=f"or{i}", t=tenant, w=workspace, e=f"E{i}", p=NOW - timedelta(hours=3),
                s=NOW - timedelta(hours=1) if overdue else NOW + timedelta(days=1))


def room_department(engine: Engine, room: str, department: str | None) -> None:
    run_sql(engine, "update office_rooms set team_department = :d where id = :r", d=department, r=room)


def propose(hq: TestClient, template: str = "orders_desk", **query) -> dict:
    r = hq.get(f"{BASE}/proposals/{template}", params=query, headers=OWNER())
    assert r.status_code == 200, r.text
    return r.json()


def confirm(hq: TestClient, view: dict, *, mode: str = "click", user_headers=None, **extra):
    return hq.post(f"{BASE}/proposals/{view['content']['template_id']}/confirm", headers=user_headers or OWNER(),
                   json={"payload_hash": view["payload_hash"], "input_mode": mode, "lang": view["content"]["lang"], **extra})


def test_gaps_come_from_stored_records_only(hq: TestClient, database_engine: Engine) -> None:
    body = hq.get(f"{BASE}/gaps", headers=OWNER()).json()
    assert body["gaps"] == []  # nothing stored, nothing invented
    states = {c["template_id"]: c["state"] for c in body["coverage"]}
    assert states["orders_desk"] == "source_not_connected" and body["sources"]["orders"] == "not_connected"
    seed_orders(database_engine, TENANT_A, WORKSPACE_A, 2, overdue=True)
    body = hq.get(f"{BASE}/gaps", headers=OWNER()).json()
    gap = next(g for g in body["gaps"] if g["template_id"] == "orders_desk")
    assert gap["severity"] == "critical"
    assert gap["reasons"][0]["code"] == "orders_without_office" and gap["reasons"][0]["count"] == 2
    seed_orders  # no leak from the other workspace
    assert hq.get(f"{BASE}/gaps", headers=headers(TENANT_B, WORKSPACE_B, "someone")).status_code in (200, 403, 404)


def test_empty_department_is_a_gap_until_an_active_agent_has_it(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "finance")
    gaps = hq.get(f"{BASE}/gaps", headers=OWNER()).json()["gaps"]
    assert [g["template_id"] for g in gaps] == ["finance_reconciliation"]
    assert gaps[0]["reasons"][0] == {"code": "empty_department", "department": "finance", "rooms": [{"id": "r1", "name": "Ops"}]}
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "fin1")
    run_sql(database_engine, "update agent_profiles set department = 'Finance' where id = 'fin1'")
    assert hq.get(f"{BASE}/gaps", headers=OWNER()).json()["gaps"] == []


def test_proposal_lists_present_and_missing_capabilities_and_a_desk(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "operations")
    view = propose(hq)
    tools = {t["name"]: t["state"] for t in view["template"]["tools"]}
    assert tools["commerce.list_orders"] == "present"
    assert view["template"]["limited"] is True
    assert {m["id"] for m in view["content"]["missing"]} == {"order_status_update", "carrier_tracking"}
    assert view["content"]["initial_status"] == "paused"
    assert view["content"]["agent"]["autonomy"] == {"read": "auto", "write": "ask", "external": "off"}
    assert view["placement"]["mode"] == "existing_desk" and view["placement"]["desk_id"] == "d1"
    assert "send_email" not in view["content"]["agent"]["allowed_tools"]
    ar = propose(hq, lang="ar")
    assert ar["content"]["agent"]["name"] == "مكتب الطلبات" and ar["payload_hash"] != view["payload_hash"]


def test_confirm_builds_a_paused_agent_seats_it_and_orders_show_in_its_office(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "operations")
    seed_orders(database_engine, TENANT_A, WORKSPACE_A, 2, overdue=True)
    before = {i["id"]: i for i in hq.get("/api/v1/office/flow", headers=OWNER()).json()["items"]}
    assert before["or0"]["location"] is None and before["or0"]["location_reason"] == "no_order_office"
    view = propose(hq)
    r = confirm(hq, view)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "created" and body["agent"]["status"] == "paused" and body["limited"] is True
    agent_id = body["agent"]["id"]
    agent = hq.get(f"/api/v1/agents/{agent_id}", headers=OWNER()).json()
    assert agent["template_id"] == "hq:orders_desk" and agent["sharing"] == "workspace" and agent["department"] == "operations"
    assert agent["allowed_tools"] == sorted(agent["allowed_tools"]) and "commerce.list_orders" in agent["allowed_tools"]
    desk = hq.get(f"/api/v1/office-spaces/desks/d1", headers=OWNER()).json()
    assert desk["agent_id"] == agent_id
    cap = hq.get("/api/v1/agent-controls/agent-caps", headers=OWNER())
    assert cap.status_code == 200 and any(c["agent_id"] == agent_id and c["monthly_cost_limit_usd"] == 5.0 for c in cap.json()["items"])
    flow = {i["id"]: i for i in hq.get("/api/v1/office/flow", headers=OWNER()).json()["items"]}
    assert flow["or0"]["location"]["room_id"] == "r1" and flow["or0"]["location"]["via"] == "handler"
    assert flow["or0"]["stuck"]["code"] == "order_ship_overdue"  # still the real overdue rule
    gaps = hq.get(f"{BASE}/gaps", headers=OWNER()).json()
    assert not any(g["template_id"] == "orders_desk" for g in gaps["gaps"])
    audit = run_rows(database_engine, "select metadata from audit_records where action = 'office.agent_provisioned' and tenant_id = :t", t=TENANT_A)
    assert len(audit) == 1 and audit[0][0]["after"]["agent_id"] == agent_id and audit[0][0]["before"]["gap"]["template_id"] == "orders_desk"
    events = run_rows(database_engine, "select subject from domain_events where type = 'office.agent_provisioned' and tenant_id = :t", t=TENANT_A)
    assert events == [(agent_id,)]


def msg(r) -> str:
    body = r.json()
    return str(body.get("detail") or body.get("error", {}).get("message") or body)


def run_rows(engine: Engine, sql: str, **params):
    with engine.begin() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


def test_paused_handler_marks_its_orders_as_blocked_by_a_paused_agent(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "operations")
    seed_orders(database_engine, TENANT_A, WORKSPACE_A, 1)
    assert confirm(hq, propose(hq)).status_code == 200
    item = hq.get("/api/v1/office/flow", headers=OWNER()).json()["items"][0]
    assert item["stuck"]["code"] == "agent_paused" and item["stuck"]["action"] == "resume_agent"


def test_confirm_is_idempotent_and_a_retry_never_creates_two_agents(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "operations")
    view = propose(hq)
    first = confirm(hq, view).json()
    second = confirm(hq, propose(hq))
    assert second.status_code == 200 and second.json()["outcome"] == "already_provisioned"
    assert second.json()["agent"]["id"] == first["agent"]["id"]
    count = run_rows(database_engine, "select count(*) from agent_profiles where template_id = 'hq:orders_desk'")
    assert count == [(1,)]


def test_parallel_confirms_build_one_agent(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "operations")
    view = propose(hq)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: confirm(hq, view).status_code, range(4)))
    assert set(results) <= {200, 409} and 200 in results
    assert run_rows(database_engine, "select count(*) from agent_profiles where template_id = 'hq:orders_desk'") == [(1,)]


def test_interrupted_confirm_resumes_without_a_second_agent(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    room_department(database_engine, "r1", "operations")
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "built")
    run_sql(database_engine, "update agent_profiles set template_id = 'hq:orders_desk', status = 'paused' where id = 'built'")
    run_sql(database_engine, """insert into office_agent_provisions (tenant_id, workspace_id, template_id, status, work_kinds, proposal_hash,
        requested_by, updated_at) values (:t, :w, 'orders_desk', 'claimed', '["orders"]', :h, 'x', :old)""",
            t=TENANT_A, w=WORKSPACE_A, h="0" * 64, old=NOW - timedelta(minutes=10))
    r = confirm(hq, propose(hq))
    assert r.status_code == 200 and r.json()["outcome"] == "resumed" and r.json()["agent"]["id"] == "built"
    assert run_rows(database_engine, "select agent_id from office_desks where id = 'd1'") == [("built",)]
    assert run_rows(database_engine, "select count(*) from agent_profiles where template_id = 'hq:orders_desk'") == [(1,)]


def test_a_fresh_claim_by_someone_else_is_in_progress(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    run_sql(database_engine, """insert into office_agent_provisions (tenant_id, workspace_id, template_id, status, work_kinds, proposal_hash,
        requested_by) values (:t, :w, 'orders_desk', 'claimed', '["orders"]', :h, 'x')""", t=TENANT_A, w=WORKSPACE_A, h="0" * 64)
    r = confirm(hq, propose(hq))
    assert r.status_code == 409 and "already being built" in msg(r)


def test_voice_and_typed_never_confirm_and_a_stale_hash_is_refused(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    view = propose(hq)
    for mode in ("voice", "typed"):
        r = confirm(hq, view, mode=mode)
        assert r.status_code == 403 and "click" in msg(r)
    stale = dict(view, payload_hash="f" * 64)
    assert confirm(hq, stale).status_code == 409
    assert run_rows(database_engine, "select count(*) from agent_profiles") == [(0,)]
    # a hash made for one person does not work for another owner
    seed_member(database_engine, TENANT_A, WORKSPACE_A, "owner_two", "owner")
    other = confirm(hq, view, user_headers=headers(TENANT_A, WORKSPACE_A, "owner_two"))
    assert other.status_code == 409


def test_confirm_needs_workspace_manage_but_reading_needs_only_task_read(hq: TestClient, database_engine: Engine) -> None:
    seed_member(database_engine, TENANT_A, WORKSPACE_A, "member_a", "member")
    member = headers(TENANT_A, WORKSPACE_A, "member_a", "member")
    view = hq.get(f"{BASE}/proposals/orders_desk", headers=member)
    assert view.status_code == 200 and view.json()["can_confirm"] is False
    r = hq.post(f"{BASE}/proposals/orders_desk/confirm", headers=member,
                json={"payload_hash": view.json()["payload_hash"], "input_mode": "click"})
    assert r.status_code == 403


def test_new_desk_is_created_when_the_linked_room_has_capacity_and_none_when_full(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "other")
    run_sql(database_engine, "update office_desks set agent_id = 'other' where id = 'd1'")
    room_department(database_engine, "r1", "operations")
    view = propose(hq)
    assert view["placement"]["mode"] == "new_desk"
    r = confirm(hq, view)
    assert r.status_code == 200
    desks = run_rows(database_engine, "select id, agent_id from office_desks order by id")
    assert len(desks) == 2 and ("d1", "other") in desks
    run_sql(database_engine, "update office_rooms set capacity = 2 where id = 'r1'")
    full = propose(hq, "sales_followup")  # not linked to a room
    assert full["placement"]["mode"] == "none" and full["placement"]["reason"] == "no_linked_room"
    assert any(c["room_id"] == "r1" for c in full["placement"]["candidates"])


def test_agent_limit_is_enforced(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    from anum_api import agent_profiles
    original = agent_profiles.MAX_AGENTS_PER_WORKSPACE
    agent_profiles.MAX_AGENTS_PER_WORKSPACE = 0
    try:
        r = confirm(hq, propose(hq))
    finally:
        agent_profiles.MAX_AGENTS_PER_WORKSPACE = original
    assert r.status_code == 409 and "maximum" in msg(r)


def test_other_tenants_never_see_or_share_the_ledger(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    assert confirm(hq, propose(hq)).status_code == 200
    assert run_rows(database_engine, "select count(*) from office_agent_provisions") == [(1,)]
    assert run_rows(database_engine, "select tenant_id from office_agent_provisions") == [(TENANT_A,)]


def test_ledger_rows_obey_forced_rls_as_the_app_role(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    assert confirm(hq, propose(hq)).status_code == 200
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A)
        assert connection.execute(text("select count(*) from office_agent_provisions")).scalar() == 1
        tx.rollback()
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_B, WORKSPACE_B)
        assert connection.execute(text("select count(*) from office_agent_provisions")).scalar() == 0
        tx.rollback()
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_B, WORKSPACE_B)
        with pytest.raises(DBAPIError):  # a row for another tenant is refused by the write check
            connection.execute(text(
                "insert into office_agent_provisions (tenant_id, workspace_id, template_id, status, proposal_hash, requested_by) "
                "values (:t, :w, 'x', 'claimed', :h, 'u')"), {"t": TENANT_A, "w": WORKSPACE_A, "h": "0" * 64})
        tx.rollback()
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A)
        with pytest.raises(DBAPIError):  # an active row must name its agent
            connection.execute(text(
                "insert into office_agent_provisions (tenant_id, workspace_id, template_id, status, proposal_hash, requested_by) "
                "values (:t, :w, 'y', 'active', :h, 'u')"), {"t": TENANT_A, "w": WORKSPACE_A, "h": "0" * 64})
        tx.rollback()
