"""Office flow, trail and team on PostgreSQL as the non-owner app role with forced RLS."""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_postgres_control_plane import (
    postgres_backend,  # noqa: F401 (fixture the hq fixture needs)
)
from test_postgres_office_hq import (
    NOW,
    OWNER_A,
    headers,
    hq,  # noqa: F401 (fixture)
    run_sql,
    seed_agent,
    seed_office,
    seed_task,
)

pytestmark = pytest.mark.database
def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def test_flow_places_work_in_the_office_of_the_agents_desk_and_flags_stuck(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tw", "ag1", "running", run_status="running")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tf", "ag1", "failed", run_status="failed", result="boom", age=600)
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tq", "ag1", "queued", age=3 * 3600)
    seed_agent(database_engine, TENANT_B, WORKSPACE_B, "b1")
    seed_task(database_engine, TENANT_B, WORKSPACE_B, "b_t", "b1", "running", run_status="running")
    r = hq.get("/api/v1/office/flow", headers=OWNER())
    assert r.status_code == 200, r.text
    body = r.json()
    items = {i["id"]: i for i in body["items"]}
    assert set(items) == {"tw", "tf", "tq"}  # nothing from workspace B
    assert items["tw"]["stage"] == "in_office" and items["tw"]["location"]["room_id"] == "r1"
    assert items["tw"]["location"]["building_id"] == "hq" and items["tw"]["location"]["floor_id"] == "f0"
    assert items["tf"]["stuck"]["code"] == "run_failed" and items["tf"]["stuck"]["facts"]["error"] == "boom"
    assert items["tq"]["stuck"]["code"] == "queued_too_long"
    assert body["rooms"]["r1"]["worst"] == "critical" and body["totals"]["stuck"] == 2
    assert body["sources"]["tasks"] == "connected" and body["thresholds"]["queue_wait_minutes"] == 30
    assert next(i["id"] for i in body["items"]) == "tf"  # critical first


def test_flow_pending_approval_waits_for_a_human(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tx", "ag1", "waiting_approval", run_status="waiting_approval")
    run_sql(database_engine, """insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason, run_id, expires_at, arguments, created_at, updated_at)
        values ('ap1', :t, :w, 'tx', 'send_email', 'high', 'pending', 'r', 'run_tx', :exp, '{}', :n, :n)""",
            t=TENANT_A, w=WORKSPACE_A, exp=NOW + timedelta(hours=1), n=NOW - timedelta(hours=6))
    item = hq.get("/api/v1/office/flow", headers=OWNER()).json()["items"][0]
    assert item["stage"] == "waiting_human" and item["waiting"]["approval_id"] == "ap1"
    assert item["stuck"]["code"] == "approval_waiting" and item["stuck"]["action"] == "decide_approval"


def test_flow_optional_sources_report_their_state(hq: TestClient, database_engine: Engine) -> None:
    body = hq.get("/api/v1/office/flow", headers=OWNER()).json()
    assert body["items"] == [] and body["sources"]["leads"] == "connected"
    assert body["sources"]["orders"] == "not_connected"  # no connector in this workspace
    viewer = hq.get("/api/v1/office/flow", headers=headers(TENANT_A, WORKSPACE_A, "someone", "viewer"))
    assert viewer.status_code in (200, 403)


def test_trail_lists_stored_events_only(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tq", "ag1", "queued")
    run_sql(database_engine, """insert into domain_events (id, tenant_id, workspace_id, type, version, subject, correlation_id, payload, created_at)
        values ('ev1', :t, :w, 'task.queued', 1, 'tq', 'c1', cast('{"status":"queued","secret":"hidden"}' as jsonb), :n)""",
            t=TENANT_A, w=WORKSPACE_A, n=NOW)
    body = hq.get("/api/v1/office/flow/items/task/tq/trail", headers=OWNER()).json()
    assert [s["type"] for s in body["steps"]] == ["task.queued"]
    assert body["steps"][0]["data"] == {"status": "queued"}  # allow-list only
    assert hq.get("/api/v1/office/flow/items/task/nope/trail", headers=OWNER()).status_code == 404
    assert hq.get("/api/v1/office/flow/items/order/o1/trail", headers=OWNER()).json()["source"] == "not_recorded"
    assert hq.get("/api/v1/office/flow/items/task/tq/trail", headers=headers(TENANT_B, WORKSPACE_B, "x")).status_code in (403, 404)


def test_team_lists_the_agents_of_a_room_with_skills_state_and_unassigned(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag2")  # no desk
    run_sql(database_engine, "update agent_profiles set allowed_skills = cast('[\"summarize\"]' as jsonb), department = 'Sales' where id = 'ag1'")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tf", "ag1", "failed", run_status="failed", result="boom")
    r = hq.get("/api/v1/office/team?room_id=r1", headers=OWNER())
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["agent_id"] for m in body["members"]] == ["ag1"]
    member = body["members"][0]
    assert member["department"] == "Sales" and member["state"] == "blocked" and member["stuck"]["code"] == "run_failed"
    assert member["desk"]["room_id"] == "r1" and body["skills_included"] is True
    assert member["skills"] and member["skills"][0]["id"] == "summarize"
    assert [a["agent_id"] for a in body["unassigned_agents"]] == ["ag2"] and body["unassigned_total"] == 1
    assert hq.get("/api/v1/office/team?room_id=missing", headers=OWNER()).status_code == 404
