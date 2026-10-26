"""Arabic names of buildings, floors and rooms on PostgreSQL as the non-owner app role (pytest -m database).

Covers the new columns (nullable, forced RLS untouched), create and edit with ``name_ar``, templates and layout
restore carrying it, the starter campus creating it, and the owner-triggered "Add Arabic names" action: click only,
hash bound, fills empty names of master records only, never overwrites, idempotent, audited with ids only,
isolated per workspace.
"""
from __future__ import annotations

import json

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_office_hq import EXEC, OWNER_A, _scoped, headers, hq, seed_member  # noqa: F401
from test_postgres_office_starter_campus import BASE, FULL, confirm, counts, preview, rows
from test_postgres_control_plane import postgres_backend  # noqa: F401

from anum_api import office_starter_campus as sc

pytestmark = pytest.mark.database
SP = "/api/v1/office-spaces"
LH = "/api/v1/office-layout"
AR = f"{BASE}/arabic-names"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


def ar_preview(hq: TestClient, user=None) -> dict:
    return ok(hq.get(f"{AR}/preview", headers=user or OWNER()))


def ar_confirm(hq: TestClient, view: dict, *, mode: str = "click", user=None, hash_: str | None = None):
    return hq.post(f"{AR}/confirm", headers=user or OWNER(),
                   json={"payload_hash": hash_ or view["payload_hash"], "input_mode": mode})


