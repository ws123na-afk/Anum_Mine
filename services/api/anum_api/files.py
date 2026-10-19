"""Workspace files (docs/files.md): bytes in object storage, metadata under tenant RLS.

Routes under ``/api/v1/files``:

* ``POST``: upload the raw body (``?name=`` or ``X-File-Name``, optional
  ``X-Content-SHA256``). Type,
  size, content signature, the malware-scan hook and the workspace quota are checked
  before anything is stored.
* ``GET``: one page, newest first by default. ``q`` searches names, ``sort`` is
  ``created_at``, ``name`` or ``size``, ``order`` ``asc``/``desc``, and ``cursor`` is the
  last file id of the previous page (keyset paging).
* ``GET /usage``: bytes used, the quota, the upload limit and the accepted types.
* ``GET``/``PATCH``/``DELETE /{id}``: metadata, rename, delete. Members rename and delete
  their own uploads; owners (``file:manage``) anyone's.
* ``GET /{id}/content``: the bytes as an attachment. ``GET /{id}/preview``: text kinds as
  bounded text. ``POST /{id}/links``: a short-lived signed link that ``GET /links/{token}``
  serves without a bearer token.
* ``POST /{id}/reindex``: index the file for retrieval again and return its state.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from . import file_policy
from .authorization import Permission
from .dependencies import has_permission, tenant_context
from .errors import ApplicationError, ErrorCode, ErrorDetail
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings


class ObjectStorage(Protocol):
    """Bytes for workspace files. Keys come from :func:`workspace_object_key`."""

    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


logger = logging.getLogger(__name__)
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
    # Set by a rename (migration 0026); None for a file never renamed.
    updated_by: str | None = None
    updated_at: datetime | None = None
    # Malware scan (migration 0075, docs/files.md#malware-scanning): None until a scanner
    # looked at the bytes (uploads before scanning, generated files); `infected` files
    # are quarantined: listed so they can be deleted, never served.
    scan_status: Literal["clean", "infected"] | None = None
    scan_signature: str | None = None
    scanned_at: datetime | None = None
    # The current version number (migration 0090_file_versions, docs/files.md#versions).
    version: int = 1


class FileVersionRecord(BaseModel):
    """An earlier version of a file: its bytes, who uploaded them and who replaced them."""

    id: str
    tenant_id: str
    workspace_id: str
    file_id: str
    version: int
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_by: str
    created_at: datetime
    replaced_by: str
    replaced_at: datetime
    scan_status: Literal["clean", "infected"] | None = None
    scan_signature: str | None = None
    scanned_at: datetime | None = None


class FileContent(BaseModel):
    """New current bytes for a file (a new version or a restored one)."""

    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_by: str
    created_at: datetime
    scan_status: Literal["clean", "infected"] | None = None
    scan_signature: str | None = None
    scanned_at: datetime | None = None


class FileSort(StrEnum):
    CREATED_AT = "created_at"
    NAME = "name"
    SIZE = "size"


class FilePageQuery(BaseModel):
    """One page of a workspace's files. ``after`` is the last record of the previous page."""

    limit: int = 50
    search: str | None = None
    sort: FileSort = FileSort.CREATED_AT
    descending: bool = True
    after: FileRecord | None = None


class FileUsage(BaseModel):
    used_bytes: int = Field(
        default=0, description="Every stored byte: current files and older versions (identical bytes of one file count once)"
    )
    file_count: int = 0
    version_bytes: int = Field(default=0, description="The part of `used_bytes` taken by older versions")
    version_count: int = 0


class StorageByType(BaseModel):
    content_type: str
    file_count: int
    bytes: int


class StorageFile(BaseModel):
    """One file with what it takes in storage: current bytes plus its older versions."""

    record: FileRecord
    version_count: int
    version_bytes: int
    total_bytes: int


class StorageReport(BaseModel):
    usage: FileUsage
    by_type: list[StorageByType]
    largest: list[StorageFile]


class StorageQuotaExceeded(Exception):
    def __init__(self, usage: FileUsage) -> None:
        super().__init__("workspace storage quota exceeded")
        self.usage = usage


class FileContentChanged(Exception):
    """The file's current bytes changed (or it was deleted) since the caller read it."""


def distinct_bytes(rows: list[tuple[str, str, int]]) -> int:
    """Bytes of ``(file_id, sha256, size)`` rows, identical content of one file counted once."""
    return sum({(file_id, digest): size for file_id, digest, size in rows}.values())


def previous_version(record: FileRecord, replaced_by: str) -> FileVersionRecord:
    """The older-version row that keeps ``record``'s current bytes when they are replaced."""
    return FileVersionRecord(
        id=new_id("filever"), tenant_id=record.tenant_id, workspace_id=record.workspace_id, file_id=record.id,
        version=record.version, content_type=record.content_type, size_bytes=record.size_bytes, sha256=record.sha256,
        storage_key=record.storage_key, created_by=record.created_by, created_at=record.created_at,
        replaced_by=replaced_by, replaced_at=utc_now(), scan_status=record.scan_status,
        scan_signature=record.scan_signature, scanned_at=record.scanned_at,
    )


def build_storage_report(
    usage: FileUsage, records: list[FileRecord], versions: list[FileVersionRecord], largest: int
) -> StorageReport:
    """The storage report from rows (the memory store; PostgreSQL aggregates in SQL first)."""
    by_type: dict[str, list[int]] = {}
    for record in records:
        entry = by_type.setdefault(record.content_type, [0, 0])
        entry[0] += 1
        entry[1] += record.size_bytes
    per_file: dict[str, list[FileVersionRecord]] = {}
    for version in versions:
        per_file.setdefault(version.file_id, []).append(version)
    files = []
    for record in records:
        olds = per_file.get(record.id, [])
        extra = sum({v.sha256: v.size_bytes for v in olds if v.sha256 != record.sha256}.values())
        files.append(StorageFile(record=record, version_count=len(olds), version_bytes=extra,
                                 total_bytes=record.size_bytes + extra))
    files.sort(key=lambda item: (-item.total_bytes, item.record.id))
    types = [StorageByType(content_type=key, file_count=value[0], bytes=value[1]) for key, value in by_type.items()]
    types.sort(key=lambda item: (-item.bytes, item.content_type))
    return StorageReport(usage=usage, by_type=types, largest=files[:largest])


