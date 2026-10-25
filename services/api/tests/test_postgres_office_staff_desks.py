"""Staff my campus on PostgreSQL as the non-owner app role with forced RLS."""
from __future__ import annotations

import concurrent.futures

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import (  # noqa: F401 (fixtures)
    EXEC,
    OWNER_A,
    _scoped,
    headers,
    hq,
    run_sql,
    seed_agent,
    seed_member,
)

from anum_api import office_staff_desks_api as api

pytestmark = pytest.mark.database
BASE = "/api/v1/office/staff-desks"
HQ = "campus-01-hq"
HQ_DESKS = 39


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def rows(engine: Engine, sql: str, **params):
    with engine.begin() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


@pytest.fixture
def campus(hq: TestClient) -> TestClient:
    view = hq.get("/api/v1/office/starter-campus/preview", headers=OWNER()).json()
    r = hq.post("/api/v1/office/starter-campus/confirm", headers=OWNER(), json={
        "payload_hash": view["payload_hash"], "input_mode": "click", "buildings": view["selected"], "timezone": view["timezone"]})
    assert r.status_code == 200 and r.json()["totals"]["desks"] == 220, r.text
    return hq


def preview(hq: TestClient, user=None, **body) -> dict:
    r = hq.post(f"{BASE}/preview", headers=user or OWNER(), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def batch(view: dict, building: str = HQ) -> dict:
    return next(b for b in view["batches"] if b["building_id"] == building)


def confirm(hq: TestClient, view: dict, building: str = HQ, *, mode: str = "click", user=None, exclude=(), **extra):
    b = batch(view, building)
    return hq.post(f"{BASE}/confirm", headers=user or OWNER(), json={
        "payload_hash": b["payload_hash"], "input_mode": mode, "building_id": building, "quota": b["quota"],
        "exclude_rooms": list(exclude), "lang": view["lang"], **extra})


def agents(engine: Engine) -> int:
    return rows(engine, "select count(*) from agent_profiles where tenant_id = :t", t=TENANT_A)[0][0]


def test_preview_of_the_default_campus_is_complete_and_stores_nothing(campus: TestClient, database_engine: Engine) -> None:
    view = preview(campus)
    t = view["totals"]
    assert t["total"] == 220 and t["plan"] == 200 and t["over_limit"] == 20 and t["no_role"] == 0 and t["occupied"] == 0
    assert view["limits"] == {"agents": 0, "max_agents": 200, "free_slots": 200, "run_cap": None}
    # 200 agents at USD 5 each: the biggest possible monthly cost if every one were turned on.
    assert view["cost"]["agents"] == 200 and view["cost"]["max_monthly_usd"] == sum(t["subtotal_usd"] for t in view["cost"]["per_template"]) > 0
    assert view["cost"]["workspace_budget"] == {"visible": True, "monthly_cost_limit_usd": None} and view["cost"]["within_budget"] is None
    assert {r["id"] for r in view["roles"]} >= {"orders_desk", "hr_people", "procurement", "security_reviewer"}
    assert view["capability_gaps"] and all(g["en"] and g["ar"] for g in view["capability_gaps"])
    assert [b["building_id"] for b in view["batches"]][:2] == [HQ, "campus-02-commerce"] and batch(view)["count"] == HQ_DESKS
    first = view["buildings"][0]["rooms"][0]["desks"][0]
    assert first["state"] == "plan" and first["agent"]["name"].endswith(" 1") and first["agent"]["monthly_cost_limit_usd"] > 0
    assert agents(database_engine) == 0 and rows(database_engine, "select count(*) from office_desk_staffing") == [(0,)]
    assert view["rules"]["created_paused"] is True


def test_only_a_click_by_an_owner_with_the_shown_hash_confirms(campus: TestClient, database_engine: Engine) -> None:
    view = preview(campus)
    for mode in ("typed", "voice"):
        assert confirm(campus, view, mode=mode).status_code == 403
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert preview(campus, user=viewer)["can_confirm"] is False
    assert confirm(campus, view, user=viewer).status_code == 403
    assert campus.post(f"{BASE}/confirm", headers=OWNER(), json={**{"payload_hash": "0" * 64, "input_mode": "click", "building_id": HQ,
                                                                    "quota": HQ_DESKS}}).status_code == 409
    other_owner = headers(TENANT_A, WORKSPACE_A, "owner_two")
    seed_member(database_engine, TENANT_A, WORKSPACE_A, "owner_two", "owner")
    assert confirm(campus, view, user=other_owner).status_code == 409, "the hash is bound to the person who saw it"
    assert campus.put(f"{BASE}/keep-empty", headers=viewer, json={"desk_ids": ["x"], "keep_empty": True}).status_code == 403
    assert agents(database_engine) == 0


def test_confirm_seats_paused_least_privilege_agents_with_caps_audit_and_events(campus: TestClient, database_engine: Engine) -> None:
    view = preview(campus)
    r = confirm(campus, view)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "created" and body["counts"]["created"] == HQ_DESKS and body["counts"]["failed"] == 0 and body["paused"] is True
    assert agents(database_engine) == HQ_DESKS
    assert rows(database_engine, "select count(*) from agent_profiles where status <> 'paused'") == [(0,)]
    assert rows(database_engine, "select count(*) from office_desks where agent_id is not null and id like 'campus-01-hq-%'") == [(HQ_DESKS,)]
    assert rows(database_engine, "select count(*) from office_desk_staffing where status = 'active' and agent_id is not null") == [(HQ_DESKS,)]
    assert rows(database_engine, "select count(*) from agent_cost_caps c join agent_profiles a on a.id = c.agent_id where c.monthly_cost_limit_usd > 0") == [(HQ_DESKS,)]
    assert rows(database_engine, "select count(distinct sharing), min(sharing) from agent_profiles") == [(1, "workspace")]
    names = [r[0] for r in rows(database_engine, "select name from agent_profiles order by name")]
    assert "Operations Dispatcher, Command Center 1" in names and len(set(names)) == HQ_DESKS
    ex = rows(database_engine, "select autonomy->>'external', allowed_tools::text from agent_profiles where name like 'Executive Assistant%' limit 1")[0]
    assert ex[0] == "off" and "send_email" not in ex[1] and "send_whatsapp" not in ex[1]
    audit = rows(database_engine, "select metadata from audit_records where action = 'office.staff_desks.room' and tenant_id = :t", t=TENANT_A)
    assert len(audit) == 9, "one record per room with desks in HQ"
    assert all(m[0]["after"]["input_mode"] == "click" and m[0]["after"]["paused"] and m[0]["after"]["desks"] for m in audit)
    assert rows(database_engine, "select count(*) from domain_events where type = 'office.agent_provisioned' and tenant_id = :t", t=TENANT_A) == [(HQ_DESKS,)]
    assert rows(database_engine, "select count(*) from audit_records where action = 'agent.created' and tenant_id = :t", t=TENANT_A) == [(HQ_DESKS,)]
    # The same click again, or a retry after a timeout, creates nothing.
    again = confirm(campus, view)
    assert again.status_code == 200 and again.json()["outcome"] == "already_staffed"
    assert agents(database_engine) == HQ_DESKS
    fresh = preview(campus)
    assert fresh["totals"]["occupied"] == HQ_DESKS and fresh["totals"]["plan"] == 161  # 200 limit minus the 39 now used
    assert fresh["limits"]["agents"] == HQ_DESKS


def test_it_never_touches_a_desk_that_already_has_an_agent(campus: TestClient, database_engine: Engine) -> None:
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "boss")
    run_sql(database_engine, "update office_desks set agent_id = 'boss' where id = 'campus-01-hq-executive-office-d1'")
    view = preview(campus)
    assert view["totals"]["occupied"] == 1
    assert confirm(campus, view).json()["counts"]["created"] == HQ_DESKS - 1
    assert rows(database_engine, "select agent_id from office_desks where id = 'campus-01-hq-executive-office-d1'") == [("boss",)]


