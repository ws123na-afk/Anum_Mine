"""PostgreSQL: the Files bucket reconcile reads storage keys as anum_maintenance (migration
``0176_files_audit_probe``, docs/deploy-oracle.md#reconciling-the-files-bucket).

Inside a tenant's RLS context the role reads every key of that tenant (vault files hidden from
other units of work, older versions, another person's private mail attachment, an export's
archive) and nothing of another tenant; without a tenant context it reads nothing; it can read
no content column and write nothing. The reconcile then keeps exactly those objects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.files import workspace_object_key
from anum_api.files_reconcile import BucketObject, postgres_references, reconcile

pytestmark = pytest.mark.database


def _key(tenant: str, workspace: str, kind: str, name: str) -> str:
    return workspace_object_key(tenant, workspace, kind, name, "0" * 64)


def _seed(engine: Engine) -> dict[str, str]:
    keys = {
        "file": _key(TENANT_A, WORKSPACE_A, "files", "file_a"),
        "vault": _key(TENANT_A, WORKSPACE_A, "files", "file_vault"),
        "version": _key(TENANT_A, WORKSPACE_A, "files", "file_a_v1"),
        "mail": _key(TENANT_A, WORKSPACE_A, "mail", "att_a"),
        "export": workspace_object_key(TENANT_A, WORKSPACE_A, "lifecycle", "job_a", "export.zip"),
        "other": _key(TENANT_B, WORKSPACE_B, "files", "file_b"),
    }
    insert_file = text(
        "insert into workspace_files (id, tenant_id, workspace_id, name, content_type, size_bytes, sha256, storage_key, created_by, vault, scan_status) "
        "values (:id, :t, :w, 'private.pdf', 'application/pdf', 10, :sha, :key, 'someone', :vault, 'clean')"
    )
    with engine.begin() as connection:
        for file_id, tenant, workspace, key, vault in (
            ("file_a", TENANT_A, WORKSPACE_A, keys["file"], None),
            ("file_vault", TENANT_A, WORKSPACE_A, keys["vault"], "hidden"),
            ("file_b", TENANT_B, WORKSPACE_B, keys["other"], None),
        ):
            connection.execute(insert_file, {"id": file_id, "t": tenant, "w": workspace, "sha": "0" * 64, "key": key, "vault": vault})
        connection.execute(
            text(
                "insert into workspace_file_versions (id, tenant_id, workspace_id, file_id, version, content_type, size_bytes, sha256, "
                "storage_key, created_by, created_at, replaced_by, replaced_at) "
                "values ('ver_a', :t, :w, 'file_a', 1, 'application/pdf', 9, :sha, :key, 'someone', now(), 'someone', now())"
            ),
            {"t": TENANT_A, "w": WORKSPACE_A, "sha": "1" * 64, "key": keys["version"]},
        )
        connection.execute(
            text(
                "insert into tenant_lifecycle_jobs (id, tenant_id, workspace_id, kind, status, requested_by, requested_at, archive_key) "
                "values ('job_a', :t, :w, 'organisation_export', 'ready', 'admin', now(), :key)"
            ),
            {"t": TENANT_A, "w": WORKSPACE_A, "key": keys["export"]},
        )
        # A private attachment of another person. Its draft and mailbox are not what this test is
        # about, so the seed skips the foreign key triggers for this one row (the seeding login is
        # the database owner; the reads below run as anum_maintenance).
        connection.execute(text("set local session_replication_role = replica"))
        connection.execute(
            text(
                "insert into mail_attachments (id, tenant_id, workspace_id, owner_user_id, draft_id, filename, content_type, size_bytes, sha256, storage_key) "
                "values ('att_a', :t, :w, 'someone_else', 'draft_x', 'x.pdf', 'application/pdf', 5, :sha, :key)"
            ),
            {"t": TENANT_A, "w": WORKSPACE_A, "sha": "2" * 64, "key": keys["mail"]},
        )
    return keys


def test_maintenance_reads_every_key_of_the_tenant_in_context_and_nothing_else(database_engine: Engine, seed_scopes: None) -> None:
    keys = _seed(database_engine)
    factory = sessionmaker(bind=database_engine)
    found = postgres_references(TENANT_A, factory)
    assert found == {keys["file"], keys["vault"], keys["version"], keys["mail"], keys["export"]}
    assert postgres_references(TENANT_B, factory) == {keys["other"]}
    assert postgres_references("tenant_unknown", factory) == set()
    # Without a tenant context: nothing (unscanned files stay visible to the malware rescan's
    # discovery, migration 0075; these are scanned). Content columns: refused. Writes: refused.
    with database_engine.connect() as connection:
        connection.execute(text("set local role anum_maintenance"))
        for table in ("workspace_files", "workspace_file_versions", "mail_attachments"):
            assert connection.execute(text(f"select count(*) from {table} where storage_key is not null")).scalar_one() == 0  # nosec B608 - fixed names
        connection.rollback()
    for refused in (
        "select name from workspace_files",
        "select filename from mail_attachments",
        "select archive_sha256 from tenant_lifecycle_jobs",
        "update workspace_files set storage_key = 'x'",
        "delete from mail_attachments",
    ):
        with database_engine.connect() as connection:
            connection.execute(text("set local role anum_maintenance"))
            connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
            with pytest.raises(DBAPIError):
                connection.execute(text(refused))
            connection.rollback()


def test_the_reconcile_keeps_what_the_database_references(database_engine: Engine, seed_scopes: None) -> None:
    keys = _seed(database_engine)
    factory = sessionmaker(bind=database_engine)
    now = datetime(2026, 11, 11, tzinfo=UTC)
    orphan = _key(TENANT_A, WORKSPACE_A, "files", "file_purged")

    class Bucket:
        def list(self):  # type: ignore[no-untyped-def]
            return [BucketObject(k, 1, now - timedelta(days=30)) for k in [*keys.values(), orphan]]

        def read(self, key: str) -> bytes:
            raise AssertionError("no logos here")

        def delete(self, key: str) -> None:
            raise AssertionError("a reconcile deletes nothing")

    report = reconcile(Bucket(), lambda tenant: postgres_references(tenant, factory), now=now)
    assert [f.item.key for f in report.orphans] == [orphan]
    assert report.kept == len(keys) and report.unreadable_tenants == {} and report.skipped == []
