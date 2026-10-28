"""Reception visitor register on PostgreSQL as the non-owner app role (pytest -m database).

Covers the migration (forced RLS, purge policy, the widened campus kind check), every route (list with paging,
search, day window and status filter, summary, create, walk-in, update, check in, check out, cancel, delete), the
permission and click rules, revision checks, host and place validation, audit and events with ids only (never the
visitor's name, company or purpose), Arabic refusals and tenant isolation, including a direct SQL check that
another tenant's RLS context sees and changes no row.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_office_hq import EXEC, OWNER_A, _scoped, headers, hq, seed_agent, seed_member, seed_office  # noqa: F401
from test_postgres_office_starter_campus import rows
from test_postgres_control_plane import postgres_backend  # noqa: F401

pytestmark = pytest.mark.database
V = "/api/v1/office/visitors"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
NAME = "Visitor Example"
COMPANY = "Example Trading"
PURPOSE = "Supplier meeting example"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def VIEWER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")


def OWNER_B() -> dict[str, str]:
    return headers(TENANT_B, WORKSPACE_B, EXEC, "owner")


def ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json() if response.content else None


def visit(hq: TestClient, **over) -> dict:  # type: ignore[no-untyped-def]
    body = {"visitor_name": NAME, "company": COMPANY, "purpose": PURPOSE, "expected_at": NOW.isoformat(),
            "input_mode": "click", **over}
    return ok(hq.post(V, headers=OWNER(), json=body), 201)


def move(hq: TestClient, v: dict, action: str, *, mode: str = "click", user=None):  # type: ignore[no-untyped-def]
    return hq.post(f"{V}/{v['id']}/{action}", headers=user or OWNER(), json={"expected_revision": v["revision"], "input_mode": mode})


# --------------------------------------------------------------------------- schema


def test_table_forces_rls_with_tenant_and_purge_policies_and_career_is_a_kind(database_engine: Engine) -> None:
    assert rows(database_engine, "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'office_visitors'") == [(True, True)]
    names = {r[0] for r in rows(database_engine, "select policyname from pg_policies where tablename = 'office_visitors'")}
    assert names == {"tenant_isolation_office_visitors", "lifecycle_purge_office_visitors"}
    check = rows(database_engine, "select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_campus_profile_kind'")[0][0]
    assert "career" in check and "personal" in check


def test_career_campus_profile_is_accepted_and_unknown_kinds_still_refused(hq: TestClient) -> None:
    saved = ok(hq.put("/api/v1/office/campus/profile", headers=OWNER(), json={"kind": "career", "icon": "briefcase"}))
    assert saved["kind"] == "career"
    assert hq.put("/api/v1/office/campus/profile", headers=OWNER(), json={"kind": "casino", "icon": "briefcase",
                                                                           "expected_revision": 1}).status_code == 422


# --------------------------------------------------------------------------- lifecycle of a visit


def test_create_check_in_out_and_list_states(hq: TestClient, database_engine: Engine) -> None:
    v = visit(hq)
    assert v["status"] == "expected" and v["revision"] == 1 and v["checked_in_at"] is None
    assert v["visitor_name"] == NAME and v["company"] == COMPANY and v["host"] is None
    inside = ok(move(hq, v, "check-in"))
    assert inside["status"] == "checked_in" and inside["checked_in_at"] and inside["revision"] == 2
    assert move(hq, v, "check-in").status_code == 409  # stale revision
    assert move(hq, inside, "cancel").status_code == 409  # only an expected visit can be cancelled
    summary = ok(hq.get(f"{V}/summary", headers=VIEWER()))
    assert summary["checked_in"] == 1 and summary["unplaced"] == 1 and NAME not in json.dumps(summary)
    out = ok(move(hq, inside, "check-out"))
    assert out["status"] == "checked_out" and out["checked_out_at"] >= out["checked_in_at"]
    assert ok(hq.get(f"{V}/summary", headers=VIEWER()))["checked_in"] == 0
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={"purpose": "Later", "expected_revision": out["revision"],
                                                             "input_mode": "click"}).status_code == 409  # finished


def test_walk_in_cancel_and_delete(hq: TestClient, database_engine: Engine) -> None:
    walk = visit(hq, arrived=True)
    assert walk["status"] == "checked_in" and walk["checked_in_at"]
    later = visit(hq, expected_at=(NOW + timedelta(days=1)).isoformat())
    gone = ok(move(hq, later, "cancel"))
    assert gone["status"] == "cancelled" and gone["cancelled_at"]
    assert move(hq, gone, "check-in").status_code == 409
    assert hq.delete(f"{V}/{gone['id']}", headers=OWNER(), params={"expected_revision": 1, "input_mode": "click"}).status_code == 409
    assert hq.delete(f"{V}/{gone['id']}", headers=OWNER(), params={"expected_revision": gone["revision"], "input_mode": "click"}).status_code == 204
    assert hq.delete(f"{V}/{gone['id']}", headers=OWNER(), params={"expected_revision": gone["revision"], "input_mode": "click"}).status_code == 404
    assert rows(database_engine, "select count(*) from office_visitors where tenant_id = :t", t=TENANT_A) == [(1,)]


def test_create_is_idempotent_by_id(hq: TestClient) -> None:
    first = visit(hq, id="visit-example-1")
    again = hq.post(V, headers=OWNER(), json={"id": "visit-example-1", "visitor_name": NAME, "company": COMPANY, "purpose": PURPOSE,
                                              "expected_at": NOW.isoformat(), "input_mode": "click"})
    assert again.status_code == 200 and again.json()["revision"] == first["revision"]
    other = hq.post(V, headers=OWNER(), json={"id": "visit-example-1", "visitor_name": "Someone Else", "purpose": PURPOSE,
                                              "expected_at": NOW.isoformat(), "input_mode": "click"})
    assert other.status_code == 409


def test_update_details_place_and_host(hq: TestClient, database_engine: Engine) -> None:
    seed_office(database_engine, TENANT_A, WORKSPACE_A)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "ag_host")
    v = visit(hq, room_id="r1", host_kind="agent", host_id="ag_host")
    assert v["building_id"] == "hq" and v["room_id"] == "r1" and v["host"] == {"kind": "agent", "id": "ag_host"}
    changed = ok(hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={"company": None, "host_kind": "user", "host_id": OWNER_A,
                                                                    "expected_revision": 1, "input_mode": "click"}))
    assert changed["company"] is None and changed["host"] == {"kind": "user", "id": OWNER_A} and changed["revision"] == 2
    same = ok(hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={"host_kind": "user", "host_id": OWNER_A, "expected_revision": 2,
                                                                 "input_mode": "click"}))
    assert same["revision"] == 2  # nothing changed, nothing written
    bad = {"expected_revision": 2, "input_mode": "click"}
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={**bad, "room_id": "nowhere"}).status_code == 422
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={**bad, "building_id": "nowhere"}).status_code == 422
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={**bad, "host_kind": "user", "host_id": "stranger"}).status_code == 422
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={**bad, "visitor_name": None}).status_code == 422
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={**bad, "host_kind": "agent"}).status_code == 422
    inside = ok(move(hq, changed, "check-in"))
    summary = ok(hq.get(f"{V}/summary", headers=VIEWER()))
    assert summary["by_room"] == [{"room_id": "r1", "count": 1}] and inside["room_id"] == "r1"
    # Deleting the room keeps the visit and clears only its room.
    with database_engine.begin() as connection:
        connection.execute(text("delete from office_desks where tenant_id = :t"), {"t": TENANT_A})
        connection.execute(text("delete from office_rooms where tenant_id = :t and id = 'r1'"), {"t": TENANT_A})
    assert rows(database_engine, "select building_id, room_id, status from office_visitors where tenant_id = :t", t=TENANT_A) == [("hq", None, "checked_in")]
    assert ok(hq.get(f"{V}/summary", headers=VIEWER()))["by_building"] == [{"building_id": "hq", "count": 1}]


def test_validation_refusals(hq: TestClient) -> None:
    body = {"visitor_name": NAME, "purpose": PURPOSE, "expected_at": NOW.isoformat(), "input_mode": "click"}
    assert hq.post(V, headers=OWNER(), json={**body, "visitor_name": "   "}).status_code == 422
    assert hq.post(V, headers=OWNER(), json={**body, "visitor_name": "bad\nname"}).status_code == 422
    assert hq.post(V, headers=OWNER(), json={**body, "expected_at": "2026-10-27T10:00:00"}).status_code == 422
    assert hq.post(V, headers=OWNER(), json={**body, "expected_at": (NOW + timedelta(days=500)).isoformat()}).status_code == 422
    assert hq.post(V, headers=OWNER(), json={**body, "host_kind": "user"}).status_code == 422
    assert hq.post(V, headers=OWNER(), json={**body, "extra": 1}).status_code == 422
    spaced = ok(hq.post(V, headers=OWNER(), json={**body, "visitor_name": "  Visitor   Example  ", "company": "  "}), 201)
    assert spaced["visitor_name"] == NAME and spaced["company"] is None


# --------------------------------------------------------------------------- reading


def test_list_pages_searches_filters_and_windows(hq: TestClient) -> None:
    for i in range(7):
        visit(hq, visitor_name=f"Guest {i}", company="Alpha" if i % 2 else "Beta", expected_at=(NOW + timedelta(minutes=i)).isoformat())
    visit(hq, visitor_name="Old Guest", expected_at=(NOW - timedelta(days=3)).isoformat())
    stayed = visit(hq, visitor_name="Long Stay", expected_at=(NOW - timedelta(days=2)).isoformat())
    ok(move(hq, stayed, "check-in"))
    first = ok(hq.get(V, headers=VIEWER(), params={"limit": 4}))
    assert len(first["items"]) == 4 and first["next_cursor"] and first["total"] == 9 and first["can_manage"] is False
    second = ok(hq.get(V, headers=VIEWER(), params={"limit": 4, "cursor": first["next_cursor"]}))
    third = ok(hq.get(V, headers=VIEWER(), params={"limit": 4, "cursor": second["next_cursor"]}))
    ids = [v["id"] for page in (first, second, third) for v in page["items"]]
    assert len(ids) == len(set(ids)) == 9 and third["next_cursor"] is None
    assert ok(hq.get(V, headers=OWNER()))["can_manage"] is True
    found = ok(hq.get(V, headers=VIEWER(), params={"q": "alpha"}))
    assert found["total"] == 3 and all(v["company"] == "Alpha" for v in found["items"])
    assert ok(hq.get(V, headers=VIEWER(), params={"q": "100%_"}))["total"] == 0
    day = {"since": (NOW - timedelta(hours=1)).isoformat(), "until": (NOW + timedelta(hours=1)).isoformat()}
    today = ok(hq.get(V, headers=VIEWER(), params=day))
    names = {v["visitor_name"] for v in today["items"]}
    assert "Long Stay" in names and "Old Guest" not in names and today["total"] == 8
    assert today["counts"] == {"expected": 7, "checked_in": 1, "checked_out": 0, "cancelled": 0}
    only_in = ok(hq.get(V, headers=VIEWER(), params={**day, "status": "checked_in"}))
    assert [v["visitor_name"] for v in only_in["items"]] == ["Long Stay"] and only_in["total"] == 1
    assert hq.get(V, headers=VIEWER(), params={"since": day["since"]}).status_code == 422
    assert hq.get(V, headers=VIEWER(), params={"cursor": "not-a-cursor"}).status_code == 422
    assert hq.get(V, headers=VIEWER(), params={"status": "lost"}).status_code == 422


# --------------------------------------------------------------------------- governance


def test_writes_need_an_owner_and_a_click(hq: TestClient) -> None:
    body = {"visitor_name": NAME, "purpose": PURPOSE, "expected_at": NOW.isoformat()}
    assert hq.post(V, headers=VIEWER(), json={**body, "input_mode": "click"}).status_code == 403
    for mode in ("typed", "voice"):
        refused = hq.post(V, headers=OWNER(), json={**body, "input_mode": mode})
        assert refused.status_code == 403 and "click" in refused.text
    assert hq.post(V, headers=OWNER(), json=body).status_code == 403  # no input mode = typed
    v = visit(hq)
    assert move(hq, v, "check-in", user=VIEWER()).status_code == 403
    assert move(hq, v, "check-in", mode="voice").status_code == 403
    assert hq.delete(f"{V}/{v['id']}", headers=VIEWER(), params={"expected_revision": 1, "input_mode": "click"}).status_code == 403
    assert hq.delete(f"{V}/{v['id']}", headers=OWNER(), params={"expected_revision": 1}).status_code == 403
    arabic = hq.post(f"{V}/{v['id']}/check-out", headers={**OWNER(), "accept-language": "ar"}, json={"expected_revision": 1, "input_mode": "click"})
    assert arabic.status_code == 409 and arabic.json()["error"]["message"] == "لا يُسجَّل خروج إلا زائر سُجّل دخوله."


def test_audit_and_events_carry_ids_never_names(hq: TestClient, database_engine: Engine) -> None:
    v = visit(hq)
    inside = ok(move(hq, v, "check-in"))
    ok(hq.patch(f"{V}/{v['id']}", headers=OWNER(), json={"purpose": "Changed purpose example", "expected_revision": inside["revision"],
                                                          "input_mode": "click"}))
    audit = rows(database_engine, "select action, target, metadata from audit_records where action like 'office.visitor.%' "
                                  "and tenant_id = :t order by created_at", t=TENANT_A)
    assert [a[0] for a in audit] == ["office.visitor.create", "office.visitor.check_in", "office.visitor.update"]
    meta = [a[2] if isinstance(a[2], dict) else json.loads(a[2]) for a in audit]
    assert meta[0]["status"] == "expected" and meta[0]["has_company"] is True and meta[0]["input_mode"] == "click"
    assert meta[1]["previous_status"] == "expected" and meta[2]["changed"] == ["purpose"]
    events = rows(database_engine, "select subject, payload from domain_events where type = 'office.visitors_changed' and tenant_id = :t", t=TENANT_A)
    assert len(events) == 3 and {e[0] for e in events} == {v["id"]}
    blob = json.dumps([audit, events], default=str)
    for secret in (NAME, COMPANY, PURPOSE, "Changed purpose example"):
        assert secret not in blob


def test_another_tenant_sees_and_changes_nothing(hq: TestClient, database_engine: Engine) -> None:
    v = visit(hq, arrived=True)
    assert ok(hq.get(V, headers=OWNER_B()))["items"] == []
    assert ok(hq.get(f"{V}/summary", headers=OWNER_B()))["checked_in"] == 0
    assert move(hq, v, "check-out", user=OWNER_B()).status_code == 404
    assert hq.delete(f"{V}/{v['id']}", headers=OWNER_B(), params={"expected_revision": 1, "input_mode": "click"}).status_code == 404
    assert hq.patch(f"{V}/{v['id']}", headers=OWNER_B(), json={"purpose": "x", "expected_revision": 1, "input_mode": "click"}).status_code == 404
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_B, WORKSPACE_B)
        assert connection.execute(text("select count(*) from office_visitors")).scalar_one() == 0
        assert connection.execute(text("update office_visitors set purpose = 'taken'")).rowcount == 0
        assert connection.execute(text("delete from office_visitors")).rowcount == 0
        with pytest.raises(Exception):
            connection.execute(text(
                "insert into office_visitors (tenant_id, workspace_id, id, visitor_name, purpose, expected_at, created_by, updated_by) "
                "values (:t, :w, 'smuggled', 'x', 'y', now(), 'u', 'u')"), {"t": TENANT_A, "w": WORKSPACE_A})
        tx.rollback()
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A)
        assert connection.execute(text("select count(*) from office_visitors")).scalar_one() == 1
        assert connection.execute(text("select purpose from office_visitors")).scalar_one() == PURPOSE
        tx.rollback()
