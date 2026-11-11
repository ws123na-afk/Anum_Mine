"""Folders, tags and bulk actions for workspace files (docs/files.md#folders, #tags, #bulk-actions):
the folder tree and its rules, listing by folder and tag, tags on files, the bulk endpoints as
all-or-nothing changes, the zip download, permissions per role and the audit records."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import file_policy, files
from anum_api.dependencies import memory_repository
from anum_api.files import FileFolderRecord, file_store, folder_depth, folder_descendants, folder_paths
from anum_api.files_organize import BULK_MAX_FILES, MAX_FOLDER_DEPTH, archive_entry_name
from anum_api.main import app
from anum_api.retrieval import memory_retrieval_store
from anum_api.schemas import utc_now

client = TestClient(app)
TENANT, WORKSPACE = "tenant_folders", "workspace_folders"


def who(user: str, role: str, workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = who("owner_1", "owner")
MEMBER = who("member_1", "member")
OTHER_MEMBER = who("member_2", "member")
VIEWER = who("viewer_1", "viewer")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    memory_retrieval_store.clear()
    memory_repository.store.audit_records.clear()
    file_policy.set_file_scanner(None)
    yield
    file_store.clear()
    memory_retrieval_store.clear()
    memory_repository.store.audit_records.clear()


def message(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["error"]["message"]


def upload(name: str, content: bytes = b"hello", headers: dict[str, str] = MEMBER, folder_id: str | None = None) -> dict:
    params = {"name": name, **({"folder_id": folder_id} if folder_id else {})}
    response = client.post("/api/v1/files", params=params, headers=headers, content=content)
    assert response.status_code == 201, response.text
    return response.json()


def folder(name: str, parent_id: str | None = None, headers: dict[str, str] = MEMBER) -> dict:
    response = client.post("/api/v1/files/folders", headers=headers, json={"name": name, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def tag(name: str, headers: dict[str, str] = MEMBER) -> dict:
    response = client.post("/api/v1/files/tags", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def names(headers: dict[str, str] = MEMBER, **params: object) -> list[str]:
    response = client.get("/api/v1/files", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return sorted(item["name"] for item in response.json())


def audits(action: str) -> list:
    return [r for r in memory_repository.store.audit_records if r.action == action]


# --------------------------------------------------------------------------- the tree helpers


def test_folder_paths_depths_and_descendants_follow_parent_links() -> None:
    now = utc_now()

    def rec(id_: str, name: str, parent: str | None) -> FileFolderRecord:
        return FileFolderRecord(id=id_, tenant_id="t", workspace_id="w", name=name, parent_id=parent, created_by="u", created_at=now)

    tree = [rec("a", "Contracts", None), rec("b", "2026", "a"), rec("c", "Q1", "b"), rec("d", "Photos", None), rec("x", "Orphan", "missing")]
    assert folder_paths(tree) == {"a": "Contracts", "b": "Contracts / 2026", "c": "Contracts / 2026 / Q1", "d": "Photos", "x": "Orphan"}
    assert folder_depth(tree, None) == 0 and folder_depth(tree, "a") == 1 and folder_depth(tree, "c") == 3
    assert folder_descendants(tree, "a") == ["b", "c"] and folder_descendants(tree, "d") == []
    # A cycle never loops: each folder reads from itself.
    loop = [rec("p", "P", "q"), rec("q", "Q", "p")]
    assert folder_paths(loop) == {"p": "Q / P", "q": "P / Q"} or folder_paths(loop)["p"].endswith("P")
    assert folder_depth(loop, "p") == 2


def test_archive_entry_names_use_folders_as_directories_and_number_duplicates() -> None:
    used: set[str] = set()
    assert archive_entry_name(None, "notes.txt", used) == "notes.txt"
    assert archive_entry_name(None, "Notes.TXT", used) == "Notes (2).TXT"
    assert archive_entry_name("Contracts / 2026", "notes.txt", used) == "Contracts/2026/notes.txt"
    assert archive_entry_name("Contracts / 2026", "notes.txt", used) == "Contracts/2026/notes (2).txt"
    assert archive_entry_name(None, "README", used) == "README" and archive_entry_name(None, "README", used) == "README (2)"
    assert archive_entry_name(None, ".env", used) == ".env" and archive_entry_name(None, ".env", used) == ".env (2)"


# --------------------------------------------------------------------------- folders


def test_folders_form_a_tree_with_paths_counts_and_unique_names_per_level() -> None:
    contracts = folder("Contracts")
    year = folder("2026", contracts["id"])
    assert year["path"] == "Contracts / 2026" and year["depth"] == 2 and year["can_manage"] is True
    upload("a.txt", folder_id=year["id"])
    upload("b.txt", folder_id=year["id"])
    upload("root.txt")

    tree = client.get("/api/v1/files/folders", headers=VIEWER)
    assert tree.status_code == 200, tree.text
    by_name = {item["name"]: item for item in tree.json()["items"]}
    assert by_name["2026"]["file_count"] == 2 and by_name["Contracts"]["file_count"] == 0
    assert by_name["2026"]["can_manage"] is False, "a viewer manages nothing"
    assert tree.json()["max_depth"] == MAX_FOLDER_DEPTH

    # Two folders at one level never share a name, whatever the case; the same name elsewhere is fine.
    taken = client.post("/api/v1/files/folders", headers=MEMBER, json={"name": "contracts"})
    assert taken.status_code == 409 and "already exists here" in message(taken)
    assert client.post("/api/v1/files/folders", headers=MEMBER, json={"name": "Contracts", "parent_id": year["id"]}).status_code == 201
    # Names follow the file-name rules.
    assert client.post("/api/v1/files/folders", headers=MEMBER, json={"name": "a/b"}).status_code == 422
    assert client.post("/api/v1/files/folders", headers=MEMBER, json={"name": "x", "parent_id": "folder_missing"}).status_code == 404

    # Files list by folder, at the root, or everywhere.
    assert names(folder=year["id"]) == ["a.txt", "b.txt"]
    assert names(folder="root") == ["root.txt"]
    assert names() == ["a.txt", "b.txt", "root.txt"]
    assert client.get("/api/v1/files", headers=MEMBER, params={"folder": "folder_missing"}).status_code == 404
    listed = client.get("/api/v1/files", headers=MEMBER, params={"folder": year["id"]}).json()[0]
    assert listed["folder_id"] == year["id"] and listed["folder_path"] == "Contracts / 2026"
    # Search within a folder.
    assert names(folder=year["id"], q="a.") == ["a.txt"]
    # An upload into a missing folder stores nothing.
    refused = client.post("/api/v1/files", params={"name": "c.txt", "folder_id": "folder_missing"}, headers=MEMBER, content=b"x")
    assert refused.status_code == 404 and len(file_store.records) == 3


def test_folders_rename_move_depth_and_cycle_rules() -> None:
    a = folder("A")
    b = folder("B", a["id"])
    c = folder("C", b["id"])
    renamed = client.patch(f"/api/v1/files/folders/{b['id']}", headers=MEMBER, json={"name": "Bee"})
    assert renamed.status_code == 200 and renamed.json()["path"] == "A / Bee" and renamed.json()["updated_by"] == "member_1"
    # Moving into itself or below is refused; moving to the root works.
    assert client.patch(f"/api/v1/files/folders/{a['id']}", headers=MEMBER, json={"parent_id": c["id"]}).status_code == 409
    assert client.patch(f"/api/v1/files/folders/{a['id']}", headers=MEMBER, json={"parent_id": a["id"]}).status_code == 409
    moved = client.patch(f"/api/v1/files/folders/{c['id']}", headers=MEMBER, json={"parent_id": None})
    assert moved.status_code == 200 and moved.json()["parent_id"] is None and moved.json()["depth"] == 1
    # A sibling with the same name blocks a move.
    folder("C", a["id"])
    clash = client.patch(f"/api/v1/files/folders/{c['id']}", headers=MEMBER, json={"parent_id": a["id"]})
    assert clash.status_code == 409 and "already exists here" in message(clash)
    # The tree is at most MAX_FOLDER_DEPTH levels deep.
    parent = None
    for level in range(MAX_FOLDER_DEPTH):
        parent = folder(f"L{level}", parent)["id"]
    deep = client.post("/api/v1/files/folders", headers=MEMBER, json={"name": "too deep", "parent_id": parent})
    assert deep.status_code == 409 and "levels deep" in message(deep)
    # Moving a subtree counts its height.
    assert client.patch(f"/api/v1/files/folders/{a['id']}", headers=MEMBER, json={"parent_id": parent}).status_code == 409
    assert client.patch("/api/v1/files/folders/folder_missing", headers=MEMBER, json={"name": "x"}).status_code == 404


def test_deleting_a_folder_refuses_moves_up_or_deletes_its_contents_all_or_nothing() -> None:
    top = folder("Top")
    inner = folder("Inner", top["id"])
    kept = upload("kept.txt", folder_id=top["id"])
    deep = upload("deep.txt", folder_id=inner["id"])
    refused = client.delete(f"/api/v1/files/folders/{top['id']}", headers=MEMBER)
    assert refused.status_code == 409 and "holds 2 files and 1 subfolder" in message(refused)

    # move_up: the files and subfolders go to the parent (here the root).
    assert client.delete(f"/api/v1/files/folders/{top['id']}", headers=MEMBER, params={"contents": "move_up"}).status_code == 204
    assert client.get(f"/api/v1/files/{kept['id']}", headers=MEMBER).json()["folder_id"] is None
    tree = {f["name"]: f for f in client.get("/api/v1/files/folders", headers=MEMBER).json()["items"]}
    assert tree["Inner"]["parent_id"] is None and set(tree) == {"Inner"}
    assert client.get(f"/api/v1/files/{deep['id']}", headers=MEMBER).json()["folder_path"] == "Inner"
    assert audits("file.folder_deleted")[-1].metadata["contents"] == "move_up"

    # delete: someone else's file inside blocks the whole delete for a member; an owner may.
    other = upload("theirs.txt", headers=OTHER_MEMBER, folder_id=inner["id"])
    blocked = client.delete(f"/api/v1/files/folders/{inner['id']}", headers=MEMBER, params={"contents": "delete"})
    assert blocked.status_code == 403 and "Nothing was changed" in message(blocked)
    assert client.get(f"/api/v1/files/{other['id']}", headers=MEMBER).status_code == 200
    assert client.get(f"/api/v1/files/{deep['id']}", headers=MEMBER).status_code == 200
    done = client.delete(f"/api/v1/files/folders/{inner['id']}", headers=OWNER, params={"contents": "delete"})
    assert done.status_code == 204
    assert client.get(f"/api/v1/files/{other['id']}", headers=MEMBER).status_code == 404
    assert client.get(f"/api/v1/files/{deep['id']}", headers=MEMBER).status_code == 404
    assert set(file_store.storage.objects) == {file_store.records[kept["id"]].storage_key}, "the bytes of deleted files are gone"
    record = audits("file.folder_deleted")[-1]
    assert record.metadata["contents"] == "delete" and sorted(record.metadata["deleted_files"]) == sorted([other["id"], deep["id"]])
    assert record.actor == "owner_1"
    # An empty folder goes without a choice; a missing one is 404.
    empty = folder("Empty")
    assert client.delete(f"/api/v1/files/folders/{empty['id']}", headers=MEMBER).status_code == 204
    assert client.delete(f"/api/v1/files/folders/{empty['id']}", headers=MEMBER).status_code == 404


def test_folder_permissions_follow_the_file_rules() -> None:
    assert client.post("/api/v1/files/folders", headers=VIEWER, json={"name": "x"}).status_code == 403
    assert client.get("/api/v1/files/folders", headers=who("nobody", "guest")).status_code == 403
    mine = folder("Mine")
    theirs = client.patch(f"/api/v1/files/folders/{mine['id']}", headers=OTHER_MEMBER, json={"name": "Taken"})
    assert theirs.status_code == 403 and "created this folder" in message(theirs)
    assert client.delete(f"/api/v1/files/folders/{mine['id']}", headers=OTHER_MEMBER).status_code == 403
    # Owners manage any folder; another workspace sees nothing.
    assert client.patch(f"/api/v1/files/folders/{mine['id']}", headers=OWNER, json={"name": "Ours"}).status_code == 200
    elsewhere = who("member_1", "member", workspace="workspace_other")
    assert client.get("/api/v1/files/folders", headers=elsewhere).json()["items"] == []
    assert client.patch(f"/api/v1/files/folders/{mine['id']}", headers=elsewhere, json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/v1/files/folders/{mine['id']}", headers=OWNER).status_code == 204


# --------------------------------------------------------------------------- tags


def test_tags_are_unique_per_workspace_and_list_with_counts() -> None:
    urgent = tag("Urgent")
    assert urgent["file_count"] == 0 and urgent["can_manage"] is True
    dup = client.post("/api/v1/files/tags", headers=OTHER_MEMBER, json={"name": "urgent"})
    assert dup.status_code == 409 and "already exists" in message(dup)
    assert client.post("/api/v1/files/tags", headers=MEMBER, json={"name": "bad\x01name"}).status_code == 422
    assert client.post("/api/v1/files/tags", headers=MEMBER, json={"name": "x" * 65}).status_code == 422
    a = upload("a.txt")
    b = upload("b.txt", headers=OTHER_MEMBER)
    tagged = client.post(f"/api/v1/files/{a['id']}/tags", headers=MEMBER, json={"tag_ids": [urgent["id"]]})
    assert tagged.status_code == 200 and tagged.json()["tags"] == [{"id": urgent["id"], "name": "Urgent"}]
    # Any member tags any file; tagging twice is a no-op; a missing tag is 404.
    assert client.post(f"/api/v1/files/{b['id']}/tags", headers=MEMBER, json={"tag_ids": [urgent["id"], urgent["id"]]}).status_code == 200
    assert client.post(f"/api/v1/files/{b['id']}/tags", headers=MEMBER, json={"tag_ids": ["tag_missing"]}).status_code == 404
    assert client.post(f"/api/v1/files/{b['id']}/tags", headers=VIEWER, json={"tag_ids": [urgent["id"]]}).status_code == 403
    listed = client.get("/api/v1/files/tags", headers=VIEWER).json()["items"]
    assert [(t["name"], t["file_count"], t["can_manage"]) for t in listed] == [("Urgent", 2, False)]
    assert names(tag=urgent["id"]) == ["a.txt", "b.txt"]
    assert client.get("/api/v1/files", headers=MEMBER, params={"tag": "tag_missing"}).status_code == 404
    untagged = client.delete(f"/api/v1/files/{b['id']}/tags/{urgent['id']}", headers=OTHER_MEMBER)
    assert untagged.status_code == 200 and untagged.json()["tags"] == []
    assert names(tag=urgent["id"]) == ["a.txt"]
    # Rename and delete: the creator or an owner. Deleting a tag removes it from every file.
    assert client.patch(f"/api/v1/files/tags/{urgent['id']}", headers=OTHER_MEMBER, json={"name": "Hot"}).status_code == 403
    renamed = client.patch(f"/api/v1/files/tags/{urgent['id']}", headers=MEMBER, json={"name": "Hot"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Hot" and renamed.json()["file_count"] == 1
    tag("Cold")
    assert client.patch(f"/api/v1/files/tags/{urgent['id']}", headers=MEMBER, json={"name": "cold"}).status_code == 409
    assert client.delete(f"/api/v1/files/tags/{urgent['id']}", headers=OTHER_MEMBER).status_code == 403
    assert client.delete(f"/api/v1/files/tags/{urgent['id']}", headers=OWNER).status_code == 204
    assert client.get(f"/api/v1/files/{a['id']}", headers=MEMBER).json()["tags"] == []
    assert audits("file.tag_deleted")[-1].metadata == {"name": "Hot", "files": 1}
    assert client.delete(f"/api/v1/files/tags/{urgent['id']}", headers=OWNER).status_code == 404


def test_deleting_a_file_drops_its_tag_links() -> None:
    t = tag("T")
    a = upload("a.txt")
    client.post(f"/api/v1/files/{a['id']}/tags", headers=MEMBER, json={"tag_ids": [t["id"]]})
    assert client.delete(f"/api/v1/files/{a['id']}", headers=MEMBER).status_code == 204
    assert client.get("/api/v1/files/tags", headers=MEMBER).json()["items"][0]["file_count"] == 0
    assert not file_store.tag_links


# --------------------------------------------------------------------------- bulk actions


def test_bulk_move_is_all_or_nothing_and_audited() -> None:
    target = folder("Target")
    a, b = upload("a.txt"), upload("b.txt")
    theirs = upload("theirs.txt", headers=OTHER_MEMBER)
    gone = upload("gone.txt")
    client.delete(f"/api/v1/files/{gone['id']}", headers=MEMBER)

    missing = client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": [a["id"], gone["id"]], "folder_id": target["id"]})
    assert missing.status_code == 404 and "1 of the 2 selected files no longer exist" in message(missing)
    refused = client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": [a["id"], theirs["id"]], "folder_id": target["id"]})
    assert refused.status_code == 403 and "1 of the 2 selected files were uploaded by someone else" in message(refused)
    assert client.get(f"/api/v1/files/{a['id']}", headers=MEMBER).json()["folder_id"] is None, "nothing moved"
    assert client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": [a["id"]], "folder_id": "folder_missing"}).status_code == 404
    assert client.post("/api/v1/files/bulk/move", headers=VIEWER, json={"file_ids": [a["id"]], "folder_id": target["id"]}).status_code == 403
    assert client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": []}).status_code == 422
    too_many = client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": [f"f{i}" for i in range(BULK_MAX_FILES + 1)]})
    assert too_many.status_code == 422

    moved = client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": [a["id"], b["id"], a["id"]], "folder_id": target["id"]})
    assert moved.status_code == 200 and moved.json() == {"count": 2}
    assert names(folder=target["id"]) == ["a.txt", "b.txt"]
    # An owner moves anyone's file; back to the root with null.
    back = client.post("/api/v1/files/bulk/move", headers=OWNER, json={"file_ids": [a["id"], theirs["id"]], "folder_id": None})
    assert back.status_code == 200 and back.json() == {"count": 1}
    record = audits("file.bulk_moved")[-1]
    assert record.target == "root" and record.actor == "owner_1" and record.metadata["moved"] == 1


def test_bulk_delete_removes_rows_bytes_and_tags_all_or_nothing() -> None:
    t = tag("T")
    a, b = upload("a.txt"), upload("b.png", PNG)
    theirs = upload("theirs.txt", headers=OTHER_MEMBER)
    client.post("/api/v1/files/bulk/tag", headers=MEMBER, json={"file_ids": [a["id"], b["id"]], "tag_ids": [t["id"]]})
    refused = client.post("/api/v1/files/bulk/delete", headers=MEMBER, json={"file_ids": [a["id"], theirs["id"]]})
    assert refused.status_code == 403 and len(file_store.records) == 3
    assert client.post("/api/v1/files/bulk/delete", headers=VIEWER, json={"file_ids": [a["id"]]}).status_code == 403
    done = client.post("/api/v1/files/bulk/delete", headers=MEMBER, json={"file_ids": [a["id"], b["id"]]})
    assert done.status_code == 200 and done.json() == {"count": 2}
    assert names() == ["theirs.txt"]
    assert set(file_store.storage.objects) == {file_store.records[theirs["id"]].storage_key}
    assert client.get("/api/v1/files/tags", headers=MEMBER).json()["items"][0]["file_count"] == 0
    record = audits("file.bulk_deleted")[-1]
    assert sorted(record.metadata["names"]) == ["a.txt", "b.png"] and record.outcome == "success"


def test_bulk_tag_and_untag_count_links_and_check_every_tag_and_file() -> None:
    t1, t2 = tag("One"), tag("Two")
    a, b = upload("a.txt"), upload("b.txt", headers=OTHER_MEMBER)
    added = client.post("/api/v1/files/bulk/tag", headers=MEMBER, json={"file_ids": [a["id"], b["id"]], "tag_ids": [t1["id"], t2["id"]]})
    assert added.status_code == 200 and added.json() == {"count": 4}
    again = client.post("/api/v1/files/bulk/tag", headers=MEMBER, json={"file_ids": [a["id"]], "tag_ids": [t1["id"]]})
    assert again.json() == {"count": 0}
    assert client.post("/api/v1/files/bulk/tag", headers=MEMBER, json={"file_ids": [a["id"]], "tag_ids": ["tag_missing"]}).status_code == 404
    assert client.post("/api/v1/files/bulk/tag", headers=MEMBER, json={"file_ids": ["file_missing"], "tag_ids": [t1["id"]]}).status_code == 404
    assert client.post("/api/v1/files/bulk/tag", headers=VIEWER, json={"file_ids": [a["id"]], "tag_ids": [t1["id"]]}).status_code == 403
    assert [t["name"] for t in client.get(f"/api/v1/files/{b['id']}", headers=MEMBER).json()["tags"]] == ["One", "Two"]
    removed = client.post("/api/v1/files/bulk/untag", headers=MEMBER, json={"file_ids": [a["id"], b["id"]], "tag_ids": [t2["id"]]})
    assert removed.json() == {"count": 2}
    assert names(tag=t2["id"]) == [] and names(tag=t1["id"]) == ["a.txt", "b.txt"]
    assert audits("file.bulk_tagged")[0].metadata["added"] == 4 and audits("file.bulk_untagged")[-1].metadata["removed"] == 2


def test_bulk_download_streams_a_zip_with_folders_as_directories() -> None:
    contracts = folder("Contracts")
    year = folder("2026", contracts["id"])
    a = upload("notes.txt", b"alpha " * 100, folder_id=year["id"])
    b = upload("notes.txt", b"beta", folder_id=year["id"])
    logo = upload("logo.png", PNG)
    response = client.post("/api/v1/files/bulk/download", headers=VIEWER, json={"file_ids": [a["id"], b["id"], logo["id"]]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="files-')
    assert response.headers["cache-control"] == "private, no-store"
    assert int(response.headers["content-length"]) == len(response.content)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        entries = {info.filename: info for info in archive.infolist()}
        assert set(entries) == {"Contracts/2026/notes.txt", "Contracts/2026/notes (2).txt", "logo.png"}
        assert archive.read("Contracts/2026/notes.txt") == b"alpha " * 100
        assert archive.read("Contracts/2026/notes (2).txt") == b"beta"
        assert archive.read("logo.png") == PNG
        assert entries["logo.png"].compress_type == zipfile.ZIP_STORED
        assert entries["Contracts/2026/notes.txt"].compress_type == zipfile.ZIP_DEFLATED
    record = audits("file.bulk_downloaded")[-1]
    assert record.actor == "viewer_1" and record.metadata["bytes"] == 600 + 4 + len(PNG)

    gone = upload("gone.txt")
    client.delete(f"/api/v1/files/{gone['id']}", headers=MEMBER)
    assert client.post("/api/v1/files/bulk/download", headers=VIEWER, json={"file_ids": [a["id"], gone["id"]]}).status_code == 404
    assert client.post("/api/v1/files/bulk/download", headers=who("nobody", "guest"), json={"file_ids": [a["id"]]}).status_code == 403


def test_bulk_download_refuses_quarantined_files_oversized_selections_and_missing_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    a = upload("a.txt", b"fine")
    bad = upload("bad.txt", b"evil")
    file_store.records[bad["id"]] = file_store.records[bad["id"]].model_copy(update={"scan_status": "infected", "scan_signature": "Eicar"})
    refused = client.post("/api/v1/files/bulk/download", headers=MEMBER, json={"file_ids": [a["id"], bad["id"]]})
    assert refused.status_code == 409 and "quarantined" in message(refused)
    monkeypatch.setattr("anum_api.files_organize.BULK_DOWNLOAD_MAX_BYTES", 3)
    big = client.post("/api/v1/files/bulk/download", headers=MEMBER, json={"file_ids": [a["id"]]})
    assert big.status_code == 413 and "Select fewer files" in message(big)
    monkeypatch.setattr("anum_api.files_organize.BULK_DOWNLOAD_MAX_BYTES", 1 << 30)
    file_store.storage.objects.clear()
    lost = client.post("/api/v1/files/bulk/download", headers=MEMBER, json={"file_ids": [a["id"]]})
    assert lost.status_code == 404 and "missing from storage" in message(lost)
    assert not audits("file.bulk_downloaded"), "a refused download is not recorded as one"


def test_fixed_paths_are_never_read_as_file_ids() -> None:
    assert client.get("/api/v1/files/folders", headers=MEMBER).status_code == 200
    assert client.get("/api/v1/files/tags", headers=MEMBER).status_code == 200
    assert client.post("/api/v1/files/bulk/move", headers=MEMBER, json={"file_ids": ["x"]}).status_code == 404
    paths = app.openapi()["paths"]
    for path in ("/api/v1/files/folders", "/api/v1/files/folders/{folder_id}", "/api/v1/files/tags", "/api/v1/files/tags/{tag_id}",
                 "/api/v1/files/{file_id}/tags", "/api/v1/files/{file_id}/tags/{tag_id}", "/api/v1/files/bulk/move",
                 "/api/v1/files/bulk/delete", "/api/v1/files/bulk/tag", "/api/v1/files/bulk/untag", "/api/v1/files/bulk/download"):
        assert path in paths, path
