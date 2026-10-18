"""Organisation export and purge in PostgreSQL (docs/organisation-lifecycle.md).

The API and the runner run as the non-owner ``anum_test_app`` role under forced RLS; the
purge switches to ``anum_tenant_purger`` and discovery to ``anum_maintenance``. The tests
prove that the registry matches the database, that the purge role sees nothing without a
purging job, and that exporting and purging tenant A never reads or touches tenant B.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api import tenant_lifecycle as lifecycle
from anum_api.db import session as db_session
from anum_api.files import file_store, workspace_object_key
from anum_api.main import app
from anum_api.schemas import utc_now
from anum_api.settings import settings
from anum_api.tenant_lifecycle_tables import LIFECYCLE_JOBS_TABLE, PURGED_TABLES, TENANT_TABLES

pytestmark = pytest.mark.database


def headers(tenant: str = TENANT_A, workspace: str = WORKSPACE_A, user: str = "admin") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": "owner,org_admin"}


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    monkeypatch.setattr(settings, "tenant_purge_batch_rows", 10)
    monkeypatch.setattr(settings, "tenant_export_batch_rows", 10)
    file_store.clear()
    yield factory
    file_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _seed(engine: Engine) -> dict[str, str]:
    """Rows in every scope: 25 tasks per workspace (several purge batches), a membership,
    a private memory and a private finance row (restrictive owner policies), an audit
    record and a workspace file with bytes."""
    keys = {}
    with engine.begin() as connection:
        for tenant, workspace in ((TENANT_A, WORKSPACE_A), (TENANT_A, WORKSPACE_A2), (TENANT_B, WORKSPACE_B)):
            for index in range(25):
                connection.execute(
                    text("insert into tasks (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status) values (:id, :t, :w, 'admin', :title, 'p', 'queued')"),
                    {"id": f"task_{workspace}_{index}", "t": tenant, "w": workspace, "title": f"{workspace} task {index}"},
                )
            connection.execute(
                text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role) values ('admin', :t, :w, 'owner')"),
                {"t": tenant, "w": workspace},
            )
            connection.execute(
                text("insert into memories (id, tenant_id, workspace_id, kind, content, owner_user_id) values (:id, :t, :w, 'note', 'private note', 'someone')"),
                {"id": f"memory_{workspace}", "t": tenant, "w": workspace},
            )
            connection.execute(
                text("insert into finance_settings (tenant_id, workspace_id, owner_user_id) values (:t, :w, 'someone')"),
                {"t": tenant, "w": workspace},
            )
            connection.execute(
                text(
                    "insert into audit_records (id, tenant_id, workspace_id, actor, action, target, outcome, correlation_id, created_at) "
                    "values (:id, :t, :w, 'admin', 'task.create', 'x', 'success', 'c', now())"
                ),
                {"id": f"audit_{workspace}", "t": tenant, "w": workspace},
            )
            key = workspace_object_key(tenant, workspace, "files", f"file_{workspace}", "digest")
            file_store.storage.put(key, f"bytes of {workspace}".encode(), "text/plain")
            keys[workspace] = key
            connection.execute(
                text(
                    "insert into workspace_files (id, tenant_id, workspace_id, name, content_type, size_bytes, sha256, storage_key, created_by) "
                    "values (:id, :t, :w, 'notes.txt', 'text/plain', 10, :sha, :key, 'admin')"
                ),
                {"id": f"file_{workspace}", "t": tenant, "w": workspace, "sha": "0" * 64, "key": key},
            )
    return keys


def _end_cooling_off(engine: Engine) -> None:
    """Move every scheduled purge into the past (the maintenance policy uses the database clock)."""
    with engine.begin() as connection:
        connection.execute(text("update tenant_lifecycle_jobs set purge_after = now() - interval '1 second' where status = 'scheduled'"))


def _counts(engine: Engine, tenant: str) -> dict[str, int]:
    with engine.begin() as connection:
        return {
            table.name: connection.execute(text(f"select count(*) from {table.name} where tenant_id = :t"), {"t": tenant}).scalar_one()  # nosec B608 - registry names
            for table in TENANT_TABLES
        }


def test_registry_matches_the_database_and_every_table_has_its_purge_policy(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        in_database = {
            row.table_name
            for row in connection.execute(
                text("select table_name from information_schema.columns where table_schema = current_schema() and column_name = 'tenant_id'")
            )
        }
        policies = {row.tablename for row in connection.execute(text("select tablename from pg_policies where policyname like 'lifecycle_purge_%'"))}
        unextended = connection.execute(
            text(
                "select tablename, policyname from pg_policies where permissive = 'RESTRICTIVE' and qual is not null "
                "and position('anum_tenant_purger' in qual) = 0 and tablename = any(:tables)"
            ),
            {"tables": [t.name for t in TENANT_TABLES]},
        ).all()
        bypass = connection.execute(text("select rolbypassrls, rolcanlogin from pg_roles where rolname = 'anum_tenant_purger'")).one()
        # Policies that apply to every role and look rows up in another table: the purger
        # needs those lookup columns (migration 0078, POLICY_LOOKUPS).
        lookups = {
            row.looked_up
            for row in connection.execute(
                text(
                    "select distinct substring(qual from 'FROM ([a-z_]+)') as looked_up from pg_policies "
                    "where qual ilike '%from %' and roles = '{public}' and tablename = any(:tables)"
                ),
                {"tables": [t.name for t in TENANT_TABLES]},
            )
        }
    assert lookups <= {"agent_profiles", "mail_account_grants"}, f"grant the purger the lookup columns of {lookups}"
    assert in_database == {t.name for t in TENANT_TABLES}, "a tenant table is missing from anum_api/tenant_lifecycle_tables.py"
    assert policies == {t.name for t in PURGED_TABLES} and LIFECYCLE_JOBS_TABLE not in policies
    assert unextended == [], f"restrictive policies would hide rows from the purge: {unextended}"
    assert tuple(bypass) == (False, False)


def test_the_purge_role_sees_and_deletes_nothing_without_a_purging_job(database_engine: Engine, app_factory: sessionmaker[Session]) -> None:
    _seed(database_engine)
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text("set local role anum_tenant_purger"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        assert connection.execute(text("select count(*) from tasks")).scalar_one() == 0
        assert connection.execute(text("delete from tasks where tenant_id = :t"), {"t": TENANT_A}).rowcount == 0
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(text("select title from tasks"))
    assert _counts(database_engine, TENANT_A)["tasks"] == 50


def test_maintenance_discovers_jobs_by_scope_columns_only(client: TestClient, database_engine: Engine) -> None:
    assert client.post("/api/v1/organization/lifecycle/exports", headers=headers()).status_code == 202
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text("set local role anum_maintenance"))
        rows = connection.execute(text("select tenant_id, kind, status from tenant_lifecycle_jobs")).all()
        assert [tuple(r) for r in rows] == [(TENANT_A, "organisation_export", "queued")]
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(text("select requested_by from tenant_lifecycle_jobs"))
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text("set local role anum_maintenance"))
        # Workspace ids only while an export of that organisation is running.
        assert connection.execute(text("select count(*) from workspaces")).scalar_one() == 0


def test_export_reads_every_workspace_of_one_tenant_only(client: TestClient, database_engine: Engine) -> None:
    _seed(database_engine)
    job = client.post("/api/v1/organization/lifecycle/exports", headers=headers())
    assert job.status_code == 202, job.text
    assert lifecycle.run_lifecycle_pass() == {"exported": 1}
    ready = client.get("/api/v1/organization/lifecycle", headers=headers()).json()["exports"][0]
    assert ready["status"] == "ready", ready
    link = client.post(f"/api/v1/organization/lifecycle/exports/{ready['id']}/link", headers=headers()).json()
    archive = zipfile.ZipFile(io.BytesIO(client.get(link["url"]).content))
    manifest = json.loads(archive.read("manifest.json"))
    assert set(manifest["workspaces"]) == {WORKSPACE_A, WORKSPACE_A2}
    tasks = [json.loads(line) for line in archive.read("data/tasks/tasks.jsonl").splitlines()]
    assert len(tasks) == 50 and {t["tenant_id"] for t in tasks} == {TENANT_A}
    assert len({t["id"] for t in tasks}) == 50
    # Rows private to a person are not readable by the organisation and not exported.
    assert manifest["tables"]["memories"]["rows"] == 0 and manifest["tables"]["finance_settings"]["rows"] == 0
    assert archive.read(f"files/{WORKSPACE_A2}/file_{WORKSPACE_A2}/notes.txt") == f"bytes of {WORKSPACE_A2}".encode()
    assert not any(WORKSPACE_B in name for name in archive.namelist())
    # Tenant B cannot open tenant A's export.
    assert client.post(f"/api/v1/organization/lifecycle/exports/{ready['id']}/link", headers=headers(TENANT_B, WORKSPACE_B)).status_code == 404


def test_organisation_purge_leaves_only_the_receipt_and_tenant_b_untouched(client: TestClient, database_engine: Engine) -> None:
    keys = _seed(database_engine)
    before_b = _counts(database_engine, TENANT_B)
    scheduled = client.post("/api/v1/organization/lifecycle/deletion", headers=headers(), json={"confirm": TENANT_A})
    assert scheduled.status_code == 202, scheduled.text
    assert lifecycle.run_lifecycle_pass() == {}
    _end_cooling_off(database_engine)
    assert lifecycle.run_lifecycle_pass() == {"purged": 1}

    after_a = {name: count for name, count in _counts(database_engine, TENANT_A).items() if count}
    assert after_a == {"workspaces": 1, "audit_records": 1, LIFECYCLE_JOBS_TABLE: 1, "deleted_organizations": 1}
    assert _counts(database_engine, TENANT_B) == before_b
    with database_engine.begin() as connection:
        tenant = connection.execute(text("select name, status from tenants where id = :t"), {"t": TENANT_A}).one()
        receipt = connection.execute(text("select action, workspace_id, metadata from audit_records where tenant_id = :t"), {"t": TENANT_A}).one()
        job = connection.execute(text("select status, row_count, file_count from tenant_lifecycle_jobs where tenant_id = :t"), {"t": TENANT_A}).one()
    assert tuple(tenant) == (lifecycle.DELETED_ORGANISATION, "deleted")
    assert receipt.action == "organisation.deleted" and receipt.workspace_id == WORKSPACE_A
    assert receipt.metadata["counts"]["tasks"] == 50 and receipt.metadata["counts"]["memories"] == 2
    assert job.status == "completed" and job.file_count == 2
    for workspace in (WORKSPACE_A, WORKSPACE_A2):
        with pytest.raises(FileNotFoundError):
            file_store.storage.get(keys[workspace])
    assert file_store.storage.get(keys[WORKSPACE_B]) == f"bytes of {WORKSPACE_B}".encode()


def test_console_receipt_and_keycloak_cleanup(client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_tenant_lifecycle import FakeKeycloak

    from anum_api import keycloak_admin

    _seed(database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "insert into platform_organizations (tenant_id, operator_user_id, display_name, plan, first_workspace_id, first_admin_email, created_at, updated_at) "
                "values (:t, 'operator_1', 'Acme A', 'standard', :w, 'admin@example.com', now(), now())"
            ),
            {"t": TENANT_A, "w": WORKSPACE_A},
        )
    admin = FakeKeycloak({"only_a": {"tenant_id": [TENANT_A]}, "a_and_b": {"tenant_id": [TENANT_B], "tenant_ids": [TENANT_A]}})
    monkeypatch.setattr(keycloak_admin, "build_admin", lambda: admin)
    assert client.post("/api/v1/organization/lifecycle/deletion", headers=headers(), json={"confirm": TENANT_A}).status_code == 202
    _end_cooling_off(database_engine)
    assert lifecycle.run_lifecycle_pass() == {"purged": 1}
    assert admin.deleted == ["only_a"] and admin.users["a_and_b"]["attributes"]["tenant_ids"] == []
    # Discovery as anum_maintenance finds nothing more to do.
    assert lifecycle.run_lifecycle_pass() == {}

    operator = {"x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B, "x-user-id": "operator_1", "x-user-roles": "owner,platform_operator"}
    [row] = client.get("/api/v1/platform/organizations/deleted", headers=operator).json()
    assert row["tenant_id"] == TENANT_A and row["display_name"] == "Acme A" and row["plan"] == "standard"
    assert row["status"] == "deleted" and row["counts"]["tasks"] == 50 and row["file_count"] == 2
    assert row["identity_cleanup"] == {"status": "removed", "removed": 1, "detached": 1, "finished_at": row["identity_cleanup"]["finished_at"]}
    someone_else = {**operator, "x-user-id": "operator_2"}
    assert client.get("/api/v1/platform/organizations/deleted", headers=someone_else).json() == []
    with database_engine.begin() as connection:
        actions = connection.execute(text("select action, outcome from audit_records where tenant_id = :t order by created_at"), {"t": TENANT_A}).all()
    assert [tuple(a) for a in actions] == [("organisation.deleted", "success"), ("organisation.identities_removed", "success")]
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text("set local role anum_platform_reader"))
        connection.execute(text("select set_config('anum.user_id', 'operator_1', true)"))
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(text("select identity_attempts, updated_at from deleted_organizations"))


def test_workspace_purge_and_cancel(client: TestClient, database_engine: Engine) -> None:
    _seed(database_engine)
    before_b = _counts(database_engine, TENANT_B)
    a2 = headers(workspace=WORKSPACE_A2)
    assert client.post("/api/v1/workspaces/current/deletion", headers=a2, json={"confirm_name": "Workspace A2"}).status_code == 202
    assert client.post("/api/v1/workspaces/current/deletion/cancel", headers=a2).status_code == 200
    _end_cooling_off(database_engine)
    assert lifecycle.run_lifecycle_pass() == {}
    assert _counts(database_engine, TENANT_A)["tasks"] == 50

    assert client.post("/api/v1/workspaces/current/deletion", headers=a2, json={"confirm_name": "Workspace A2"}).status_code == 202
    _end_cooling_off(database_engine)
    assert lifecycle.run_lifecycle_pass() == {"purged": 1}
    with database_engine.begin() as connection:
        left = connection.execute(text("select workspace_id, count(*) from tasks where tenant_id = :t group by 1"), {"t": TENANT_A}).all()
        memories = connection.execute(text("select workspace_id from memories where tenant_id = :t"), {"t": TENANT_A}).scalars().all()
        name = connection.execute(text("select name from workspaces where id = :w"), {"w": WORKSPACE_A2}).scalar_one()
        status = connection.execute(text("select status from tenants where id = :t"), {"t": TENANT_A}).scalar_one()
    assert [tuple(row) for row in left] == [(WORKSPACE_A, 25)]
    assert memories == [WORKSPACE_A]
    assert name == lifecycle.DELETED_WORKSPACE and status == "active"
    assert _counts(database_engine, TENANT_B) == before_b