def test_keep_empty_is_remembered_and_cleared(campus: TestClient, database_engine: Engine) -> None:
    desk = "campus-01-hq-executive-office-d2"
    r = campus.put(f"{BASE}/keep-empty", headers=OWNER(), json={"desk_ids": [desk], "keep_empty": True})
    assert r.status_code == 200 and r.json()["changed"] == [desk]
    assert campus.put(f"{BASE}/keep-empty", headers=OWNER(), json={"desk_ids": [desk], "keep_empty": True}).json()["unchanged"] == [desk]
    view = preview(campus)
    assert view["totals"]["keep_empty"] == 1 and view["totals"]["plan"] == 200 and view["totals"]["over_limit"] == 19
    assert confirm(campus, view).json()["counts"]["created"] == HQ_DESKS - 1
    assert rows(database_engine, "select agent_id from office_desks where id = :d", d=desk) == [(None,)]
    assert rows(database_engine, "select count(*) from audit_records where action = 'office.staff_desks.keep_empty'") == [(1,)]
    assert campus.put(f"{BASE}/keep-empty", headers=OWNER(), json={"desk_ids": [desk], "keep_empty": False}).json()["changed"] == [desk]
    assert preview(campus)["totals"]["keep_empty"] == 0
    seated = "campus-01-hq-executive-office-d1"
    assert campus.put(f"{BASE}/keep-empty", headers=OWNER(), json={"desk_ids": [seated], "keep_empty": True}).status_code == 409
    assert campus.put(f"{BASE}/keep-empty", headers=OWNER(), json={"desk_ids": ["nope"], "keep_empty": True}).status_code == 404


