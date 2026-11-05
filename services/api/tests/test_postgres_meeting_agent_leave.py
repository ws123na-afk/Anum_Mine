"""An owner removes an AI agent from a live meeting, as the non-owner app role (pytest -m database).

Migration 0121 stores when (``left_at``) and by whom (``removed_by``) on the agent's participant row. The meeting
detail then gives that agent a ``left_at`` (the headquarters unseats it), it takes no further turn, the office no
longer shows it in a meeting, and audit and room event carry ids only. Never a leave the owner did not click.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_postgres_control_plane import (
    postgres_backend,  # noqa: F401 (fixture the hq fixture needs)
)
from test_postgres_live_agent_meeting_room import FakeGateway
from test_postgres_office_hq import EXEC, OWNER_A, headers, hq, seed_agent  # noqa: F401 (fixtures)
from test_postgres_office_starter_campus import rows

from anum_api.meeting_execution import execute_meeting_turn
from anum_api.schemas import TenantContext

pytestmark = pytest.mark.database
MEETINGS = "/api/v1/agent-meetings"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


def live_meeting(hq: TestClient, engine: Engine) -> tuple[dict, str, str]:
    seed_agent(engine, TENANT_A, WORKSPACE_A, "ag_stays")
    seed_agent(engine, TENANT_A, WORKSPACE_A, "ag_leaves")
    meeting = ok(hq.post(MEETINGS, headers=OWNER(), json={"title": "Weekly sync"}), 201)
    for agent in ("ag_stays", "ag_leaves"):
        meeting = ok(hq.post(f"{MEETINGS}/{meeting['id']}/agents", headers=OWNER(), json={"agent_id": agent, "expected_revision": meeting["revision"]}), 201)
    meeting = ok(hq.post(f"{MEETINGS}/{meeting['id']}/state", headers=OWNER(), json={"status": "active", "expected_revision": meeting["revision"]}))
    return meeting, "ag_stays", "ag_leaves"


def remove(hq: TestClient, meeting: dict, agent: str, user=None):  # type: ignore[no-untyped-def]
    return hq.post(f"{MEETINGS}/{meeting['id']}/agents/{agent}/remove", headers=user or OWNER(), json={"expected_revision": meeting["revision"]})


def test_migration_adds_the_leave_columns_and_only_an_agent_can_have_them(database_engine: Engine) -> None:
    columns = {r[0] for r in rows(database_engine, "select column_name from information_schema.columns where table_name = 'agent_meeting_participants'")}
    assert {"left_at", "removed_by"} <= columns
    check = rows(database_engine, "select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_agent_meeting_participant_left'")[0][0]
    assert "kind" in check and "removed_by" in check


def test_owner_removes_an_agent_and_it_leaves_for_real(hq: TestClient, database_engine: Engine) -> None:
    meeting, stays, leaves = live_meeting(hq, database_engine)
    before = ok(hq.get(f"{MEETINGS}/{meeting['id']}", headers=OWNER()))
    assert {p["actor_id"]: p["left_at"] for p in before["participants"]} == {stays: None, leaves: None}
    # Both agents are in the meeting at their desks.
    presence = {i["agent_id"]: i["state"] for i in ok(hq.get("/api/v1/office/presence", headers=OWNER()))["items"]}
    assert presence.get(stays) == "meeting" and presence.get(leaves) == "meeting"

    removed = ok(remove(hq, meeting, leaves))
    assert removed["revision"] == meeting["revision"] + 1 and removed["status"] == "active"
    after = ok(hq.get(f"{MEETINGS}/{meeting['id']}", headers=OWNER()))
    by_actor = {p["actor_id"]: p for p in after["participants"]}
    assert by_actor[leaves]["left_at"] is not None and by_actor[leaves]["removed_by"] == OWNER_A
    assert by_actor[stays]["left_at"] is None and by_actor[stays]["removed_by"] is None
    # A second click changes nothing; the agent cannot leave twice.
    again = ok(remove(hq, removed, leaves))
    assert again["revision"] == removed["revision"]
    assert ok(hq.get(f"{MEETINGS}/{meeting['id']}", headers=OWNER()))["participants"] == after["participants"]

    presence = {i["agent_id"]: i["state"] for i in ok(hq.get("/api/v1/office/presence", headers=OWNER()))["items"]}
    assert presence.get(stays) == "meeting" and presence.get(leaves) != "meeting"

    # Only the agent that stayed speaks from now on.
    context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=OWNER_A, roles=["owner"])
    gateway = FakeGateway(texts=["First point.", "Second point."])
    speakers = [asyncio.run(execute_meeting_turn(context, meeting["id"], f"turn-after-leave-{n}", gateway))["speaker_id"] for n in range(2)]
    assert speakers == [stays, stays]

    # Audit and the room's event: ids only.
    audit = rows(database_engine, "select metadata from audit_records where action = 'agent_meeting.agent_remove' and tenant_id = :t", t=TENANT_A)
    assert len(audit) == 1
    meta = audit[0][0] if isinstance(audit[0][0], dict) else json.loads(audit[0][0])
    assert meta["agent_id"] == leaves and meta["meeting_id"] == meeting["id"]
    left = rows(database_engine, "select actor_id, payload from agent_meeting_events where kind = 'agent_left' and session_id = :s", s=meeting["id"])
    assert len(left) == 1 and left[0][0] == OWNER_A
    assert (left[0][1] if isinstance(left[0][1], dict) else json.loads(left[0][1])) == {"agent_id": leaves}


def test_removal_refusals(hq: TestClient, database_engine: Engine) -> None:
    meeting, stays, _ = live_meeting(hq, database_engine)
    assert remove(hq, meeting, "ag_nobody").status_code == 404
    assert remove(hq, {**meeting, "revision": meeting["revision"] - 1}, stays).status_code == 409
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert remove(hq, meeting, stays, user=viewer).status_code == 403
    # Not live: nothing to leave from (a scheduled meeting's agents are its plan; an ended meeting is a record).
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag_planned")
    planned = ok(hq.post(MEETINGS, headers=OWNER(), json={"title": "Later"}), 201)
    planned = ok(hq.post(f"{MEETINGS}/{planned['id']}/agents", headers=OWNER(), json={"agent_id": "ag_planned", "expected_revision": planned["revision"]}), 201)
    refused = remove(hq, planned, "ag_planned")
    assert refused.status_code == 409
    arabic = hq.post(f"{MEETINGS}/{planned['id']}/agents/ag_planned/remove", headers={**OWNER(), "accept-language": "ar"},
                     json={"expected_revision": planned["revision"]})
    assert arabic.status_code == 409 and arabic.json()["error"]["message"] == "لا يمكن إزالة الوكلاء إلا من اجتماع جارٍ أو متوقف مؤقتًا."
    ended = ok(hq.post(f"{MEETINGS}/{meeting['id']}/state", headers=OWNER(), json={"status": "finished", "expected_revision": meeting["revision"]}))
    assert remove(hq, ended, stays).status_code == 409
    assert all(r[0] is None for r in rows(database_engine, "select left_at from agent_meeting_participants where tenant_id = :t", t=TENANT_A))