def strip_arabic(engine: Engine, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> None:
    """What a campus created before this change looks like: no Arabic name on any record."""
    with engine.begin() as connection:
        for table in ("office_buildings", "office_floors", "office_rooms"):
            connection.execute(text(f"update {table} set name_ar = null where tenant_id = :t and workspace_id = :w"),
                               {"t": tenant, "w": workspace})


def built_campus(hq: TestClient, engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    assert counts(engine) == FULL


# --------------------------------------------------------------------------- schema


def test_new_columns_are_nullable_and_row_level_security_is_still_forced(database_engine: Engine) -> None:
    cols = rows(database_engine, """select table_name, is_nullable, character_maximum_length from information_schema.columns
        where column_name = 'name_ar' and table_name in ('office_buildings', 'office_floors', 'office_rooms') order by table_name""")
    assert cols == [("office_buildings", "YES", 100), ("office_floors", "YES", 100), ("office_rooms", "YES", 100)]
    forced = rows(database_engine, """select relname, relrowsecurity, relforcerowsecurity from pg_class
        where relname in ('office_buildings', 'office_floors') order by relname""")
    assert forced == [("office_buildings", True, True), ("office_floors", True, True)]


# --------------------------------------------------------------------------- create and edit


def test_create_edit_and_clear_arabic_names(hq: TestClient, database_engine: Engine) -> None:
    h = OWNER()
    ok(hq.post(f"{SP}/buildings", headers=h, json={"id": "b1", "name": "HQ", "name_ar": "  المقر   الرئيسي ", "timezone": "Asia/Dubai"}), 201)
    ok(hq.post(f"{SP}/buildings", headers=h, json={"id": "b2", "name": "Plain", "timezone": "Asia/Dubai"}), 201)
    ok(hq.post(f"{SP}/floors", headers=h, json={"id": "f1", "building_id": "b1", "level": 0, "name": "Ground", "name_ar": "الطابق الأرضي"}), 201)
    ok(hq.post(f"{SP}/rooms", headers=h, json={"id": "r1", "floor_id": "f1", "name": "Lobby", "name_ar": "البهو", "kind": "reception", "capacity": 4}), 201)
    b1 = ok(hq.get(f"{SP}/buildings/b1", headers=h))
    assert b1["name_ar"] == "المقر الرئيسي", "whitespace is normalised"
    assert ok(hq.get(f"{SP}/buildings/b2", headers=h))["name_ar"] is None
    assert ok(hq.get(f"{SP}/floors/f1", headers=h))["name_ar"] == "الطابق الأرضي"
    assert ok(hq.get(f"{SP}/rooms/r1", headers=h))["name_ar"] == "البهو"
    # blank, control characters and too long are refused
    base = {"id": "bx", "name": "X", "timezone": "Asia/Dubai"}
    for bad in ("   ", "a\x00b", "ا" * 101):
        assert hq.post(f"{SP}/buildings", headers=h, json={**base, "name_ar": bad}).status_code == 422
    # edit and clear
    edited = ok(hq.patch(f"{SP}/buildings/b2", headers=h, json={"expected_revision": 1, "name_ar": "عادي"}))
    assert edited["name_ar"] == "عادي" and edited["revision"] == 2
    cleared = ok(hq.patch(f"{SP}/buildings/b2", headers=h, json={"expected_revision": 2, "name_ar": None}))
    assert cleared["name_ar"] is None
    floor = ok(hq.patch(f"{SP}/floors/f1", headers=h, json={"expected_revision": 1, "name_ar": "الأرضي"}))
    assert floor["name_ar"] == "الأرضي"
    assert hq.patch(f"{SP}/floors/f1", headers=h, json={"expected_revision": 2, "name_ar": ""}).status_code == 422
    # the audit record keeps the before and after value of the edit
    audit = rows(database_engine, "select metadata from audit_records where action = 'office.floor.update' and tenant_id = :t", t=TENANT_A)
    assert audit and audit[-1][0]["before"]["name_ar"] == "الطابق الأرضي" and audit[-1][0]["after"]["name_ar"] == "الأرضي"
    # a member cannot write
    member = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert hq.patch(f"{SP}/floors/f1", headers=member, json={"expected_revision": 3, "name_ar": "x"}).status_code == 403


def test_idempotent_create_without_an_arabic_name_keeps_its_old_hash(hq: TestClient) -> None:
    h = {**OWNER(), "Idempotency-Key": "key-arabic-0001"}
    body = {"id": "bk", "name": "Keyed", "timezone": "Asia/Dubai"}
    first = hq.post(f"{SP}/buildings", headers=h, json=body)
    again = hq.post(f"{SP}/buildings", headers=h, json=body)
    assert first.status_code == 201 and again.status_code == 200 and again.json()["id"] == "bk"


# --------------------------------------------------------------------------- templates and restore


def test_floor_and_building_templates_carry_arabic_names(hq: TestClient, database_engine: Engine) -> None:
    h = OWNER()
    ok(hq.post(f"{SP}/buildings", headers=h, json={"id": "hq1", "name": "HQ", "name_ar": "المقر", "timezone": "Asia/Dubai"}), 201)
    ok(hq.post(f"{SP}/floors", headers=h, json={"id": "g", "building_id": "hq1", "level": 0, "name": "Ground", "name_ar": "الطابق الأرضي"}), 201)
    ok(hq.post(f"{SP}/rooms/from-template", headers=h, json={"id": "ops", "floor_id": "g", "template_id": "open_office", "x": 0, "z": 0}), 201)
    ok(hq.patch(f"{SP}/rooms/ops", headers=h, json={"expected_revision": 1, "name_ar": "العمليات"}))
    ok(hq.post(f"{LH}/templates/from-floor", headers=h, json={"id": "tf", "floor_id": "g", "name": "Standard"}), 201)
    ok(hq.post(f"{LH}/templates/from-building", headers=h, json={"id": "tb", "building_id": "hq1", "name": "Campus"}), 201)
    stored = rows(database_engine, "select definition from office_layout_templates where id = 'tf'")[0][0]
    assert stored["name_ar"] == "الطابق الأرضي" and stored["rooms"][0]["name_ar"] == "العمليات"
    # same English name: the floor keeps its Arabic name and so do its rooms
    made = ok(hq.post(f"{LH}/floors/from-template", headers=h, json={"template_id": "tf", "id": "f2", "building_id": "hq1", "level": 1}), 201)
    assert ok(hq.get(f"{SP}/floors/f2", headers=h))["name_ar"] == "الطابق الأرضي"
    assert ok(hq.get(f"{SP}/rooms/{made['room_ids'][0]}", headers=h))["name_ar"] == "العمليات"
    # a new English name must bring its own Arabic name: the old one would be wrong
    ok(hq.post(f"{LH}/floors/from-template", headers=h, json={"template_id": "tf", "id": "f3", "building_id": "hq1", "level": 2, "name": "Third"}), 201)
    assert ok(hq.get(f"{SP}/floors/f3", headers=h))["name_ar"] is None
    ok(hq.post(f"{LH}/floors/from-template", headers=h, json={"template_id": "tf", "id": "f4", "building_id": "hq1", "level": 3,
                                                              "name": "Fourth", "name_ar": "الرابع"}), 201)
    assert ok(hq.get(f"{SP}/floors/f4", headers=h))["name_ar"] == "الرابع"
    # a building from a template: floors keep theirs, the building takes the one given
    nb = ok(hq.post(f"{LH}/buildings/from-template", headers=h, json={"template_id": "tb", "id": "hq2", "name": "Branch",
                                                                      "name_ar": "الفرع", "timezone": "Asia/Dubai"}), 201)
    assert nb["building"]["name_ar"] == "الفرع"
    names = {f["name_ar"] for f in ok(hq.get(f"{SP}/floors?parent_id=hq2", headers=h))["items"]}
    assert "الطابق الأرضي" in names


def test_restore_brings_the_room_arabic_name_back(hq: TestClient) -> None:
    h = OWNER()
    ok(hq.post(f"{SP}/buildings", headers=h, json={"id": "hq1", "name": "HQ", "timezone": "Asia/Dubai"}), 201)
    ok(hq.post(f"{SP}/floors", headers=h, json={"id": "g", "building_id": "hq1", "level": 0, "name": "Ground"}), 201)
    ok(hq.post(f"{SP}/rooms/from-template", headers=h, json={"id": "meet", "floor_id": "g", "template_id": "meeting_room", "x": 0, "z": 0}), 201)
    ok(hq.patch(f"{SP}/rooms/meet", headers=h, json={"expected_revision": 1, "name_ar": "قاعة الاجتماعات"}))
    revs = ok(hq.get(f"{LH}/floors/g/revisions", headers=h))
    good = revs["head_revision"]
    meet = ok(hq.get(f"{SP}/rooms/meet", headers=h))
    ok(hq.patch(f"{SP}/rooms/meet", headers=h, json={"expected_revision": meet["revision"], "name_ar": "اسم آخر"}))
    head = ok(hq.get(f"{LH}/floors/g/revisions", headers=h))["head_revision"]
    assert head == good + 1, "an Arabic name change is a layout revision"
    view = ok(hq.get(f"{LH}/floors/g/revisions/{good}", headers=h))
    assert view["diff"]["summary"]["rooms_changed"] == 1
    done = ok(hq.post(f"{LH}/floors/g/revisions/{good}/restore", headers=h, json={
        "payload_hash": view["payload_hash"], "expected_head": head, "confirm_unassign": [], "input_mode": "click"}))
    assert done["restored"] is True
    assert ok(hq.get(f"{SP}/rooms/meet", headers=h))["name_ar"] == "قاعة الاجتماعات"


# --------------------------------------------------------------------------- starter campus


def test_a_new_starter_campus_has_arabic_names_everywhere(hq: TestClient, database_engine: Engine) -> None:
    built_campus(hq, database_engine)
    for table in ("office_buildings", "office_floors", "office_rooms"):
        assert rows(database_engine, f"select count(*) from {table} where name_ar is null or name_ar = ''") == [(0,)]
    named = dict(rows(database_engine, "select name, name_ar from office_buildings"))
    assert named["ANUM HQ"] == "المقر الرئيسي لأنوم" and named["ANUM 360 Commerce"] == "أنوم 360 للتجارة" and named["Tyre Pulse"] == "تاير بلس"
    floors = dict(rows(database_engine, "select name, name_ar from office_floors"))
    assert floors["Floor 1: Reception and Meeting Rooms"] == "الطابق الأول: الاستقبال وغرف الاجتماعات"
    # nothing left for the backfill to do
    assert ar_preview(hq)["total"] == 0


# --------------------------------------------------------------------------- the "Add Arabic names" action


def test_preview_lists_master_records_without_arabic_and_stores_nothing(hq: TestClient, database_engine: Engine) -> None:
    built_campus(hq, database_engine)
    strip_arabic(database_engine)
    view = ar_preview(hq)
    assert view["counts"] == {"buildings": 8, "floors": 18, "rooms": 62} and view["total"] == 88
    assert view["can_confirm"] is True and view["rules"]["overwrites"] is False
    first = view["items"][0]
    assert first["kind"] == "buildings" and first["name_ar"]
    assert rows(database_engine, "select count(*) from office_buildings where name_ar is not null") == [(0,)]


def test_only_a_click_by_an_owner_with_the_shown_hash_fills(hq: TestClient, database_engine: Engine) -> None:
    built_campus(hq, database_engine)
    strip_arabic(database_engine)
    view = ar_preview(hq)
    for mode in ("typed", "voice"):
        assert ar_confirm(hq, view, mode=mode).status_code == 403
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert ar_preview(hq, viewer)["can_confirm"] is False
    assert ar_confirm(hq, view, user=viewer).status_code == 403
    assert ar_confirm(hq, view, hash_="0" * 64).status_code == 409
    assert hq.post(f"{AR}/confirm", headers=OWNER(), json={"payload_hash": view["payload_hash"]}).status_code == 403
    assert rows(database_engine, "select count(*) from office_rooms where name_ar is not null") == [(0,)]


def test_fill_adds_names_only_where_empty_and_matching_and_is_idempotent(hq: TestClient, database_engine: Engine) -> None:
    built_campus(hq, database_engine)
    strip_arabic(database_engine)
    # The owner typed one Arabic name and renamed one building: neither may be touched.
    with database_engine.begin() as connection:
        connection.execute(text("update office_buildings set name_ar = 'اسم مخصص' where id = 'campus-01-hq'"))
        connection.execute(text("update office_buildings set name = 'My own tower' where id = 'campus-02-commerce'"))
        connection.execute(text("update office_rooms set name = 'Reception desk' where id = 'campus-01-hq-reception'"))
    view = ar_preview(hq)
    assert view["counts"] == {"buildings": 6, "floors": 18, "rooms": 61}
    done = ok(ar_confirm(hq, view))
    assert done["outcome"] == "filled" and done["filled"] == 85 and done["skipped"] == 0
    names = dict(rows(database_engine, "select id, name_ar from office_buildings"))
    assert names["campus-01-hq"] == "اسم مخصص", "an Arabic name the owner typed is never replaced"
    assert names["campus-02-commerce"] is None, "a renamed building no longer matches the master list"
    assert names["campus-03-tyre-pulse"] == "تاير بلس"
    assert rows(database_engine, "select name_ar from office_rooms where id = 'campus-01-hq-reception'") == [(None,)]
    assert rows(database_engine, "select count(*) from office_floors where name_ar is null") == [(0,)]
    # nothing else about any record changed: positions, sizes and names are as they were
    assert counts(database_engine) == FULL
    assert rows(database_engine, "select name from office_buildings where id = 'campus-02-commerce'") == [("My own tower",)]
    # the second click and a stale click both write nothing
    again = ok(ar_confirm(hq, view))
    assert again["outcome"] == "nothing_to_add" and again["filled"] == 0
    assert ar_preview(hq)["total"] == 0
    # audit: one record, ids and counts only, never the text
    audit = rows(database_engine, "select metadata from audit_records where action = 'office.starter_campus.arabic_names' and tenant_id = :t", t=TENANT_A)
    assert len(audit) == 1
    text_dump = json.dumps(audit[0][0], ensure_ascii=False)
    assert "تاير" not in text_dump and "Tyre Pulse" not in text_dump
    assert audit[0][0]["after"]["filled"] == {"buildings": 6, "floors": 18, "rooms": 61} and audit[0][0]["after"]["input_mode"] == "click"
    events = rows(database_engine, "select payload from domain_events where type = 'office.layout_changed' and tenant_id = :t", t=TENANT_A)
    assert all("name_ar" not in json.dumps(e[0], ensure_ascii=False) and "تاير" not in json.dumps(e[0], ensure_ascii=False) for e in events)


def test_a_change_between_preview_and_click_is_a_conflict(hq: TestClient, database_engine: Engine) -> None:
    built_campus(hq, database_engine)
    strip_arabic(database_engine)
    view = ar_preview(hq)
    with database_engine.begin() as connection:
        connection.execute(text("update office_floors set name_ar = 'مكتوب بيد المالك' where id = 'campus-01-hq-l1'"))
    assert ar_confirm(hq, view).status_code == 409
    fresh = ar_preview(hq)
    assert ar_confirm(hq, fresh).status_code == 200
    assert rows(database_engine, "select name_ar from office_floors where id = 'campus-01-hq-l1'") == [("مكتوب بيد المالك",)]


def test_filling_a_room_name_is_a_layout_revision_and_restorable(hq: TestClient, database_engine: Engine) -> None:
    ok(hq.post(f"{SP}/buildings", headers=OWNER(), json={"id": "b", "name": "ANUM HQ", "timezone": "Asia/Dubai"}), 201)
    ok(hq.post(f"{SP}/floors", headers=OWNER(), json={"id": "g", "building_id": "b", "level": 0, "name": "Ground floor"}), 201)
    ok(hq.post(f"{SP}/rooms", headers=OWNER(), json={"id": "rc", "floor_id": "g", "name": "Reception", "kind": "reception", "capacity": 4}), 201)
    before = ok(hq.get(f"{LH}/floors/g/revisions", headers=OWNER()))["head_revision"]
    view = ar_preview(hq)
    assert view["counts"] == {"buildings": 1, "floors": 1, "rooms": 1}
    ok(ar_confirm(hq, view))
    assert ok(hq.get(f"{SP}/rooms/rc", headers=OWNER()))["name_ar"] == "الاستقبال"
    assert ok(hq.get(f"{LH}/floors/g/revisions", headers=OWNER()))["head_revision"] == before + 1


def test_each_workspace_is_filled_on_its_own(hq: TestClient, database_engine: Engine) -> None:
    seed_member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    other = headers(TENANT_B, WORKSPACE_B, "owner_b")
    built_campus(hq, database_engine)
    assert confirm(hq, preview(hq, user=other), user=other).status_code == 200
    strip_arabic(database_engine, TENANT_A, WORKSPACE_A)
    strip_arabic(database_engine, TENANT_B, WORKSPACE_B)
    ok(ar_confirm(hq, ar_preview(hq)))
    assert rows(database_engine, "select count(*) from office_buildings where name_ar is not null and tenant_id = :t", t=TENANT_A) == [(8,)]
    assert rows(database_engine, "select count(*) from office_buildings where name_ar is not null and tenant_id = :t", t=TENANT_B) == [(0,)]
    assert ar_preview(hq, other)["total"] == 88
    # as the app role under row level security a workspace never sees the other's rows
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_B, WORKSPACE_B, "owner_b")
        assert connection.execute(text("select count(*) from office_buildings where name_ar is not null")).scalar_one() == 0
        tx.rollback()


# --------------------------------------------------------------------------- the planner itself (no database)


def test_master_arabic_names_are_professional_unique_per_building_and_fit_the_column() -> None:
    for b in sc.CAMPUS:
        assert b.ar and b.ar != b.en and len(b.ar) <= 100
        assert len({f.ar for f in b.floors}) >= 1
        for f in b.floors:
            assert f.ar and len(f.ar) <= 100 and "Floor" not in f.ar
            names = [r.ar for r in f.rooms]
            assert len(set(names)) == len(names), "two rooms on one floor may not share an Arabic name"
            assert all(n and len(n) <= 100 for n in names)
    assert sc.CAMPUS[0].ar == "المقر الرئيسي لأنوم"


def test_arabic_lookup_has_no_ambiguous_name() -> None:
    every_building = {sc._norm_name(b.en) for b in sc.CAMPUS}
    assert set(sc.ARABIC_BY_NAME["buildings"]) == every_building
    floors = {sc._norm_name(f.en) for b in sc.CAMPUS for f in b.floors}
    assert set(sc.ARABIC_BY_NAME["floors"]) == floors, "two floors with one English name must share one Arabic name"
    rooms = {sc._norm_name(r.en) for b in sc.CAMPUS for f in b.floors for r in f.rooms}
    assert set(sc.ARABIC_BY_NAME["rooms"]) == rooms


def test_plan_arabic_names_is_pure_and_never_lists_a_filled_or_unknown_name() -> None:
    rows_in = {
        "buildings": [{"id": "a", "name": "ANUM HQ", "name_ar": None}, {"id": "b", "name": "Tyre Pulse", "name_ar": "تاير"},
                      {"id": "c", "name": "Mine", "name_ar": None}, {"id": "d", "name": "  anum   hq ", "name_ar": "  "}],
        "floors": [], "rooms": [{"id": "r", "name": "Reception", "name_ar": ""}],
    }
    plan = sc.plan_arabic_names(rows_in)
    assert [(i["kind"], i["id"]) for i in plan["items"]] == [("buildings", "a"), ("buildings", "d"), ("rooms", "r")]
    assert plan == sc.plan_arabic_names(rows_in)
    one = sc.arabic_proposal_hash("t", "w", "u", plan)
    assert one != sc.arabic_proposal_hash("t", "w", "other", plan) and one != sc.arabic_proposal_hash("t2", "w", "u", plan)