def test_rooms_and_buildings_can_be_left_out_and_the_run_can_be_capped(campus: TestClient, database_engine: Engine) -> None:
    room = "campus-01-hq-command-center"
    view = preview(campus, buildings=[HQ, "campus-02-commerce"], exclude_rooms=[room])
    assert view["totals"]["excluded"] > 0 and [b["building_id"] for b in view["batches"]] == [HQ, "campus-02-commerce"]
    assert batch(view)["count"] == HQ_DESKS - 6
    assert confirm(campus, view, exclude=[room]).json()["counts"]["created"] == HQ_DESKS - 6
    assert rows(database_engine, "select count(*) from office_desks where room_id = :r and agent_id is not null", r=room) == [(0,)]
    capped = preview(campus, buildings=["campus-03-tyre-pulse"], max_agents=5)
    assert capped["totals"]["plan"] == 5 and capped["totals"]["over_cap"] == 24 and batch(capped, "campus-03-tyre-pulse")["quota"] == 5
    assert confirm(campus, capped, "campus-03-tyre-pulse").json()["counts"]["created"] == 5
    assert agents(database_engine) == HQ_DESKS - 6 + 5


def test_the_200_agent_limit_is_counted_and_desks_beyond_it_stay_empty(campus: TestClient, database_engine: Engine) -> None:
    for i in range(195):
        seed_agent(database_engine, TENANT_A, WORKSPACE_A, f"filler_{i}")
    view = preview(campus)
    assert view["limits"]["free_slots"] == 5 and view["totals"]["plan"] == 5 and view["totals"]["over_limit"] == 215
    assert [(b["building_id"], b["new_agents"]) for b in view["batches"]] == [(HQ, 5)]
    body = confirm(campus, view).json()
    assert body["counts"]["created"] == 5 and agents(database_engine) == 200
    full = preview(campus)
    assert full["totals"]["plan"] == 0 and full["batches"] == [] and full["limits"]["free_slots"] == 0


def test_the_limit_reached_while_running_fails_the_rest_with_a_reason_and_leaves_no_claim(
        campus: TestClient, database_engine: Engine) -> None:
    view = preview(campus)
    for i in range(190):  # others add agents after the preview: the batch is now larger than the room left
        seed_agent(database_engine, TENANT_A, WORKSPACE_A, f"late_{i}")
    assert confirm(campus, view).status_code == 409, "the plan changed, the old hash is refused"
    again = preview(campus)
    body = confirm(campus, again).json()
    assert body["counts"]["created"] == 10 and agents(database_engine) == 200


def test_a_failure_in_one_desk_or_room_never_double_creates_and_a_retry_finishes(
        campus: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    real_create, real_seat = api._create_agent, api._seat_room
    calls = {"n": 0}

    def flaky_create(context, item, resolved, lang):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real_create(context, item, resolved, lang)

    def failing_seat(context, room_id, ready, digest, lang):
        if room_id.endswith("security-operations"):
            raise api._SeatRefused("conflict")
        return real_seat(context, room_id, ready, digest, lang)

    monkeypatch.setattr(api, "_create_agent", flaky_create)
    monkeypatch.setattr(api, "_seat_room", failing_seat)
    view = preview(campus)
    body = confirm(campus, view).json()
    reasons = {r["reason"] for r in body["results"] if r["status"] == "failed"}
    assert body["outcome"] == "partial" and reasons == {"agent_failed", "seat_conflict"}
    assert body["counts"]["failed"] == 1 + 5 and body["counts"]["created"] == HQ_DESKS - 6
    sec = [r for r in body["results"] if r["room_id"].endswith("security-operations")]
    assert len(sec) == 5 and all(r["status"] == "failed" and r["agent_id"] for r in sec), "built but not seated, and remembered"
    assert rows(database_engine, "select count(*) from office_desks where room_id like '%security-operations' and agent_id is not null") == [(0,)]
    built = agents(database_engine)
    monkeypatch.setattr(api, "_create_agent", real_create)
    monkeypatch.setattr(api, "_seat_room", real_seat)
    retry_view = preview(campus)
    assert retry_view["totals"]["resume"] == 5 and retry_view["totals"]["plan"] > 0
    done = confirm(campus, retry_view).json()
    assert done["counts"] == {"created": 1, "seated_existing": 5, "skipped": 0, "failed": 0}, done["results"][:8]
    assert done["outcome"] == "created"
    assert agents(database_engine) == built + 1, "the five built agents were seated, not built again"
    assert rows(database_engine, "select count(*) from office_desks where agent_id is not null and id like 'campus-01-hq-%'") == [(HQ_DESKS,)]
    assert rows(database_engine, "select count(*) from (select template_id from agent_profiles group by template_id having count(*) > 1) x") == [(0,)]


def test_two_confirms_at_once_never_create_two_agents_for_a_desk(campus: TestClient, database_engine: Engine) -> None:
    view = preview(campus, buildings=["campus-04-corporate-services"])
    b = "campus-04-corporate-services"
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: confirm(campus, view, b), range(2)))
    assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
    assert agents(database_engine) == 28
    assert rows(database_engine, "select count(*) from office_desks where agent_id is not null") == [(28,)]
    assert rows(database_engine, "select count(distinct agent_id) from office_desks where agent_id is not null") == [(28,)]