def sort_key(record: FileRecord, sort: FileSort) -> tuple[Any, str]:
    if sort == FileSort.NAME:
        return (record.name.lower(), record.id)
    if sort == FileSort.SIZE:
        return (record.size_bytes, record.id)
    return (record.created_at, record.id)


class FileMetadataStore(Protocol):
    """Workspace file metadata. Bytes stay in :class:`ObjectStorage`.

    The PostgreSQL store (``anum_api.db.workspace_settings_repository``) keeps metadata in
    the RLS-protected ``workspace_files`` table.
    """

    def add(self, record: FileRecord, quota_bytes: int | None = None) -> FileRecord:
        """Add ``record``; with ``quota_bytes``, refuse (:class:`StorageQuotaExceeded`) when the
        workspace would go over it, checked under a per-workspace lock."""
        ...

    def get(self, context: TenantContext, file_id: str) -> FileRecord | None: ...
    def list_files(self, context: TenantContext, limit: int) -> list[FileRecord]: ...
    def list_page(self, context: TenantContext, query: FilePageQuery) -> list[FileRecord]: ...
    def usage(self, context: TenantContext) -> FileUsage: ...
    def rename(self, context: TenantContext, file_id: str, name: str) -> FileRecord | None: ...
    def delete(self, context: TenantContext, file_id: str) -> bool: ...
    def record_scan(self, context: TenantContext, file_id: str, sha256: str, status: str, signature: str | None) -> bool:
        """Store a scan result if the file is still unscanned with these bytes."""
        ...

    def added_bytes(self, context: TenantContext, file_id: str, sha256: str, size: int) -> int:
        """What storing ``sha256`` as a version of ``file_id`` adds to usage (0 when the file has those bytes)."""
        ...

    def replace_content(
        self, context: TenantContext, file_id: str, expected_sha256: str, content: FileContent,
        quota_bytes: int | None = None, source_version_id: str | None = None,
    ) -> FileRecord:
        """Make ``content`` the current version and keep the current one as an older version.

        Raises :class:`FileContentChanged` when the file is gone or its bytes are no longer
        ``expected_sha256``, and :class:`StorageQuotaExceeded` past ``quota_bytes`` (checked under
        the same per-workspace lock as uploads). With ``source_version_id`` (a restore) the
        older version must still exist in the same transaction, or :class:`FileContentChanged`
        is raised, so a restore never makes a concurrently deleted version current."""
        ...

    def list_versions(
        self, context: TenantContext, file_id: str, limit: int, offset: int
    ) -> tuple[list[FileVersionRecord], int]:
        """Older versions, newest first, and how many there are."""
        ...

    def get_version(self, context: TenantContext, file_id: str, version_id: str) -> FileVersionRecord | None: ...
    def delete_version(self, context: TenantContext, file_id: str, version_id: str) -> FileVersionRecord | None: ...

    def key_in_use(self, context: TenantContext, file_id: str, storage_key: str) -> bool:
        """Whether the file's current version or an older one still uses ``storage_key``."""
        ...

    def storage_keys(self, context: TenantContext, file_id: str) -> list[str]:
        """Every object key of a file, current and older versions."""
        ...

    def storage_report(self, context: TenantContext, largest: int) -> StorageReport: ...


