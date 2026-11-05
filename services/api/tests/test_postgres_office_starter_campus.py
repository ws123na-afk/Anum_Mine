"""One click starter campus on PostgreSQL as the non-owner app role with forced RLS."""
from __future__ import annotations

import concurrent.futures

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import (
    postgres_backend,
)
from test_postgres_office_hq import (
    EXEC,
    OWNER_A,
    _scoped,
    headers,
    hq,
    run_sql,
    seed_member,
)

from anum_api import office_starter_campus as sc

pytestmark = pytest.mark.database
BASE = "/api/v1/office/starter-campus"
FULL = {"buildings": 8, "floors": 18, "rooms": 62, "desks": 220}


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def rows(engine: Engine, sql: str, **params):
    with engine.begin() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


def counts(engine: Engine, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, int]:
    out = {}
    for key, table in (("buildings", "office_buildings"), ("floors", "office_floors"), ("rooms", "office_rooms"), ("desks", "office_desks")):
        out[key] = rows(engine, f"select count(*) from {table} where tenant_id = :t and workspace_id = :w", t=tenant, w=workspace)[0][0]
    return out


def preview(hq: TestClient, user=None, **query) -> dict:
    r = hq.get(f"{BASE}/preview", params=query, headers=user or OWNER())
    assert r.status_code == 200, r.text
    return r.json()


def confirm(hq: TestClient, view: dict, *, mode: str = "click", user=None, **extra):
    body = {"payload_hash": view["payload_hash"], "input_mode": mode, "buildings": view["selected"], "timezone": view["timezone"], **extra}
    return hq.post(f"{BASE}/confirm", headers=user or OWNER(), json=body)


