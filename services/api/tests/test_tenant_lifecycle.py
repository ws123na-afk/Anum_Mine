"""Organisation export and deletion, and workspace deletion, against the in-memory
backend (docs/organisation-lifecycle.md), plus the completeness check of the table
registry against every migration."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import zipfile
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest import mock

import alembic
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from anum_api import tenant_lifecycle as lifecycle
from anum_api.files import file_store, workspace_object_key
from anum_api.main import app, store
from anum_api.schemas import utc_now
from anum_api.settings import settings
from anum_api.tenant_lifecycle_tables import BY_NAME, LIFECYCLE_JOBS_TABLE, PURGED_TABLES, TENANT_TABLES

API_ROOT = Path(__file__).parents[1]
TENANT_A, WORKSPACE_A, WORKSPACE_A2 = "tenant_life_a", "workspace_life_a", "workspace_life_a2"
TENANT_B, WORKSPACE_B = "tenant_life_b", "workspace_life_b"


def headers(user: str = "owner_a", *, roles: str = "owner,org_admin", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


ADMIN_A = headers()
OWNER_A = headers("plain_owner", roles="owner")
ADMIN_MEMBER_A = headers("admin_member", roles="member,org_admin")
VIEWER_A = headers("viewer_a", roles="viewer")
ADMIN_B = headers("owner_b", tenant=TENANT_B, workspace=WORKSPACE_B)


def _clear() -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.tenants,
                       store.workspaces, store.memberships, store.invitations, store.audit_records):
        collection.clear()
    lifecycle.memory_lifecycle_store.clear()
    lifecycle.memory_receipts.clear()
    from anum_api.organisations import memory_branding

    memory_branding.platform.pop(TENANT_A, None)
    file_store.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    test_client = TestClient(app)
    for tenant, workspace, name in ((TENANT_A, WORKSPACE_A, "Desk A"), (TENANT_A, WORKSPACE_A2, "Desk A2"), (TENANT_B, WORKSPACE_B, "Desk B")):
        owner = headers("owner_a" if tenant == TENANT_A else "owner_b", tenant=tenant, workspace=workspace)
        if tenant not in store.tenants:
            assert test_client.post("/api/v1/tenants", headers=owner, json={"name": tenant}).status_code == 201
        assert test_client.post("/api/v1/workspaces", headers=owner, json={"name": name}).status_code == 201
        assert test_client.post("/api/v1/workspace-memberships/current", headers=owner).status_code == 201
        for title in ("first", "second"):
            assert test_client.post("/api/v1/tasks", headers=owner, json={"title": f"{name} {title}", "prompt": "summarize"}).status_code in (200, 201)
    try:
        yield test_client
    finally:
        _clear()


def _due(days: int = 0, hours: int = 0):  # type: ignore[no-untyped-def]
    return utc_now() + timedelta(days=days, hours=hours, seconds=1)


def _put_file(tenant: str, workspace: str, name: str, content: bytes) -> str:
    from anum_api.files import FileRecord

    file_id = f"file_{name.replace('.', '_')}"
    key = workspace_object_key(tenant, workspace, "files", file_id, "digest")
    file_store.storage.put(key, content, "text/plain")
    now = utc_now()
    file_store.records[file_id] = FileRecord(
        id=file_id, tenant_id=tenant, workspace_id=workspace, name=name, content_type="text/plain",
        size_bytes=len(content), sha256="0" * 64, storage_key=key, created_by="owner", created_at=now,
        updated_by="owner", updated_at=now,
    )
    return key


# --------------------------------------------------------------------------- registry completeness


class _RecordingOp:
    """Stands in for ``alembic.op`` and records which tables (and columns) exist."""

    def __init__(self) -> None:
        self.tables: dict[str, list[str]] = {}

    def create_table(self, name, *items, **kwargs):  # type: ignore[no-untyped-def]
        self.tables[name] = [item.name for item in items if isinstance(item, sa.Column)]

    def drop_table(self, name, **kwargs):  # type: ignore[no-untyped-def]
        self.tables.pop(name, None)

    def add_column(self, table, column, **kwargs):  # type: ignore[no-untyped-def]
        if table in self.tables:
            self.tables[table].append(column.name)

    def drop_column(self, table, column, **kwargs):  # type: ignore[no-untyped-def]
        if table in self.tables and column in self.tables[table]:
            self.tables[table].remove(column)

    def rename_table(self, old, new, **kwargs):  # type: ignore[no-untyped-def]
        if old in self.tables:
            self.tables[new] = self.tables.pop(old)

    def execute(self, sql, *args, **kwargs):  # type: ignore[no-untyped-def]
        statement = str(sql)
        for match in re.finditer(r"create table (?:if not exists )?(\w+)\s*\((.*?)\n\s*\)\s*;", statement, re.I | re.S):
            self.tables[match.group(1)] = re.findall(r"^\s*(\w+)\s", match.group(2), re.M)
        for match in re.finditer(r"drop table (?:if exists )?(\w+)", statement, re.I):
            self.tables.pop(match.group(1), None)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return mock.MagicMock()


def _tables_after_every_migration() -> dict[str, list[str]]:
    recorder = _RecordingOp()
    recorder.execute((API_ROOT / "migrations" / "0001_foundation.sql").read_text())
    with mock.patch.object(alembic, "op", recorder), mock.patch.dict(sys.modules, {"alembic.op": recorder}):
        for path in sorted((API_ROOT / "migrations" / "versions").glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"lifecycle_replay_{path.stem}", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if path.stem != "0001_foundation":
                module.upgrade()
    return recorder.tables


def test_every_tenant_table_is_listed_for_export_and_purge() -> None:
    tables = _tables_after_every_migration()
    tenant_tables = {name for name, columns in tables.items() if "tenant_id" in columns}
    assert len(tenant_tables) > 150, "the migration replay found too few tables; the recorder is broken"
    listed = {table.name for table in TENANT_TABLES}
    missing = sorted(tenant_tables - listed)
    stale = sorted(listed - tenant_tables)
    assert not missing, f"Tenant tables not in anum_api/tenant_lifecycle_tables.py (export and purge would skip them): {missing}"
    assert not stale, f"Listed tables that no migration creates: {stale}"
    for table in TENANT_TABLES:
        columns = tables[table.name]
        if table.workspace_column is not None:
            assert table.workspace_column in columns, table.name
        elif table.name != LIFECYCLE_JOBS_TABLE:
            assert "workspace_id" not in columns, f"{table.name} has a workspace_id: a workspace deletion would skip it"
        if table.storage_key_column:
            assert table.storage_key_column in columns
    with_keys = {name for name, columns in tables.items() if "storage_key" in columns}
    assert with_keys == {t.name for t in TENANT_TABLES if t.storage_key_column}, "a table with object storage keys is not marked"


def test_every_purged_table_is_covered_by_the_purge_migration() -> None:
    spec = importlib.util.spec_from_file_location("lifecycle_0079", API_ROOT / "migrations" / "versions" / "0079_tenant_lifecycle.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    in_migration = set(module._all_tables())
    # Tables added after 0079 bring their own purge policy in their own migration.
    later = {
        "skill_publisher_keys": "0083_skill_signing",
        "skill_signing_settings": "0083_skill_signing",
        "business_change_orders": "0080_business_change_orders",
        "business_usage_ledger": "0080_business_change_orders",
        **{name: "0082_business_pipeline" for name in (
            "business_outreach_settings", "business_target_profiles", "business_observations",
            "business_pipeline_runs", "business_outbox", "business_replies")},
    }
    for table, revision in later.items():
        text = (API_ROOT / "migrations" / "versions" / f"{revision}.py").read_text(encoding="utf-8")
        assert table in text and "create policy lifecycle_purge_{table}" in text
    assert in_migration | set(later) == {t.name for t in PURGED_TABLES}
    assert LIFECYCLE_JOBS_TABLE not in in_migration
    assert len(module.revision) <= 32 and module.down_revision == "0078_demo_records"
    for name in in_migration:
        predicate = module._purge_predicate(name)
        assert "anum.tenant_id" in predicate and "status = 'purging'" in predicate
        assert ("workspace_deletion" in predicate) == (BY_NAME[name].workspace_column is not None)


def test_secret_columns_are_left_out_of_the_export() -> None:
    kept, left_out = lifecycle.exportable_columns(
        ["id", "api_key_ciphertext", "access_token_enc", "token_hash", "verify_token_hash", "password", "token",
         "embedding", "secret_ciphertext", "credentials_encrypted", "input_tokens", "token_last4", "name"]
    )
    assert kept == ["id", "input_tokens", "token_last4", "name"]
    assert "api_key_ciphertext" in left_out and "embedding" in left_out
    assert lifecycle.csv_cell("=SUM(A1)") == "'=SUM(A1)" and lifecycle.csv_cell(None) == ""


# --------------------------------------------------------------------------- permissions


def test_summary_tells_each_role_what_it_may_do(client: TestClient) -> None:
    admin = client.get("/api/v1/organization/lifecycle", headers=ADMIN_A).json()
    assert admin["can_manage_organisation"] is True and admin["can_delete_workspace"] is True
    assert admin["organisation_confirmation"] == TENANT_A and admin["workspace_confirmation"] == "Desk A"
    assert admin["cooling_off_hours"] == settings.tenant_deletion_cooling_off_hours
    owner = client.get("/api/v1/organization/lifecycle", headers=OWNER_A).json()
    assert owner["can_manage_organisation"] is False and owner["can_delete_workspace"] is True
    viewer = client.get("/api/v1/organization/lifecycle", headers=VIEWER_A).json()
    assert viewer["can_manage_organisation"] is False and viewer["can_delete_workspace"] is False
    assert viewer["exports"] == []


@pytest.mark.parametrize("caller", [OWNER_A, ADMIN_MEMBER_A, VIEWER_A])
def test_only_an_org_admin_who_owns_the_workspace_exports_or_deletes_the_organisation(client: TestClient, caller: dict[str, str]) -> None:
    for method, path, body in (
        ("post", "/api/v1/organization/lifecycle/exports", None),
        ("post", "/api/v1/organization/lifecycle/deletion", {"confirm": TENANT_A}),
        ("post", "/api/v1/organization/lifecycle/deletion/cancel", None),
    ):
        response = getattr(client, method)(path, headers=caller, json=body)
        assert response.status_code == 403, (path, response.text)
        assert "org_admin" in response.text
    assert lifecycle.memory_lifecycle_store.jobs == {}


def test_members_cannot_delete_a_workspace(client: TestClient) -> None:
    for caller in (VIEWER_A, ADMIN_MEMBER_A):
        assert client.post("/api/v1/workspaces/current/deletion", headers=caller, json={"confirm_name": "Desk A"}).status_code == 403


# --------------------------------------------------------------------------- tenant isolation


def test_tenant_a_cannot_see_download_or_cancel_tenant_b_jobs(client: TestClient) -> None:
    export_b = client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_B).json()
    lifecycle.run_lifecycle_pass()
    deletion_b = client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_B, json={"confirm": TENANT_B})
    assert deletion_b.status_code == 202

    summary_a = client.get("/api/v1/organization/lifecycle", headers=ADMIN_A).json()
    assert summary_a["exports"] == [] and summary_a["organisation_deletion"] is None
    assert client.post(f"/api/v1/organization/lifecycle/exports/{export_b['id']}/link", headers=ADMIN_A).status_code == 404
    assert client.post("/api/v1/organization/lifecycle/deletion/cancel", headers=ADMIN_A).status_code == 409
    # Tenant A cannot schedule tenant B's deletion by typing B's id either.
    assert client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": TENANT_B}).status_code == 422
    # A link minted for B does not open A's data, and a tampered one is refused.
    link_b = client.post(f"/api/v1/organization/lifecycle/exports/{export_b['id']}/link", headers=ADMIN_B).json()
    token = link_b["url"].rsplit("/", 1)[1]
    payload, signature = token.split(".")
    forged = lifecycle._b64(lifecycle._unb64(payload).replace(TENANT_B.encode(), TENANT_A.encode())) + "." + signature
    assert client.get(f"/api/v1/organization/lifecycle/exports/download/{forged}").status_code == 410
    still_b = client.get("/api/v1/organization/lifecycle", headers=ADMIN_B).json()
    assert still_b["organisation_deletion"]["status"] == "scheduled"


# --------------------------------------------------------------------------- export


def test_export_runs_in_the_background_and_downloads_with_a_signed_link(client: TestClient) -> None:
    _put_file(TENANT_A, WORKSPACE_A, "notes.txt", b"hello from A")
    _put_file(TENANT_B, WORKSPACE_B, "secret.txt", b"tenant B only")
    queued = client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_A)
    assert queued.status_code == 202 and queued.json()["status"] == "queued"
    assert client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_A).status_code == 409
    job_id = queued.json()["id"]
    assert client.post(f"/api/v1/organization/lifecycle/exports/{job_id}/link", headers=ADMIN_A).status_code == 409

    assert lifecycle.run_lifecycle_pass() == {"exported": 1}
    ready = client.get("/api/v1/organization/lifecycle", headers=ADMIN_A).json()["exports"][0]
    assert ready["status"] == "ready" and ready["archive_bytes"] > 0 and ready["file_count"] == 1
    link = client.post(f"/api/v1/organization/lifecycle/exports/{job_id}/link", headers=ADMIN_A)
    assert link.status_code == 200
    download = client.get(link.json()["url"])
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    assert download.headers["cache-control"] == "no-store"

    archive = zipfile.ZipFile(io.BytesIO(download.content))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == lifecycle.FORMAT and manifest["organisation_id"] == TENANT_A
    assert set(manifest["workspaces"]) == {WORKSPACE_A, WORKSPACE_A2}
    assert set(manifest["tables"]) == {t.name for t in PURGED_TABLES}
    tasks = [json.loads(line) for line in archive.read("data/tasks/tasks.jsonl").splitlines()]
    assert len(tasks) == 4 and {task["tenant_id"] for task in tasks} == {TENANT_A}
    assert "csv/tasks/tasks.csv" in archive.namelist()
    names = archive.namelist()
    assert f"files/{WORKSPACE_A}/file_notes_txt/notes.txt" in names
    assert archive.read(f"files/{WORKSPACE_A}/file_notes_txt/notes.txt") == b"hello from A"
    assert not any("secret" in name for name in names)
    invitations = archive.read("data/organisation/workspace_invitations.jsonl")
    assert b"token_hash" not in invitations
    actions = {r.action for r in store.audit_records if r.tenant_id == TENANT_A}
    assert {"organisation.export.requested", "organisation.export.ready", "organisation.export.link_issued", "organisation.export.downloaded"} <= actions


def test_expired_links_and_archives_are_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_A).json()["id"]
    lifecycle.run_lifecycle_pass()
    url = client.post(f"/api/v1/organization/lifecycle/exports/{job_id}/link", headers=ADMIN_A).json()["url"]
    token = url.rsplit("/", 1)[1]
    with pytest.raises(lifecycle.InvalidLink):
        lifecycle.verify_link(token, now=utc_now() + timedelta(seconds=settings.tenant_export_link_ttl_seconds + 5))
    assert lifecycle.run_lifecycle_pass(now=_due(hours=settings.tenant_export_retention_hours)) == {"expired": 1}
    job = lifecycle.memory_lifecycle_store.jobs[job_id]
    assert job.status == "expired" and job.archive_key is None
    assert client.get(url).status_code == 410
    assert client.post(f"/api/v1/organization/lifecycle/exports/{job_id}/link", headers=ADMIN_A).status_code == 410


def test_an_archive_over_the_limit_fails_with_a_reason(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tenant_export_max_bytes", 1024 * 1024)
    _put_file(TENANT_A, WORKSPACE_A, "big.bin", os.urandom(1536 * 1024))
    client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_A)
    lifecycle.run_lifecycle_pass()
    failed = client.get("/api/v1/organization/lifecycle", headers=ADMIN_A).json()["exports"][0]
    assert failed["status"] == "failed" and "ANUM_TENANT_EXPORT_MAX_BYTES" in failed["error"]


# --------------------------------------------------------------------------- deletion


def test_organisation_deletion_needs_the_exact_id_and_waits_for_the_cooling_off(client: TestClient) -> None:
    wrong = client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": "tenant_life"})
    assert wrong.status_code == 422 and lifecycle.memory_lifecycle_store.jobs == {}
    scheduled = client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": f" {TENANT_A} "})
    assert scheduled.status_code == 202
    job = scheduled.json()
    assert job["status"] == "scheduled"
    assert client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": TENANT_A}).status_code == 409
    # Before the cooling-off ends nothing happens.
    assert lifecycle.run_lifecycle_pass(now=_due(hours=settings.tenant_deletion_cooling_off_hours - 1)) == {}
    assert len([t for t in store.tasks.values() if t.tenant_id == TENANT_A]) == 4


def test_cancel_during_the_cooling_off_keeps_everything(client: TestClient) -> None:
    client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": TENANT_A})
    cancelled = client.post("/api/v1/organization/lifecycle/deletion/cancel", headers=ADMIN_A)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_by"] == "owner_a"
    assert client.post("/api/v1/organization/lifecycle/deletion/cancel", headers=ADMIN_A).status_code == 409
    assert lifecycle.run_lifecycle_pass(now=_due(days=30)) == {}
    assert len([t for t in store.tasks.values() if t.tenant_id == TENANT_A]) == 4
    assert client.get("/api/v1/organization/lifecycle", headers=ADMIN_A).json()["organisation_deletion"] is None
    # A new deletion can be scheduled after a cancel.
    assert client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": TENANT_A}).status_code == 202
    actions = [r.action for r in store.audit_records if r.tenant_id == TENANT_A]
    assert actions.count("organisation.deletion.scheduled") == 2 and "organisation.deletion.cancelled" in actions


def test_organisation_purge_removes_every_row_and_file_and_keeps_a_receipt(client: TestClient) -> None:
    from anum_api import public_logos

    key_a = _put_file(TENANT_A, WORKSPACE_A2, "a.txt", b"a")
    key_b = _put_file(TENANT_B, WORKSPACE_B, "b.txt", b"b")
    png = b"\x89PNG\r\n\x1a\n" + b"logo"
    logo_a = public_logos.publish(TENANT_A, png, "image/png")
    logo_a2 = public_logos.publish(TENANT_A, png + b"-v2", "image/png")
    logo_b = public_logos.publish(TENANT_B, png, "image/png")
    client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_A)
    lifecycle.run_lifecycle_pass()
    archive_key = next(iter(lifecycle.memory_lifecycle_store.jobs.values())).archive_key
    job_id = client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": TENANT_A}).json()["id"]
    b_tasks = len([t for t in store.tasks.values() if t.tenant_id == TENANT_B])

    # The archive's retention ends at the same time; either way it is deleted.
    assert lifecycle.run_lifecycle_pass(now=_due(hours=settings.tenant_deletion_cooling_off_hours))["purged"] == 1
    assert client.post("/api/v1/organization/lifecycle/deletion/cancel", headers=ADMIN_A).status_code in (403, 409)

    assert not [t for t in store.tasks.values() if t.tenant_id == TENANT_A]
    assert not [m for m in store.memberships.values() if m.tenant_id == TENANT_A]
    assert [w.id for w in store.workspaces.values() if w.tenant_id == TENANT_A] == [WORKSPACE_A]
    tombstone = store.workspaces[WORKSPACE_A]
    assert tombstone.name == lifecycle.DELETED_WORKSPACE and tombstone.archived_at is not None
    assert store.tenants[TENANT_A].status == "deleted" and store.tenants[TENANT_A].name == lifecycle.DELETED_ORGANISATION
    receipts = [r for r in store.audit_records if r.tenant_id == TENANT_A]
    assert [r.action for r in receipts] == ["organisation.deleted"]
    assert receipts[0].metadata["job_id"] == job_id and receipts[0].metadata["rows"] > 0
    assert receipts[0].metadata["counts"]["tasks"] == 4
    with pytest.raises(FileNotFoundError):
        file_store.storage.get(key_a)
    with pytest.raises(FileNotFoundError):
        file_store.storage.get(archive_key)
    jobs = [j for j in lifecycle.memory_lifecycle_store.jobs.values() if j.tenant_id == TENANT_A]
    assert [(j.id, j.status) for j in jobs] == [(job_id, "completed")]
    # Tenant B is untouched.
    assert len([t for t in store.tasks.values() if t.tenant_id == TENANT_B]) == b_tasks
    assert file_store.storage.get(key_b) == b"b"
    assert store.tenants[TENANT_B].status == "active"
    # Every public logo version of tenant A is gone; tenant B's still serves.
    for path in (logo_a, logo_a2):
        assert client.get(path).status_code == 404
    assert client.get(logo_b).status_code == 200
    assert public_logos.purge(TENANT_A) == 0


def test_workspace_deletion_removes_only_that_workspace(client: TestClient) -> None:
    owner_a2 = headers(workspace=WORKSPACE_A2)
    assert client.post("/api/v1/workspaces/current/deletion", headers=owner_a2, json={"confirm_name": "Desk A"}).status_code == 422
    scheduled = client.post("/api/v1/workspaces/current/deletion", headers=owner_a2, json={"confirm_name": "Desk A2"})
    assert scheduled.status_code == 202
    assert client.get("/api/v1/organization/lifecycle", headers=owner_a2).json()["workspace_deletion"]["status"] == "scheduled"
    assert client.get("/api/v1/organization/lifecycle", headers=ADMIN_A).json()["workspace_deletion"] is None
    key = _put_file(TENANT_A, WORKSPACE_A2, "a2.txt", b"x")
    keep = _put_file(TENANT_A, WORKSPACE_A, "a.txt", b"y")

    assert lifecycle.run_lifecycle_pass(now=_due(hours=settings.tenant_deletion_cooling_off_hours)) == {"purged": 1}
    assert not [t for t in store.tasks.values() if t.workspace_id == WORKSPACE_A2]
    assert len([t for t in store.tasks.values() if t.workspace_id == WORKSPACE_A]) == 2
    assert store.workspaces[WORKSPACE_A2].name == lifecycle.DELETED_WORKSPACE
    assert store.tenants[TENANT_A].status == "active"
    assert [r.action for r in store.audit_records if r.workspace_id == WORKSPACE_A2] == ["workspace.deleted"]
    with pytest.raises(FileNotFoundError):
        file_store.storage.get(key)
    assert file_store.storage.get(keep) == b"y"


def test_workspace_deletion_cancel_and_archived_workspaces(client: TestClient) -> None:
    assert client.post("/api/v1/workspaces/current/archive", headers=ADMIN_A, json={"confirm_name": "Desk A"}).status_code == 200
    # Archived workspaces refuse writes, but deletion and its cancel still work there.
    assert client.post("/api/v1/workspaces/current/deletion", headers=ADMIN_A, json={"confirm_name": "Desk A"}).status_code == 202
    assert client.post("/api/v1/workspaces/current/deletion/cancel", headers=ADMIN_A).status_code == 200
    assert client.post("/api/v1/organization/lifecycle/exports", headers=ADMIN_A).status_code == 202
    assert lifecycle.run_lifecycle_pass(now=_due(days=30)) == {"exported": 1}
    assert len([t for t in store.tasks.values() if t.workspace_id == WORKSPACE_A]) == 2


def test_purge_order_puts_children_before_parents() -> None:
    tables = list(PURGED_TABLES)
    ordered = [t.name for t in lifecycle._ordered(tables, [("tasks", "workspaces"), ("agent_runs", "tasks"), ("workspace_memberships", "workspaces")])]
    assert ordered.index("agent_runs") < ordered.index("tasks") < ordered.index("workspaces")
    assert ordered[:2] == ["workspace_invitations", "workspace_memberships"]


def test_runner_is_built_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tenant_lifecycle_enabled", False)
    assert lifecycle.build_lifecycle_runner(settings) is None
    monkeypatch.setattr(settings, "tenant_lifecycle_enabled", True)
    runner = lifecycle.build_lifecycle_runner(settings)
    assert runner is not None and runner.poll_interval == settings.tenant_lifecycle_poll_seconds


# --------------------------------------------------------------------------- Keycloak accounts and the console


class FakeKeycloak:
    """Stands in for Keycloak's admin API: users with tenant_id / tenant_ids attributes."""

    def __init__(self, users: dict[str, dict[str, list[str]]], *, fail: bool = False) -> None:
        self.users = {uid: {"id": uid, "username": uid, "attributes": attrs} for uid, attrs in users.items()}
        self.fail = fail
        self.deleted: list[str] = []
        self.updated: list[str] = []

    def users_with_attribute(self, name: str, value: str) -> list[dict]:
        if self.fail:
            raise RuntimeError("keycloak down")
        return [dict(u) for u in self.users.values() if value in (u["attributes"].get(name) or [])]

    def delete_user(self, user_id: str) -> None:
        self.users.pop(user_id, None)
        self.deleted.append(user_id)

    def update_attributes(self, user: dict, attributes: dict) -> None:
        self.users[user["id"]]["attributes"] = attributes
        self.updated.append(user["id"])


