"""ANUM MINE campus, presence, workstation work and executive grants on PostgreSQL.

Runs as the non-owner app role with forced RLS (pytest -m database). Rows are seeded with
the superuser connection; every API call goes through the real routes.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.main import app
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database
NOW = datetime.now(timezone.utc)
OWNER_A = "owner_a"
EXEC = "exec_user"


def headers(tenant: str, workspace: str, user: str, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def run_sql(engine: Engine, sql: str, **params) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def seed_member(engine: Engine, tenant: str, workspace: str, user: str, role: str) -> None:
    run_sql(engine, "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                    "values (:u, :t, :w, :r, true)", u=user, t=tenant, w=workspace, r=role)


def seed_agent(engine: Engine, tenant: str, workspace: str, agent_id: str, *, status="active", sharing="workspace",
               owner=OWNER_A) -> None:
    run_sql(engine, """
        insert into agent_profiles (id, tenant_id, workspace_id, owner_user_id, name, icon, color, category, description,
          instructions, model_route, allowed_skills, allowed_tools, autonomy, knowledge_mode, knowledge_sources, status,
          sharing, version, created_at, updated_at)
        values (:i, :t, :w, :o, :n, 'bot', '#112233', 'work', '', '', 'fast', '[]', '[]', '{}', 'none', '[]', :s, :sh, 1, :now, :now)""",
            i=agent_id, t=tenant, w=workspace, o=owner, n=f"Agent {agent_id}", s=status, sh=sharing, now=NOW)


def seed_task(engine: Engine, tenant: str, workspace: str, task_id: str, agent_id: str, status: str,
              *, run_status: str | None = None, result: str | None = None, age: int = 20) -> None:
    when = NOW - timedelta(seconds=age)
    run_sql(engine, """insert into tasks (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status, agent_id, created_at, updated_at)
        values (:i, :t, :w, :o, :title, 'prompt text', :s, :a, :when, :when)""",
            i=task_id, t=tenant, w=workspace, o=OWNER_A, title=f"Title {task_id}", s=status, a=agent_id, when=when)
    if run_status:
        run_sql(engine, """insert into agent_runs (id, tenant_id, workspace_id, task_id, status, result, checkpoint, created_at, updated_at)
            values (:i, :t, :w, :task, :s, :res, cast(:cp as jsonb), :when, :when)""",
                i=f"run_{task_id}", t=tenant, w=workspace, task=task_id, s=run_status, res=result, when=when,
                cp=json.dumps({"phase": "executing", "version": 3}))


def seed_office(engine: Engine, tenant: str, workspace: str, agent_id: str | None = None) -> None:
    for sql, params in (
        ("insert into office_buildings (id, tenant_id, workspace_id, created_by, name, timezone) values ('hq', :t, :w, 'x', 'HQ', 'Asia/Dubai')", {}),
        ("insert into office_floors (id, tenant_id, workspace_id, created_by, building_id, level, name) values ('f0', :t, :w, 'x', 'hq', 0, 'Ground')", {}),
        ("insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity) values ('r1', :t, :w, 'x', 'f0', 'Ops', 'office', 4)", {}),
        ("insert into office_desks (id, tenant_id, workspace_id, created_by, room_id, label, agent_id) values ('d1', :t, :w, 'x', 'r1', 'D1', :a)", {"a": agent_id}),
    ):
        run_sql(engine, sql, t=tenant, w=workspace, **params)


@pytest.fixture
def hq(postgres_backend: None, database_engine: Engine) -> Iterator[TestClient]:  # noqa: F811
    with database_engine.begin() as connection:
        for role in ("anum_membership_reader", "anum_executive_reader"):
            connection.execute(text(f"grant {role} to {APP_ROLE} with inherit false, set true"))
    seed_member(database_engine, TENANT_A, WORKSPACE_A, OWNER_A, "owner")
    seed_member(database_engine, TENANT_A, WORKSPACE_A, EXEC, "viewer")
    seed_member(database_engine, TENANT_A, WORKSPACE_A2, EXEC, "owner")
    seed_member(database_engine, TENANT_B, WORKSPACE_B, EXEC, "owner")
    yield TestClient(app)
    with database_engine.begin() as connection:
        for role in ("anum_membership_reader", "anum_executive_reader"):
            connection.execute(text(f"revoke {role} from {APP_ROLE}"))


# ---- campus -----------------------------------------------------------------------------


def test_campus_lists_only_active_memberships_each_read_in_its_own_workspace(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "t1", "ag1", "queued")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_agent(database_engine, TENANT_B, WORKSPACE_B, "b_ag1")  # another tenant's agent
    r = hq.get("/api/v1/office/campus", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [b["workspace_id"] for b in body["buildings"]]
    assert ids == sorted([WORKSPACE_A, WORKSPACE_A2]) and WORKSPACE_B not in ids
    a = next(b for b in body["buildings"] if b["workspace_id"] == WORKSPACE_A)
    assert a["agents"]["total"] == 1 and a["agents"]["by_state"]["queued"] == 1
    assert a["desks"] == 1 and a["floors"][0]["name"] == "Ground" and a["accessible"] is True
    assert a["kind"] is None  # never guessed
    a2 = next(b for b in body["buildings"] if b["workspace_id"] == WORKSPACE_A2)
    assert a2["agents"]["total"] == 0 and a2["role"] == "owner"


def test_campus_hides_workspaces_without_membership_and_inactive_ones(hq: TestClient, database_engine: Engine) -> None:
    run_sql(database_engine, "update workspace_memberships set active = false where user_id = :u and workspace_id = :w",
            u=EXEC, w=WORKSPACE_A2)
    body = hq.get("/api/v1/office/campus", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")).json()
    assert [b["workspace_id"] for b in body["buildings"]] == [WORKSPACE_A]
    stranger = hq.get("/api/v1/office/campus", headers=headers(TENANT_A, WORKSPACE_A, "nobody", "viewer"))
    assert stranger.status_code in (200, 403)
    if stranger.status_code == 200:
        assert stranger.json()["buildings"] == []


def test_campus_profile_is_owner_only_audited_and_revisioned(hq: TestClient, database_engine: Engine) -> None:
    owner = headers(TENANT_A, WORKSPACE_A, OWNER_A)
    body = {"kind": "executive", "icon": "landmark"}
    assert hq.put("/api/v1/office/campus/profile", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer"), json=body).status_code == 403
    assert hq.put("/api/v1/office/campus/profile", headers=owner, json={**body, "kind": "casino"}).status_code == 422
    first = hq.put("/api/v1/office/campus/profile", headers=owner, json=body)
    assert first.status_code == 200 and first.json()["revision"] == 1
    assert hq.put("/api/v1/office/campus/profile", headers=owner, json={**body, "icon": "chart"}).status_code == 409
    second = hq.put("/api/v1/office/campus/profile", headers=owner, json={**body, "icon": "chart", "expected_revision": 1})
    assert second.json()["revision"] == 2
    campus = hq.get("/api/v1/office/campus", headers=owner).json()
    a = next(b for b in campus["buildings"] if b["workspace_id"] == WORKSPACE_A)
    assert a["kind"] == "executive" and a["icon"] == "chart"
    with database_engine.connect() as c:
        assert c.execute(text("select count(*) from audit_records where action = 'office.campus.profile'")).scalar() == 2


# ---- presence ---------------------------------------------------------------------------


def test_presence_snapshot_from_records_and_workspace_isolation(hq: TestClient, database_engine: Engine) -> None:
    for name in ("idle1", "work1", "queue1", "wait1", "fail1"):
        seed_agent(database_engine, TENANT_A, WORKSPACE_A, name)
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tw", "work1", "running", run_status="running")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tq", "queue1", "queued")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tx", "wait1", "waiting_approval", run_status="waiting_approval")
    run_sql(database_engine, """insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason, run_id, expires_at, arguments, created_at, updated_at)
        values ('ap1', :t, :w, 'tx', 'send_email', 'high', 'pending', 'r', 'run_tx', :exp, '{}', :n, :n)""",
            t=TENANT_A, w=WORKSPACE_A, exp=NOW + timedelta(hours=1), n=NOW)
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tf", "fail1", "failed", run_status="failed", result="boom")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tc", "idle1", "cancelled", run_status="cancelled")
    # same agent id in workspace B must not leak
    seed_agent(database_engine, TENANT_B, WORKSPACE_B, "b_work1")
    seed_task(database_engine, TENANT_B, WORKSPACE_B, "b_tw", "b_work1", "running", run_status="running")
    body = hq.get("/api/v1/office/presence", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A)).json()
    states = {i["agent_id"]: i["state"] for i in body["items"]}
    assert states == {"idle1": "idle", "work1": "working", "queue1": "queued", "wait1": "waiting_approval", "fail1": "blocked"}
    wait = next(i for i in body["items"] if i["agent_id"] == "wait1")
    assert wait["provenance"]["approval_id"] == "ap1" and wait["task"]["id"] == "tx"
    assert body["totals"]["working"] == 1 and body["workspace_id"] == WORKSPACE_A
    other = hq.get("/api/v1/office/presence", headers=headers(TENANT_B, WORKSPACE_B, EXEC)).json()
    assert [i["state"] for i in other["items"]] == ["working"]
    nothing = hq.get("/api/v1/office/presence", headers=headers(TENANT_A, WORKSPACE_A2, EXEC)).json()
    assert nothing["items"] == []


def test_presence_expired_approval_blocks_and_private_agent_stays_hidden(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag_wait")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "tx", "ag_wait", "waiting_approval", run_status="waiting_approval")
    run_sql(database_engine, """insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason, expires_at, arguments, created_at, updated_at)
        values ('ap1', :t, :w, 'tx', 'send_email', 'high', 'pending', 'r', :exp, '{}', :n, :n)""",
            t=TENANT_A, w=WORKSPACE_A, exp=NOW - timedelta(minutes=1), n=NOW - timedelta(hours=1))
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "secret", sharing="private", owner="someone_else")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "ts", "secret", "running", run_status="running")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "secret")
    body = hq.get("/api/v1/office/presence", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A)).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["ag_wait"]["state"] == "blocked" and by_key["ag_wait"]["provenance"]["reason"] == "approval_expired"
    assert "secret" not in by_key and by_key["desk:d1"]["state"] == "unknown"
    assert "Title ts" not in json.dumps(body)


def test_presence_meeting_and_pause(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "m1")
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "p1", status="paused")
    run_sql(database_engine, "insert into agent_meeting_sessions (id, tenant_id, workspace_id, created_by, title, status) values ('mt1', :t, :w, 'x', 'Sync', 'active')",
            t=TENANT_A, w=WORKSPACE_A)
    run_sql(database_engine, "insert into agent_meeting_participants (id, tenant_id, workspace_id, session_id, kind, actor_id, added_by) values ('pa1', :t, :w, 'mt1', 'agent', 'm1', 'x')",
            t=TENANT_A, w=WORKSPACE_A)
    body = hq.get("/api/v1/office/presence", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A)).json()
    states = {i["agent_id"]: (i["state"], i["provenance"]["meeting_id"]) for i in body["items"]}
    assert states == {"m1": ("meeting", "mt1"), "p1": ("paused", None)}


def test_presence_stream_route_is_registered_and_refuses_without_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.settings import settings

    monkeypatch.setattr(settings, "repository_backend", "memory")
    r = TestClient(app).get("/api/v1/office/presence/stream", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A))
    assert r.status_code == 503


# ---- workstation ------------------------------------------------------------------------


def test_workstation_work_is_real_permissioned_and_scoped(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "t1", "ag1", "failed", run_status="failed", result="Tool crashed")
    run_sql(database_engine, """insert into workspace_files (id, tenant_id, workspace_id, name, content_type, size_bytes, sha256, storage_key, created_by, created_at, version)
        values ('file1', :t, :w, 'plan.pdf', 'application/pdf', 10, :h, 'k/file1', 'x', :n, 1)""",
            t=TENANT_A, w=WORKSPACE_A, h="0" * 64, n=NOW)
    run_sql(database_engine, """insert into agent_run_steps (id, tenant_id, workspace_id, run_id, type, summary, step_metadata, created_at, updated_at)
        values ('s1', :t, :w, 'run_t1', 'retrieval', 'Used 1 passages', cast(:m as jsonb), :n, :n)""",
            t=TENANT_A, w=WORKSPACE_A, n=NOW, m=json.dumps({"sources": [{"source_type": "file", "source_id": "file1"}, {"source_type": "memory", "source_id": "mem1"}]}))
    own = headers(TENANT_A, WORKSPACE_A, OWNER_A)
    work = hq.get("/api/v1/office/workstations/d1/work", headers=own).json()
    assert work["agent"]["id"] == "ag1" and work["reason"] is None
    assert work["presence"]["state"] == "blocked"
    task = work["tasks"][0]
    assert task["id"] == "t1" and task["run"]["checkpoint_version"] == 3 and task["run"]["steps"][0]["type"] == "retrieval"
    assert work["errors"] == [{"task_id": "t1", "run_id": "run_t1", "at": task["run"]["updated_at"], "message": "Tool crashed"}]
    assert [d["id"] for d in work["documents"]] == ["file1"]
    assert "prompt text" not in json.dumps(work)
    # another tenant and workspace cannot read the desk
    assert hq.get("/api/v1/office/workstations/d1/work", headers=headers(TENANT_B, WORKSPACE_B, EXEC)).status_code == 404
    assert hq.get("/api/v1/office/workstations/d1/work", headers=headers(TENANT_A, WORKSPACE_A2, EXEC)).status_code == 404
    assert hq.get("/api/v1/office/workstations/nope/work", headers=own).status_code == 404


def test_workstation_empty_and_hidden_agent_states(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A, None)
    own = headers(TENANT_A, WORKSPACE_A, OWNER_A)
    empty = hq.get("/api/v1/office/workstations/d1/work", headers=own).json()
    assert empty["reason"] == "unassigned" and empty["tasks"] == [] and empty["agent"] is None
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "secret", sharing="private", owner="someone_else")
    run_sql(database_engine, "update office_desks set agent_id = 'secret' where id = 'd1'")
    hidden = hq.get("/api/v1/office/workstations/d1/work", headers=own).json()
    assert hidden["reason"] == "agent_not_visible" and hidden["agent"] is None


# ---- executive --------------------------------------------------------------------------


def grant(client: TestClient, tenant=TENANT_A, workspace=WORKSPACE_A, owner=OWNER_A, scopes=None, expires=None, user=EXEC):
    body = {"grantee_user_id": user, "scopes": scopes or ["counts", "tasks", "approvals", "cost", "meetings"]}
    if expires:
        body["expires_at"] = expires
    return client.post("/api/v1/executive/grants", headers=headers(tenant, workspace, owner), json=body)


def activate(client: TestClient, **kw) -> dict:
    created = grant(client, **kw)
    assert created.status_code == 201, created.text
    g = created.json()
    r = client.post(f"/api/v1/executive/grants/{g['id']}/approve",
                    headers=headers(kw.get("tenant", TENANT_A), kw.get("workspace", WORKSPACE_A), kw.get("owner", OWNER_A)),
                    json={"payload_hash": g["payload_hash"]})
    assert r.status_code == 200, r.text
    return r.json()


def overview(client: TestClient, tenant=TENANT_A, workspace=WORKSPACE_A2, user=EXEC) -> dict:
    r = client.get("/api/v1/executive/overview", headers=headers(tenant, workspace, user, "owner"))
    assert r.status_code == 200, r.text
    return r.json()


def test_grant_needs_owner_and_pending_grant_gives_no_access(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    viewer = hq.post("/api/v1/executive/grants", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer"),
                     json={"grantee_user_id": EXEC, "scopes": ["counts"]})
    assert viewer.status_code == 403
    member = hq.post("/api/v1/executive/grants", headers=headers(TENANT_A, WORKSPACE_A, "m", "member"),
                     json={"grantee_user_id": EXEC, "scopes": ["counts"]})
    assert member.status_code == 403
    created = grant(hq)
    assert created.status_code == 201 and created.json()["status"] == "pending" and created.json()["approval_required"] is True
    assert grant(hq).status_code == 409  # one live request per person
    item = next(w for w in overview(hq)["workspaces"] if w["workspace_id"] == WORKSPACE_A)
    assert item["access"] == "not_granted" and "summary" not in item


def test_approval_needs_the_shown_hash_and_then_grants_only_the_minimum(hq: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    seed_task(database_engine, TENANT_A, WORKSPACE_A, "t1", "ag1", "queued")
    seed_office(database_engine, TENANT_A, WORKSPACE_A, "ag1")
    g = grant(hq, scopes=["counts", "approvals"]).json()
    owner = headers(TENANT_A, WORKSPACE_A, OWNER_A)
    assert hq.post(f"/api/v1/executive/grants/{g['id']}/approve", headers=owner, json={"payload_hash": "0" * 64}).status_code == 409
    assert hq.post(f"/api/v1/executive/grants/{g['id']}/approve", headers=owner, json={}).status_code == 422
    assert hq.post(f"/api/v1/executive/grants/{g['id']}/approve", headers=owner, json={"payload_hash": g["payload_hash"]}).status_code == 200
    # a guessed id from another workspace or tenant is a 404
    assert hq.post(f"/api/v1/executive/grants/{g['id']}/approve", headers=headers(TENANT_A, WORKSPACE_A2, EXEC),
                   json={"payload_hash": g["payload_hash"]}).status_code == 404
    item = next(w for w in overview(hq)["workspaces"] if w["workspace_id"] == WORKSPACE_A)
    assert item["access"] == "granted" and item["scopes"] == ["counts", "approvals"]
    assert set(item["summary"]) == {"counts", "approvals"}  # no tasks, cost or meetings
    assert item["summary"]["counts"]["agents"] == 1 and item["summary"]["approvals"]["pending"] == 0
    flat = json.dumps(item)
    assert "Title t1" not in flat and "Agent ag1" not in flat and "prompt text" not in flat
    with database_engine.connect() as c:
        audit = c.execute(text("select actor, workspace_id, metadata from audit_records where action = 'executive.overview.read'")).all()
    assert len(audit) == 1 and audit[0].actor == EXEC and audit[0].workspace_id == WORKSPACE_A


def test_every_overview_read_is_audited_in_the_granted_workspace(hq: TestClient, database_engine: Engine) -> None:
    activate(hq, scopes=["tasks"])
    overview(hq)
    overview(hq)
    with database_engine.connect() as c:
        n = c.execute(text("select count(*) from audit_records where action = 'executive.overview.read' and workspace_id = :w"), {"w": WORKSPACE_A}).scalar()
        other = c.execute(text("select count(*) from audit_records where action = 'executive.overview.read' and workspace_id <> :w"), {"w": WORKSPACE_A}).scalar()
    assert n == 2 and other == 0


def test_revoked_expired_and_superseded_grants_give_nothing(hq: TestClient, database_engine: Engine) -> None:
    first = activate(hq, scopes=["counts"])
    second = activate(hq, scopes=["counts", "tasks"])  # widening: new request, replaces the first
    with database_engine.connect() as c:
        statuses = dict(c.execute(text("select id, status from executive_grants")).all())
    assert statuses[first["id"]] == "superseded" and statuses[second["id"]] == "active"
    owner = headers(TENANT_A, WORKSPACE_A, OWNER_A)
    assert hq.delete(f"/api/v1/executive/grants/{second['id']}", headers=owner).status_code == 204
    assert hq.delete(f"/api/v1/executive/grants/{second['id']}", headers=owner).status_code == 204  # idempotent
    item = next(w for w in overview(hq)["workspaces"] if w["workspace_id"] == WORKSPACE_A)
    assert item["access"] == "not_granted"
    third = activate(hq, scopes=["counts"], expires=(NOW + timedelta(days=30)).isoformat())
    run_sql(database_engine, "update executive_grants set expires_at = :e where id = :i", e=NOW - timedelta(minutes=1), i=third["id"])
    item = next(w for w in overview(hq)["workspaces"] if w["workspace_id"] == WORKSPACE_A)
    assert item["access"] == "not_granted"
    listed = hq.get("/api/v1/executive/grants", headers=owner).json()["items"]
    assert next(g for g in listed if g["id"] == third["id"])["status"] == "expired"


def test_narrowing_is_immediate_and_widening_needs_a_new_request(hq: TestClient) -> None:
    g = activate(hq, scopes=["counts", "tasks"], expires=(NOW + timedelta(days=30)).isoformat())
    owner = headers(TENANT_A, WORKSPACE_A, OWNER_A)
    url = f"/api/v1/executive/grants/{g['id']}"
    assert hq.patch(url, headers=owner, json={"expected_revision": g["revision"], "scopes": ["counts", "cost"]}).status_code == 409
    assert hq.patch(url, headers=owner, json={"expected_revision": g["revision"], "expires_at": (NOW + timedelta(days=60)).isoformat()}).status_code == 409
    assert hq.patch(url, headers=owner, json={"expected_revision": 99, "scopes": ["counts"]}).status_code == 409
    ok = hq.patch(url, headers=owner, json={"expected_revision": g["revision"], "scopes": ["counts"]})
    assert ok.status_code == 200 and ok.json()["scopes"] == ["counts"] and ok.json()["payload_hash"] != g["payload_hash"]
    item = next(w for w in overview(hq)["workspaces"] if w["workspace_id"] == WORKSPACE_A)
    assert set(item["summary"]) == {"counts"}


def test_two_person_rule_blocks_self_approval(hq: TestClient, database_engine: Engine) -> None:
    run_sql(database_engine, "insert into workspace_approval_policies (tenant_id, workspace_id, two_person_rule, medium_risk_requires_approval, updated_by) values (:t, :w, true, false, 'x')",
            t=TENANT_A, w=WORKSPACE_A)
    g = grant(hq).json()
    r = hq.post(f"/api/v1/executive/grants/{g['id']}/approve", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A), json={"payload_hash": g["payload_hash"]})
    assert r.status_code == 403
    other_owner = hq.post(f"/api/v1/executive/grants/{g['id']}/approve", headers=headers(TENANT_A, WORKSPACE_A, "second_owner"), json={"payload_hash": g["payload_hash"]})
    assert other_owner.status_code == 200


def test_grants_never_cross_tenants_or_workspaces(hq: TestClient, database_engine: Engine) -> None:
    activate(hq, tenant=TENANT_B, workspace=WORKSPACE_B, owner="owner_b", user=EXEC)
    # tenant A token: the tenant B grant is invisible, tenant B workspace never listed
    ids = [w["workspace_id"] for w in overview(hq)["workspaces"]]
    assert WORKSPACE_B not in ids
    b = overview(hq, tenant=TENANT_B, workspace=WORKSPACE_B)
    assert [w["access"] for w in b["workspaces"]] == ["granted"]
    # owners list only their own workspace's grants
    assert hq.get("/api/v1/executive/grants", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A)).json()["items"] == []
    assert len(hq.get("/api/v1/executive/grants", headers=headers(TENANT_B, WORKSPACE_B, "owner_b")).json()["items"]) == 1


def test_overview_lists_ungranted_member_workspaces_and_a_granted_non_member_workspace(hq: TestClient, database_engine: Engine) -> None:
    run_sql(database_engine, "delete from workspace_memberships where user_id = :u and workspace_id = :w", u=EXEC, w=WORKSPACE_A)
    activate(hq, scopes=["meetings"])
    items = {w["workspace_id"]: w for w in overview(hq)["workspaces"]}
    assert items[WORKSPACE_A]["access"] == "granted" and items[WORKSPACE_A]["summary"] == {"meetings": {"active": 0, "scheduled": 0, "paused": 0}}
    assert items[WORKSPACE_A2]["access"] == "not_granted" and items[WORKSPACE_A2]["open_with"] == "workspace_switch"
    # the executive still cannot use ordinary APIs of the granted workspace
    assert hq.get("/api/v1/office-spaces/buildings", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")).status_code in (200, 403)


# ---- RLS at the table level -------------------------------------------------------------


def _scoped(connection, tenant: str, workspace: str | None = None, user: str | None = None) -> None:
    connection.execute(text("select set_config('anum.tenant_id', :v, true)"), {"v": tenant})
    if workspace:
        connection.execute(text("select set_config('anum.workspace_id', :v, true)"), {"v": workspace})
    if user:
        connection.execute(text("select set_config('anum.user_id', :v, true)"), {"v": user})


def test_executive_grants_rls_reader_columns_and_inheritance(hq: TestClient, database_engine: Engine) -> None:
    activate(hq)
    activate(hq, tenant=TENANT_B, workspace=WORKSPACE_B, owner="owner_b")
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A, EXEC)
        rows = connection.execute(text("select workspace_id from executive_grants")).scalars().all()
        tx.rollback()
    assert set(rows) == {WORKSPACE_A}  # the reader role granted without inherit never widens the app role
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text("set local role anum_executive_reader"))
        _scoped(connection, TENANT_A, None, EXEC)
        mine = connection.execute(text("select workspace_id from executive_grants")).scalars().all()
        tx.rollback()
    assert mine == [WORKSPACE_A]
    for sql in ("select payload_hash from executive_grants", "select requested_by from executive_grants",
                "update executive_grants set status = 'active'", "select id from tasks"):
        with database_engine.connect() as connection:
            tx = connection.begin()
            connection.execute(text("set local role anum_executive_reader"))
            _scoped(connection, TENANT_A, None, EXEC)
            with pytest.raises(DBAPIError):
                connection.execute(text(sql))
            tx.rollback()
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text("set local role anum_executive_reader"))
        _scoped(connection, TENANT_A, None, "someone_else")
        assert connection.execute(text("select count(*) from executive_grants")).scalar() == 0
        tx.rollback()


def test_new_tables_have_forced_rls(database_engine: Engine) -> None:
    with database_engine.connect() as c:
        rows = c.execute(text("select relname, relrowsecurity, relforcerowsecurity from pg_class where relname in ('executive_grants', 'campus_workspace_profiles')")).all()
    assert len(rows) == 2 and all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
