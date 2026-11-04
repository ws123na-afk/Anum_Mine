"""The document vault in PostgreSQL (migration 0133_document_vault), as the non-owner app role.

Row policies, not only the API, keep vault files out of every other unit of work and a private
document visible only to its owner and the people it is shared with; other workspaces see nothing.
Also: duplicates, versions, trash and permanent deletion, the daily job (reminders and purge) and
the rotation of encrypted numbers.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import APP_ROLE, client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.files import file_store
from anum_api.vault import jobs

pytestmark = pytest.mark.database
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def who(user: str, role: str = "member", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


ALICE, BOB, OWNER = who("alice"), who("bob"), who("owner_1", "owner")


def add(client: TestClient, content: bytes = PDF, headers: dict[str, str] = ALICE, **params: str) -> dict:  # noqa: F811
    response = client.post("/api/v1/documents", params={"name": "doc.pdf", **params}, headers=headers, content=content)
    assert response.status_code == 201, response.text
    return response.json()


def count_as(engine: Engine, sql: str, *, user: str | None = None, vault: bool = False, job: bool = False,
             tenant: str = TENANT_A, workspace: str = WORKSPACE_A, params: dict | None = None) -> int:
    """A count as the app role in one RLS context, with the vault and job flags as given."""
    with engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
                           {"t": tenant, "w": workspace})
        if user:
            connection.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        if vault:
            connection.execute(text("select set_config('anum.vault', 'on', true)"))
        if job:
            connection.execute(text("select set_config('anum.vault_job', 'on', true)"))
        return int(connection.execute(text(sql), params or {}).scalar_one())


def test_row_policies_hide_vault_files_and_private_documents(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    private = add(client, category="passport")
    shared = add(client, PDF + b"%2", category="certificate", access_class="workspace")
    # Files never list them; without anum.vault the rows do not exist for the app role.
    assert client.get("/api/v1/files", headers=OWNER).json() == []
    assert count_as(database_engine, "select count(*) from workspace_files") == 0
    assert count_as(database_engine, "select count(*) from workspace_files", vault=True) == 2
    # A private document: its owner only, then the person it is shared with.
    assert count_as(database_engine, "select count(*) from vault_documents", user="bob", vault=True) == 1
    assert count_as(database_engine, "select count(*) from vault_documents", user="alice", vault=True) == 2
    assert count_as(database_engine, "select count(*) from vault_documents", user="owner_1", vault=True) == 1
    assert client.post(f"/api/v1/documents/{private['id']}/access", headers=ALICE, json={"user_id": "bob"}).status_code in (201, 422)
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text("insert into vault_document_access (tenant_id, workspace_id, document_id, user_id, granted_by) "
                                "values (:t, :w, :d, 'bob', 'alice') on conflict do nothing"), {"t": TENANT_A, "w": WORKSPACE_A, "d": private["id"]})
    assert count_as(database_engine, "select count(*) from vault_documents", user="bob", vault=True) == 2
    assert client.get(f"/api/v1/documents/{private['id']}", headers=BOB).status_code == 200
    assert client.get(f"/api/v1/documents/{private['id']}", headers=OWNER).status_code == 404
    # The job flag sees every document; another workspace sees none, flags or not.
    assert count_as(database_engine, "select count(*) from vault_documents", job=True, vault=True) == 2
    for workspace, tenant in ((WORKSPACE_B, TENANT_B),):
        assert count_as(database_engine, "select count(*) from vault_documents", user="alice", vault=True, job=True,
                        tenant=tenant, workspace=workspace) == 0
        assert client.get(f"/api/v1/documents/{shared['id']}", headers=who("alice", tenant=tenant, workspace=workspace)).status_code == 404
    # A forged write of someone else's private row is refused by the policy's check.
    with pytest.raises(Exception, match="row-level security"), database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'mallory', true), set_config('anum.vault', 'on', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        connection.execute(text("insert into vault_documents (tenant_id, workspace_id, id, file_id, owner_user_id, title, category, content_sha256, "
                                "created_by, updated_by) values (:t, :w, 'vdoc_forged', :f, 'alice', 'x', 'other', :s, 'mallory', 'mallory')"),
                           {"t": TENANT_A, "w": WORKSPACE_A, "f": "file_x", "s": "0" * 64})


def test_duplicates_versions_trash_and_deletion_in_postgres(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    first = add(client, category="receipt")
    again = client.post("/api/v1/documents", params={"name": "doc.pdf", "category": "receipt"}, headers=ALICE, content=PDF)
    assert again.status_code == 409
    newer = client.post(f"/api/v1/documents/{first['id']}/versions", headers=ALICE, content=PDF + b"%v2")
    assert newer.status_code == 201, newer.text
    assert client.get(f"/api/v1/documents/{first['id']}/versions", headers=ALICE).json()["total"] == 1
    patched = client.patch(f"/api/v1/documents/{first['id']}", headers=ALICE, json={"row_version": newer.json()["row_version"], "number": "AB-123456"})
    assert patched.status_code == 200 and patched.json()["number_masked"] == "•••• 3456"
    with database_engine.connect() as connection:
        stored = connection.execute(text("select number_ciphertext, number_last4 from vault_documents")).one()
    assert stored.number_last4 == "3456" and "123456" not in stored.number_ciphertext
    assert client.delete(f"/api/v1/documents/{first['id']}", headers=ALICE).json()["status"] == "deleted"
    assert client.post(f"/api/v1/documents/{first['id']}/restore", headers=ALICE).json()["status"] == "active"
    client.delete(f"/api/v1/documents/{first['id']}", headers=ALICE)
    assert client.delete(f"/api/v1/documents/{first['id']}", headers=ALICE, params={"permanent": "true"}).status_code == 204
    assert count_as(database_engine, "select count(*) from workspace_files", vault=True) == 0
    assert count_as(database_engine, "select count(*) from workspace_file_versions") == 0
    assert count_as(database_engine, "select count(*) from vault_access_log", job=True, vault=True) == 0
    assert not file_store.storage.objects  # type: ignore[attr-defined]


def test_the_daily_job_reminds_once_and_purges_the_trash(client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(jobs, "PAGE_SIZE", 1)
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role) values (:t, :w, 'alice', 'member')"),
                           {"t": TENANT_A, "w": WORKSPACE_A})
    created = add(client, category="visa", title="Visa")
    soon = (date.today() + timedelta(days=1)).isoformat()
    client.patch(f"/api/v1/documents/{created['id']}", headers=ALICE, json={"row_version": 1, "expiry_date": soon})
    expired = add(client, PDF + b"%x", category="visa", title="Old visa")
    client.patch(f"/api/v1/documents/{expired['id']}", headers=ALICE, json={"row_version": 1, "expiry_date": (date.today() - timedelta(days=5)).isoformat()})
    later = add(client, PDF + b"%y", category="visa", title="Visa 2")
    client.patch(f"/api/v1/documents/{later['id']}", headers=ALICE, json={"row_version": 1, "expiry_date": (date.today() + timedelta(days=2)).isoformat()})
    first = jobs.run()
    # Pages of one document: the expired one is skipped before the limit, both due ones are reminded.
    assert first["reminders"] == 2 and first["failed"] == 0
    assert jobs.run()["reminders"] == 0
    assert count_as(database_engine, "select count(*) from user_notifications where kind = 'document_expiry'", user="alice") == 2
    trashed = add(client, PDF + b"%t", category="other")
    client.delete(f"/api/v1/documents/{trashed['id']}", headers=ALICE)
    from anum_api.schemas import utc_now

    assert jobs.run(now=utc_now() + timedelta(days=31))["purged"] == 1
    assert count_as(database_engine, "select count(*) from vault_documents", job=True, vault=True) == 3


def test_numbers_are_rotated_with_the_secrets_key(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from anum_api import secret_box
    from anum_api.rotate_secrets import rotate_vault_numbers
    from anum_api.secret_box import SecretCipher
    from anum_api.settings import settings

    created = add(client, category="national_id")
    client.patch(f"/api/v1/documents/{created['id']}", headers=ALICE, json={"row_version": 1, "number": "784-1990-1234567-1"})
    raw = settings.secrets_key.get_secret_value() if settings.secrets_key else ""
    old_keys = [key.strip().encode() for key in raw.split(",") if key.strip()] or [
        base64.urlsafe_b64encode(hashlib.sha256(secret_box._LOCAL_DEV_KEY_MATERIAL).digest())]
    new_key = Fernet.generate_key()
    report = rotate_vault_numbers(SecretCipher([new_key, *old_keys]))
    assert report.rotated == 1 and report.failed == 0
    with database_engine.connect() as connection:
        token = connection.execute(text("select number_ciphertext from vault_documents")).scalar_one()
    assert SecretCipher([new_key]).decrypt(token) == "784199012345671"


def test_assistant_readable_documents_survive_a_retrieval_reindex_and_follow_new_versions(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    doc = client.post("/api/v1/documents", params={"name": "lease.txt", "category": "contract", "access_class": "workspace"}, headers=ALICE,
                      content=b"Lease terms: rent is due on the first.").json()
    opened = client.patch(f"/api/v1/documents/{doc['id']}", headers=ALICE, json={"row_version": 1, "assistant_may_read": True})
    assert opened.status_code == 200, opened.text
    sources = "select count(*) from retrieval_sources where source_type = 'file'"
    assert count_as(database_engine, sources) == 1
    assert client.post("/api/v1/retrieval/index", headers=ALICE).status_code == 200
    assert count_as(database_engine, sources) == 1
    assert client.get("/api/v1/files", headers=OWNER).json() == []
    assert client.post(f"/api/v1/documents/{doc['id']}/versions", headers=ALICE, content=b"Lease terms: rent is due on the fifth.").status_code == 201
    current = count_as(database_engine, "select count(*) from retrieval_sources s join workspace_files f on f.tenant_id = s.tenant_id "
                       "and f.workspace_id = s.workspace_id and f.id = s.source_id where s.content_sha256 = f.sha256")
    assert current == 1
