from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import timedelta
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.object_storage import (
    ObjectStorageClient,
    ObjectStorageNotConfiguredError,
    build_object_storage_client,
)
from anum_api.repository import InMemoryRepository
from anum_api.schemas import FileObject, Task, TaskStatus, TenantContext, utc_now
from anum_api.settings import settings
from anum_api.store import InMemoryStore

from conftest import FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context


# moto's `mock_aws` intercepts requests by matching against AWS's real
# endpoint hostnames (it patches botocore's request layer, not raw sockets),
# so a made-up hostname like a real MinIO deployment would use is never
# intercepted and the request falls through to a real (failing) DNS lookup.
# Using the standard AWS S3 endpoint here keeps the client code path
# identical to a real S3-compatible deployment (endpoint_url is set, not
# None) while staying fully within moto's mock.
_MOTO_ENDPOINT_URL = "https://s3.amazonaws.com"


def make_client(bucket: str = "anum-files-test") -> ObjectStorageClient:
    return ObjectStorageClient(
        endpoint_url=_MOTO_ENDPOINT_URL,
        bucket=bucket,
        region="us-east-1",
        access_key="test",
        secret_key="test",
    )


# ---------------------------------------------------------------------------
# ObjectStorageClient (moto-backed)
# ---------------------------------------------------------------------------


def test_not_configured_raises_before_touching_boto3() -> None:
    with pytest.raises(ObjectStorageNotConfiguredError):
        ObjectStorageClient(
            endpoint_url=None,
            bucket="anum-files",
            region="us-east-1",
            access_key=None,
            secret_key=None,
        )


def test_build_object_storage_client_returns_none_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_endpoint_url", None)
    assert build_object_storage_client() is None


@mock_aws
def test_build_object_storage_client_returns_client_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_endpoint_url", _MOTO_ENDPOINT_URL)
    monkeypatch.setattr(settings, "object_storage_bucket", "anum-files-configured")
    monkeypatch.setattr(settings, "object_storage_region", "us-east-1")
    monkeypatch.setattr(settings, "object_storage_access_key", "test")
    monkeypatch.setattr(settings, "object_storage_secret_key", "test")

    client = build_object_storage_client()

    assert client is not None
    assert client.bucket == "anum-files-configured"


@mock_aws
def test_bucket_is_created_on_first_use() -> None:
    client = make_client("anum-files-autocreate")

    # No error is raised constructing the client above; a second client
    # against the same (now-existing) bucket must also succeed, exercising
    # the "already exists" tolerance path.
    make_client("anum-files-autocreate")

    assert client.head("missing-key") is None


