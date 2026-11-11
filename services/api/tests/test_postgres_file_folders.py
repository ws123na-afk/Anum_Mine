"""Folders, tags and bulk actions in PostgreSQL (migration 0168_files_folders_tags): forced RLS on
the three new tables, the unique indexes answering 409 without breaking the unit of work, the
cascades, paging within a folder and by tag, bulk changes as all or nothing, the zip download and
the audit rows, all as the non-owner app role."""

from __future__ import annotations

import io
import zipfile

import pytest
from conftest import TENANT_B, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

pytestmark = pytest.mark.database
TABLES = ("workspace_file_folders", "workspace_file_tags", "workspace_file_tag_links")


def as_role(role: str, user: str = "user_test", tenant: str = "tenant_test_a", workspace: str = "workspace_test_a") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = as_role("owner")
MEMBER = as_role("member", "member_1")
OTHER = as_role("member", "member_2")
ELSEWHERE = as_role("owner", "owner_b", TENANT_B, WORKSPACE_B)
NEXT_DOOR = as_role("owner", "user_test", workspace=WORKSPACE_A2)


def _upload(client: TestClient, name: str, size: int = 3, headers: dict[str, str] = MEMBER, folder_id: str | None = None) -> dict:  # noqa: F811
    params = {"name": name, **({"folder_id": folder_id} if folder_id else {})}
    response = client.post("/api/v1/files", params=params, headers=headers, content=b"x" * size)
    assert response.status_code == 201, response.text
    return response.json()


