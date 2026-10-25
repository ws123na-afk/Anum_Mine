"""Layout revision history, restore and templates against the non-owner PostgreSQL app role.

Run with pytest -m database. Covers: the snapshot commits (or rolls back) with the save, restore is exact and
makes a new revision, click only, hash and head checks, occupied desks, idempotency, pruning, isolation and RLS.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
client = TestClient(app)
SP = "/api/v1/office-spaces"
LH = "/api/v1/office-layout"


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield database_engine


def hd(tenant: str = TENANT_A, workspace: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": "owner_test", "x-user-roles": role}


def ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


def seed_floor(h: dict[str, str], *, desks: bool = True) -> None:
    ok(client.post(f"{SP}/buildings", headers=h, json={"id": "hq", "name": "HQ", "timezone": "Asia/Riyadh"}), 201)
    ok(client.post(f"{SP}/floors", headers=h, json={"id": "g", "building_id": "hq", "level": 0, "name": "Ground"}), 201)
    ok(client.post(f"{SP}/rooms/from-template", headers=h, json={"id": "ops", "floor_id": "g", "template_id": "open_office", "x": 0, "z": 0, "with_desks": desks}), 201)
    ok(client.post(f"{SP}/rooms/from-template", headers=h, json={"id": "meet", "floor_id": "g", "template_id": "meeting_room", "x": 20, "z": 0}), 201)


def revisions(h: dict[str, str], floor: str = "g") -> dict:
    return ok(client.get(f"{LH}/floors/{floor}/revisions", headers=h))


def room(h: dict[str, str], rid: str) -> dict:
    return ok(client.get(f"{SP}/rooms/{rid}", headers=h))


def move(h: dict[str, str], rid: str, x: float, z: float) -> dict:
    r = room(h, rid)
    return ok(client.put(f"{SP}/floors/g/layout", headers=h, json={"rooms": [
        {"id": rid, "expected_revision": r["revision"], "x": x, "z": z, "width": r["scene_width"], "depth": r["scene_depth"]}]}))


def detail(h: dict[str, str], n: int, against: str = "current") -> dict:
    return ok(client.get(f"{LH}/floors/g/revisions/{n}?against={against}", headers=h))


def restore(h: dict[str, str], n: int, head: int, payload_hash: str, confirm: list[str] | None = None, mode: str = "click"):  # type: ignore[no-untyped-def]
    return client.post(f"{LH}/floors/g/revisions/{n}/restore", headers=h, json={
        "payload_hash": payload_hash, "expected_head": head, "confirm_unassign": confirm or [], "input_mode": mode})


def seed_agent(engine: Engine, agent_id: str, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> None:
    with engine.begin() as c:
        c.execute(text("""
            insert into agent_profiles (id, tenant_id, workspace_id, owner_user_id, name, icon, color, category, description,
              instructions, model_route, allowed_skills, allowed_tools, autonomy, knowledge_mode, knowledge_sources, status,
              sharing, version, created_at, updated_at)
            values (:i, :t, :w, 'owner_test', :n, 'bot', '#112233', 'work', '', '', 'fast', '[]', '[]', '{}', 'none', '[]',
                    'active', 'workspace', 1, :now, :now)"""),
                  {"i": agent_id, "t": tenant, "w": workspace, "n": f"Agent {agent_id}", "now": datetime.now(timezone.utc)})


def test_baseline_then_one_revision_per_change(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    listing = revisions(h)
    first = listing["items"][-1]
    assert first["action"] == "baseline" or first["revision_no"] == 1
    before = listing["head_revision"]
    move(h, "meet", 30, 0)
    after = revisions(h)
    assert after["head_revision"] == before + 1
    newest = after["items"][0]
    assert newest["action"] == "office.floor.layout" and newest["is_current"] and newest["summary"]["rooms_moved"] == 1
    assert newest["actor"] == "owner_test"


def test_failed_save_writes_no_revision(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    head = revisions(h)["head_revision"]
    r = room(h, "meet")
    bad = client.put(f"{SP}/floors/g/layout", headers=h, json={"rooms": [
        {"id": "meet", "expected_revision": r["revision"], "x": 1, "z": 1, "width": r["scene_width"], "depth": r["scene_depth"]}]})
    assert bad.status_code == 422
    assert revisions(h)["head_revision"] == head


def test_restore_is_exact_and_adds_a_new_revision(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    start = revisions(h)["head_revision"]
    original = {k: room(h, "meet")[k] for k in ("scene_x", "scene_z", "scene_width", "scene_depth", "rotation", "door_side")}
    move(h, "meet", 30, 10)
    ok(client.patch(f"{SP}/rooms/meet", headers=h, json={"name": "Board room", "expected_revision": room(h, "meet")["revision"]}))
    head = revisions(h)["head_revision"]
    assert head == start + 2
    view = detail(h, start)
    assert view["diff"]["summary"]["rooms_moved"] == 1 and not view["unassign"] and view["can_restore"]
    result = ok(restore(h, start, head, view["payload_hash"]))
    assert result["restored"] and result["revision_no"] == head + 1 and result["restored_from"] == start
    back = room(h, "meet")
    assert {k: back[k] for k in original} == original and back["name"] == "Meeting room"
    listing = revisions(h)
    assert listing["head_revision"] == head + 1
    assert [i["revision_no"] for i in listing["items"]][:3] == [head + 1, head, head - 1]
    assert listing["items"][0]["restored_from"] == start
    # old revisions are untouched: the pre restore state is still there to go back to
    assert detail(h, head)["revision"]["revision_no"] == head


def test_restore_recreates_deleted_room_and_desks_and_swaps_names(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    start = revisions(h)["head_revision"]
    desks_before = ok(client.get(f"{SP}/desks?parent_id=ops&limit=100", headers=h))["items"]
    assert len(desks_before) == 12
    for d in desks_before:
        assert client.delete(f"{SP}/desks/{d['id']}?expected_revision={d['revision']}", headers=h).status_code == 204
    ops = room(h, "ops")
    assert client.delete(f"{SP}/rooms/ops?expected_revision={ops['revision']}", headers=h).status_code == 204
    # swap the other room's name with a deleted room's name is exercised by renaming meet to the removed name
    ok(client.patch(f"{SP}/rooms/meet", headers=h, json={"name": "Open office", "expected_revision": room(h, "meet")["revision"]}))
    head = revisions(h)["head_revision"]
    view = detail(h, start)
    assert view["diff"]["summary"]["rooms_added"] == 1
    ok(restore(h, start, head, view["payload_hash"]))
    assert room(h, "ops")["name"] == "Open office" and room(h, "meet")["name"] == "Meeting room"
    assert len(ok(client.get(f"{SP}/desks?parent_id=ops&limit=100", headers=h))["items"]) == 12


def test_restore_is_click_only_and_checks_hash_and_head(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    start = revisions(h)["head_revision"]
    move(h, "meet", 30, 10)
    head = revisions(h)["head_revision"]
    view = detail(h, start)
    for mode in ("typed", "voice"):
        assert restore(h, start, head, view["payload_hash"], mode=mode).status_code == 403
    assert restore(h, start, head, "0" * 64).status_code == 409
    assert restore(h, start, head - 1, view["payload_hash"]).status_code == 409
    stale = restore(h, start, head - 1, view["payload_hash"]).json()
    assert "STALE_LAYOUT" in str(stale)
    assert restore(hd(role="member"), start, head, view["payload_hash"]).status_code == 403
    assert client.get(f"{LH}/floors/g/revisions", headers=hd(role="viewer")).status_code == 200
    assert detail(hd(role="viewer"), start)["can_restore"] is False
    assert room(h, "meet")["scene_x"] == 30  # nothing above changed the floor
    # a hash shown to another person does not work for this one
    other = hd()
    other["x-user-id"] = "someone_else"
    assert restore(other, start, head, view["payload_hash"]).status_code == 409
    assert restore(h, 9999, head, view["payload_hash"]).status_code == 404


def test_restore_is_idempotent(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    start = revisions(h)["head_revision"]
    move(h, "meet", 30, 10)
    head = revisions(h)["head_revision"]
    pay = detail(h, start)["payload_hash"]
    first = ok(restore(h, start, head, pay))
    again = ok(restore(h, start, head, pay))
    assert again["replayed"] is True and again["revision_no"] == first["revision_no"]
    assert revisions(h)["head_revision"] == first["revision_no"]
    # restoring what the floor already looks like changes nothing
    noop = ok(restore(h, first["revision_no"], first["revision_no"], detail(h, first["revision_no"])["payload_hash"]))
    assert noop["noop"] is True and revisions(h)["head_revision"] == first["revision_no"]


def test_restore_that_removes_an_occupied_desk_needs_the_exact_list(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    start = revisions(h)["head_revision"]
    ops_desks = ok(client.get(f"{SP}/desks?parent_id=ops&limit=100", headers=h))["items"]
    assert len(ops_desks) == 12
    # one more desk is not possible (capacity 12), so free one, add a new one, seat an agent there
    victim = ops_desks[0]
    assert client.delete(f"{SP}/desks/{victim['id']}?expected_revision={victim['revision']}", headers=h).status_code == 204
    ok(client.post(f"{SP}/desks", headers=h, json={"id": "late", "room_id": "ops", "label": "Late desk"}), 201)
    seed_agent(db, "agent_seated")
    late = ok(client.get(f"{SP}/desks/late", headers=h))
    ok(client.patch(f"{SP}/desks/late/assignment", headers=h, json={"agent_id": "agent_seated", "expected_revision": late["revision"]}))
    head = revisions(h)["head_revision"]
    view = detail(h, start)
    assert [u["desk_id"] for u in view["unassign"]] == ["late"] and view["unassign"][0]["label"] == "Late desk"
    refused = restore(h, start, head, view["payload_hash"])
    assert refused.status_code == 409 and "UNASSIGN_NOT_CONFIRMED" in refused.text
    assert restore(h, start, head, view["payload_hash"], ["wrong"]).status_code == 409
    assert ok(client.get(f"{SP}/desks/late", headers=h))["agent_id"] == "agent_seated"
    done = ok(restore(h, start, head, view["payload_hash"], ["late"]))
    assert done["unassigned"][0]["desk_id"] == "late"
    assert client.get(f"{SP}/desks/late", headers=h).status_code == 404
    with db.begin() as c:
        assert c.execute(text("select count(*) from agent_profiles where id = 'agent_seated'")).scalar_one() == 1
        audit = c.execute(text("select metadata from audit_records where action = 'office.floor.restore'")).scalars().all()
    assert audit and audit[0]["after"]["unassigned_desks"] == ["late"] and audit[0]["after"]["input_mode"] == "click"


def test_revisions_are_pruned_to_the_cap_and_never_updated(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "office_layout_revision_cap", 5)
    h = hd()
    seed_floor(h)
    for i in range(8):
        move(h, "meet", 20 + (i % 2) * 5 + 1, 0)
    listing = revisions(h)
    assert len(listing["items"]) == 5 and listing["cap"] == 5
    numbers = [i["revision_no"] for i in listing["items"]]
    assert numbers == sorted(numbers, reverse=True) and numbers[0] == listing["head_revision"]
    assert client.get(f"{LH}/floors/g/revisions/1", headers=h).status_code == 404
    with pytest.raises(DBAPIError):
        with db.begin() as c:
            c.execute(text("update office_floor_revisions set action = 'x'"))


def test_isolation_and_rls(db: Engine) -> None:
    a, b = hd(), hd(TENANT_B, WORKSPACE_B)
    seed_floor(a)
    seed_floor(b)
    move(a, "meet", 30, 0)
    assert client.get(f"{LH}/floors/g/revisions", headers=b).json()["head_revision"] == revisions(b)["head_revision"]
    assert revisions(b)["head_revision"] < revisions(a)["head_revision"]
    ok(client.post(f"{LH}/templates/from-floor", headers=a, json={"id": "tpl", "floor_id": "g", "name": "Standard"}), 201)
    assert ok(client.get(f"{LH}/templates", headers=b))["items"] == []
    assert client.get(f"{LH}/floors/nope/revisions", headers=a).status_code == 404
    with db.begin() as c:
        c.execute(text(f"set local role {APP_ROLE}"))
        assert c.execute(text("select count(*) from office_floor_revisions")).scalar_one() == 0
        assert c.execute(text("select count(*) from office_layout_templates")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            c.execute(text("insert into office_layout_templates (id, tenant_id, workspace_id, kind, name, definition, created_by) "
                           f"values ('x', '{TENANT_A}', '{WORKSPACE_A}', 'floor', 'x', '{{}}', 'u')"))


def test_floor_and_building_templates(db: Engine) -> None:
    h = hd()
    seed_floor(h)
    seed_agent(db, "agent_t")
    d = ok(client.get(f"{SP}/desks?parent_id=ops&limit=1", headers=h))["items"][0]
    ok(client.patch(f"{SP}/desks/{d['id']}/assignment", headers=h, json={"agent_id": "agent_t", "expected_revision": d["revision"]}))
    saved = ok(client.post(f"{LH}/templates/from-floor", headers=h, json={"id": "t1", "floor_id": "g", "name": "Standard floor"}), 201)
    assert saved["summary"] == {"floors": 1, "rooms": 2, "desks": 12}
    assert client.post(f"{LH}/templates/from-floor", headers=h, json={"id": "t2", "floor_id": "g", "name": "Standard floor"}).status_code == 409
    assert client.post(f"{LH}/templates/from-floor", headers=hd(role="member"), json={"id": "t3", "floor_id": "g", "name": "Nope"}).status_code == 403
    made = ok(client.post(f"{LH}/floors/from-template", headers=h, json={"template_id": "t1", "id": "f1", "building_id": "hq", "level": 1, "name": "First"}), 201)
    assert len(made["room_ids"]) == 2 and made["desk_count"] == 12
    new_desks = ok(client.get(f"{SP}/desks?parent_id={made['room_ids'][0]}&limit=100", headers=h))["items"]
    other = ok(client.get(f"{SP}/desks?parent_id={made['room_ids'][1]}&limit=100", headers=h))["items"]
    assert all(x["agent_id"] is None for x in new_desks + other)
    assert client.post(f"{LH}/floors/from-template", headers=h, json={"template_id": "t1", "id": "f2", "building_id": "hq", "level": 1}).status_code == 409
    assert revisions(h, "f1")["head_revision"] >= 1
    # a building template needs two floors to show it keeps both
    bt = ok(client.post(f"{LH}/templates/from-building", headers=h, json={"id": "b1", "building_id": "hq", "name": "Campus"}), 201)
    assert bt["summary"]["floors"] == 2
    nb = ok(client.post(f"{LH}/buildings/from-template", headers=h, json={"template_id": "b1", "id": "hq2", "name": "Branch", "timezone": "Asia/Dubai"}), 201)
    assert len(nb["floor_ids"]) == 2 and nb["room_count"] == 4
    assert client.post(f"{LH}/buildings/from-template", headers=h, json={"template_id": "t1", "id": "hq3", "name": "X", "timezone": "Asia/Dubai"}).status_code == 404
    renamed = ok(client.patch(f"{LH}/templates/t1", headers=h, json={"name": "Renamed", "expected_revision": 1}))
    assert renamed["revision"] == 2
    assert client.patch(f"{LH}/templates/t1", headers=h, json={"name": "Again", "expected_revision": 1}).status_code == 409
    assert client.delete(f"{LH}/templates/t1?expected_revision=2", headers=h).status_code == 204
    assert [t["id"] for t in ok(client.get(f"{LH}/templates?kind=floor", headers=h))["items"]] == []
