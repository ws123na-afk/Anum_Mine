"""System health notices in PostgreSQL (docs/system-health.md#notices).

As the non-owner application role: the start and stop records go to the notice workspace's audit log, a start
without a clean stop before it gives each active owner one in-app notice, and the daily red notice goes out once a
UTC day; every row stays in its tenant under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api import system_health as sh
from anum_api.db import session as db_session
from anum_api.settings import settings

pytestmark = pytest.mark.database
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
def scope(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:  # type: ignore[no-untyped-def]
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "system_health_tenant_id", TENANT_A)
    monkeypatch.setattr(settings, "system_health_workspace_id", WORKSPACE_A)
    monkeypatch.setattr(settings, "backup_status_dir", str(tmp_path))  # no status files: backups are red
    with database_engine.begin() as connection:
        for user_id, role, active in (("user_owner", "owner", True), ("user_left", "owner", False), ("user_member", "member", True)):
            connection.execute(text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role, active) "
                                    "values (:t, :w, :u, :r, :a)"), {"t": TENANT_A, "w": WORKSPACE_A, "u": user_id, "r": role, "a": active})
        connection.execute(text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role) values (:t, :w, 'user_b', 'owner')"),
                           {"t": TENANT_B, "w": WORKSPACE_B})
    yield


def _notices(engine: Engine, kind: str) -> list[tuple[str, str, str]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(
            "select tenant_id, user_id, title from user_notifications where kind = :k order by user_id"), {"k": kind}).all()]


def test_restart_and_daily_notices_reach_only_the_active_owners(scope: None, database_engine: Engine) -> None:
    assert sh.record_start(NOW) is False
    sh.record_stop(NOW + timedelta(minutes=30))
    assert sh.record_start(NOW + timedelta(hours=1)) is False
    assert sh.record_start(NOW + timedelta(hours=2)) is True
    assert _notices(database_engine, sh.RESTART_KIND) == [(TENANT_A, "user_owner", "ANUM restarted without a clean shutdown")]
    with database_engine.connect() as connection:
        actions = connection.execute(text(
            "select tenant_id, action from audit_records where target = 'system:api' order by created_at, action")).all()
    assert [tuple(row) for row in actions] == [
        (TENANT_A, sh.STARTED), (TENANT_A, sh.STOPPED), (TENANT_A, sh.STARTED),
        (TENANT_A, sh.RESTARTED), (TENANT_A, sh.STARTED), (TENANT_A, "system.restart_notice_sent")]
    restarts = {item["key"]: item for item in sh.evaluate(NOW + timedelta(hours=3))["items"]}["restarts"]
    assert restarts["state"] == "red"

    assert sh.run_health_check(NOW + timedelta(hours=3))["notified"] == 1
    assert sh.run_health_check(NOW + timedelta(hours=4))["notified"] == 0
    (notice,) = _notices(database_engine, sh.NOTICE_KIND)
    assert notice[:2] == (TENANT_A, "user_owner") and notice[2].startswith("System health:")
    # Under RLS another tenant sees none of it.
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
                           {"t": TENANT_B, "w": WORKSPACE_B})
        assert connection.execute(text("select count(*) from audit_records where target = 'system:api'")).scalar_one() == 0