def test_preview_lists_everything_and_stores_nothing(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    assert view["totals"]["create"] == FULL and view["totals"]["skip"] == {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}
    assert len(view["buildings"]) == 8 and view["selected"] == list(sc.BUILDING_KEYS)
    assert view["rules"] == {"creates_agents": False, "seats_agents": False, "changes_existing": False,
                             "fills_empty_departments": True}
    assert view["timezone"] == "Asia/Dubai" and view["can_confirm"] is True
    room = view["buildings"][0]["floors"][0]["rooms"][0]
    assert room["name"]["ar"] and room["action"] == "create" and room["id"] == "campus-01-hq-facilities"
    assert counts(database_engine) == {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}


def test_selection_changes_the_hash_and_unknown_keys_are_refused(hq: TestClient) -> None:
    all_view, two = preview(hq), preview(hq, buildings="hq,tyre-pulse")
    assert two["selected"] == ["hq", "tyre-pulse"] and two["payload_hash"] != all_view["payload_hash"]
    assert two["totals"]["create"] == {"buildings": 2, "floors": 7, "rooms": 24, "desks": 68}
    assert hq.get(f"{BASE}/preview", params={"buildings": "hq,nope"}, headers=OWNER()).status_code == 422
    assert hq.get(f"{BASE}/preview", params={"buildings": ""}, headers=OWNER()).status_code == 422
    assert hq.get(f"{BASE}/preview", params={"timezone": "Mars/Base"}, headers=OWNER()).status_code == 422


def test_only_a_click_by_an_owner_with_the_shown_hash_confirms(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    for mode in ("typed", "voice"):
        assert confirm(hq, view, mode=mode).status_code == 403
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert preview(hq, user=viewer)["can_confirm"] is False
    assert confirm(hq, view, user=viewer).status_code == 403
    bad = {**view, "payload_hash": "0" * 64}
    assert confirm(hq, bad).status_code == 409
    assert hq.post(f"{BASE}/confirm", headers=OWNER(), json={"payload_hash": view["payload_hash"], "buildings": ["hq"], "timezone": "Asia/Dubai"}).status_code == 403
    assert counts(database_engine) == {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}


def test_confirm_builds_the_campus_empty_audited_and_idempotent(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    r = confirm(hq, view)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "created" and body["totals"] == FULL and body["agents_created"] == 0
    assert [b["status"] for b in body["buildings"]] == ["created"] * 8
    assert counts(database_engine) == FULL
    assert rows(database_engine, "select count(*) from agent_profiles where tenant_id = :t", t=TENANT_A) == [(0,)]
    assert rows(database_engine, "select count(*) from office_desks where agent_id is not null") == [(0,)]
    assert rows(database_engine, "select count(*) from office_rooms where scene_x is null or name_ar is null") == [(0,)]
    linked = dict(rows(database_engine, "select team_department, count(*) from office_rooms where team_department is not null group by 1"))
    assert set(linked) == {"sales", "support", "operations", "finance", "tyre_engineering", "marketing", "technology", "research",
                           "legal", "compliance", "security", "hr", "procurement", "executive"}
    assert rows(database_engine, "select count(*) from office_floors where width_m is null or depth_m is null") == [(0,)]
    audit = rows(database_engine, "select metadata from audit_records where action = 'office.starter_campus.building' and tenant_id = :t", t=TENANT_A)
    assert len(audit) == 8
    hq_audit = next(m[0] for m in audit if m[0]["office_resource"] == "campus-01-hq")
    assert hq_audit["after"]["created"] == {"buildings": 1, "floors": 5, "rooms": 17, "desks": 39}
    assert hq_audit["after"]["input_mode"] == "click" and hq_audit["before"]["existing"] == []
    events = rows(database_engine, "select count(*) from domain_events where type = 'office.layout_changed' and tenant_id = :t", t=TENANT_A)
    assert events == [(8,)]
    # The same click again, or a retry after a timeout, writes nothing.
    again = confirm(hq, view)
    assert again.status_code == 200 and again.json()["outcome"] == "already_set_up" and again.json()["totals"] == {k: 0 for k in FULL}
    assert counts(database_engine) == FULL
    fresh = preview(hq)
    assert fresh["totals"]["create"] == {k: 0 for k in FULL} and {b["status"] for b in fresh["buildings"]} == {"complete"}
    # The floors respect the builder's rules: every room inside its floor, none overlapping.
    bad = rows(database_engine, """select count(*) from office_rooms r join office_floors f on f.id = r.floor_id and f.workspace_id = r.workspace_id
        where r.scene_x < 0 or r.scene_z < 0 or r.scene_x + r.scene_width > f.width_m or r.scene_z + r.scene_depth > f.depth_m""")
    assert bad == [(0,)]
    overlap = rows(database_engine, """select count(*) from office_rooms a join office_rooms b on a.floor_id = b.floor_id and a.id < b.id
        where a.scene_x < b.scene_x + b.scene_width - 0.000001 and b.scene_x < a.scene_x + a.scene_width - 0.000001
          and a.scene_z < b.scene_z + b.scene_depth - 0.000001 and b.scene_z < a.scene_z + a.scene_depth - 0.000001""")
    assert overlap == [(0,)]


def test_choose_buildings_then_add_the_rest_later(hq: TestClient, database_engine: Engine) -> None:
    first = preview(hq, buildings="hq,tyre-pulse")
    assert confirm(hq, first).json()["totals"] == {"buildings": 2, "floors": 7, "rooms": 24, "desks": 68}
    view = preview(hq)
    states = {b["key"]: b["status"] for b in view["buildings"]}
    assert states["hq"] == states["tyre-pulse"] == "complete" and states["commerce"] == "new"
    assert view["totals"]["create"] == {"buildings": 6, "floors": 11, "rooms": 38, "desks": 152}
    assert confirm(hq, view).json()["outcome"] == "created"
    assert counts(database_engine) == FULL


def test_rerun_never_changes_what_the_owner_edited_and_restores_what_is_missing(hq: TestClient, database_engine: Engine) -> None:
    confirm(hq, preview(hq))
    run_sql(database_engine, "update office_rooms set name = 'Front desk', revision = revision + 1 where id = 'campus-01-hq-reception'")
    run_sql(database_engine, "update office_rooms set scene_x = scene_x + 0.5, revision = revision + 1 where id = 'campus-02-commerce-retail'")
    run_sql(database_engine, "update office_buildings set name = 'My HQ' where id = 'campus-01-hq'")
    run_sql(database_engine, "delete from office_desks where room_id = 'campus-02-commerce-orders'")
    run_sql(database_engine, "delete from office_rooms where id = 'campus-02-commerce-orders'")
    before = rows(database_engine, "select id, name, scene_x, revision from office_rooms where id in ('campus-01-hq-reception','campus-02-commerce-retail')")
    view = preview(hq)
    assert view["totals"]["create"] == {"buildings": 0, "floors": 0, "rooms": 1, "desks": 6}
    assert confirm(hq, view).json()["outcome"] == "created"
    assert counts(database_engine) == FULL
    assert rows(database_engine, "select id, name, scene_x, revision from office_rooms where id in ('campus-01-hq-reception','campus-02-commerce-retail')") == before
    assert rows(database_engine, "select name from office_buildings where id = 'campus-01-hq'") == [("My HQ",)]


def test_a_building_that_fails_leaves_nothing_behind_and_the_rest_still_build(hq: TestClient, database_engine: Engine) -> None:
    # The owner already has a desk with an id the tyre pulse fleet room needs: that one building cannot be saved.
    run_sql(database_engine, "insert into office_buildings (id, tenant_id, workspace_id, created_by, name, timezone) values ('mine', :t, :w, 'x', 'Mine', 'Asia/Dubai')", t=TENANT_A, w=WORKSPACE_A)
    run_sql(database_engine, "insert into office_floors (id, tenant_id, workspace_id, created_by, building_id, level, name) values ('minef', :t, :w, 'x', 'mine', 0, 'G')", t=TENANT_A, w=WORKSPACE_A)
    run_sql(database_engine, "insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity) values ('miner', :t, :w, 'x', 'minef', 'R', 'office', 4)", t=TENANT_A, w=WORKSPACE_A)
    run_sql(database_engine, "insert into office_desks (id, tenant_id, workspace_id, created_by, room_id, label) values ('campus-03-tyre-pulse-fleet-d1', :t, :w, 'x', 'miner', 'D')", t=TENANT_A, w=WORKSPACE_A)
    view = preview(hq)
    body = confirm(hq, view).json()
    assert body["outcome"] == "partial"
    state = {b["key"]: b for b in body["buildings"]}
    assert state["tyre-pulse"]["status"] == "failed" and "Nothing was created" in state["tyre-pulse"]["message"]
    assert sum(1 for b in body["buildings"] if b["status"] == "created") == 7
    assert rows(database_engine, "select count(*) from office_buildings where id like 'campus-03%'") == [(0,)]
    assert rows(database_engine, "select count(*) from office_floors where id like 'campus-03%'") == [(0,)]
    assert rows(database_engine, "select count(*) from office_rooms where id like 'campus-03%'") == [(0,)]
    assert rows(database_engine, "select count(*) from audit_records where action = 'office.starter_campus.building' and target = 'campus-03-tyre-pulse'") == [(0,)]
    run_sql(database_engine, "delete from office_desks where id = 'campus-03-tyre-pulse-fleet-d1'")
    retry = confirm(hq, preview(hq))
    assert retry.json()["outcome"] == "created" and retry.json()["totals"]["buildings"] == 1
    assert counts(database_engine) == {"buildings": 9, "floors": 19, "rooms": 63, "desks": 220}


def test_two_confirms_at_once_never_duplicate(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq, buildings="hq,commerce")
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: confirm(hq, view), range(2)))
    assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
    assert counts(database_engine) == {"buildings": 2, "floors": 8, "rooms": 29, "desks": 97}
    assert rows(database_engine, "select count(*) from audit_records where action = 'office.starter_campus.building'") == [(2,)]


def test_each_workspace_gets_its_own_campus_and_never_reads_another(hq: TestClient, database_engine: Engine) -> None:
    seed_member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    other = headers(TENANT_B, WORKSPACE_B, "owner_b")
    confirm(hq, preview(hq, buildings="hq"))
    b_view = preview(hq, user=other)
    assert b_view["totals"]["create"] == FULL, "another tenant's records do not count as existing"
    assert confirm(hq, b_view, user=other).status_code == 200
    assert counts(database_engine, TENANT_A, WORKSPACE_A)["buildings"] == 1 and counts(database_engine, TENANT_B, WORKSPACE_B) == FULL
    # As the app role under row level security each workspace sees only its own rows.
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A, OWNER_A)
        assert connection.execute(text("select count(*) from office_rooms")).scalar_one() == 17
        _scoped(connection, TENANT_B, WORKSPACE_B, "owner_b")
        assert connection.execute(text("select count(*) from office_rooms")).scalar_one() == 62
        tx.rollback()
    audit_tenants = {r[0] for r in rows(database_engine, "select distinct tenant_id from audit_records where action = 'office.starter_campus.building'")}
    assert audit_tenants == {TENANT_A, TENANT_B}


def test_the_new_rooms_make_the_organisation_gaps_panel_propose_agents(hq: TestClient, database_engine: Engine) -> None:
    confirm(hq, preview(hq))
    gaps = hq.get("/api/v1/office/provisioning/gaps", headers=OWNER()).json()["gaps"]
    assert {g["template_id"] for g in gaps} >= {"finance_reconciliation", "sales_followup", "support_triage", "fleet_maintenance", "operations_dispatcher",
                                                   "marketing_specialist", "technology_coordinator", "research_analyst", "legal_reviewer", "compliance_reviewer"}
    assert rows(database_engine, "select count(*) from agent_profiles where tenant_id = :t", t=TENANT_A) == [(0,)]
