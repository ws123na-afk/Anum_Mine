"""What ANUM learned, in PostgreSQL (migration 0031_learning, docs/learning.md).

The API runs as the non-owner ``anum_test_app`` role. Every learning row is private to
one user in one workspace: the forced RLS policies check ``anum.tenant_id``,
``anum.workspace_id`` and ``anum.user_id``. "Restart" clears the in-memory stores, so
everything is read back from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import automation as automation_module
from anum_api.db import session as db_session
from anum_api.learning_store import learned_instructions, learning_data
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings
from anum_api.user_preferences import user_preference_store

pytestmark = pytest.mark.database

TABLES = (
    "learning_profiles",
    "learning_signals",
    "learning_preferences",
    "learning_routines",
    "learning_notes",
    "learning_suggestions",
)


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
COLLEAGUE = headers("user_other")
OTHER_WORKSPACE = headers(workspace_id=WORKSPACE_A2)
OTHER_TENANT = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)


def ctx(hdrs: dict[str, str]) -> TenantContext:
    return TenantContext(
        tenant_id=hdrs["x-tenant-id"], workspace_id=hdrs["x-workspace-id"], user_id=hdrs["x-user-id"], roles=[hdrs["x-user-roles"]]
    )


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(automation_module, "_postgres_engine", None)
    learning_data.clear()
    user_preference_store.clear()
    yield factory
    learning_data.clear()
    user_preference_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _learn(client: TestClient, hdrs: dict[str, str], label: str) -> None:
    sent = client.post(
        "/api/v1/learning/feedback",
        headers=hdrs,
        json={"surface": "email", "contact": f"{label}@example.com", "original": f"Hello {label}, a long draft", "final": f"Dear {label},\nShort.\nBest regards"},
    )
    assert sent.status_code == 200 and sent.json()["recorded"] is True, sent.text
    assert client.post("/api/v1/learning/preferences", headers=hdrs, json={"text": f"{label} prefers Arabic"}).status_code == 201


def _visible(app_session: Callable[..., Iterator[Session]], hdrs: dict[str, str] | None, table: str, as_user: str | None) -> int:
    context = tenant_context(hdrs["x-tenant-id"], hdrs["x-workspace-id"]) if hdrs else tenant_context()
    with app_session(context) as session:
        if as_user is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": as_user})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608 - fixed table names


def test_learning_persists_and_is_read_back_after_a_restart(client: TestClient) -> None:
    _learn(client, ME, "mine")
    assert client.put("/api/v1/learning/profile", headers=ME, json={"tone": "casual", "notes": "First names only."}).status_code == 200
    learning_data.clear()  # restart: everything below comes from PostgreSQL

    overview = client.get("/api/v1/learning", headers=ME).json()
    assert overview["state"] == "on" and overview["counts"]["signals"] == {"draft_edited": 1} and overview["counts"]["preferences"] == 1
    profile = client.get("/api/v1/learning/profile", headers=ME).json()
    assert profile["derived"]["greeting"] is None and profile["derived"]["samples"] == 1
    assert profile["effective"]["tone"] == "casual" and profile["overrides"]["notes"] == "First names only."
    block = learned_instructions(ctx(ME), surface="email", contact="mine@example.com")
    assert block is not None and "mine prefers Arabic" in block and "First names only." in block
    page = client.get("/api/v1/learning/signals", headers=ME, params={"q": "mine@"}).json()
    assert page["total"] == 1 and page["items"][0]["metadata"]["edit"]["sign_off_after"] == "Best regards"
    assert client.put("/api/v1/learning/settings", headers=ME, json={"enabled": False}).json()["state"] == "off_by_user"
    learning_data.clear()
    assert client.get("/api/v1/learning", headers=ME).json()["state"] == "off_by_user"
    assert learned_instructions(ctx(ME), surface="email") is None


def test_every_learning_table_is_private_to_its_user_under_rls(
    client: TestClient, app_session: Callable[..., Iterator[Session]], database_engine: Engine
) -> None:
    for label, hdrs in (("mine", ME), ("colleague", COLLEAGUE), ("elsewhere", OTHER_WORKSPACE), ("foreign", OTHER_TENANT)):
        _learn(client, hdrs, label)
        assert client.get("/api/v1/learning/notes", headers=hdrs).status_code == 200
    for table in ("learning_profiles", "learning_signals", "learning_preferences", "learning_notes"):
        assert _visible(app_session, ME, table, "user_test") == 1, table
        assert _visible(app_session, ME, table, None) == 0, table
        assert _visible(app_session, ME, table, "user_nobody") == 0, table
    # The API never shows another user's rows, even with a known id.
    (theirs,) = client.get("/api/v1/learning/preferences", headers=COLLEAGUE).json()["items"]
    assert client.delete(f"/api/v1/learning/preferences/{theirs['id']}", headers=ME).status_code == 404
    assert client.put(f"/api/v1/learning/preferences/{theirs['id']}", headers=ME, json={"text": "Hijacked"}).status_code == 404
    assert [item["text"] for item in client.get("/api/v1/learning/preferences", headers=ME).json()["items"]] == ["mine prefers Arabic"]

    # Writing a row for another user, another workspace or another tenant is refused by WITH CHECK.
    for tenant_id, workspace_id, user_id in ((TENANT_A, WORKSPACE_A, "user_other"), (TENANT_A, WORKSPACE_A2, "user_test"), (TENANT_B, WORKSPACE_B, "user_test")):
        with pytest.raises(DBAPIError):
            with app_session(tenant_context()) as session:
                session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
                session.execute(
                    text(
                        "insert into learning_preferences (id, tenant_id, workspace_id, user_id, text) "
                        "values ('pref_forged', :t, :w, :u, 'forged')"
                    ),
                    {"t": tenant_id, "w": workspace_id, "u": user_id},
                )
    # Another user's statements change nothing of mine.
    with app_session(tenant_context(), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
        assert session.execute(text("update learning_preferences set text = 'changed' where user_id = 'user_test'")).rowcount == 0
        assert session.execute(text("delete from learning_signals where user_id = 'user_test'")).rowcount == 0
    with database_engine.connect() as connection:
        mine = connection.execute(text("select text from learning_preferences where user_id = 'user_test' and workspace_id = :w"), {"w": WORKSPACE_A}).scalar_one()
        forced = connection.execute(
            text("select count(*) from pg_class where relname = any(:tables) and relrowsecurity and relforcerowsecurity"), {"tables": list(TABLES)}
        ).scalar_one()
    assert mine == "mine prefers Arabic" and forced == len(TABLES)


def test_routines_are_mined_from_my_own_tasks_and_accepted_into_a_paused_postgres_schedule(
    client: TestClient, database_engine: Engine
) -> None:
    now = utc_now()
    last_sunday = (now - timedelta(days=(now.weekday() + 1) % 7 or 7)).replace(hour=6, minute=5, second=0, microsecond=0)
    for hdrs in (ME, COLLEAGUE):
        for week in range(4):
            for offset, prompt in ((0, "Check Amazon orders"), (30, "Email the supplier about restock")):
                task = client.post("/api/v1/tasks", headers=hdrs, json={"title": prompt, "prompt": prompt}).json()
                at = last_sunday - timedelta(weeks=week) + timedelta(minutes=offset)
                with database_engine.begin() as connection:
                    connection.execute(text("update tasks set created_at = :at, updated_at = :at where id = :id"), {"at": at, "id": task["id"]})
    detected = client.post("/api/v1/learning/routines/detect", headers=ME, json={"timezone": "Asia/Riyadh"})
    assert detected.status_code == 200, detected.text
    (routine,) = detected.json()["items"]
    assert routine["cron"] == "0 9 * * 0" and [step["prompt"] for step in routine["steps"]] == ["Check Amazon orders", "Email the supplier about restock"]

    accepted = client.post(f"/api/v1/learning/routines/{routine['id']}/accept", headers=ME)
    assert accepted.status_code == 200, accepted.text
    with database_engine.connect() as connection:
        schedule = connection.execute(
            text("select enabled, cron, timezone, created_by from automation_schedules where id = :id"), {"id": accepted.json()["schedule_id"]}
        ).one()
    assert (schedule.enabled, schedule.cron, schedule.timezone, schedule.created_by) == (False, "0 9 * * 0", "Asia/Riyadh", "user_test")
    assert client.post(f"/api/v1/learning/routines/{routine['id']}/confirm", headers=ME).json()["status"] == "confirmed"
    with database_engine.connect() as connection:
        assert connection.execute(text("select enabled from automation_schedules where id = :id"), {"id": accepted.json()["schedule_id"]}).scalar_one() is True
        audit = connection.execute(text("select action from audit_records where action like 'learning.%' order by created_at")).scalars().all()
    assert audit == ["learning.routine_accepted", "learning.routine_confirmed"]
    # The colleague's identical habit is theirs: they get their own proposal, I see only mine.
    learning_data.clear()
    assert len(client.get("/api/v1/learning/routines", headers=ME).json()["items"]) == 1
    assert client.get("/api/v1/learning/routines", headers=COLLEAGUE).json()["items"] == []


def test_applying_an_autonomy_suggestion_changes_the_policy_in_the_same_workspace_only(client: TestClient, database_engine: Engine) -> None:
    assert client.put("/api/v1/approval-policy", headers=ME, json={"two_person_rule": False, "medium_risk_requires_approval": True}).status_code == 200
    with database_engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        for key, value in (("anum.tenant_id", TENANT_A), ("anum.workspace_id", WORKSPACE_A), ("anum.user_id", "user_test")):
            connection.execute(text("select set_config(:key, :value, true)"), {"key": key, "value": value})
        for index in range(5):
            connection.execute(
                text(
                    "insert into learning_signals (id, tenant_id, workspace_id, user_id, kind, surface, tool, metadata, created_at) "
                    "values (:id, :t, :w, 'user_test', 'approval_approved', 'approval', 'crm.update', '{\"risk_level\": \"medium\"}', now())"
                ),
                {"id": f"signal_seed_{index}", "t": TENANT_A, "w": WORKSPACE_A},
            )
    elsewhere = client.get("/api/v1/approval-policy", headers=OTHER_WORKSPACE).json()["medium_risk_requires_approval"]
    (suggestion,) = client.get("/api/v1/learning/notes", headers=ME).json()["items"][0]["suggestions"]
    assert suggestion["rule"] == "autonomy_loosen"
    member = headers(role="member")
    assert client.post(f"/api/v1/learning/suggestions/{suggestion['id']}/apply", headers=member).status_code == 403
    applied = client.post(f"/api/v1/learning/suggestions/{suggestion['id']}/apply", headers=ME)
    assert applied.status_code == 200, applied.text
    assert client.get("/api/v1/approval-policy", headers=ME).json()["medium_risk_requires_approval"] is False
    assert client.get("/api/v1/approval-policy", headers=OTHER_WORKSPACE).json()["medium_risk_requires_approval"] == elsewhere
    with database_engine.connect() as connection:
        actions = connection.execute(text("select action from audit_records where target in (:s, :w)"), {"s": suggestion["id"], "w": WORKSPACE_A}).scalars().all()
    assert "learning.suggestion_applied" in actions and "approval_policy.updated" in actions