def _folder(client: TestClient, name: str, parent_id: str | None = None, headers: dict[str, str] = MEMBER) -> dict:  # noqa: F811
    response = client.post("/api/v1/files/folders", headers=headers, json={"name": name, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def _tag(client: TestClient, name: str, headers: dict[str, str] = MEMBER) -> dict:  # noqa: F811
    response = client.post("/api/v1/files/tags", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _names(client: TestClient, headers: dict[str, str] = MEMBER, **params: object) -> list[str]:  # noqa: F811
    response = client.get("/api/v1/files", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()]


def _count(engine: Engine, sql: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(sql)).scalar_one())


def test_new_tables_have_forced_rls_and_scope_every_read(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    with database_engine.connect() as connection:
        for table in TABLES:
            rls = connection.execute(
                text("select relrowsecurity, relforcerowsecurity from pg_class where relname = :t"), {"t": table}
            ).one()
            assert tuple(rls) == (True, True), table
            policies = {
                row[0] for row in connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).all()
            }
            assert policies == {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"}, table
    contracts = _folder(client, "Contracts")
    urgent = _tag(client, "Urgent")
    inside = _upload(client, "inside.txt", folder_id=contracts["id"])
    client.post(f"/api/v1/files/{inside['id']}/tags", headers=MEMBER, json={"tag_ids": [urgent["id"]]})
    # The other workspace of the same tenant and another tenant see no folder, tag or link.
    for headers in (ELSEWHERE, NEXT_DOOR):
        assert client.get("/api/v1/files/folders", headers=headers).json()["items"] == []
        assert client.get("/api/v1/files/tags", headers=headers).json()["items"] == []
        assert client.patch(f"/api/v1/files/folders/{contracts['id']}", headers=headers, json={"name": "x"}).status_code == 404
        assert client.delete(f"/api/v1/files/tags/{urgent['id']}", headers=headers).status_code == 404
        assert client.post("/api/v1/files/bulk/move", headers=headers, json={"file_ids": [inside["id"]], "folder_id": None}).status_code == 404
        # A folder or tag of another scope cannot be named: the scoped foreign keys and the row policy both refuse.
        assert client.post("/api/v1/files", params={"name": "a.txt", "folder_id": contracts["id"]}, headers=headers, content=b"x").status_code == 404
    assert _count(database_engine, "select count(*) from workspace_file_tag_links") == 1
    listed = client.get("/api/v1/files", headers=OWNER, params={"folder": contracts["id"]}).json()
    assert listed[0]["folder_path"] == "Contracts" and listed[0]["tags"] == [{"id": urgent["id"], "name": "Urgent"}]


def test_unique_indexes_answer_409_and_the_unit_of_work_goes_on(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    top = _folder(client, "Top")
    _folder(client, "Child", top["id"])
    assert client.post("/api/v1/files/folders", headers=OTHER, json={"name": "CHILD", "parent_id": top["id"]}).status_code == 409
    assert client.post("/api/v1/files/folders", headers=OTHER, json={"name": "Child"}).status_code == 201, "the same name at another level"
    assert client.post("/api/v1/files/folders", headers=OTHER, json={"name": "top"}).status_code == 409
    _tag(client, "Paid")
    assert client.post("/api/v1/files/tags", headers=OTHER, json={"name": "paid"}).status_code == 409
    other = _tag(client, "Other")
    assert client.patch(f"/api/v1/files/tags/{other['id']}", headers=MEMBER, json={"name": "PAID"}).status_code == 409
    # A refused insert left nothing behind and the next write works.
    assert _count(database_engine, "select count(*) from workspace_file_folders") == 3
    assert _count(database_engine, "select count(*) from workspace_file_tags") == 2
    assert client.post("/api/v1/files/tags", headers=OTHER, json={"name": "Due"}).status_code == 201


def test_folder_tree_paging_and_cascades_in_postgres(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    a = _folder(client, "A")
    b = _folder(client, "B", a["id"])
    c = _folder(client, "C", b["id"])
    for i in range(5):
        _upload(client, f"f{i}.txt", folder_id=c["id"])
    _upload(client, "root.txt")
    moved = client.patch(f"/api/v1/files/folders/{c['id']}", headers=MEMBER, json={"parent_id": None, "name": "Sea"})
    assert moved.status_code == 200 and moved.json()["path"] == "Sea" and moved.json()["file_count"] == 5
    assert client.patch(f"/api/v1/files/folders/{a['id']}", headers=MEMBER, json={"parent_id": b["id"]}).status_code == 409
    # Keyset paging within a folder.
    first = client.get("/api/v1/files", headers=MEMBER, params={"folder": c["id"], "sort": "name", "limit": 2}).json()
    second = client.get("/api/v1/files", headers=MEMBER, params={"folder": c["id"], "sort": "name", "limit": 2, "cursor": first[-1]["id"]}).json()
    third = client.get("/api/v1/files", headers=MEMBER, params={"folder": c["id"], "sort": "name", "limit": 2, "cursor": second[-1]["id"]}).json()
    assert [f["name"] for f in first + second + third] == ["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt"]
    assert _names(client, folder="root") == ["root.txt"]
    assert _names(client, folder=c["id"], q="f3") == ["f3.txt"]
    # Deleting a parent cascades to subfolders; the files inside the deleted tree go to the root (SET NULL),
    # so no row ever points at a missing folder.
    assert client.delete(f"/api/v1/files/folders/{a['id']}", headers=MEMBER, params={"contents": "move_up"}).status_code == 204
    assert {f["name"] for f in client.get("/api/v1/files/folders", headers=MEMBER).json()["items"]} == {"B", "Sea"}
    with database_engine.begin() as connection:
        connection.execute(text("set local role anum_test_app"))
        connection.execute(text("select set_config('anum.tenant_id', 'tenant_test_a', true)"))
        connection.execute(text("select set_config('anum.workspace_id', 'workspace_test_a', true)"))
        connection.execute(text("delete from workspace_file_folders where name = 'Sea'"))
    assert _count(database_engine, "select count(*) from workspace_files where folder_id is not null") == 0
    assert len(_names(client, folder="root")) == 6
    assert _count(database_engine, "select count(*) from workspace_files where folder_id not in (select id from workspace_file_folders)") == 0


def test_bulk_changes_are_all_or_nothing_and_audited(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    target = _folder(client, "Target")
    tag = _tag(client, "T")
    mine = [_upload(client, f"m{i}.txt") for i in range(3)]
    theirs = _upload(client, "theirs.txt", headers=OTHER)
    ids = [f["id"] for f in mine] + [theirs["id"]]
    # One foreign file refuses the whole move, delete and tag-free check: no row changed.
    assert client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": ids, "folder_id": target["id"]}).status_code == 403
    assert _count(database_engine, "select count(*) from workspace_files where folder_id is not null") == 0
    assert client.post("/api/v1/files/bulk/delete", headers=MEMBER, json={"file_ids": ids}).status_code == 403
    assert _count(database_engine, "select count(*) from workspace_files") == 4
    gone = _upload(client, "gone.txt")
    client.delete(f"/api/v1/files/{gone['id']}", headers=MEMBER)
    assert client.post("/api/v1/files/bulk/tag", headers=MEMBER, json={"file_ids": ids + [gone["id"]], "tag_ids": [tag["id"]]}).status_code == 404
    assert _count(database_engine, "select count(*) from workspace_file_tag_links") == 0
    # The owner moves, tags and deletes everyone's files; the audit rows sit in the same database.
    assert client.post("/api/v1/files/bulk/move", headers=OWNER, json={"file_ids": ids, "folder_id": target["id"]}).json() == {"count": 4}
    assert client.post("/api/v1/files/bulk/tag", headers=OWNER, json={"file_ids": ids, "tag_ids": [tag["id"]]}).json() == {"count": 4}
    assert client.get("/api/v1/files/tags", headers=MEMBER).json()["items"][0]["file_count"] == 4
    assert client.post("/api/v1/files/bulk/untag", headers=MEMBER, json={"file_ids": ids[:1], "tag_ids": [tag["id"]]}).json() == {"count": 1}
    assert client.post("/api/v1/files/bulk/delete", headers=OWNER, json={"file_ids": ids[:2]}).json() == {"count": 2}
    assert _count(database_engine, "select count(*) from workspace_files") == 2
    assert _count(database_engine, "select count(*) from workspace_file_tag_links") == 2, "deleted files take their links"
    assert client.get("/api/v1/files/folders", headers=MEMBER).json()["items"][0]["file_count"] == 2
    with database_engine.connect() as connection:
        actions = connection.execute(
            text("select action, actor from audit_records where tenant_id = 'tenant_test_a' and action like 'file.bulk_%' order by created_at")
        ).all()
    assert [tuple(row) for row in actions] == [
        ("file.bulk_moved", "user_test"), ("file.bulk_tagged", "user_test"), ("file.bulk_untagged", "member_1"), ("file.bulk_deleted", "user_test"),
    ]
    # Deleting the tag removes the remaining links.
    assert client.delete(f"/api/v1/files/tags/{tag['id']}", headers=OWNER).status_code == 204
    assert _count(database_engine, "select count(*) from workspace_file_tag_links") == 0


def test_bulk_download_zips_files_from_postgres_metadata(client: TestClient) -> None:  # noqa: F811
    folder = _folder(client, "Reports")
    sub = _folder(client, "Q1", folder["id"])
    a = _upload(client, "a.txt", 10, folder_id=sub["id"])
    b = _upload(client, "b.txt", 20)
    response = client.post("/api/v1/files/bulk/download", headers=as_role("viewer", "viewer_1"), json={"file_ids": [a["id"], b["id"]]})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["Reports/Q1/a.txt", "b.txt"]
        assert archive.read("Reports/Q1/a.txt") == b"x" * 10
    assert client.post("/api/v1/files/bulk/download", headers=ELSEWHERE, json={"file_ids": [a["id"]]}).status_code == 404
