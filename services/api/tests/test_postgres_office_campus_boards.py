"""Campus boards on PostgreSQL, as the non-owner app role with forced RLS (pytest -m database).

Every other workspace's board is read in that workspace's own tenant and workspace context with the caller's real
membership: a person who is not a member of a workspace gets nothing of it, and nothing crosses tenants.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from anum_api import office_campus_boards as ocb
from anum_api.main import app
from anum_api.schemas import TenantContext
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database
NOW = datetime.now(timezone.utc)
OWNER_A = "owner_a"
EXEC = "exec_user"
LOGO = "ab" * 32


def headers(tenant: str, workspace: str, user: str, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def run_sql(engine: Engine, sql: str, **params) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def seed_member(engine: Engine, tenant: str, workspace: str, user: str, role: str) -> None:
    run_sql(engine, "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                    "values (:u, :t, :w, :r, true)", u=user, t=tenant, w=workspace, r=role)


def seed_workspace(engine: Engine, tenant: str, workspace: str, prefix: str) -> None:
    """One registry building with a sign, two rooms, an agent at a desk and three open tasks (one waiting, one blocked)."""
    p = {"t": tenant, "w": workspace}
    for sql, extra in (
        ("insert into office_buildings (id, tenant_id, workspace_id, created_by, name, name_ar, timezone, sign_style, brand_color, logo_sha256) "
         "values (:b, :t, :w, 'x', :name, 'مبنى', 'Asia/Dubai', 'plaque', '#0f5b45', :logo)",
         {"b": f"{prefix}_hq", "name": f"{prefix} Tower", "logo": LOGO}),
        ("insert into office_floors (id, tenant_id, workspace_id, created_by, building_id, level, name) values (:f, :t, :w, 'x', :b, 0, 'Ground')",
         {"f": f"{prefix}_f0", "b": f"{prefix}_hq"}),
        ("insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity, team_department) "
         "values (:r, :t, :w, 'x', :f, :name, 'office', 4, 'sales')", {"r": f"{prefix}_r1", "f": f"{prefix}_f0", "name": f"{prefix} Sales room"}),
        ("insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity) "
         "values (:r, :t, :w, 'x', :f, :name, 'meeting', 6)", {"r": f"{prefix}_r2", "f": f"{prefix}_f0", "name": f"{prefix} Board room"}),
        ("insert into agent_profiles (id, tenant_id, workspace_id, owner_user_id, name, icon, color, category, description, instructions, "
         "model_route, allowed_skills, allowed_tools, autonomy, knowledge_mode, knowledge_sources, status, sharing, version, created_at, updated_at) "
         "values (:a, :t, :w, :o, :name, 'bot', '#112233', 'work', '', '', 'fast', '[]', '[]', '{}', 'none', '[]', 'active', 'workspace', 1, :now, :now)",
         {"a": f"{prefix}_ag", "o": OWNER_A, "name": f"{prefix} Agent", "now": NOW}),
        ("insert into office_desks (id, tenant_id, workspace_id, created_by, room_id, label, agent_id) values (:d, :t, :w, 'x', :r, 'D1', :a)",
         {"d": f"{prefix}_d1", "r": f"{prefix}_r1", "a": f"{prefix}_ag"}),
    ):
        run_sql(engine, sql, **p, **extra)
    for task_id, status in ((f"{prefix}_t1", "queued"), (f"{prefix}_t2", "waiting_approval"), (f"{prefix}_t3", "failed")):
        run_sql(engine, """insert into tasks (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status, agent_id, created_at, updated_at)
            values (:i, :t, :w, :o, :title, 'prompt', :s, :a, :when, :when)""",
                **p, i=task_id, o=OWNER_A, title=f"Secret title {task_id}", s=status, a=f"{prefix}_ag", when=NOW - timedelta(seconds=30))
    run_sql(engine, """insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason, expires_at, arguments, created_at, updated_at)
        values (:i, :t, :w, :task, 'send_email', 'high', 'pending', 'r', :exp, '{}', :n, :n)""",
            **p, i=f"{prefix}_ap", task=f"{prefix}_t2", exp=NOW + timedelta(hours=1), n=NOW)
    # The failed task (with its failed run) is at the blocked stage.
    run_sql(engine, """insert into agent_runs (id, tenant_id, workspace_id, task_id, status, result, checkpoint, created_at, updated_at)
        values (:i, :t, :w, :task, 'failed', 'boom', '{}'::jsonb, :when, :when)""",
            **p, i=f"{prefix}_run3", task=f"{prefix}_t3", when=NOW - timedelta(seconds=10))


@pytest.fixture
def boards(postgres_backend: None, database_engine: Engine) -> Iterator[TestClient]:  # noqa: F811
    with database_engine.begin() as connection:
        connection.execute(text(f"grant anum_membership_reader to {APP_ROLE} with inherit false, set true"))
    seed_member(database_engine, TENANT_A, WORKSPACE_A, OWNER_A, "owner")
    seed_member(database_engine, TENANT_A, WORKSPACE_A, EXEC, "viewer")
    seed_member(database_engine, TENANT_A, WORKSPACE_A2, EXEC, "member")
    seed_member(database_engine, TENANT_B, WORKSPACE_B, EXEC, "owner")
    seed_workspace(database_engine, TENANT_A, WORKSPACE_A2, "atwo")
    seed_workspace(database_engine, TENANT_B, WORKSPACE_B, "bee")
    ocb.clear_cache()
    yield TestClient(app)
    ocb.clear_cache()
    with database_engine.begin() as connection:
        connection.execute(text(f"revoke anum_membership_reader from {APP_ROLE}"))


def test_member_gets_each_other_workspace_read_in_its_own_context(boards: TestClient) -> None:
    r = boards.get("/api/v1/office/campus/boards", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [b["workspace_id"] for b in body["boards"]] == [WORKSPACE_A2]  # not the open one, not another tenant's
    board = body["boards"][0]
    assert board["accessible"] is True and board["buildings_total"] == 1
    building = board["buildings"][0]
    assert building == {"id": "atwo_hq", "name": "atwo Tower", "name_ar": "مبنى", "floors": 1, "rooms": 2,
                        "sign": {"style": "plaque", "color": "#0f5b45", "logo_sha256": LOGO}}
    assert board["rooms"]["total"] == 2 and {r["name"] for r in board["rooms"]["items"]} == {"atwo Sales room", "atwo Board room"}
    assert board["departments"] == ["sales"]
    work = board["work"]
    assert work["source"] == "connected" and (work["open"], work["waiting"], work["blocked"]) == (3, 1, 1)
    assert work["by_building"]["atwo_hq"] == {"open": 3, "waiting": 1, "blocked": 1}
    text_out = json.dumps(body)
    assert "bee" not in text_out and "Secret title" not in text_out and "prompt" not in text_out


def test_non_member_of_a_workspace_gets_nothing_of_it(boards: TestClient) -> None:
    r = boards.get("/api/v1/office/campus/boards", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A, "owner"))
    assert r.status_code == 200, r.text
    assert r.json()["boards"] == [] and "atwo" not in r.text
    # Even naming the workspace as a cursor reveals nothing: it is not one of the caller's boards.
    refused = boards.get(f"/api/v1/office/campus/boards?cursor={WORKSPACE_A2}", headers=headers(TENANT_A, WORKSPACE_A, OWNER_A, "owner"))
    assert refused.status_code == 400 and "atwo" not in refused.text


def test_nothing_crosses_tenants(boards: TestClient) -> None:
    # In tenant B the same person sees only tenant B's memberships (here only the open workspace, so no boards).
    other = boards.get("/api/v1/office/campus/boards", headers=headers(TENANT_B, WORKSPACE_B, EXEC, "owner"))
    assert other.status_code == 200 and other.json()["boards"] == [] and "atwo" not in other.text
    # In tenant A, tenant B's workspace is never listed.
    mine = boards.get("/api/v1/office/campus/boards", headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer"))
    assert WORKSPACE_B not in [b["workspace_id"] for b in mine.json()["boards"]] and "bee" not in mine.text


def test_rls_alone_refuses_a_forged_cross_tenant_context(boards: TestClient) -> None:
    # The read itself runs as the app role under forced RLS: a context that names tenant A with tenant B's
    # workspace id sees no rows of tenant B, whatever the code above it did.
    forged = ocb.read_board(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_B, user_id=EXEC, roles=["owner"]), NOW)
    assert forged["buildings"] == [] and forged["rooms"]["total"] == 0 and forged["departments"] == []
    assert forged["work"]["open"] == 0
    # And a context for a workspace of the same tenant only sees that workspace.
    wrong = ocb.read_board(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=EXEC, roles=["viewer"]), NOW)
    assert wrong["buildings"] == [] and "atwo" not in json.dumps(wrong)


def test_a_removed_membership_stops_the_board_at_once(boards: TestClient, database_engine: Engine) -> None:
    h = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert [b["workspace_id"] for b in boards.get("/api/v1/office/campus/boards", headers=h).json()["boards"]] == [WORKSPACE_A2]
    run_sql(database_engine, "update workspace_memberships set active = false where user_id = :u and workspace_id = :w", u=EXEC, w=WORKSPACE_A2)
    after = boards.get("/api/v1/office/campus/boards", headers=h)  # the cached board must not outlive the membership
    assert after.json()["boards"] == [] and "atwo" not in after.text
