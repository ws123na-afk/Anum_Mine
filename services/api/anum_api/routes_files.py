"""File attachment endpoints backed by object storage + the repository.

Not mounted on `anum_api.main.app` here — this module only defines the
`APIRouter`; wiring it into the app (and building the object storage client
dependency into `anum_api.dependencies`) is owned by another part of this
build.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from .authorization import Permission
from .dependencies import (
    idempotency_key_header,
    repository_context,
    require_permission,
    tenant_context,
)
from .idempotency_support import run_idempotently
from .object_storage import (
    ObjectStorageClient,
    ObjectStorageNotConfiguredError,
    build_object_storage_client,
)
from .repository import AnumRepository
from .schemas import FileObject, TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/files", tags=["files"])


class FileDownloadResponse(FileObject):
    download_url: str


async def object_storage_context() -> ObjectStorageClient:
    """Build (or reject) the object storage client for this request.

    A dedicated dependency (rather than a module-level singleton) so tests
    can override it with a moto-backed client via
    `app.dependency_overrides[object_storage_context]`.
    """

    try:
        client = build_object_storage_client()
    except ObjectStorageNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is not configured",
        )
    return client


def _object_key(context: TenantContext, file_id: str, filename: str | None) -> str:
    safe_name = filename or file_id
    return f"{context.tenant_id}/{context.workspace_id}/{file_id}/{safe_name}"


@router.post("", response_model=FileObject, status_code=status.HTTP_201_CREATED)
async def upload_file(
    upload: UploadFile = File(...),
    task_id: str | None = Form(default=None),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    storage: ObjectStorageClient = Depends(object_storage_context),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.FILE_CREATE)

    if task_id is not None and repository.get_task(task_id, context) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    data = await upload.read()
    content_type = upload.content_type or "application/octet-stream"
    content_checksum = hashlib.sha256(data).hexdigest()

    async def _upload_file() -> tuple[int, FileObject]:
        file_id = new_id("file")
        key = _object_key(context, file_id, upload.filename)
        result = storage.upload(key, data, content_type)
        file = FileObject(
            id=file_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=task_id,
            owner_user_id=context.user_id,
            bucket=result["bucket"],
            key=key,
            checksum_sha256=result["checksum_sha256"],
            size_bytes=result["size_bytes"],
            content_type=content_type,
            created_at=utc_now(),
        )
        repository.save_file(file)
        return status.HTTP_201_CREATED, file

    return await run_idempotently(
        context=context,
        action="file.create",
        key=idempotency_key,
        payload={
            "task_id": task_id,
            "filename": upload.filename,
            "content_type": content_type,
            "checksum_sha256": content_checksum,
        },
        execute=_upload_file,
    )


@router.get("/{file_id}", response_model=FileDownloadResponse)
async def get_file(
    file_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    storage: ObjectStorageClient = Depends(object_storage_context),
) -> FileDownloadResponse:
    require_permission(context, Permission.FILE_READ)
    file = repository.get_file(file_id, context)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    download_url = storage.generate_presigned_download_url(file.key)
    return FileDownloadResponse(**file.model_dump(), download_url=download_url)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    storage: ObjectStorageClient = Depends(object_storage_context),
    idempotency_key: str | None = Depends(idempotency_key_header),
) -> Response:
    require_permission(context, Permission.FILE_DELETE)

    async def _delete_file() -> tuple[int, None]:
        file = repository.get_file(file_id, context)
        if file is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        storage.delete(file.key)
        repository.delete_file(file_id, context)
        return status.HTTP_204_NO_CONTENT, None

    return await run_idempotently(
        context=context,
        action="file.delete",
        key=idempotency_key,
        payload={"file_id": file_id},
        execute=_delete_file,
    )
