"""The upload rescan job in PostgreSQL (migration ``0075_file_scan_status``), as the non-owner
app role: ``anum_maintenance`` sees only unscanned rows and only their ids and scope, the
verdict is recorded inside each workspace's RLS context, infected files are quarantined and
no longer served, and a second run finds nothing left."""

from __future__ import annotations

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api import file_policy, file_rescan
from anum_api.db import session as db_session
from anum_api.files import open_file_metadata_store

pytestmark = pytest.mark.database


def as_member(tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": "member_1", "x-user-roles": "member"}


class SignatureScanner:
    def __init__(self) -> None:
        self.seen: list[bytes] = []

    def scan(self, name: str, content_type: str, content: bytes) -> file_policy.ScanVerdict:
        self.seen.append(content)
        if b"MALWARE" in content:
            return file_policy.ScanVerdict(clean=False, reason="Eicar-Test-Signature")
        return file_policy.ScanVerdict(clean=True)


def _upload(client: TestClient, name: str, content: bytes, headers: dict[str, str]) -> dict:  # noqa: F811
    response = client.post("/api/v1/files", params={"name": name}, headers=headers, content=content)
    assert response.status_code == 201, response.text
    return response.json()


def test_rescan_quarantines_infected_files_across_tenants_under_rls(
    client: TestClient, database_engine: Engine  # noqa: F811
) -> None:
    file_policy.set_file_scanner(None)
    bad = _upload(client, "old.txt", b"MALWARE inside", as_member())
    good = _upload(client, "notes.txt", b"plain notes", as_member(TENANT_B, WORKSPACE_B))
    scanner = SignatureScanner()
    file_policy.set_file_scanner(scanner)
    try:
        fresh = _upload(client, "fresh.txt", b"fresh notes", as_member())
    finally:
        file_policy.set_file_scanner(None)
    assert fresh["scan_status"] == "clean"

    discovery = file_rescan.postgres_discovery(db_session.SessionLocal)
    assert sorted(row[0] for row in discovery("", 50)) == sorted([bad["id"], good["id"]])

    scanner.seen.clear()
    result = file_rescan.rescan(scanner, discovery)
    assert (result.unscanned, result.scanned, result.clean, result.quarantined) == (2, 2, 1, 1)
    assert result.quarantined_ids == [bad["id"]] and len(scanner.seen) == 2
    assert discovery("", 50) == []
    assert file_rescan.rescan(scanner, discovery).unscanned == 0

    with open_file_metadata_store(tenant_context()) as store:
        stored = store.get(tenant_context(), bad["id"])
    assert stored is not None and stored.scan_status == "infected" and stored.scan_signature == "Eicar-Test-Signature"
    download = client.get(f"/api/v1/files/{bad['id']}/content", headers=as_member())
    assert download.status_code == 409 and "quarantined" in download.json()["error"]["message"]
    other = client.get(f"/api/v1/files/{good['id']}/content", headers=as_member(TENANT_B, WORKSPACE_B))
    assert other.status_code == 200


def test_the_maintenance_role_reads_only_ids_scope_and_scan_state(
    client: TestClient, database_engine: Engine  # noqa: F811
) -> None:
    file_policy.set_file_scanner(None)
    _upload(client, "old.txt", b"plain", as_member())
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select id, tenant_id, workspace_id, scan_status from workspace_files")).all()
            assert len(rows) == 1 and rows[0].scan_status is None
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select name, storage_key from workspace_files"))
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("update workspace_files set scan_status = 'clean'"))
        with connection.begin():
            connection.execute(text("update workspace_files set scan_status = 'clean'"))
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            # Scanned rows are invisible to the maintenance role.
            assert connection.execute(text("select count(*) from workspace_files")).scalar_one() == 0
