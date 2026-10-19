"""File versions in PostgreSQL (migration 0084_file_versions): versions persist, count against
the quota under the workspace lock, stay inside their workspace under forced RLS, and go with
their file, all as the non-owner app role."""

from __future__ import annotations

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api import files as files_module
from anum_api.files import open_file_metadata_store
from anum_api.settings import settings

pytestmark = pytest.mark.database


def as_role(role: str, user: str = "user_test", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = as_role("owner")
MEMBER = as_role("member", "member_1")


def _upload(client: TestClient, name: str, size: int) -> dict:  # noqa: F811 - the fixture, passed in
    response = client.post("/api/v1/files", params={"name": name}, headers=MEMBER, content=b"x" * size)
    assert response.status_code == 201, response.text
    return response.json()


def test_versions_persist_count_against_the_quota_and_stay_inside_their_workspace(  # noqa: F811
    client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "files_workspace_quota_bytes", 100)
    file_id = _upload(client, "plan.txt", 30)["id"]

    def version(content: bytes):
        return client.post(f"/api/v1/files/{file_id}/versions", headers={**MEMBER, "content-type": "text/plain"}, content=content)

    assert version(b"b" * 40).status_code == 201
    over = version(b"c" * 40)
    assert over.status_code == 413 and "storage quota" in over.json()["error"]["message"]
    page = client.get(f"/api/v1/files/{file_id}/versions", headers=MEMBER).json()
    assert page["file"]["version"] == 2 and [item["version"] for item in page["items"]] == [1]
    first = page["items"][0]

    # A restore shares the bytes (no extra space) and the current bytes become an older version.
    restored = client.post(f"/api/v1/files/{file_id}/versions/{first['id']}/restore", headers=MEMBER)
    assert restored.status_code == 200 and restored.json()["version"] == 3
    usage = client.get("/api/v1/files/usage", headers=OWNER).json()
    assert (usage["used_bytes"], usage["version_bytes"], usage["version_count"]) == (70, 40, 2)
    report = client.get("/api/v1/files/storage", headers=OWNER).json()
    assert report["largest"][0]["total_bytes"] == 70 and report["largest"][0]["version_count"] == 2
    assert report["by_kind"][0]["kind"] == "text" and report["by_kind"][0]["bytes"] == 30

    # Another tenant sees nothing of it (RLS, and the scoped foreign key).
    other = as_role("owner", "owner_b", TENANT_B, WORKSPACE_B)
    assert client.get(f"/api/v1/files/{file_id}/versions", headers=other).status_code == 404
    assert client.get("/api/v1/files/usage", headers=other).json()["used_bytes"] == 0
    with database_engine.connect() as connection:
        rls = connection.execute(text(
            "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'workspace_file_versions'"
        )).one()
        assert tuple(rls) == (True, True)

    # Deleting the file takes its versions with it (cascade) and frees the space and the bytes.
    assert client.delete(f"/api/v1/files/{file_id}", headers=MEMBER).status_code == 204
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from workspace_file_versions")).scalar_one() == 0
    assert client.get("/api/v1/files/usage", headers=OWNER).json()["used_bytes"] == 0
    assert files_module.file_store.storage.objects == {}  # type: ignore[attr-defined]


def test_a_stale_replace_is_refused_and_keeps_the_winner(client: TestClient) -> None:  # noqa: F811
    created = _upload(client, "race.txt", 10)
    context = tenant_context()
    winner = client.post(
        f"/api/v1/files/{created['id']}/versions", headers={**MEMBER, "content-type": "text/plain"}, content=b"w" * 11
    )
    assert winner.status_code == 201
    content = files_module.FileContent(
        content_type="text/plain", size_bytes=12, sha256="1" * 64, storage_key="tenants/x/workspaces/y/files/race/1",
        created_by="member_1", created_at=files_module.utc_now(),
    )
    with pytest.raises(files_module.FileContentChanged), open_file_metadata_store(context) as store:
        store.replace_content(context, created["id"], created["sha256"], content, 1000)
    with open_file_metadata_store(context) as store:
        current = store.get(context, created["id"])
        assert current is not None and current.version == 2
        assert store.list_versions(context, created["id"], 10, 0)[1] == 1
