from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings


class ObjectStorage(Protocol):
    """Bytes for workspace files. Keys come from :func:`workspace_object_key`."""

    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


_KEY_PART = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")


def workspace_object_key(tenant_id: str, workspace_id: str, *parts: str) -> str:
    """``tenants/<tenant>/workspaces/<workspace>/<parts...>`` with every part validated.

    Tenant and workspace are always the leading path segments, so a bucket policy,
    lifecycle rule or export can be scoped to one tenant or workspace by prefix.
    """
    segments = (tenant_id, workspace_id, *parts)
    if not parts or any(not _KEY_PART.fullmatch(segment) for segment in segments):
        raise ValueError("Invalid object key component")
    return "/".join(("tenants", tenant_id, "workspaces", workspace_id, *parts))


class LocalObjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid object key")
        return path

    def put(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class InMemoryObjectStorage:
    """Per-process storage for tests and throwaway local runs."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self._lock = RLock()

    def put(self, key: str, content: bytes, content_type: str) -> None:
        with self._lock:
            self.objects[key] = (bytes(content), content_type)

    def get(self, key: str) -> bytes:
        with self._lock:
            if key not in self.objects:
                raise FileNotFoundError(key)
            return self.objects[key][0]

    def delete(self, key: str) -> None:
        with self._lock:
            self.objects.pop(key, None)


class S3ObjectStorage:
    """Adapter for any S3-compatible store (SeaweedFS locally, S3 or equivalents in the cloud).

    ``client`` is a boto3 S3 client (see :func:`create_s3_client`). Missing objects
    raise ``FileNotFoundError`` like the local adapter. With ``create_bucket`` the
    bucket is created on first write if it does not exist (local storage only; real
    environments provision buckets, encryption and lifecycle with OpenTofu).
    """

    def __init__(
        self,
        client: object,
        bucket: str,
        *,
        server_side_encryption: str = "",
        create_bucket: bool = False,
    ) -> None:
        self.client, self.bucket = client, bucket
        self.server_side_encryption = server_side_encryption
        self._create_bucket = create_bucket
        self._bucket_ready = not create_bucket
        self._lock = RLock()

    def _client_error_code(self, exc: Exception) -> str:
        response = getattr(exc, "response", None) or {}
        return str(response.get("Error", {}).get("Code", ""))

    def ensure_bucket(self) -> None:
        with self._lock:
            if self._bucket_ready:
                return
            try:
                self.client.head_bucket(Bucket=self.bucket)  # type: ignore[attr-defined]
            except Exception as exc:
                if self._client_error_code(exc) not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
                self.client.create_bucket(Bucket=self.bucket)  # type: ignore[attr-defined]
            self._bucket_ready = True

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self.ensure_bucket()
        extra = {"ServerSideEncryption": self.server_side_encryption} if self.server_side_encryption else {}
        self.client.put_object(  # type: ignore[attr-defined]
            Bucket=self.bucket, Key=key, Body=content, ContentType=content_type, **extra
        )

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]
        except Exception as exc:
            if self._client_error_code(exc) in {"404", "NoSuchKey", "NoSuchBucket"}:
                raise FileNotFoundError(key) from exc
            raise
        return response["Body"].read()  # type: ignore[no-any-return]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]


def create_s3_client(config: Any) -> object:
    """A boto3 S3 client for ``ANUM_S3_*`` settings (path-style, SigV4, bounded retries)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint or None,
        region_name=config.s3_region,
        aws_access_key_id=config.s3_access_key,
        aws_secret_access_key=config.s3_secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


def build_object_storage(config: Any) -> ObjectStorage:
    backend = config.object_storage_backend
    if backend == "local":
        return LocalObjectStorage(Path(config.object_storage_local_path))
    if backend == "memory":
        return InMemoryObjectStorage()
    if backend == "s3":
        return S3ObjectStorage(
            create_s3_client(config),
            config.s3_bucket,
            server_side_encryption=config.s3_server_side_encryption,
            create_bucket=config.s3_create_bucket,
        )
    raise RuntimeError(f"Unsupported object storage backend: {backend}")


class FileRecord(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_by: str
    created_at: datetime


class FileMetadataStore(Protocol):
    """Workspace file metadata. Bytes stay in :class:`ObjectStorage`.

    The PostgreSQL store (``anum_api.db.workspace_settings_repository``) keeps metadata in
    the RLS-protected ``workspace_files`` table.
    """

    def add(self, record: FileRecord) -> FileRecord: ...
    def get(self, context: TenantContext, file_id: str) -> FileRecord | None: ...
    def list_files(self, context: TenantContext, limit: int) -> list[FileRecord]: ...
    def delete(self, context: TenantContext, file_id: str) -> bool: ...


class FileStore:
    """Object storage for the bytes plus the in-memory metadata store (``memory`` backend)."""

    def __init__(self, storage: ObjectStorage) -> None:
        self.storage = storage
        self.records: dict[str, FileRecord] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            for record in self.records.values():
                self.storage.delete(record.storage_key)
            self.records.clear()

    def add(self, record: FileRecord) -> FileRecord:
        with self._lock:
            self.records[record.id] = record
        return record

    def get(self, context: TenantContext, file_id: str) -> FileRecord | None:
        record = self.records.get(file_id)
        if record is None or record.tenant_id != context.tenant_id or record.workspace_id != context.workspace_id:
            return None
        return record

    def list_files(self, context: TenantContext, limit: int) -> list[FileRecord]:
        with self._lock:
            return [r for r in self.records.values() if r.tenant_id == context.tenant_id
                    and r.workspace_id == context.workspace_id][:limit]

    def delete(self, context: TenantContext, file_id: str) -> bool:
        with self._lock:
            if self.get(context, file_id) is None:
                return False
            self.records.pop(file_id, None)
            return True


file_store = FileStore(build_object_storage(settings))


@contextmanager
def open_file_metadata_store(context: TenantContext) -> Iterator[FileMetadataStore]:
    """File metadata for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.workspace_settings_repository import SqlAlchemyFileMetadataStore

        return SqlAlchemyFileMetadataStore(session)

    with open_scoped_store(context, file_store, sql_store) as store:
        yield store


router = APIRouter(prefix="/api/v1/files", tags=["files"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _record(file_id: str, context: TenantContext) -> FileRecord:
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
    if record is None:
        raise HTTPException(404, "File not found")
    return record


@router.post("", response_model=FileRecord, status_code=status.HTTP_201_CREATED)
async def upload_file(request: Request, context: TenantContext = Depends(tenant_context),
                      x_file_name: str = Header(min_length=1, max_length=255),
                      x_content_sha256: str | None = Header(default=None)) -> FileRecord:
    require_permission(context, Permission.MEMORY_CREATE)
    name = Path(x_file_name).name
    if name != x_file_name or not re.fullmatch(r"[^\x00-\x1f\\/]+", name):
        raise HTTPException(422, "Invalid file name")
    content = await request.body()
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413 if content else 422, "File must contain 1 byte to 25 MiB")
    digest = hashlib.sha256(content).hexdigest()
    if x_content_sha256 is not None and x_content_sha256.lower() != digest:
        raise HTTPException(422, "Content checksum mismatch")
    file_id = new_id("file")
    try:
        key = workspace_object_key(context.tenant_id, context.workspace_id, "files", file_id, digest)
    except ValueError as exc:
        raise HTTPException(422, "Invalid tenant or workspace identifier") from exc
    content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    record = FileRecord(id=file_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        name=name, content_type=content_type, size_bytes=len(content), sha256=digest,
                        storage_key=key, created_by=context.user_id, created_at=utc_now())
    file_store.storage.put(key, content, content_type)
    try:
        with open_file_metadata_store(context) as store:
            store.add(record)
    except BaseException:
        # No metadata, no object: never leave unreachable bytes behind.
        file_store.storage.delete(key)
        raise
    return record


@router.get("", response_model=list[FileRecord])
def list_files(limit: int = Query(default=100, ge=1, le=500), context: TenantContext = Depends(tenant_context)) -> list[FileRecord]:
    require_permission(context, Permission.MEMORY_READ)
    with open_file_metadata_store(context) as store:
        return store.list_files(context, limit)


@router.get("/{file_id}", response_model=FileRecord)
def get_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> FileRecord:
    require_permission(context, Permission.MEMORY_READ)
    return _record(file_id, context)


@router.get("/{file_id}/content")
def download_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.MEMORY_READ)
    record = _record(file_id, context)
    try:
        content = file_store.storage.get(record.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    return Response(content, media_type=record.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{record.name}"',
                             "ETag": record.sha256})


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.MEMORY_DELETE)
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
        if record is None:
            raise HTTPException(404, "File not found")
        store.delete(context, file_id)
    # Bytes go only after the metadata delete committed, so a file is never listed without content.
    file_store.storage.delete(record.storage_key)
    return Response(status_code=204)
