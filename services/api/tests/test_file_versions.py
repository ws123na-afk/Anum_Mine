"""File versions and the storage report through the API (docs/files.md#versions): a new
version keeps the old bytes, old versions list, download and restore, the quota counts every
version and refuses with a clear error, and roles decide who may change versions."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import file_policy, files
from anum_api.files import file_store
from anum_api.main import app
from anum_api.retrieval import memory_retrieval_store
from anum_api.settings import settings

client = TestClient(app)
TENANT, WORKSPACE = "tenant_versions", "workspace_versions"


def who(user: str, role: str, workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = who("owner_1", "owner")
MEMBER = who("member_1", "member")
OTHER_MEMBER = who("member_2", "member")
VIEWER = who("viewer_1", "viewer")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    memory_retrieval_store.clear()
    file_policy.set_file_scanner(None)
    yield
    file_store.clear()
    memory_retrieval_store.clear()
    file_policy.set_file_scanner(None)


def upload(name: str, content: bytes, headers: dict[str, str] = MEMBER):
    return client.post("/api/v1/files", params={"name": name}, headers=headers, content=content)


def new_version(file_id: str, content: bytes, headers: dict[str, str] = MEMBER, content_type: str = "text/plain"):
    return client.post(f"/api/v1/files/{file_id}/versions", headers={**headers, "content-type": content_type}, content=content)


def message(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["error"]["message"]


def versions(file_id: str, headers: dict[str, str] = MEMBER, **params: int):
    response = client.get(f"/api/v1/files/{file_id}/versions", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_new_version_keeps_the_old_bytes_and_lists_newest_first() -> None:
    created = upload("notes.txt", b"first").json()
    assert created["version"] == 1
    second = new_version(created["id"], b"second version")
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2 and second.json()["size_bytes"] == len(b"second version")
    assert second.json()["name"] == "notes.txt" and second.json()["id"] == created["id"]
    new_version(created["id"], b"third")

    page = versions(created["id"])
    assert page["file"]["version"] == 3 and page["total"] == 2
    assert [item["version"] for item in page["items"]] == [2, 1]
    assert page["items"][1]["size_bytes"] == 5 and page["items"][1]["downloadable"] is True
    assert "storage_key" in page["items"][0]  # an internal key, shown to nobody who cannot read the file anyway
    assert versions(created["id"], limit=1, offset=1)["items"][0]["version"] == 1

    old = page["items"][1]
    download = client.get(f"/api/v1/files/{created['id']}/versions/{old['id']}/content", headers=VIEWER)
    assert download.status_code == 200 and download.content == b"first"
    assert "notes (version 1).txt" in download.headers["content-disposition"]
    assert client.get(f"/api/v1/files/{created['id']}/content", headers=VIEWER).content == b"third"


def test_restore_makes_an_old_version_current_and_can_itself_be_undone() -> None:
    created = upload("notes.txt", b"first").json()
    new_version(created["id"], b"second!")
    old = versions(created["id"])["items"][0]
    restored = client.post(f"/api/v1/files/{created['id']}/versions/{old['id']}/restore", headers=MEMBER)
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3 and restored.json()["sha256"] == old["sha256"]
    assert client.get(f"/api/v1/files/{created['id']}/content", headers=MEMBER).content == b"first"
    page = versions(created["id"])
    assert [item["version"] for item in page["items"]] == [2, 1]
    # The restore shares the bytes: nothing extra is stored.
    usage = client.get("/api/v1/files/usage", headers=MEMBER).json()
    assert usage["used_bytes"] == len(b"first") + len(b"second!") and usage["version_count"] == 2
    again = client.post(f"/api/v1/files/{created['id']}/versions/{old['id']}/restore", headers=MEMBER)
    assert again.status_code == 409 and "same content" in message(again)


def test_the_same_content_is_refused_and_the_type_must_match_the_extension() -> None:
    created = upload("notes.txt", b"same").json()
    same = new_version(created["id"], b"same")
    assert same.status_code == 409 and "same content" in message(same)
    empty = new_version(created["id"], b"")
    assert empty.status_code == 422
    png = upload("logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).json()
    wrong = new_version(png["id"], b"<html>not a png</html>", content_type="image/png")
    assert wrong.status_code == 415
    assert versions(png["id"])["total"] == 0 and versions(created["id"])["total"] == 0


def test_the_quota_counts_every_version_and_refuses_with_the_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "files_workspace_quota_bytes", 100)
    created = upload("a.txt", b"x" * 40).json()
    assert new_version(created["id"], b"y" * 40).status_code == 201
    usage = client.get("/api/v1/files/usage", headers=MEMBER).json()
    assert usage["used_bytes"] == 80 and usage["version_bytes"] == 40 and usage["quota_bytes"] == 100
    refused = new_version(created["id"], b"z" * 40)
    assert refused.status_code == 413
    assert "storage quota" in message(refused) and "older versions" in message(refused)
    assert refused.json()["error"]["details"][0]["type"] == "storage_quota"
    assert versions(created["id"])["total"] == 1
    assert len(file_store.storage.objects) == 2  # the refused bytes are not left behind

    # Deleting an older version frees its space, and the same upload then fits.
    old = versions(created["id"])["items"][0]
    assert client.delete(f"/api/v1/files/{created['id']}/versions/{old['id']}", headers=MEMBER).status_code == 204
    assert client.get("/api/v1/files/usage", headers=MEMBER).json()["used_bytes"] == 40
    assert new_version(created["id"], b"z" * 40).status_code == 201


def test_permissions_follow_upload_and_manage() -> None:
    created = upload("a.txt", b"one").json()
    assert new_version(created["id"], b"two", VIEWER).status_code == 403
    denied = new_version(created["id"], b"two", OTHER_MEMBER)
    assert denied.status_code == 403 and "uploaded this file or a workspace owner" in message(denied)
    assert new_version(created["id"], b"two", OWNER).status_code == 201
    old = versions(created["id"], VIEWER)["items"][0]  # viewers read
    assert client.post(f"/api/v1/files/{created['id']}/versions/{old['id']}/restore", headers=OTHER_MEMBER).status_code == 403
    assert client.delete(f"/api/v1/files/{created['id']}/versions/{old['id']}", headers=VIEWER).status_code == 403
    assert client.get(f"/api/v1/files/{created['id']}/versions/{old['id']}/content", headers=VIEWER).status_code == 200


def test_versions_are_scoped_to_the_workspace_and_missing_ones_say_so() -> None:
    created = upload("a.txt", b"one").json()
    new_version(created["id"], b"two")
    old = versions(created["id"])["items"][0]
    elsewhere = who("owner_1", "owner", workspace="workspace_other")
    assert client.get(f"/api/v1/files/{created['id']}/versions", headers=elsewhere).status_code == 404
    assert client.get(f"/api/v1/files/{created['id']}/versions/{old['id']}/content", headers=elsewhere).status_code == 404
    missing = client.get(f"/api/v1/files/{created['id']}/versions/nope/content", headers=MEMBER)
    assert missing.status_code == 404 and "Reload the versions" in message(missing)
    assert client.delete(f"/api/v1/files/{created['id']}/versions/nope", headers=MEMBER).status_code == 404
    assert new_version("nope", b"x").status_code == 404


def test_deleting_a_file_deletes_every_version_and_its_bytes() -> None:
    created = upload("a.txt", b"one").json()
    new_version(created["id"], b"two")
    new_version(created["id"], b"three")
    assert len(file_store.storage.objects) == 3
    assert client.delete(f"/api/v1/files/{created['id']}", headers=MEMBER).status_code == 204
    assert file_store.storage.objects == {} and file_store.versions == {}
    assert client.get("/api/v1/files/usage", headers=MEMBER).json()["used_bytes"] == 0


def test_a_quarantined_version_cannot_be_downloaded_or_restored() -> None:
    created = upload("a.txt", b"one").json()
    new_version(created["id"], b"two")
    old = next(iter(file_store.versions.values()))
    file_store.versions[old.id] = old.model_copy(update={"scan_status": "infected", "scan_signature": "Test.Sig"})
    page = versions(created["id"])
    assert page["items"][0]["downloadable"] is False
    blocked = client.get(f"/api/v1/files/{created['id']}/versions/{old.id}/content", headers=MEMBER)
    assert blocked.status_code == 409 and "Test.Sig" in message(blocked)
    assert client.post(f"/api/v1/files/{created['id']}/versions/{old.id}/restore", headers=MEMBER).status_code == 409
    assert client.delete(f"/api/v1/files/{created['id']}/versions/{old.id}", headers=MEMBER).status_code == 204


def test_the_storage_report_splits_usage_and_lists_the_largest_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "files_workspace_quota_bytes", 1000)
    big = upload("big.txt", b"b" * 200).json()
    new_version(big["id"], b"c" * 300)
    upload("small.csv", b"a,b\n1,2\n")
    report = client.get("/api/v1/files/storage", headers=VIEWER)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["usage"]["used_bytes"] == 200 + 300 + 8 and body["usage"]["quota_bytes"] == 1000
    assert body["usage"]["version_bytes"] == 200 and body["usage"]["file_count"] == 2
    assert body["largest"][0]["file"]["name"] == "big.txt"
    assert body["largest"][0]["version_count"] == 1 and body["largest"][0]["version_bytes"] == 200
    assert body["largest"][0]["total_bytes"] == 500
    kinds = {item["kind"]: item for item in body["by_kind"]}
    assert kinds["text"]["bytes"] == 300 and kinds["data"]["bytes"] == 8
    other = client.get("/api/v1/files/storage", headers=who("owner_9", "owner", workspace="workspace_empty")).json()
    assert other["usage"]["used_bytes"] == 0 and other["largest"] == [] and other["by_kind"] == []


def test_a_restore_racing_a_version_delete_is_refused_and_leaves_the_file_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    created = upload("a.txt", b"one").json()
    new_version(created["id"], b"two!")
    old = versions(created["id"])["items"][0]
    real_get = file_store.storage.get

    def get_then_lose_the_version(key: str) -> bytes:
        # The delete lands after the restore read the version and before it swaps the bytes in.
        data = real_get(key)
        assert client.delete(f"/api/v1/files/{created['id']}/versions/{old['id']}", headers=MEMBER).status_code == 204
        return data

    with monkeypatch.context() as patched:
        patched.setattr(file_store.storage, "get", get_then_lose_the_version)
        refused = client.post(f"/api/v1/files/{created['id']}/versions/{old['id']}/restore", headers=MEMBER)
    assert refused.status_code == 409 and "changed or was deleted" in message(refused)
    assert client.get(f"/api/v1/files/{created['id']}", headers=MEMBER).json()["version"] == 2
    assert client.get(f"/api/v1/files/{created['id']}/content", headers=MEMBER).content == b"two!"
