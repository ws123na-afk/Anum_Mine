from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


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


class S3ObjectStorage:
    """MinIO-ready adapter accepting a boto3-compatible S3 client."""
    def __init__(self, client: object, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)  # type: ignore[attr-defined]

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()  # type: ignore[attr-defined,index,no-any-return]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]


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


class FileStore:
    def __init__(self, storage: ObjectStorage) -> None:
        self.storage = storage
        self.records: dict[str, FileRecord] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            for record in self.records.values():
                self.storage.delete(record.storage_key)
            self.records.clear()


file_store = FileStore(LocalObjectStorage(Path(".anum-data/objects")))
router = APIRouter(prefix="/api/v1/files", tags=["files"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _record(file_id: str, context: TenantContext) -> FileRecord:
    record = file_store.records.get(file_id)
    if record is None or record.tenant_id != context.tenant_id or record.workspace_id != context.workspace_id:
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
    key = f"{context.tenant_id}/{context.workspace_id}/{file_id}/{digest}"
    content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    file_store.storage.put(key, content, content_type)
    record = FileRecord(id=file_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        name=name, content_type=content_type, size_bytes=len(content), sha256=digest,
                        storage_key=key, created_by=context.user_id, created_at=utc_now())
    with file_store._lock:
        file_store.records[file_id] = record
    return record


@router.get("", response_model=list[FileRecord])
def list_files(limit: int = Query(default=100, ge=1, le=500), context: TenantContext = Depends(tenant_context)) -> list[FileRecord]:
    require_permission(context, Permission.MEMORY_READ)
    return [r for r in file_store.records.values() if r.tenant_id == context.tenant_id
            and r.workspace_id == context.workspace_id][:limit]


@router.get("/{file_id}", response_model=FileRecord)
def get_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> FileRecord:
    require_permission(context, Permission.MEMORY_READ)
    return _record(file_id, context)


@router.get("/{file_id}/content")
def download_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.MEMORY_READ)
    record = _record(file_id, context)
    return Response(file_store.storage.get(record.storage_key), media_type=record.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{record.name}"',
                             "ETag": record.sha256})


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.MEMORY_DELETE)
    record = _record(file_id, context)
    file_store.storage.delete(record.storage_key)
    with file_store._lock:
        file_store.records.pop(file_id, None)
    return Response(status_code=204)