def _keycloak_users() -> dict[str, dict[str, list[str]]]:
    return {
        "only_a": {"tenant_id": [TENANT_A]},
        "home_a_also_b": {"tenant_id": [TENANT_A], "tenant_ids": [TENANT_B]},
        "home_b_also_a": {"tenant_id": [TENANT_B], "tenant_ids": [TENANT_A, "tenant_life_c"]},
        "only_b": {"tenant_id": [TENANT_B]},
    }


def test_keycloak_cleanup_removes_only_people_in_no_other_organisation_and_is_idempotent() -> None:
    from anum_api.keycloak_admin import remove_organisation_users

    admin = FakeKeycloak(_keycloak_users())
    result = remove_organisation_users(admin, TENANT_A)
    assert (result.removed, result.detached) == (1, 2)
    assert admin.deleted == ["only_a"]
    assert admin.users["home_a_also_b"]["attributes"] == {"tenant_id": [TENANT_B], "tenant_ids": []}
    assert admin.users["home_b_also_a"]["attributes"] == {"tenant_id": [TENANT_B], "tenant_ids": ["tenant_life_c"]}
    assert admin.users["only_b"]["attributes"] == {"tenant_id": [TENANT_B]}
    again = remove_organisation_users(admin, TENANT_A)
    assert (again.removed, again.detached) == (0, 0)