def test_turn_on_lists_paused_agents_shows_the_cap_total_and_needs_a_click(campus: TestClient, database_engine: Engine) -> None:
    confirm(campus, preview(campus, buildings=["campus-08-career"]), "campus-08-career")
    paused = campus.get(f"{BASE}/paused", headers=OWNER(), params={"limit": 2}).json()
    assert paused["total"] == 6 and len(paused["items"]) == 2 and len(paused["all_ids"]) == 6
    ids = paused["all_ids"][:3]
    pv = campus.post(f"{BASE}/turn-on/preview", headers=OWNER(), json={"agent_ids": ids}).json()
    assert pv["max_monthly_usd"] == 15.0 and len(pv["items"]) == 3 and pv["agents_without_cap"] == []
    body = {"agent_ids": ids, "payload_hash": pv["payload_hash"], "input_mode": "click"}
    for mode in ("typed", "voice"):
        assert campus.post(f"{BASE}/turn-on", headers=OWNER(), json={**body, "input_mode": mode}).status_code == 403
    assert campus.post(f"{BASE}/turn-on", headers=OWNER(), json={**body, "payload_hash": "1" * 64}).status_code == 409
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert campus.post(f"{BASE}/turn-on", headers=viewer, json=body).status_code == 403
    assert rows(database_engine, "select count(*) from agent_profiles where status = 'active'") == [(0,)]
    r = campus.post(f"{BASE}/turn-on", headers=OWNER(), json=body).json()
    assert r["outcome"] == "on" and r["counts"] == {"on": 3, "failed": 0}
    assert rows(database_engine, "select count(*) from agent_profiles where status = 'active'") == [(3,)]
    assert campus.get(f"{BASE}/paused", headers=OWNER()).json()["total"] == 3
    assert rows(database_engine, "select count(*) from audit_records where action = 'office.staff_desks.turn_on'") == [(1,)]
    assert campus.post(f"{BASE}/turn-on", headers=OWNER(), json=body).json()["outcome"] in ("nothing_to_turn_on",), "already on"


def test_a_workspace_budget_is_compared_with_the_cap_total(campus: TestClient, database_engine: Engine) -> None:
    campus.put("/api/v1/model-budgets/workspace", headers=OWNER(), json={"monthly_cost_limit_usd": 100})
    view = preview(campus)
    assert view["cost"]["workspace_budget"]["monthly_cost_limit_usd"] == 100 and view["cost"]["within_budget"] is False
    assert preview(campus, buildings=["campus-08-career"])["cost"]["within_budget"] is True


def test_another_workspace_sees_and_changes_nothing(campus: TestClient, database_engine: Engine) -> None:
    confirm(campus, preview(campus, buildings=["campus-08-career"]), "campus-08-career")
    seed_member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    other = headers(TENANT_B, WORKSPACE_B, "owner_b")
    view = preview(campus, user=other)
    assert view["totals"]["total"] == 0 and view["batches"] == [] and view["limits"]["agents"] == 0
    assert campus.post(f"{BASE}/confirm", headers=other, json={"payload_hash": "0" * 64, "input_mode": "click", "building_id": "campus-08-career", "quota": 6}).status_code == 404
    assert campus.get(f"{BASE}/paused", headers=other).json()["total"] == 0
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A, OWNER_A)
        assert connection.execute(text("select count(*) from office_desk_staffing")).scalar_one() == 6
        _scoped(connection, TENANT_B, WORKSPACE_B, "owner_b")
        assert connection.execute(text("select count(*) from office_desk_staffing")).scalar_one() == 0
        tx.rollback()


def test_the_ledger_table_has_forced_rls(database_engine: Engine) -> None:
    assert rows(database_engine, "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'office_desk_staffing'") == [(True, True)]
