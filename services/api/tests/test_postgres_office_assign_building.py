"""Assign work and add-a-building on PostgreSQL as the non-owner app role with forced RLS."""
from __future__ import annotations

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import (  # noqa: F401 (fixtures)
    EXEC,
    OWNER_A,
    headers,
    hq,
    run_sql,
    seed_agent,
    seed_office,
)

pytestmark = pytest.mark.database
ASSIGN = "/api/v1/office/assignments"
BUILD = "/api/v1/office/building-proposals"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def body(**extra) -> dict:
    return {"agent_id": "ag1", "title": "Check stock", "instructions": "Count the tyres.", "run": False, "source": "room", **extra}


def count(engine: Engine, sql: str, **params) -> int:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


# ---- assign ------------------------------------------------------------------------------


def test_assign_creates_one_task_per_key_and_audits(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    h = {**OWNER(), "Idempotency-Key": "assign-key-0001"}
    first = hq.post(ASSIGN, headers=h, json=body(expected_result="A number", context_note="Ops room", priority="high"))
    assert first.status_code == 201, first.text
    task = first.json()["task"]
    assert task["agent_id"] == "ag1" and task["priority"] == "high" and first.json()["replayed"] is False
    assert "Expected result:\nA number" in task["prompt"] and "Context:\nOps room" in task["prompt"]
    again = hq.post(ASSIGN, headers=h, json=body(expected_result="A number", context_note="Ops room", priority="high", source="desk"))
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["task"]["id"] == task["id"]
    assert count(database_engine, "select count(*) from tasks where agent_id = 'ag1'") == 1
    different = hq.post(ASSIGN, headers=h, json=body(title="Other"))
    assert different.status_code == 409
    assert count(database_engine, "select count(*) from audit_records where action = 'office.assignment.create'") == 1
    with database_engine.connect() as c:
        meta = c.execute(text("select metadata::text from audit_records where action = 'office.assignment.create'")).scalar_one()
    assert "Count the tyres" not in meta  # ids and flags only, never the instructions


def test_assign_requires_a_key_permission_and_a_usable_agent(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "paused1", status="paused")
    assert hq.post(ASSIGN, headers=OWNER(), json=body()).status_code == 422  # no Idempotency-Key
    viewer = {**headers(TENANT_A, WORKSPACE_A, EXEC, "viewer"), "Idempotency-Key": "assign-key-0002"}
    assert hq.post(ASSIGN, headers=viewer, json=body()).status_code == 403
    h = {**OWNER(), "Idempotency-Key": "assign-key-0003"}
    paused = hq.post(ASSIGN, headers=h, json=body(agent_id="paused1"))
    assert paused.status_code == 409 and "paused" in paused.text
    missing = hq.post(ASSIGN, headers={**OWNER(), "Idempotency-Key": "assign-key-0004"}, json=body(agent_id="nope"))
    assert missing.status_code == 404
    assert count(database_engine, "select count(*) from tasks") == 0
    assert count(database_engine, "select count(*) from office_create_keys where key like 'assign-key-%'") == 0  # a refused call keeps no claim


def test_assign_is_tenant_scoped(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_B, WORKSPACE_B, "bag1")
    r = hq.post(ASSIGN, headers={**OWNER(), "Idempotency-Key": "assign-key-0005"}, json=body(agent_id="bag1"))
    assert r.status_code == 404 and count(database_engine, "select count(*) from tasks") == 0
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    ok = hq.post(ASSIGN, headers={**OWNER(), "Idempotency-Key": "assign-key-0006"}, json=body())
    assert ok.status_code == 201
    other = headers(TENANT_B, WORKSPACE_B, EXEC)
    assert hq.post(ASSIGN, headers={**other, "Idempotency-Key": "assign-key-0006"}, json=body()).status_code in (403, 404)


def test_assign_with_run_starts_it_or_reports_why_not(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    r = hq.post(ASSIGN, headers={**OWNER(), "Idempotency-Key": "assign-key-0007"}, json=body(run=True))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["task"]["agent_id"] == "ag1"
    assert out["run"] is not None or out["run_error"] is not None  # a started run, or the reason it did not start


def test_assigned_task_appears_in_the_flow_of_the_agents_office(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    r = hq.post(ASSIGN, headers={**OWNER(), "Idempotency-Key": "assign-key-0008"}, json=body())
    task_id = r.json()["task"]["id"]
    flow = hq.get("/api/v1/office/flow", headers=OWNER())
    assert flow.status_code == 200, flow.text
    assert task_id in flow.text


# ---- building proposal ---------------------------------------------------------------------


def test_proposal_is_read_only_and_confirm_builds_atomically(hq: TestClient, database_engine: Engine) -> None:
    view = hq.get(BUILD, params={"name": "Tyre Pulse", "floors": 3}, headers=OWNER()).json()
    assert view["creates"] == {"buildings": 1, "floors": 3, "rooms": 6, "desks": 0, "agents": 0} and view["can_confirm"] is True
    assert count(database_engine, "select count(*) from office_buildings") == 0
    payload = {"name": "Tyre Pulse", "floors": 3, "lang": "en", "payload_hash": view["payload_hash"]}
    for mode in ("voice", "typed"):
        assert hq.post(f"{BUILD}/confirm", headers=OWNER(), json={**payload, "input_mode": mode}).status_code == 403
    assert count(database_engine, "select count(*) from office_buildings") == 0
    assert hq.post(f"{BUILD}/confirm", headers=OWNER(), json={**payload, "payload_hash": "0" * 64, "input_mode": "click"}).status_code == 409
    h = {**OWNER(), "Idempotency-Key": "building-key-01"}
    done = hq.post(f"{BUILD}/confirm", headers=h, json={**payload, "input_mode": "click"})
    assert done.status_code == 201, done.text
    assert count(database_engine, "select count(*) from office_floors where building_id = 'bld-tyre-pulse'") == 3
    assert count(database_engine, "select count(*) from office_rooms") == 6
    assert count(database_engine, "select count(*) from audit_records where action = 'office.building.create_from_proposal'") == 1
    replay = hq.post(f"{BUILD}/confirm", headers=h, json={**payload, "input_mode": "click"})
    assert replay.status_code == 200 and replay.json()["replayed"] is True and replay.json()["building"]["id"] == "bld-tyre-pulse"
    other = hq.post(f"{BUILD}/confirm", headers=h, json={**payload, "name": "Other Place", "input_mode": "click"})
    assert other.status_code == 409
    assert count(database_engine, "select count(*) from office_buildings") == 1
    # the same name again is refused in the proposal itself
    assert hq.get(BUILD, params={"name": "tyre pulse", "floors": 1}, headers=OWNER()).status_code == 422


def test_confirm_needs_workspace_manage_and_the_same_user(hq: TestClient, database_engine: Engine) -> None:
    view = hq.get(BUILD, params={"name": "Depot", "floors": 1}, headers=OWNER()).json()
    payload = {"name": "Depot", "floors": 1, "lang": "en", "payload_hash": view["payload_hash"], "input_mode": "click"}
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert hq.get(BUILD, params={"name": "Depot", "floors": 1}, headers=viewer).json()["can_confirm"] is False
    assert hq.post(f"{BUILD}/confirm", headers=viewer, json=payload).status_code == 403
    assert count(database_engine, "select count(*) from office_buildings") == 0


def test_pa_tool_only_proposes_and_asks_one_question(hq: TestClient, database_engine: Engine) -> None:
    def call(args):
        r = hq.post("/api/v1/executive-pa/invoke", headers=OWNER(), json={"tool": "propose_add_building", "arguments": args, "input_mode": "voice"})
        assert r.status_code == 200, r.text
        return r.json()
    ok = call({"name": "Tyre Pulse", "floors": 3})
    assert ok["status"] == "proposal" and ok["payload_hash"] and ok["creates"]["floors"] == 3
    assert call({"floors": 3})["status"] == "needs_info" and call({"floors": 3})["missing"] == "name"
    assert call({"name": "Tyre Pulse"})["missing"] == "floors"
    assert call({"name": "X", "floors": 99})["status"] == "invalid"
    assert count(database_engine, "select count(*) from office_buildings") == 0