def test_http_admin_uses_a_service_account_and_treats_404_as_done() -> None:
    import httpx

    from anum_api.keycloak_admin import HttpKeycloakAdmin

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/token"):
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "example"})
        assert request.headers["authorization"] == "Bearer example"
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": "u1", "attributes": {"tenant_id": [TENANT_A]}}])
        return httpx.Response(404)

    admin = HttpKeycloakAdmin("http://keycloak.test", "anum", "anum-lifecycle", "example", transport=httpx.MockTransport(handler))
    assert admin.users_with_attribute("tenant_id", TENANT_A)[0]["id"] == "u1"
    admin.delete_user("u1")
    assert ("DELETE", "/admin/realms/anum/users/u1") in calls


def _onboarded_by(operator: str) -> None:
    from anum_api.organisations import memory_branding

    memory_branding.platform[TENANT_A] = {
        "tenant_id": TENANT_A, "operator_user_id": operator, "display_name": "Acme A", "plan": "trial",
        "first_workspace_id": WORKSPACE_A, "first_admin_email": "admin@example.com", "created_at": utc_now(),
    }


def _purge_tenant_a(client: TestClient) -> str:
    job_id = client.post("/api/v1/organization/lifecycle/deletion", headers=ADMIN_A, json={"confirm": TENANT_A}).json()["id"]
    lifecycle.run_lifecycle_pass(now=_due(hours=settings.tenant_deletion_cooling_off_hours))
    return job_id