class FileStore:
    """Object storage for the bytes plus the in-memory metadata store (``memory`` backend)."""

    def __init__(self, storage: ObjectStorage) -> None:
        self.storage = storage
        self.records: dict[str, FileRecord] = {}
        self.versions: dict[str, FileVersionRecord] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            for record in self.records.values():
                self.storage.delete(record.storage_key)
            for version in self.versions.values():
                self.storage.delete(version.storage_key)
            self.records.clear()
            self.versions.clear()

    def _versions_of(self, context: TenantContext, file_id: str) -> list[FileVersionRecord]:
        return [v for v in self._scoped_versions(context) if v.file_id == file_id]

    def _scoped_versions(self, context: TenantContext) -> list[FileVersionRecord]:
        return [v for v in self.versions.values()
                if v.tenant_id == context.tenant_id and v.workspace_id == context.workspace_id]

    def _scoped(self, context: TenantContext) -> list[FileRecord]:
        return [r for r in self.records.values()
                if r.tenant_id == context.tenant_id and r.workspace_id == context.workspace_id]

    def add(self, record: FileRecord, quota_bytes: int | None = None) -> FileRecord:
        scope = TenantContext(tenant_id=record.tenant_id, workspace_id=record.workspace_id, user_id=record.created_by)
        with self._lock:
            if quota_bytes is not None:
                current = self.usage(scope)
                if current.used_bytes + record.size_bytes > quota_bytes:
                    raise StorageQuotaExceeded(current)
            self.records[record.id] = record
        return record

    def get(self, context: TenantContext, file_id: str) -> FileRecord | None:
        record = self.records.get(file_id)
        if record is None or record.tenant_id != context.tenant_id or record.workspace_id != context.workspace_id:
            return None
        return record

    def list_files(self, context: TenantContext, limit: int) -> list[FileRecord]:
        with self._lock:
            return sorted(self._scoped(context), key=lambda r: (r.created_at, r.id))[:limit]

    def list_page(self, context: TenantContext, query: FilePageQuery) -> list[FileRecord]:
        with self._lock:
            items = self._scoped(context)
        if query.search:
            needle = query.search.lower()
            items = [r for r in items if needle in r.name.lower()]
        items.sort(key=lambda r: sort_key(r, query.sort), reverse=query.descending)
        if query.after is not None:
            boundary = sort_key(query.after, query.sort)
            if query.descending:
                items = [r for r in items if sort_key(r, query.sort) < boundary]
            else:
                items = [r for r in items if sort_key(r, query.sort) > boundary]
        return items[: query.limit]

    def usage(self, context: TenantContext) -> FileUsage:
        with self._lock:
            items = self._scoped(context)
            versions = self._scoped_versions(context)
        current = sum(r.size_bytes for r in items)
        used = distinct_bytes(
            [(r.id, r.sha256, r.size_bytes) for r in items] + [(v.file_id, v.sha256, v.size_bytes) for v in versions]
        )
        return FileUsage(used_bytes=used, file_count=len(items), version_bytes=used - current, version_count=len(versions))

    def rename(self, context: TenantContext, file_id: str, name: str) -> FileRecord | None:
        with self._lock:
            record = self.get(context, file_id)
            if record is None:
                return None
            renamed = record.model_copy(update={"name": name, "updated_by": context.user_id, "updated_at": utc_now()})
            self.records[file_id] = renamed
            return renamed

    def delete(self, context: TenantContext, file_id: str) -> bool:
        with self._lock:
            if self.get(context, file_id) is None:
                return False
            self.records.pop(file_id, None)
            for version in self._versions_of(context, file_id):
                self.versions.pop(version.id, None)
            return True

    def added_bytes(self, context: TenantContext, file_id: str, sha256: str, size: int) -> int:
        with self._lock:
            record = self.get(context, file_id)
            known = (record is not None and record.sha256 == sha256) or any(
                v.sha256 == sha256 for v in self._versions_of(context, file_id)
            )
        return 0 if known else size

    def replace_content(
        self, context: TenantContext, file_id: str, expected_sha256: str, content: FileContent,
        quota_bytes: int | None = None, source_version_id: str | None = None,
    ) -> FileRecord:
        with self._lock:
            record = self.get(context, file_id)
            if record is None or record.sha256 != expected_sha256:
                raise FileContentChanged(file_id)
            if source_version_id is not None and self.get_version(context, file_id, source_version_id) is None:
                raise FileContentChanged(file_id)
            if quota_bytes is not None:
                current = self.usage(context)
                if current.used_bytes + self.added_bytes(context, file_id, content.sha256, content.size_bytes) > quota_bytes:
                    raise StorageQuotaExceeded(current)
            old = previous_version(record, context.user_id)
            self.versions[old.id] = old
            updated = record.model_copy(update={**content.model_dump(), "version": record.version + 1})
            self.records[file_id] = updated
            return updated

    def list_versions(
        self, context: TenantContext, file_id: str, limit: int, offset: int
    ) -> tuple[list[FileVersionRecord], int]:
        with self._lock:
            items = sorted(self._versions_of(context, file_id), key=lambda v: v.version, reverse=True)
        return items[offset: offset + limit], len(items)

    def get_version(self, context: TenantContext, file_id: str, version_id: str) -> FileVersionRecord | None:
        with self._lock:
            return next((v for v in self._versions_of(context, file_id) if v.id == version_id), None)

    def delete_version(self, context: TenantContext, file_id: str, version_id: str) -> FileVersionRecord | None:
        with self._lock:
            version = self.get_version(context, file_id, version_id)
            if version is not None:
                self.versions.pop(version.id, None)
            return version

    def key_in_use(self, context: TenantContext, file_id: str, storage_key: str) -> bool:
        with self._lock:
            record = self.get(context, file_id)
            return (record is not None and record.storage_key == storage_key) or any(
                v.storage_key == storage_key for v in self._versions_of(context, file_id)
            )

    def storage_keys(self, context: TenantContext, file_id: str) -> list[str]:
        with self._lock:
            record = self.get(context, file_id)
            keys = [record.storage_key] if record is not None else []
            keys += [v.storage_key for v in self._versions_of(context, file_id)]
        return list(dict.fromkeys(keys))

    def storage_report(self, context: TenantContext, largest: int) -> StorageReport:
        with self._lock:
            items = self._scoped(context)
            versions = self._scoped_versions(context)
        return build_storage_report(self.usage(context), items, versions, largest)

    def record_scan(self, context: TenantContext, file_id: str, sha256: str, status: str, signature: str | None) -> bool:
        with self._lock:
            record = self.get(context, file_id)
            if record is None or record.sha256 != sha256 or record.scan_status is not None:
                return False
            self.records[file_id] = record.model_copy(
                update={"scan_status": status, "scan_signature": signature, "scanned_at": utc_now()}
            )
            return True

    def unscanned(self, after_id: str, limit: int) -> list[FileRecord]:
        """Memory backend discovery for the rescan job (ids in order, unscanned only)."""
        with self._lock:
            items = sorted((r for r in self.records.values() if r.scan_status is None and r.id > after_id), key=lambda r: r.id)
        return items[:limit]


file_store = FileStore(build_object_storage(settings))