@mock_aws
def test_upload_download_round_trip_verifies_checksum() -> None:
    client = make_client()
    payload = b"hello object storage"

    result = client.upload("docs/example.txt", payload, "text/plain")

    assert result["checksum_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["size_bytes"] == len(payload)
    assert result["bucket"] == client.bucket

    assert client.download("docs/example.txt") == payload


@mock_aws
def test_head_returns_metadata_for_existing_key() -> None:
    client = make_client()
    client.upload("docs/head-me.txt", b"content", "text/plain")

    head = client.head("docs/head-me.txt")

    assert head is not None
    assert head["size_bytes"] == len(b"content")
    assert head["content_type"] == "text/plain"


@mock_aws
def test_head_returns_none_for_missing_key() -> None:
    client = make_client()
    assert client.head("does/not/exist.txt") is None


@mock_aws
def test_delete_removes_object() -> None:
    client = make_client()
    client.upload("docs/delete-me.txt", b"bye", "text/plain")

    client.delete("docs/delete-me.txt")

    assert client.head("docs/delete-me.txt") is None


@mock_aws
def test_generate_presigned_download_url_points_at_key() -> None:
    client = make_client()
    client.upload("docs/presign.txt", b"data", "text/plain")

    url = client.generate_presigned_download_url("docs/presign.txt", expires_in_seconds=120)

    assert "presign.txt" in url
    assert client.bucket in url


# ---------------------------------------------------------------------------
# InMemoryRepository file methods
# ---------------------------------------------------------------------------


def make_context(tenant_id: str = "tenant_a", workspace_id: str = "workspace_a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id="user_a",
        roles=["owner"],
    )


def make_file(
    file_id: str = "file_1",
    tenant_id: str = "tenant_a",
    workspace_id: str = "workspace_a",
    task_id: str | None = "task_1",
) -> FileObject:
    return FileObject(
        id=file_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        task_id=task_id,
        owner_user_id="user_a",
        bucket="anum-files",
        key=f"{tenant_id}/{workspace_id}/{file_id}/example.txt",
        checksum_sha256=hashlib.sha256(b"example").hexdigest(),
        size_bytes=7,
        content_type="text/plain",
        created_at=utc_now(),
    )


def test_in_memory_save_and_get_file_round_trips() -> None:
    repository = InMemoryRepository(InMemoryStore())
    file = repository.save_file(make_file())

    assert repository.get_file(file.id, make_context()) == file


def test_in_memory_get_file_hides_other_tenant() -> None:
    repository = InMemoryRepository(InMemoryStore())
    file = repository.save_file(make_file())

    assert repository.get_file(file.id, make_context(tenant_id="tenant_b")) is None


def test_in_memory_list_files_for_task_is_scoped_and_ordered() -> None:
    repository = InMemoryRepository(InMemoryStore())
    first = repository.save_file(make_file("file_1"))
    second = make_file("file_2")
    second.created_at = first.created_at + timedelta(seconds=1)
    second = repository.save_file(second)
    # Different task: should not show up.
    repository.save_file(make_file("file_3", task_id="task_other"))
    # Different tenant, same task id: should not show up either.
    repository.save_file(make_file("file_4", tenant_id="tenant_b"))

    files = repository.list_files_for_task("task_1", make_context())

    assert [file.id for file in files] == [first.id, second.id]


def test_in_memory_delete_file_is_scoped_by_tenant() -> None:
    repository = InMemoryRepository(InMemoryStore())
    file = repository.save_file(make_file())

    assert repository.delete_file(file.id, make_context(tenant_id="tenant_b")) is False
    assert repository.get_file(file.id, make_context()) is not None

    assert repository.delete_file(file.id, make_context()) is True
    assert repository.get_file(file.id, make_context()) is None
    assert repository.delete_file(file.id, make_context()) is False


# ---------------------------------------------------------------------------
# SqlAlchemyRepository file methods (requires ANUM_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------


@pytest.mark.database
def test_postgres_file_round_trip_is_scoped_and_durable(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    task = Task(
        id="task_file_a",
        title="File task",
        prompt="Attach a file",
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    file = FileObject(
        id="file_repo_a",
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        task_id=task.id,
        owner_user_id="user_test",
        bucket="anum-files",
        key=f"{context.tenant_id}/{context.workspace_id}/file_repo_a/report.pdf",
        checksum_sha256=hashlib.sha256(b"report").hexdigest(),
        size_bytes=6,
        content_type="application/pdf",
        created_at=FIXED_NOW,
    )

    with repository_factory(context, commit=True) as repository:
        repository.create_task(task)
        repository.save_file(file)

    with repository_factory(context) as reloaded:
        assert reloaded.get_file(file.id, context) == file
        assert reloaded.list_files_for_task(task.id, context) == [file]

    hidden_contexts = (
        tenant_context(TENANT_B, WORKSPACE_B),
        tenant_context(TENANT_A, WORKSPACE_A2),
    )
    for hidden_context in hidden_contexts:
        with repository_factory(hidden_context) as repository:
            assert repository.get_file(file.id, hidden_context) is None
            assert repository.list_files_for_task(task.id, hidden_context) == []

    with repository_factory(context, commit=True) as repository:
        assert repository.delete_file(file.id, context) is True

    with repository_factory(context) as reloaded:
        assert reloaded.get_file(file.id, context) is None


@pytest.mark.database
def test_postgres_save_file_rejects_missing_task(
    seed_scopes: None,
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    context = tenant_context()
    file = FileObject(
        id="file_repo_missing_task",
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        task_id="task_does_not_exist",
        owner_user_id="user_test",
        bucket="anum-files",
        key="k",
        checksum_sha256=hashlib.sha256(b"x").hexdigest(),
        size_bytes=1,
        content_type="text/plain",
        created_at=FIXED_NOW,
    )

    with repository_factory(context) as repository:
        with pytest.raises(ValueError):
            repository.save_file(file)


# ---------------------------------------------------------------------------
# routes_files.py (via a standalone app that mounts only this router)
# ---------------------------------------------------------------------------


def build_test_app(storage_client: ObjectStorageClient) -> FastAPI:
    from anum_api import routes_files

    app = FastAPI()
    app.include_router(routes_files.router)
    app.dependency_overrides[routes_files.object_storage_context] = lambda: storage_client
    return app


HEADERS = {
    "x-tenant-id": "tenant_a",
    "x-workspace-id": "workspace_a",
    "x-user-id": "user_a",
    "x-user-roles": "owner,member",
}


@pytest.fixture
def route_client() -> Iterator[TestClient]:
    from anum_api.dependencies import memory_repository

    memory_repository.store.tasks.clear()
    memory_repository.store.files.clear()

    with mock_aws():
        storage_client = make_client("anum-files-routes")
        app = build_test_app(storage_client)
        with TestClient(app) as client:
            yield client

    memory_repository.store.tasks.clear()
    memory_repository.store.files.clear()


def test_upload_get_and_delete_file_round_trip(route_client: TestClient) -> None:
    upload = route_client.post(
        "/api/v1/files",
        headers=HEADERS,
        files={"upload": ("notes.txt", BytesIO(b"hello there"), "text/plain")},
    )
    assert upload.status_code == 201
    body = upload.json()
    assert body["content_type"] == "text/plain"
    assert body["size_bytes"] == len(b"hello there")
    assert body["checksum_sha256"] == hashlib.sha256(b"hello there").hexdigest()
    file_id = body["id"]

    fetched = route_client.get(f"/api/v1/files/{file_id}", headers=HEADERS)
    assert fetched.status_code == 200
    assert "download_url" in fetched.json()

    deleted = route_client.delete(f"/api/v1/files/{file_id}", headers=HEADERS)
    assert deleted.status_code == 204

    missing = route_client.get(f"/api/v1/files/{file_id}", headers=HEADERS)
    assert missing.status_code == 404


def test_upload_attaches_to_task_when_provided(route_client: TestClient) -> None:
    from anum_api.dependencies import memory_repository

    task = Task(
        id="task_upload_a",
        title="Attach a file",
        prompt="prompt",
        status=TaskStatus.CREATED,
        tenant_id="tenant_a",
        workspace_id="workspace_a",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    memory_repository.create_task(task)

    upload = route_client.post(
        "/api/v1/files",
        headers=HEADERS,
        data={"task_id": task.id},
        files={"upload": ("report.pdf", BytesIO(b"pdf-bytes"), "application/pdf")},
    )

    assert upload.status_code == 201
    assert upload.json()["task_id"] == task.id


def test_upload_rejects_unknown_task(route_client: TestClient) -> None:
    upload = route_client.post(
        "/api/v1/files",
        headers=HEADERS,
        data={"task_id": "task_does_not_exist"},
        files={"upload": ("report.pdf", BytesIO(b"pdf-bytes"), "application/pdf")},
    )

    assert upload.status_code == 404


def test_upload_requires_file_permission(route_client: TestClient) -> None:
    viewer_headers = {**HEADERS, "x-user-roles": "viewer"}

    upload = route_client.post(
        "/api/v1/files",
        headers=viewer_headers,
        files={"upload": ("notes.txt", BytesIO(b"hello"), "text/plain")},
    )

    assert upload.status_code == 403


def test_get_file_returns_404_for_unknown_id(route_client: TestClient) -> None:
    response = route_client.get("/api/v1/files/file_missing", headers=HEADERS)
    assert response.status_code == 404


def test_routes_return_503_when_object_storage_not_configured() -> None:
    from anum_api import routes_files

    app = FastAPI()
    app.include_router(routes_files.router)
    # No override: object_storage_context() calls the real
    # build_object_storage_client(), which returns None because
    # settings.object_storage_endpoint_url is unset in tests by default.
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/files",
            headers=HEADERS,
            files={"upload": ("notes.txt", BytesIO(b"hello"), "text/plain")},
        )

    assert response.status_code == 503
