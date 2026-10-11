"""Workspace files in PostgreSQL (migration 0029_file_management): keyset paging, search,
rename columns and the quota lock, all as the non-owner app role under RLS."""

from __future__ import annotations

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api import files as files_module
from anum_api.files import FilePageQuery, FileSort, open_file_metadata_store
from anum_api.settings import settings

pytestmark = pytest.mark.database


def as_role(role: str, user: str = "user_test", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = as_role("owner")
MEMBER = as_role("member", "member_1")


def _upload(client: TestClient,  # noqa: F811 - the fixture, passed in
            name: str, size: int, headers: dict[str, str] = MEMBER) -> dict:
    response = client.post("/api/v1/files", params={"name": name}, headers=headers, content=b"x" * size)
    assert response.status_code == 201, response.text
    return response.json()


def test_keyset_pages_search_and_sorts_match_in_postgres(client: TestClient) -> None:  # noqa: F811
    for name, size in (("beta.txt", 30), ("Alpha.txt", 10), ("gamma.csv", 20), ("delta.txt", 40), ("alpha_2.md", 5)):
        _upload(client, name, size)
    _upload(client, "elsewhere.txt", 1, as_role("member", "member_1", workspace=WORKSPACE_A2))
    _upload(client, "foreign.txt", 1, as_role("member", "member_1", TENANT_B, WORKSPACE_B))

    def names(**params: object) -> list[str]:
        response = client.get("/api/v1/files", headers=OWNER, params=params)
        assert response.status_code == 200, response.text
        return [item["name"] for item in response.json()]

    assert names() == ["alpha_2.md", "delta.txt", "gamma.csv", "Alpha.txt", "beta.txt"]
    # Bytewise ("C") order of the lower-cased name: "." sorts before "_".
    assert names(sort="name") == ["Alpha.txt", "alpha_2.md", "beta.txt", "delta.txt", "gamma.csv"]
    assert names(sort="size", order="asc") == ["alpha_2.md", "Alpha.txt", "gamma.csv", "beta.txt", "delta.txt"]
    assert names(q="ALPHA") == ["alpha_2.md", "Alpha.txt"]
    # LIKE wildcards in the search are literal.
    assert names(q="a_2") == ["alpha_2.md"] and names(q="%") == [] and names(q="_") == ["alpha_2.md"]

    for sort, order in (("created_at", "desc"), ("name", "asc"), ("name", "desc"), ("size", "desc"), ("size", "asc")):
        expected = names(sort=sort, order=order)
        paged: list[str] = []
        cursor = None
        while True:
            params = {"sort": sort, "order": order, "limit": 2, **({"cursor": cursor} if cursor else {})}
            items = client.get("/api/v1/files", headers=OWNER, params=params).json()
            paged += [item["name"] for item in items]
            if len(items) < 2:
                break
            cursor = items[-1]["id"]
        assert paged == expected, (sort, order)

    usage = client.get("/api/v1/files/usage", headers=OWNER).json()
    assert (usage["used_bytes"], usage["file_count"]) == (105, 5)


def test_rename_persists_who_and_when(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    record = _upload(client, "draft.txt", 3)
    renamed = client.patch(f"/api/v1/files/{record['id']}", headers=MEMBER, json={"name": "final.txt"})
    assert renamed.status_code == 200, renamed.text
    files_module.file_store.records.clear()
    with open_file_metadata_store(tenant_context()) as store:
        stored = store.get(tenant_context(), record["id"])
    assert stored is not None and stored.name == "final.txt" and stored.updated_by == "member_1"
    assert stored.updated_at is not None and stored.created_by == "member_1"
    other = as_role("owner", "owner_b", TENANT_B, WORKSPACE_B)
    assert client.patch(f"/api/v1/files/{record['id']}", headers=other, json={"name": "x.txt"}).status_code == 404
    with database_engine.connect() as connection:
        indexes = set(connection.execute(text(
            "select indexname from pg_indexes where tablename = 'workspace_files'"
        )).scalars())
    assert {"ix_workspace_files_scope_name", "ix_workspace_files_scope_size"} <= indexes


def test_the_quota_holds_under_the_workspace_lock(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(settings, "files_workspace_quota_bytes", 20)
    _upload(client, "a.txt", 15)
    context = tenant_context()
    record = files_module.FileRecord(
        id="file_race", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, name="b.txt", content_type="text/plain",
        size_bytes=10, sha256="0" * 64, storage_key="tenants/x/workspaces/y/files/file_race/0", created_by="u",
        created_at=files_module.utc_now(),
    )
    with pytest.raises(files_module.StorageQuotaExceeded) as refused, open_file_metadata_store(context) as store:
        store.add(record, 20)
    assert refused.value.usage.used_bytes == 15
    with open_file_metadata_store(context) as store:
        assert [r.name for r in store.list_page(context, FilePageQuery(sort=FileSort.NAME, descending=False))] == ["a.txt"]
    response = client.post("/api/v1/files", params={"name": "c.txt"}, headers=MEMBER, content=b"y" * 6)
    assert response.status_code == 413 and "storage quota" in response.json()["error"]["message"]
    assert len(files_module.file_store.storage.objects) == 1  # type: ignore[attr-defined]