@contextmanager
def open_file_metadata_store(context: TenantContext) -> Iterator[FileMetadataStore]:
    """File metadata for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.workspace_settings_repository import SqlAlchemyFileMetadataStore

        return SqlAlchemyFileMetadataStore(session)

    with open_scoped_store(context, file_store, sql_store) as store:
        yield store


# --------------------------------------------------------------------------- views

IndexState = Literal["indexed", "stale", "failed", "skipped", "pending"]


class FileIndex(BaseModel):
    """Retrieval index state of one file (docs/memory.md#retrieval)."""

    status: IndexState = Field(description="`pending`: no index row yet; `stale`: indexed with another embedding model")
    error: str | None = Field(default=None, description="Why a file was skipped or failed, e.g. `not_text`, `too_large`")
    chunk_count: int = 0
    embedding_model: str | None = None
    indexed_at: datetime | None = None


class FileView(FileRecord):
    kind: file_policy.FileKind | None = Field(default=None, description="None for a type uploaded before the allow-list")
    previewable: bool = False
    can_manage: bool = Field(default=False, description="Whether the caller may rename and delete this file")
    index: FileIndex | None = Field(default=None, description="None when the index state could not be read")


class FileUsageView(FileUsage):
    quota_bytes: int
    max_upload_bytes: int
    link_ttl_seconds: int
    accepted: str = Field(description="A sentence naming the accepted types")
    accepted_extensions: list[str]
    can_upload: bool
    can_manage_all: bool


class FileRename(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class FileLinkRequest(BaseModel):
    disposition: Literal["attachment", "inline"] = "attachment"


class FileLink(BaseModel):
    url: str = Field(description="Path on the API, valid without a bearer token until `expires_at`")
    expires_at: datetime


class FilePreview(BaseModel):
    kind: file_policy.FileKind
    text: str
    truncated: bool
    size_bytes: int


class FileVersionView(FileVersionRecord):
    downloadable: bool = Field(description="False for a quarantined version (the scanner found malware in it)")


class FileVersionPage(BaseModel):
    """A file's older versions, newest first; ``file`` is the current version."""

    file: FileView
    items: list[FileVersionView]
    total: int
    limit: int
    offset: int


class StorageKind(BaseModel):
    kind: file_policy.FileKind | Literal["other"]
    file_count: int
    bytes: int


class StorageFileView(BaseModel):
    file: FileView
    version_count: int
    version_bytes: int = Field(description="Bytes of older versions that differ from the current one")
    total_bytes: int


class StorageView(BaseModel):
    """Usage against the workspace quota, by kind, and the files that take the most space."""

    usage: FileUsageView
    by_kind: list[StorageKind]
    largest: list[StorageFileView]


router = APIRouter(prefix="/api/v1/files", tags=["files"])
PREVIEW_MAX_BYTES = 256 * 1024
_NAME = re.compile(r"[^\x00-\x1f\x7f\\/]+")
READ_DENIED = "Seeing files needs a viewer, member or owner role in this workspace. Ask a workspace owner for access."
UPLOAD_DENIED = "Uploading and changing files needs a member or owner role in this workspace. Ask a workspace owner."
MANAGE_DENIED = "Only the person who uploaded this file or a workspace owner can rename or delete it."


def _require(context: TenantContext, permission: Permission, message: str) -> None:
    if not has_permission(context, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, message)


def _can_manage(context: TenantContext, record: FileRecord) -> bool:
    if has_permission(context, Permission.FILE_MANAGE):
        return True
    return record.created_by == context.user_id and has_permission(context, Permission.FILE_UPLOAD)


def _valid_name(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or Path(name).name != name or not _NAME.fullmatch(name):
        raise HTTPException(422, "Invalid file name: it cannot be empty or contain /, \\ or control characters.")
    return name


def _record(file_id: str, context: TenantContext) -> FileRecord:
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
    if record is None:
        raise HTTPException(404, "File not found")
    return record


def quarantine_message(record: FileRecord) -> str:
    found = record.scan_signature or "malware"
    return (
        f"{record.name} is quarantined: the malware scanner found {found} in it, so it cannot be opened, "
        "previewed, shared or indexed. Delete it, or ask a workspace owner to."
    )


def _servable(file_id: str, context: TenantContext) -> FileRecord:
    """The record of a file whose bytes may be served; quarantined files answer 409."""
    record = _record(file_id, context)
    if record.scan_status == "infected":
        raise HTTPException(409, quarantine_message(record))
    return record


def _index_state(source: Any, model: str | None) -> FileIndex:
    if source is None:
        return FileIndex(status="pending")
    value: IndexState = source.status.value
    if value == "indexed" and model is not None and source.embedding_model != model:
        value = "stale"
    return FileIndex(status=value, error=source.error, chunk_count=source.chunk_count,
                     embedding_model=source.embedding_model, indexed_at=source.indexed_at)


def _views(context: TenantContext, records: list[FileRecord]) -> list[FileView]:
    from .retrieval import SourceType, open_retrieval_store
    from .retrieval_api import current_embedding_model

    sources: dict[str, Any] | None = {}
    model: str | None = None
    if records:
        try:
            with open_retrieval_store(context) as store:
                sources = store.get_sources(context, SourceType.FILE, [r.id for r in records])
            model = current_embedding_model(context)
        except Exception:  # noqa: BLE001 - the files still list; their index state reads as unknown.
            sources = None
    views = []
    for record in records:
        kind = file_policy.kind_of(record.content_type, record.name)
        views.append(FileView(
            **record.model_dump(),
            kind=kind,
            previewable=kind in file_policy.PREVIEWABLE_KINDS and record.scan_status != "infected",
            can_manage=_can_manage(context, record),
            index=None if sources is None else _index_state(sources.get(record.id), model),
        ))
    return views


def human_bytes(value: int) -> str:
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value} B"


def _quota_error(usage: FileUsage, size: int) -> ApplicationError:
    quota = settings.files_workspace_quota_bytes
    return ApplicationError(
        ErrorCode.PAYLOAD_TOO_LARGE,
        f"This workspace has used {human_bytes(usage.used_bytes)} of its {human_bytes(quota)} storage quota, so a "
        f"{human_bytes(size)} file does not fit. Delete files or older versions you no longer need (Storage lists "
        "the largest), or ask the operator to raise ANUM_FILES_WORKSPACE_QUOTA_BYTES.",
        status_code=413,
        details=[ErrorDetail(type="storage_quota", message=f"used={usage.used_bytes} quota={quota}")],
    )


def _too_large(limit: int) -> HTTPException:
    return HTTPException(413, f"Files can be at most {human_bytes(limit)}. Split or compress the file and upload it again.")


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


def resolve_upload_type(name: str, declared: str | None) -> file_policy.AllowedType:
    """The stored type of an upload named ``name``, or ``415`` naming what is accepted."""
    try:
        return file_policy.resolve_content_type(name, declared)
    except file_policy.UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc


SCANNER_STARTING = (
    "The malware scanner is still starting (it loads its virus signatures after a restart, "
    "which takes a few minutes), so nothing was stored. Try again in a few minutes."
)
SCANNER_UNAVAILABLE = "The malware scanner is unavailable, so nothing was stored. Try again later."


def check_upload_content(name: str, allowed: file_policy.AllowedType, content: bytes) -> bool:
    """The checks every uploaded file passes before it is stored: not empty, the content
    signature agrees with the type, and the malware-scan hook (docs/files.md#malware-scanning).

    Used by workspace uploads and by Email Center attachments (docs/email.md#attachments).
    Returns whether a scanner looked at the bytes (``False`` when none is installed).
    """
    if not content:
        raise HTTPException(422, "The file is empty. Choose a file with content.")
    if not file_policy.content_matches_type(content, allowed):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"The content of {name} is not a valid {allowed.content_type} file. Check the file and its extension.",
        )
    scanner = file_policy.file_scanner()
    if scanner is None:
        return False
    try:
        verdict = scanner.scan(name, allowed.content_type, content)
    except file_policy.ScannerStarting as exc:
        raise HTTPException(503, SCANNER_STARTING, headers={"Retry-After": "60"}) from exc
    except Exception as exc:
        raise HTTPException(503, SCANNER_UNAVAILABLE) from exc
    if not verdict.clean:
        found = verdict.reason or "malware"
        raise HTTPException(
            422,
            f"The malware scanner found {found} in {name}, so it was not stored. "
            "Delete the file and ask the sender for a clean copy.",
        )
    return verdict.scanned


async def read_upload_body(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 past ``limit`` bytes (declared or streamed)."""
    return await _read_body(request, limit)


@router.post("", response_model=FileView, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    context: TenantContext = Depends(tenant_context),
    name_param: str | None = Query(
        default=None, alias="name", min_length=1, max_length=255,
        description="The file name; use this (URL-encoded) for names that are not ASCII",
    ),
    x_file_name: str | None = Header(default=None, min_length=1, max_length=255),
    x_content_sha256: str | None = Header(default=None),
) -> FileView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    raw_name = name_param or x_file_name
    if raw_name is None:
        raise HTTPException(422, "Name the file: send ?name= (or the X-File-Name header for ASCII names).")
    if Path(raw_name).name != raw_name:
        raise HTTPException(422, "Invalid file name: send the name only, without a folder.")
    name = _valid_name(raw_name)
    allowed = resolve_upload_type(name, request.headers.get("content-type"))
    limit = settings.max_upload_body_bytes
    content = await _read_body(request, limit)
    digest = hashlib.sha256(content).hexdigest()
    if x_content_sha256 is not None and x_content_sha256.lower() != digest:
        raise HTTPException(422, "Content checksum mismatch: the upload was corrupted in transit. Try again.")
    # The scan is blocking network I/O (clamd): never on the event loop (docs/realtime.md#event-loop).
    scanned = await asyncio.to_thread(check_upload_content, name, allowed, content)
    quota = settings.files_workspace_quota_bytes
    with open_file_metadata_store(context) as store:
        current = store.usage(context)
    if current.used_bytes + len(content) > quota:
        raise _quota_error(current, len(content))
    file_id = new_id("file")
    try:
        key = workspace_object_key(context.tenant_id, context.workspace_id, "files", file_id, digest)
    except ValueError as exc:
        raise HTTPException(422, "Invalid tenant or workspace identifier") from exc
    now = utc_now()
    record = FileRecord(id=file_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        name=name, content_type=allowed.content_type, size_bytes=len(content), sha256=digest,
                        storage_key=key, created_by=context.user_id, created_at=now,
                        scan_status="clean" if scanned else None, scanned_at=now if scanned else None)
    file_store.storage.put(key, content, allowed.content_type)
    try:
        with open_file_metadata_store(context) as store:
            store.add(record, quota)
    except StorageQuotaExceeded as exc:
        # A concurrent upload took the space between the check above and this insert.
        file_store.storage.delete(key)
        raise _quota_error(exc.usage, len(content)) from exc
    except BaseException:
        # No metadata, no object: never leave unreachable bytes behind.
        file_store.storage.delete(key)
        raise
    # Text files become retrievable context (docs/memory.md#retrieval); best effort,
    # a failure is recorded on the index row and retried by POST /{id}/reindex.
    from .retrieval_api import index_uploaded_file

    await index_uploaded_file(context, record, content)
    # Automations can start on uploads (docs/automation.md#event-triggers); ids and metadata only.
    from .automation_triggers import record_inbound_events

    record_inbound_events(
        context,
        [
            (
                "file.uploaded",
                record.id,
                {
                    "file_id": record.id,
                    "name": record.name,
                    "content_type": record.content_type,
                    "size_bytes": record.size_bytes,
                    "uploaded_by": context.user_id,
                },
            )
        ],
    )
    return _views(context, [record])[0]


async def save_generated_file(context: TenantContext, name: str, content: bytes) -> FileView:
    """Store a file the API generated for the caller (a report export, docs/reports.md).

    The same rules as an upload apply: an allowed type for the name, the workspace quota,
    tenant-scoped object keys and metadata under RLS, and retrieval indexing for text.
    The caller checks ``FILE_UPLOAD`` first.
    """
    name = _valid_name(name)
    try:
        allowed = file_policy.resolve_content_type(name, None)
    except file_policy.UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    quota = settings.files_workspace_quota_bytes
    with open_file_metadata_store(context) as store:
        current = store.usage(context)
    if current.used_bytes + len(content) > quota:
        raise _quota_error(current, len(content))
    digest = hashlib.sha256(content).hexdigest()
    file_id = new_id("file")
    key = workspace_object_key(context.tenant_id, context.workspace_id, "files", file_id, digest)
    record = FileRecord(id=file_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        name=name, content_type=allowed.content_type, size_bytes=len(content), sha256=digest,
                        storage_key=key, created_by=context.user_id, created_at=utc_now())
    file_store.storage.put(key, content, allowed.content_type)
    try:
        with open_file_metadata_store(context) as store:
            store.add(record, quota)
    except StorageQuotaExceeded as exc:
        file_store.storage.delete(key)
        raise _quota_error(exc.usage, len(content)) from exc
    except BaseException:
        file_store.storage.delete(key)
        raise
    from .retrieval_api import index_uploaded_file

    await index_uploaded_file(context, record, content)
    return _views(context, [record])[0]


def file_link(context: TenantContext, record: FileRecord) -> FileLink:
    """A short-lived download link for ``record`` (the same link ``POST /{id}/links`` mints)."""
    expires_at = utc_now().replace(microsecond=0) + timedelta(seconds=settings.files_link_ttl_seconds)
    token = file_policy.sign_link(file_policy.LinkClaims(
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
        file_id=record.id, sha256=record.sha256, inline=False, expires_at=expires_at))
    return FileLink(url=f"{router.prefix}/links/{token}", expires_at=expires_at)


@router.get("", response_model=list[FileView])
def list_files(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=80, description="Id of the last file of the previous page"),
    q: str | None = Query(default=None, max_length=255, description="Case-insensitive part of the file name"),
    sort: FileSort = FileSort.CREATED_AT,
    order: Literal["asc", "desc"] | None = Query(
        default=None, description="Default: desc (newest, largest first); asc (A to Z) for name"
    ),
    context: TenantContext = Depends(tenant_context),
) -> list[FileView]:
    _require(context, Permission.FILE_READ, READ_DENIED)
    descending = (order or ("asc" if sort == FileSort.NAME else "desc")) == "desc"
    with open_file_metadata_store(context) as store:
        after = None
        if cursor:
            after = store.get(context, cursor)
            if after is None:
                raise HTTPException(400, "Unknown or invalid cursor: the file it names is gone. Reload the list.")
        records = store.list_page(
            context,
            FilePageQuery(limit=limit, search=(q or "").strip() or None, sort=sort, descending=descending, after=after),
        )
    return _views(context, records)


@router.get("/usage", response_model=FileUsageView)
def file_usage(context: TenantContext = Depends(tenant_context)) -> FileUsageView:
    _require(context, Permission.FILE_READ, READ_DENIED)
    with open_file_metadata_store(context) as store:
        usage = store.usage(context)
    return _usage_view(context, usage)


STORAGE_LARGEST = 10


@router.get("/storage", response_model=StorageView)
def storage_report(context: TenantContext = Depends(tenant_context)) -> StorageView:
    """Usage against the quota, split by kind, and the ten files (with their older versions) that take the most space."""
    _require(context, Permission.FILE_READ, READ_DENIED)
    with open_file_metadata_store(context) as store:
        report = store.storage_report(context, STORAGE_LARGEST)
    kinds: dict[str, list[int]] = {}
    for row in report.by_type:
        kind = file_policy.kind_of(row.content_type, "")
        entry = kinds.setdefault(kind.value if kind is not None else "other", [0, 0])
        entry[0] += row.file_count
        entry[1] += row.bytes
    by_kind = [StorageKind(kind=key, file_count=value[0], bytes=value[1]) for key, value in kinds.items()]  # type: ignore[arg-type]
    by_kind.sort(key=lambda item: (-item.bytes, str(item.kind)))
    views = _views(context, [item.record for item in report.largest])
    largest = [
        StorageFileView(file=view, version_count=item.version_count, version_bytes=item.version_bytes, total_bytes=item.total_bytes)
        for view, item in zip(views, report.largest, strict=True)
    ]
    return StorageView(usage=_usage_view(context, report.usage), by_kind=by_kind, largest=largest)


def _usage_view(context: TenantContext, usage: FileUsage) -> FileUsageView:
    return FileUsageView(
        **usage.model_dump(),
        quota_bytes=settings.files_workspace_quota_bytes,
        max_upload_bytes=settings.max_upload_body_bytes,
        link_ttl_seconds=settings.files_link_ttl_seconds,
        accepted=file_policy.ACCEPTED_SUMMARY,
        accepted_extensions=sorted(ext for allowed in file_policy.ALLOWED_TYPES for ext in allowed.extensions),
        can_upload=has_permission(context, Permission.FILE_UPLOAD),
        can_manage_all=has_permission(context, Permission.FILE_MANAGE),
    )


@router.get("/links/{token}")
def serve_link(token: str) -> Response:
    """The bytes behind a short-lived link. The signed link is the credential (no bearer
    token), so it works in ``<a href>`` and ``fetch`` without headers; it names the
    tenant, workspace, user and file it was minted for, and RLS applies as for that user."""
    try:
        claims = file_policy.verify_link(token)
    except file_policy.InvalidLink as exc:
        raise HTTPException(410, "This file link has expired or is invalid. Open the file again for a new link.") from exc
    context = TenantContext(tenant_id=claims.tenant_id, workspace_id=claims.workspace_id, user_id=claims.user_id)
    if settings.auth_mode == "oidc":
        from .dependencies import lookup_membership

        membership = lookup_membership(context)
        if membership is None or not membership.active:
            raise HTTPException(403, "The person who opened this link is no longer a member of the workspace.")
    with open_file_metadata_store(context) as store:
        record = store.get(context, claims.file_id)
    if record is None or record.sha256 != claims.sha256:
        raise HTTPException(404, "File not found")
    if record.scan_status == "infected":
        raise HTTPException(409, quarantine_message(record))
    try:
        content = file_store.storage.get(record.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    kind = file_policy.kind_of(record.content_type, record.name)
    inline = claims.inline and kind in file_policy.PREVIEWABLE_KINDS
    media = record.content_type if inline else "application/octet-stream"
    if inline and kind in file_policy.TEXT_PREVIEW_KINDS:
        media = "text/plain; charset=utf-8"
    headers = {
        "Content-Disposition": file_policy.content_disposition("inline" if inline else "attachment", record.name),
        "ETag": f'"{record.sha256}"',
        "Cache-Control": "private, no-store",
        # Opened directly, the bytes render with no script and no network access.
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox",
    }
    return Response(content, media_type=media, headers=headers)


@router.get("/{file_id}", response_model=FileView)
def get_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> FileView:
    _require(context, Permission.FILE_READ, READ_DENIED)
    return _views(context, [_record(file_id, context)])[0]


@router.patch("/{file_id}", response_model=FileView)
def rename_file(file_id: str, body: FileRename, context: TenantContext = Depends(tenant_context)) -> FileView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    name = _valid_name(body.name)
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
        if record is None:
            raise HTTPException(404, "File not found")
        if not _can_manage(context, record):
            raise HTTPException(403, MANAGE_DENIED)
        old_suffix = Path(record.name).suffix.lower()
        if Path(name).suffix.lower() != old_suffix:
            keep = f"the {old_suffix} extension" if old_suffix else "the name without an extension"
            raise HTTPException(422, f"Keep {keep}: renaming does not change a file's type.")
        renamed = store.rename(context, file_id, name)
    if renamed is None:
        raise HTTPException(404, "File not found")
    return _views(context, [renamed])[0]


@router.get("/{file_id}/content")
def download_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.FILE_READ, READ_DENIED)
    record = _servable(file_id, context)
    try:
        content = file_store.storage.get(record.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    return Response(content, media_type=record.content_type,
                    headers={"Content-Disposition": file_policy.content_disposition("attachment", record.name),
                             "ETag": record.sha256, "Cache-Control": "private, no-store"})


@router.get("/{file_id}/preview", response_model=FilePreview)
def preview_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> FilePreview:
    """Text, Markdown and data files as text, at most 256 KiB (``truncated`` says when more
    exists). Images and PDFs are previewed from their bytes; other kinds have no preview."""
    _require(context, Permission.FILE_READ, READ_DENIED)
    record = _servable(file_id, context)
    kind = file_policy.kind_of(record.content_type, record.name)
    if kind not in file_policy.TEXT_PREVIEW_KINDS:
        raise HTTPException(422, "Only text, Markdown and data files have a text preview. Download this file to open it.")
    try:
        content = file_store.storage.get(record.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    head = content[:PREVIEW_MAX_BYTES]
    return FilePreview(kind=kind, text=head.decode("utf-8", errors="replace"),
                       truncated=len(content) > PREVIEW_MAX_BYTES, size_bytes=record.size_bytes)


@router.post("/{file_id}/links", response_model=FileLink, status_code=status.HTTP_201_CREATED)
def create_link(file_id: str, body: FileLinkRequest | None = None,
                context: TenantContext = Depends(tenant_context)) -> FileLink:
    _require(context, Permission.FILE_READ, READ_DENIED)
    record = _servable(file_id, context)
    inline = (body or FileLinkRequest()).disposition == "inline"
    if inline and file_policy.kind_of(record.content_type, record.name) not in file_policy.PREVIEWABLE_KINDS:
        raise HTTPException(422, "This kind of file has no preview; ask for a download link instead.")
    expires_at = utc_now().replace(microsecond=0) + timedelta(seconds=settings.files_link_ttl_seconds)
    token = file_policy.sign_link(file_policy.LinkClaims(
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
        file_id=record.id, sha256=record.sha256, inline=inline, expires_at=expires_at))
    return FileLink(url=f"{router.prefix}/links/{token}", expires_at=expires_at)


@router.post("/{file_id}/reindex", response_model=FileView)
async def reindex_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> FileView:
    """Index one file for retrieval again (spends model budget for text files)."""
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    record = _servable(file_id, context)
    try:
        content = file_store.storage.get(record.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    from .retrieval_api import reindex_one_file

    await reindex_one_file(context, record, content)
    return _views(context, [record])[0]


# --------------------------------------------------------------------------- versions

VERSION_CHANGED = "This file changed or was deleted while you were working on it. Reload it and try again."
SAME_CONTENT = "This is the same content as the current version, so nothing changed."


def _delete_objects(keys: list[str]) -> None:
    for key in keys:
        try:
            file_store.storage.delete(key)
        except Exception as exc:  # noqa: BLE001 - the metadata is gone, so the bytes are unreachable either way.
            logger.warning("file_object_delete_failed error_class=%s", type(exc).__name__)


def _manageable(context: TenantContext, file_id: str) -> FileRecord:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    record = _record(file_id, context)
    if not _can_manage(context, record):
        raise HTTPException(403, VERSION_DENIED)
    return record


VERSION_DENIED = "Only the person who uploaded this file or a workspace owner can change its versions."


def _version(context: TenantContext, file_id: str, version_id: str) -> FileVersionRecord:
    with open_file_metadata_store(context) as store:
        version = store.get_version(context, file_id, version_id)
    if version is None:
        raise HTTPException(404, "Version not found: it may have been deleted. Reload the versions.")
    return version


def _version_quarantined(record: FileRecord, version: FileVersionRecord) -> str:
    found = version.scan_signature or "malware"
    return (
        f"Version {version.version} of {record.name} is quarantined: the malware scanner found {found} in it, "
        "so it cannot be downloaded or restored. Delete that version."
    )


def version_download_name(name: str, version: int) -> str:
    path = Path(name)
    return f"{path.stem} (version {version}){path.suffix}"


async def _make_current(
    context: TenantContext, record: FileRecord, content: FileContent, data: bytes, *, key_existed: bool,
    source_version_id: str | None = None,
) -> FileRecord:
    """Swap in new current bytes under the quota; never leave unreachable bytes behind."""
    quota = settings.files_workspace_quota_bytes
    try:
        with open_file_metadata_store(context) as store:
            updated = store.replace_content(context, record.id, record.sha256, content, quota, source_version_id)
    except StorageQuotaExceeded as exc:
        if not key_existed:
            file_store.storage.delete(content.storage_key)
        raise _quota_error(exc.usage, content.size_bytes) from exc
    except FileContentChanged as exc:
        if not key_existed:
            file_store.storage.delete(content.storage_key)
        raise HTTPException(409, VERSION_CHANGED) from exc
    except BaseException:
        if not key_existed:
            file_store.storage.delete(content.storage_key)
        raise
    # The index follows the current bytes (docs/memory.md#retrieval); best effort, as on upload.
    from .retrieval_api import reindex_one_file

    if updated.scan_status != "infected":
        await reindex_one_file(context, updated, data)
    return updated


@router.get("/{file_id}/versions", response_model=FileVersionPage)
def list_file_versions(
    file_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> FileVersionPage:
    """The current version and one page of older versions, newest first."""
    _require(context, Permission.FILE_READ, READ_DENIED)
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
        if record is None:
            raise HTTPException(404, "File not found")
        items, total = store.list_versions(context, file_id, limit, offset)
    return FileVersionPage(
        file=_views(context, [record])[0],
        items=[FileVersionView(**item.model_dump(), downloadable=item.scan_status != "infected") for item in items],
        total=total, limit=limit, offset=offset,
    )


@router.post("/{file_id}/versions", response_model=FileView, status_code=status.HTTP_201_CREATED)
async def upload_file_version(
    file_id: str,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    x_content_sha256: str | None = Header(default=None),
) -> FileView:
    """Upload new content for a file. The current bytes become an older version; the name and
    type stay (the content must match the file's extension), and the same checks as an upload
    apply: size, signature, the malware scan and the workspace quota."""
    record = _manageable(context, file_id)
    allowed = resolve_upload_type(record.name, request.headers.get("content-type"))
    content = await _read_body(request, settings.max_upload_body_bytes)
    digest = hashlib.sha256(content).hexdigest()
    if x_content_sha256 is not None and x_content_sha256.lower() != digest:
        raise HTTPException(422, "Content checksum mismatch: the upload was corrupted in transit. Try again.")
    scanned = await asyncio.to_thread(check_upload_content, record.name, allowed, content)
    if digest == record.sha256:
        raise HTTPException(409, SAME_CONTENT)
    quota = settings.files_workspace_quota_bytes
    with open_file_metadata_store(context) as store:
        current = store.usage(context)
        added = store.added_bytes(context, file_id, digest, len(content))
    if current.used_bytes + added > quota:
        raise _quota_error(current, len(content))
    try:
        key = workspace_object_key(context.tenant_id, context.workspace_id, "files", file_id, digest)
    except ValueError as exc:
        raise HTTPException(422, "Invalid tenant or workspace identifier") from exc
    now = utc_now()
    new = FileContent(content_type=allowed.content_type, size_bytes=len(content), sha256=digest, storage_key=key,
                      created_by=context.user_id, created_at=now,
                      scan_status="clean" if scanned else None, scanned_at=now if scanned else None)
    file_store.storage.put(key, content, allowed.content_type)
    updated = await _make_current(context, record, new, content, key_existed=added == 0)
    return _views(context, [updated])[0]


@router.get("/{file_id}/versions/{version_id}/content")
def download_file_version(file_id: str, version_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.FILE_READ, READ_DENIED)
    record = _record(file_id, context)
    version = _version(context, file_id, version_id)
    if version.scan_status == "infected":
        raise HTTPException(409, _version_quarantined(record, version))
    try:
        content = file_store.storage.get(version.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    name = version_download_name(record.name, version.version)
    return Response(content, media_type="application/octet-stream",
                    headers={"Content-Disposition": file_policy.content_disposition("attachment", name),
                             "ETag": f'"{version.sha256}"', "Cache-Control": "private, no-store"})


@router.post("/{file_id}/versions/{version_id}/restore", response_model=FileView)
async def restore_file_version(file_id: str, version_id: str, context: TenantContext = Depends(tenant_context)) -> FileView:
    """Make an older version current again. The current bytes become an older version too, so a
    restore can itself be undone; the restored bytes are shared, so it takes no extra space."""
    record = _manageable(context, file_id)
    version = _version(context, file_id, version_id)
    if version.scan_status == "infected":
        raise HTTPException(409, _version_quarantined(record, version))
    if version.sha256 == record.sha256:
        raise HTTPException(409, SAME_CONTENT)
    try:
        data = file_store.storage.get(version.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    new = FileContent(content_type=version.content_type, size_bytes=version.size_bytes, sha256=version.sha256,
                      storage_key=version.storage_key, created_by=context.user_id, created_at=utc_now(),
                      scan_status=version.scan_status, scan_signature=version.scan_signature, scanned_at=version.scanned_at)
    updated = await _make_current(context, record, new, data, key_existed=True, source_version_id=version.id)
    return _views(context, [updated])[0]


@router.delete("/{file_id}/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file_version(file_id: str, version_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Delete one older version (the current one is never deleted this way) to free its space."""
    _manageable(context, file_id)
    with open_file_metadata_store(context) as store:
        version = store.delete_version(context, file_id, version_id)
        if version is None:
            raise HTTPException(404, "Version not found: it may have been deleted. Reload the versions.")
        in_use = store.key_in_use(context, file_id, version.storage_key)
    # Shared bytes (identical content of another version) stay; others go after the commit.
    if not in_use:
        _delete_objects([version.storage_key])
    return Response(status_code=204)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
        if record is None:
            raise HTTPException(404, "File not found")
        if not _can_manage(context, record):
            raise HTTPException(403, MANAGE_DENIED)
        keys = store.storage_keys(context, file_id)
        store.delete(context, file_id)
    # Bytes (every version's) go only after the metadata delete committed, so a file is never
    # listed without content.
    _delete_objects(keys)
    # Retrieval already ignores chunks of a file without metadata; this drops them.
    from .retrieval_api import forget_file

    forget_file(context, file_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- shared helpers


def store_workspace_file(context: TenantContext, name: str, content: bytes, content_type: str) -> FileRecord:
    """Store bytes then metadata under the workspace quota; never leave unreachable bytes behind.

    Used by meeting recordings (docs/meetings.md), which are workspace files too.
    """
    name = _valid_name(name)
    quota = settings.files_workspace_quota_bytes
    with open_file_metadata_store(context) as store:
        current = store.usage(context)
    if current.used_bytes + len(content) > quota:
        raise _quota_error(current, len(content))
    digest = hashlib.sha256(content).hexdigest()
    file_id = new_id("file")
    try:
        key = workspace_object_key(context.tenant_id, context.workspace_id, "files", file_id, digest)
    except ValueError as exc:
        raise HTTPException(422, "Invalid tenant or workspace identifier") from exc
    record = FileRecord(id=file_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        name=name, content_type=content_type, size_bytes=len(content), sha256=digest,
                        storage_key=key, created_by=context.user_id, created_at=utc_now())
    file_store.storage.put(key, content, content_type)
    try:
        with open_file_metadata_store(context) as store:
            store.add(record, quota)
    except StorageQuotaExceeded as exc:
        file_store.storage.delete(key)
        raise _quota_error(exc.usage, len(content)) from exc
    except BaseException:
        file_store.storage.delete(key)
        raise
    return record


def remove_workspace_file(context: TenantContext, file_id: str) -> bool:
    """Delete one file's metadata, then (after that commit) its bytes and retrieval chunks."""
    with open_file_metadata_store(context) as store:
        record = store.get(context, file_id)
        if record is None:
            return False
        keys = store.storage_keys(context, file_id)
        store.delete(context, file_id)
    _delete_objects(keys)
    from .retrieval_api import forget_file

    forget_file(context, file_id)
    return True


def valid_file_name(value: str) -> str:
    return _valid_name(value)
