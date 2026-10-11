"""Workspace file management through the API (docs/files.md): types and limits, quota,
search, sort and keyset paging, rename and delete per role, previews, short-lived links,
re-indexing and the malware-scan hook."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import file_policy, files
from anum_api.files import file_store
from anum_api.main import app
from anum_api.retrieval import memory_retrieval_store
from anum_api.settings import settings

client = TestClient(app)
TENANT, WORKSPACE = "tenant_files", "workspace_files"


def who(user: str, role: str, workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = who("owner_1", "owner")
MEMBER = who("member_1", "member")
OTHER_MEMBER = who("member_2", "member")
VIEWER = who("viewer_1", "viewer")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


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


def upload(name: str, content: bytes, headers: dict[str, str] = MEMBER, content_type: str | None = None):
    """Uploads name the file in the query (URL-encoded), as the web and mobile clients do."""
    extra = {} if content_type is None else {"content-type": content_type}
    return client.post("/api/v1/files", params={"name": name}, headers={**headers, **extra}, content=content)


def message(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["error"]["message"]


# --------------------------------------------------------------------------- types and limits


def test_the_extension_decides_the_type_and_unsupported_types_are_refused_with_a_reason() -> None:
    plain = upload("notes.md", b"# Notes\nbody", content_type="application/octet-stream")
    assert plain.status_code == 201, plain.text
    assert plain.json()["content_type"] == "text/markdown"
    assert plain.json()["kind"] == "markdown" and plain.json()["previewable"] is True

    for name, content_type in (("tool.exe", "application/octet-stream"), ("page.html", "text/html"),
                               ("logo.svg", "image/svg+xml"), ("README", "application/octet-stream")):
        refused = upload(name, b"payload", content_type=content_type)
        assert refused.status_code == 415, name
        assert "Accepted:" in message(refused) and ".pdf" not in message(refused).split("Accepted:")[0]
    assert upload("data", b'{"a": 1}', content_type="application/json").json()["kind"] == "data"
    assert len(file_store.records) == 2


def test_binary_content_must_match_its_extension() -> None:
    disguised = upload("photo.png", b"<html><script>alert(1)</script></html>", content_type="image/png")
    assert disguised.status_code == 415
    assert "is not a valid image/png file" in message(disguised)
    assert upload("photo.png", PNG).status_code == 201
    assert upload("scan.pdf", PDF).json()["kind"] == "pdf"
    assert upload("notes.txt", b"text\x00with nul").status_code == 415
    assert len(file_store.storage.objects) == 2  # type: ignore[attr-defined]


def test_ascii_names_still_work_in_the_header_and_a_name_is_required() -> None:
    by_header = client.post("/api/v1/files", headers={**MEMBER, "x-file-name": "plain.txt"}, content=b"x")
    assert by_header.status_code == 201 and by_header.json()["name"] == "plain.txt"
    unnamed = client.post("/api/v1/files", headers=MEMBER, content=b"x")
    assert unnamed.status_code == 422 and "Name the file" in message(unnamed)
    accented = upload("r\u00e9sum\u00e9 \u6587\u4ef6.txt", b"x")
    assert accented.status_code == 201 and accented.json()["name"] == "r\u00e9sum\u00e9 \u6587\u4ef6.txt"


def test_empty_oversized_and_misnamed_uploads_say_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = upload("empty.txt", b"")
    assert empty.status_code == 422 and "empty" in message(empty)
    monkeypatch.setattr(settings, "max_upload_body_bytes", 16)
    too_big = upload("big.txt", b"x" * 17)
    assert too_big.status_code == 413
    assert "at most 16 B" in message(too_big)
    assert upload("../escape.txt", b"x").status_code == 422
    assert upload("tab\tname.txt", b"x").status_code == 422
    assert file_store.records == {}


def test_the_workspace_quota_refuses_an_upload_that_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "files_workspace_quota_bytes", 20)
    assert upload("a.txt", b"0123456789").status_code == 201
    refused = upload("b.txt", b"0123456789A")
    assert refused.status_code == 413
    body = refused.json()["error"]
    assert body["code"] == "payload_too_large"
    assert "storage quota" in body["message"] and "Delete files" in body["message"]
    assert body["details"][0]["type"] == "storage_quota"
    assert [r.name for r in file_store.records.values()] == ["a.txt"]
    # Another workspace has its own quota.
    assert upload("c.txt", b"0123456789A", headers=who("member_1", "member", "workspace_other")).status_code == 201

    usage = client.get("/api/v1/files/usage", headers=VIEWER).json()
    assert usage["used_bytes"] == 10 and usage["file_count"] == 1 and usage["quota_bytes"] == 20
    assert usage["max_upload_bytes"] == settings.max_upload_body_bytes
    assert ".pdf" in usage["accepted_extensions"] and usage["can_upload"] is False
    assert client.get("/api/v1/files/usage", headers=OWNER).json()["can_manage_all"] is True


def test_a_concurrent_upload_that_takes_the_space_first_leaves_no_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "files_workspace_quota_bytes", 20)
    original_usage = files.FileStore.usage
    calls = {"n": 0}

    def usage_lagging_once(self, context):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return files.FileUsage() if calls["n"] == 1 else original_usage(self, context)

    assert upload("first.txt", b"0123456789").status_code == 201
    monkeypatch.setattr(files.FileStore, "usage", usage_lagging_once)
    refused = upload("second.txt", b"0123456789AB")
    assert refused.status_code == 413
    stored = [key for key in file_store.storage.objects]  # type: ignore[attr-defined]
    assert len(stored) == 1


def test_the_malware_scan_hook_can_refuse_or_fail_an_upload() -> None:
    class Refusing:
        def scan(self, name: str, content_type: str, content: bytes) -> file_policy.ScanVerdict:
            return file_policy.ScanVerdict(clean=b"EICAR" not in content, reason="Eicar-Test-Signature")

    class Broken:
        def scan(self, name: str, content_type: str, content: bytes) -> file_policy.ScanVerdict:
            raise ConnectionError("scanner down")

    file_policy.set_file_scanner(Refusing())
    refused = upload("sample.txt", b"EICAR test")
    assert refused.status_code == 422 and "Eicar-Test-Signature" in message(refused)
    assert upload("clean.txt", b"fine").status_code == 201
    file_policy.set_file_scanner(Broken())
    failed = upload("later.txt", b"fine")
    assert failed.status_code == 503 and "nothing was stored" in message(failed)
    assert [r.name for r in file_store.records.values()] == ["clean.txt"]


# --------------------------------------------------------------------------- listing


def _seed(names_and_sizes: list[tuple[str, int]]) -> list[dict]:
    created = []
    for name, size in names_and_sizes:
        response = upload(name, b"x" * size)
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


def _names(response) -> list[str]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()]


def test_list_sorts_searches_and_pages_with_a_keyset_cursor() -> None:
    _seed([("beta.txt", 30), ("Alpha.txt", 10), ("gamma.csv", 20), ("delta.txt", 40), ("alpha-2.md", 5)])

    newest = _names(client.get("/api/v1/files", headers=VIEWER))
    assert newest == ["alpha-2.md", "delta.txt", "gamma.csv", "Alpha.txt", "beta.txt"]
    assert _names(client.get("/api/v1/files?sort=name", headers=VIEWER)) == [
        "alpha-2.md", "Alpha.txt", "beta.txt", "delta.txt", "gamma.csv"]
    assert _names(client.get("/api/v1/files?sort=size", headers=VIEWER))[0] == "delta.txt"
    assert _names(client.get("/api/v1/files?sort=size&order=asc", headers=VIEWER))[0] == "alpha-2.md"
    assert _names(client.get("/api/v1/files?q=ALPHA", headers=VIEWER)) == ["alpha-2.md", "Alpha.txt"]
    assert _names(client.get("/api/v1/files?q=%25", headers=VIEWER)) == []

    seen: list[str] = []
    cursor = None
    while True:
        page = client.get("/api/v1/files", headers=VIEWER, params={"sort": "name", "limit": 2, **({"cursor": cursor} if cursor else {})})
        items = page.json()
        seen += [item["name"] for item in items]
        if len(items) < 2:
            break
        cursor = items[-1]["id"]
    assert seen == ["alpha-2.md", "Alpha.txt", "beta.txt", "delta.txt", "gamma.csv"]

    unknown = client.get("/api/v1/files?cursor=file_gone", headers=VIEWER)
    assert unknown.status_code == 400 and "Reload the list" in message(unknown)
    assert client.get("/api/v1/files?sort=owner", headers=VIEWER).status_code == 422
    assert client.get("/api/v1/files", headers=who("member_1", "member", "workspace_other")).json() == []


def test_list_rows_carry_kind_index_state_and_what_the_caller_may_do() -> None:
    text = upload("guide.md", b"# Guide\nUse the release checklist.").json()
    image = upload("logo.png", PNG, headers=OTHER_MEMBER).json()
    assert text["index"]["status"] == "indexed" and text["index"]["chunk_count"] >= 1
    assert image["index"] == {"status": "skipped", "error": "not_text", "chunk_count": 0,
                              "embedding_model": None, "indexed_at": image["index"]["indexed_at"]}
    rows = {row["name"]: row for row in client.get("/api/v1/files", headers=MEMBER).json()}
    assert rows["guide.md"]["can_manage"] is True and rows["logo.png"]["can_manage"] is False
    assert all(row["can_manage"] for row in client.get("/api/v1/files", headers=OWNER).json())
    assert not any(row["can_manage"] for row in client.get("/api/v1/files", headers=VIEWER).json())


# --------------------------------------------------------------------------- rename and delete


def test_members_rename_and_delete_their_own_files_and_owners_anyones() -> None:
    mine = upload("draft.txt", b"one").json()
    theirs = upload("theirs.txt", b"two", headers=OTHER_MEMBER).json()

    renamed = client.patch(f"/api/v1/files/{mine['id']}", headers=MEMBER, json={"name": "  final.txt "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "final.txt"
    assert renamed.json()["updated_by"] == "member_1" and renamed.json()["updated_at"]
    assert renamed.json()["created_by"] == "member_1"

    refused = client.patch(f"/api/v1/files/{theirs['id']}", headers=MEMBER, json={"name": "mine-now.txt"})
    assert refused.status_code == 403 and "uploaded this file or a workspace owner" in message(refused)
    assert client.delete(f"/api/v1/files/{theirs['id']}", headers=MEMBER).status_code == 403
    viewer = client.patch(f"/api/v1/files/{mine['id']}", headers=VIEWER, json={"name": "v.txt"})
    assert viewer.status_code == 403 and "member or owner role" in message(viewer)
    assert client.delete(f"/api/v1/files/{mine['id']}", headers=VIEWER).status_code == 403

    assert client.patch(f"/api/v1/files/{theirs['id']}", headers=OWNER, json={"name": "kept.txt"}).status_code == 200
    changed_type = client.patch(f"/api/v1/files/{mine['id']}", headers=MEMBER, json={"name": "final.pdf"})
    assert changed_type.status_code == 422 and "Keep the .txt extension" in message(changed_type)
    assert client.patch(f"/api/v1/files/{mine['id']}", headers=MEMBER, json={"name": "a/b.txt"}).status_code == 422
    assert client.patch("/api/v1/files/file_missing", headers=OWNER, json={"name": "x.txt"}).status_code == 404

    assert client.delete(f"/api/v1/files/{mine['id']}", headers=MEMBER).status_code == 204
    assert client.delete(f"/api/v1/files/{theirs['id']}", headers=OWNER).status_code == 204
    assert client.get("/api/v1/files", headers=OWNER).json() == []
    assert file_store.storage.objects == {}  # type: ignore[attr-defined]


def test_a_viewer_reads_but_cannot_upload() -> None:
    refused = upload("v.txt", b"x", headers=VIEWER)
    assert refused.status_code == 403 and "member or owner role" in message(refused)
    record = upload("shared.txt", b"hello").json()
    assert client.get(f"/api/v1/files/{record['id']}", headers=VIEWER).status_code == 200
    assert client.get(f"/api/v1/files/{record['id']}/content", headers=VIEWER).content == b"hello"
    assert client.get(f"/api/v1/files/{record['id']}/preview", headers=VIEWER).json()["text"] == "hello"
    assert client.post(f"/api/v1/files/{record['id']}/reindex", headers=VIEWER).status_code == 403
    nobody = client.get("/api/v1/files", headers={**VIEWER, "x-user-roles": "guest"})
    assert nobody.status_code == 403 and "viewer, member or owner" in message(nobody)


# --------------------------------------------------------------------------- previews and links


def test_text_previews_are_bounded_and_binary_kinds_have_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "PREVIEW_MAX_BYTES", 8)
    note = upload("long.md", "# Überblick\nmore text".encode()).json()
    preview = client.get(f"/api/v1/files/{note['id']}/preview", headers=VIEWER).json()
    assert preview["kind"] == "markdown" and preview["truncated"] is True
    assert preview["text"].startswith("# Über") and len(preview["text"]) <= 8
    image = upload("logo.png", PNG).json()
    refused = client.get(f"/api/v1/files/{image['id']}/preview", headers=VIEWER)
    assert refused.status_code == 422 and "Download this file" in message(refused)


def test_short_lived_links_serve_bytes_without_a_bearer_token() -> None:
    record = upload("Q3 \"plan\" été.txt", b"plan body").json()
    link = client.post(f"/api/v1/files/{record['id']}/links", headers=VIEWER, json={})
    assert link.status_code == 201
    body = link.json()
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(0) < expires - datetime.now(UTC) <= timedelta(seconds=settings.files_link_ttl_seconds)
    served = client.get(body["url"])
    assert served.status_code == 200 and served.content == b"plan body"
    disposition = served.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="Q3 _plan_ ')
    assert "filename*=UTF-8''Q3%20%22plan%22%20%C3%A9t%C3%A9.txt" in disposition
    assert served.headers["content-type"] == "application/octet-stream"
    assert served.headers["cache-control"] == "private, no-store"
    assert "sandbox" in served.headers["content-security-policy"]

    image = upload("logo.png", PNG).json()
    inline = client.post(f"/api/v1/files/{image['id']}/links", headers=VIEWER, json={"disposition": "inline"}).json()
    shown = client.get(inline["url"])
    assert shown.headers["content-type"] == "image/png" and shown.headers["content-disposition"].startswith("inline;")
    archive = upload("bundle.zip", b"PK\x03\x04" + b"\x00" * 26).json()
    no_preview = client.post(f"/api/v1/files/{archive['id']}/links", headers=VIEWER, json={"disposition": "inline"})
    assert no_preview.status_code == 422

    # Another workspace cannot mint a link for the file.
    other = who("member_1", "member", "workspace_other")
    assert client.post(f"/api/v1/files/{record['id']}/links", headers=other, json={}).status_code == 404


def test_tampered_expired_and_orphaned_links_are_refused() -> None:
    record = upload("secret.txt", b"secret").json()
    url = client.post(f"/api/v1/files/{record['id']}/links", headers=MEMBER, json={}).json()["url"]
    token = url.rsplit("/", 1)[1]
    payload, signature = token.split(".")
    forged = file_policy.sign_link(file_policy.LinkClaims(
        tenant_id=TENANT, workspace_id="workspace_other", user_id="member_1", file_id=record["id"],
        sha256=record["sha256"], inline=False, expires_at=datetime.now(UTC) + timedelta(minutes=5)))
    assert client.get(f"/api/v1/files/links/{payload}.{signature[:-2]}AA").status_code == 410
    assert client.get("/api/v1/files/links/not-a-token").status_code == 410
    # A validly signed link for another workspace finds nothing there (RLS scope).
    assert client.get(f"/api/v1/files/links/{forged}").status_code == 404
    expired = file_policy.sign_link(file_policy.LinkClaims(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_1", file_id=record["id"],
        sha256=record["sha256"], inline=False, expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    gone = client.get(f"/api/v1/files/links/{expired}")
    assert gone.status_code == 410 and "Open the file again" in message(gone)
    assert client.delete(f"/api/v1/files/{record['id']}", headers=MEMBER).status_code == 204
    assert client.get(url).status_code == 404


def test_link_keys_follow_secrets_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    claims = file_policy.LinkClaims(tenant_id="t", workspace_id="w", user_id="u", file_id="f", sha256="0" * 64,
                                    inline=True, expires_at=datetime.now(UTC) + timedelta(minutes=1))
    monkeypatch.setattr(settings, "secrets_key", SecretStr(old))
    token = file_policy.sign_link(claims)
    monkeypatch.setattr(settings, "secrets_key", SecretStr(f"{new},{old}"))
    assert file_policy.verify_link(token).file_id == "f"
    monkeypatch.setattr(settings, "secrets_key", SecretStr(new))
    with pytest.raises(file_policy.InvalidLink):
        file_policy.verify_link(token)


# --------------------------------------------------------------------------- re-index


def test_reindex_reports_the_files_index_state() -> None:
    record = upload("notes.txt", b"Deploy on Tuesdays after the review.").json()
    memory_retrieval_store.clear()
    assert client.get(f"/api/v1/files/{record['id']}", headers=VIEWER).json()["index"]["status"] == "pending"
    again = client.post(f"/api/v1/files/{record['id']}/reindex", headers=MEMBER)
    assert again.status_code == 200
    assert again.json()["index"]["status"] == "indexed"
    assert client.post("/api/v1/files/file_missing/reindex", headers=MEMBER).status_code == 404


def test_cors_allows_the_upload_headers_and_rename() -> None:
    origin = settings.cors_origins[0]
    preflight = client.options(
        "/api/v1/files/file_1",
        headers={"origin": origin, "access-control-request-method": "PATCH",
                 "access-control-request-headers": "x-file-name,x-content-sha256,content-type"},
    )
    assert preflight.status_code == 200
    assert "PATCH" in preflight.headers["access-control-allow-methods"]
    allowed = preflight.headers["access-control-allow-headers"].lower()
    assert "x-file-name" in allowed and "x-content-sha256" in allowed