def test_purge_removes_keycloak_accounts_and_audits_it(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import keycloak_admin

    admin = FakeKeycloak(_keycloak_users())
    monkeypatch.setattr(keycloak_admin, "build_admin", lambda: admin)
    _onboarded_by("operator_1")
    _purge_tenant_a(client)
    receipt = lifecycle.memory_receipts.get(TENANT_A)
    assert receipt is not None and receipt.status == "deleted"
    assert (receipt.identity_status, receipt.identity_removed, receipt.identity_detached) == ("removed", 1, 2)
    assert admin.deleted == ["only_a"]
    audits = [r for r in store.audit_records if r.tenant_id == TENANT_A]
    assert [r.action for r in audits] == ["organisation.deleted", "organisation.identities_removed"]
    assert audits[1].metadata["removed"] == 1 and audits[1].outcome == "success"
    # Nothing left to do: later passes do not touch Keycloak again.
    assert lifecycle.run_lifecycle_pass() == {}


def test_keycloak_failures_are_retried_then_marked_failed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import keycloak_admin

    admin = FakeKeycloak(_keycloak_users(), fail=True)
    monkeypatch.setattr(keycloak_admin, "build_admin", lambda: admin)
    _purge_tenant_a(client)
    assert lifecycle.memory_receipts.get(TENANT_A).identity_status == "pending"
    for _ in range(lifecycle.IDENTITY_MAX_ATTEMPTS - 2):
        assert lifecycle.run_lifecycle_pass() == {"identities_retry": 1}
    assert lifecycle.run_lifecycle_pass() == {"identities_failed": 1}
    receipt = lifecycle.memory_receipts.get(TENANT_A)
    assert receipt.identity_status == "failed" and receipt.identity_attempts == lifecycle.IDENTITY_MAX_ATTEMPTS
    failures = [r for r in store.audit_records if r.action == "organisation.identities_removed"]
    assert failures and all(r.outcome == "failure" for r in failures)


def test_without_a_keycloak_client_the_receipt_says_not_configured(client: TestClient) -> None:
    _purge_tenant_a(client)
    assert lifecycle.memory_receipts.get(TENANT_A).identity_status == "not_configured"
    receipt_audit = next(r for r in store.audit_records if r.action == "organisation.deleted")
    assert receipt_audit.metadata["identity_cleanup"] == "not_configured"


def test_platform_console_lists_deleted_organisations_read_only(client: TestClient) -> None:
    operator = headers("operator_1", roles="owner,platform_operator", tenant=TENANT_B, workspace=WORKSPACE_B)
    other_operator = headers("operator_2", roles="owner,platform_operator", tenant=TENANT_B, workspace=WORKSPACE_B)
    _onboarded_by("operator_1")
    assert client.get("/api/v1/platform/organizations/deleted", headers=operator).json() == []
    _purge_tenant_a(client)
    listed = client.get("/api/v1/platform/organizations/deleted", headers=operator)
    assert listed.status_code == 200
    [row] = listed.json()
    assert row["tenant_id"] == TENANT_A and row["display_name"] == "Acme A" and row["status"] == "deleted"
    assert row["counts"]["tasks"] == 4 and row["row_count"] >= 4 and row["deleted_at"]
    assert row["identity_cleanup"]["status"] == "not_configured"
    assert "job_id" not in row and "operator_user_id" not in row
    assert client.get("/api/v1/platform/organizations/deleted", headers=other_operator).json() == []
    assert client.get("/api/v1/platform/organizations/deleted", headers=ADMIN_A).status_code == 403
    # The organisation no longer appears among the live ones.
    assert all(o["tenant_id"] != TENANT_A for o in client.get("/api/v1/platform/organizations", headers=operator).json())
