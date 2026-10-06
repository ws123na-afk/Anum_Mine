"""Workspace file storage backends (docs/files.md).

The S3 adapter is tested two ways:

* always, against moto's in-process S3 (boto3 requests are SigV4-signed and
  serialised as for a real endpoint, then answered by moto);
* tests marked ``s3`` run against a real S3-compatible endpoint (SeaweedFS in compose) when
  ``ANUM_TEST_S3_ENDPOINT`` is reachable (``docker compose -f infra/docker/compose.yaml
  up s3``; credentials from ``ANUM_TEST_S3_ACCESS_KEY``/``ANUM_TEST_S3_SECRET_KEY``).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws

from anum_api.files import (
    InMemoryObjectStorage,
    LocalObjectStorage,
    S3ObjectStorage,
    build_object_storage,
    create_s3_client,
    file_store,
    router as files_router,
    workspace_object_key,
)
from anum_api.settings import Settings

app = FastAPI()
app.include_router(files_router)
client = TestClient(app)
OWNER = {"x-tenant-id": "tenant_obj", "x-workspace-id": "workspace_obj", "x-user-id": "owner", "x-user-roles": "owner"}


def _s3_config(**overrides: object) -> SimpleNamespace:
    values = {
        "s3_endpoint": "",
        "s3_region": "us-east-1",
        "s3_bucket": "anum-test",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "s3_server_side_encryption": "",
        "s3_create_bucket": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_workspace_object_key_is_tenant_and_workspace_scoped() -> None:
    assert (
        workspace_object_key("tenant_a", "workspace_b", "files", "file_1", "abc")
        == "tenants/tenant_a/workspaces/workspace_b/files/file_1/abc"
    )
    for bad in ("..", ".", "a/b", "", "a b", "x" * 200):
        with pytest.raises(ValueError):
            workspace_object_key("tenant_a", "workspace_b", "files", bad)
    with pytest.raises(ValueError):
        workspace_object_key("../tenant", "workspace_b", "files")
    with pytest.raises(ValueError):
        workspace_object_key("tenant_a", "workspace_b")


def test_build_object_storage_selects_backend(tmp_path: Path) -> None:
    assert isinstance(
        build_object_storage(Settings(object_storage_backend="local", object_storage_local_path=str(tmp_path))),
        LocalObjectStorage,
    )
    assert isinstance(build_object_storage(Settings(object_storage_backend="memory")), InMemoryObjectStorage)
    assert isinstance(build_object_storage(Settings(object_storage_backend="s3")), S3ObjectStorage)
    with pytest.raises(ValueError):
        Settings(object_storage_backend="ftp")


def test_in_memory_storage_round_trip() -> None:
    storage = InMemoryObjectStorage()
    storage.put("tenants/t/workspaces/w/files/f/d", b"bytes", "text/plain")
    assert storage.get("tenants/t/workspaces/w/files/f/d") == b"bytes"
    storage.delete("tenants/t/workspaces/w/files/f/d")
    with pytest.raises(FileNotFoundError):
        storage.get("tenants/t/workspaces/w/files/f/d")


@pytest.fixture
def moto_s3() -> Iterator[None]:
    with mock_aws():
        yield


def test_s3_storage_round_trip_with_bucket_creation_and_encryption(moto_s3: None) -> None:
    config = _s3_config(s3_server_side_encryption="AES256")
    s3 = create_s3_client(config)
    storage = S3ObjectStorage(s3, config.s3_bucket, server_side_encryption="AES256", create_bucket=True)
    key = workspace_object_key("tenant_a", "workspace_a", "files", "file_1", "digest")

    storage.put(key, b"phase three", "text/plain")

    assert storage.get(key) == b"phase three"
    head = s3.head_object(Bucket="anum-test", Key=key)
    assert head["ContentType"] == "text/plain"
    assert head["ServerSideEncryption"] == "AES256"
    storage.delete(key)
    with pytest.raises(FileNotFoundError):
        storage.get(key)


def test_s3_storage_without_create_bucket_does_not_touch_the_bucket(moto_s3: None) -> None:
    s3 = create_s3_client(_s3_config())
    storage = S3ObjectStorage(s3, "missing-bucket")
    with pytest.raises(Exception) as raised:
        storage.put("tenants/t/workspaces/w/files/f/d", b"x", "text/plain")
    assert "NoSuchBucket" in str(raised.value)


def test_files_api_stores_bytes_under_the_tenant_prefix_in_s3(moto_s3: None) -> None:
    s3 = create_s3_client(_s3_config())
    original = file_store.storage
    file_store.storage = S3ObjectStorage(s3, "anum-api-test", create_bucket=True)
    try:
        uploaded = client.post(
            "/api/v1/files",
            headers={**OWNER, "x-file-name": "notes.txt", "content-type": "text/plain"},
            content=b"stored in s3",
        )
        assert uploaded.status_code == 201
        record = uploaded.json()
        assert record["storage_key"] == (
            f"tenants/tenant_obj/workspaces/workspace_obj/files/{record['id']}/{record['sha256']}"
        )
        keys = [item["Key"] for item in s3.list_objects_v2(Bucket="anum-api-test")["Contents"]]
        assert keys == [record["storage_key"]]
        assert client.get(f"/api/v1/files/{record['id']}/content", headers=OWNER).content == b"stored in s3"
        other = {**OWNER, "x-workspace-id": "workspace_other"}
        assert client.get(f"/api/v1/files/{record['id']}/content", headers=other).status_code == 404
        assert client.delete(f"/api/v1/files/{record['id']}", headers=OWNER).status_code == 204
        assert s3.list_objects_v2(Bucket="anum-api-test").get("KeyCount") == 0
    finally:
        file_store.clear()
        file_store.storage = original


def test_download_of_missing_bytes_is_404() -> None:
    original = file_store.storage
    file_store.storage = InMemoryObjectStorage()
    try:
        record = client.post(
            "/api/v1/files", headers={**OWNER, "x-file-name": "gone.txt"}, content=b"soon gone"
        ).json()
        file_store.storage.delete(record["storage_key"])
        assert client.get(f"/api/v1/files/{record['id']}/content", headers=OWNER).status_code == 404
    finally:
        file_store.clear()
        file_store.storage = original


S3_ENDPOINT = os.getenv("ANUM_TEST_S3_ENDPOINT", "http://127.0.0.1:9000")


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), 0.5):
            return True
    except OSError:
        return False


@pytest.mark.s3
@pytest.mark.skipif(not _reachable(S3_ENDPOINT), reason=f"No S3-compatible endpoint at {S3_ENDPOINT}")
def test_round_trip_against_real_s3_compatible_endpoint() -> None:
    config = _s3_config(
        s3_endpoint=S3_ENDPOINT,
        s3_access_key=os.getenv("ANUM_TEST_S3_ACCESS_KEY", "anum"),
        s3_secret_key=os.getenv("ANUM_TEST_S3_SECRET_KEY", "anum-local-secret"),
        s3_bucket=os.getenv("ANUM_TEST_S3_BUCKET", "anum-test"),
    )
    storage = S3ObjectStorage(create_s3_client(config), config.s3_bucket, create_bucket=True)
    key = workspace_object_key("tenant_s3", "workspace_s3", "files", uuid4().hex, "digest")
    payload = os.urandom(256 * 1024)
    storage.put(key, payload, "application/octet-stream")
    try:
        assert storage.get(key) == payload
    finally:
        storage.delete(key)
    with pytest.raises(FileNotFoundError):
        storage.get(key)
