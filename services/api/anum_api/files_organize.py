"""Folders, tags and bulk actions for workspace files (docs/files.md#folders, #tags, #bulk-actions).

Routes under ``/api/v1/files`` (this router is included before ``files.router`` so that
``/folders``, ``/tags`` and ``/bulk/...`` are never read as a file id):

* ``GET``/``POST /folders``, ``PATCH``/``DELETE /folders/{id}``: the folder tree of the workspace.
  A folder is deleted empty, or with ``?contents=move_up`` (its files and subfolders go to its
  parent) or ``?contents=delete`` (everything inside is deleted, all or nothing).
* ``GET``/``POST /tags``, ``PATCH``/``DELETE /tags/{id}``, ``POST /{file_id}/tags``,
  ``DELETE /{file_id}/tags/{tag_id}``: the workspace's tags and the tags on one file.
* ``POST /bulk/move|delete|tag|untag``: one change to a list of files, all or nothing, at most
  ``BULK_MAX_FILES`` ids. ``POST /bulk/download``: the files as one zip, folders as directories,
  streamed from object storage through a temporary file.

Permissions follow the file routes: ``file:read`` to see, ``file:upload`` to create folders and
tags and to tag files, and the creator or ``file:manage`` to rename, move or delete a folder, a
tag or a file. Destructive and bulk actions write an audit record in the same unit of work.
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import file_policy
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, tenant_context
from .files import (
    FOLDER_NOT_FOUND,
    MANAGE_DENIED,
    MAX_FOLDERS,
    READ_DENIED,
    STREAM_CHUNK_BYTES,
    TAG_NOT_FOUND,
    UPLOAD_DENIED,
    FileFolderRecord,
    FileMetadataStore,
    FileNameTaken,
    FileRecord,
    FileTagRecord,
    FileView,
    _can_manage,
    _chunks,
    _delete_objects,
    _files_get,
    _require,
    _valid_name,
    _views,
    file_store,
    folder_depth,
    folder_descendants,
    folder_paths,
    human_bytes,
    open_file_metadata_store,
    quarantine_message,
)
from .schemas import TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/files", tags=["files"])

MAX_FOLDER_DEPTH = 10
MAX_TAGS = 500
MAX_TAG_NAME = 64
BULK_MAX_FILES = 500
FOLDER_DELETE_MAX_FILES = 2000
BULK_DOWNLOAD_MAX_BYTES = 1024 * 1024 * 1024
FOLDER_MANAGE_DENIED = "Only the person who created this folder or a workspace owner can rename, move or delete it."
TAG_MANAGE_DENIED = "Only the person who created this tag or a workspace owner can rename or delete it."
# A folder's contents when it is deleted: refuse unless empty, move everything one level up, or
# delete everything inside (docs/files.md#folders).
ContentsChoice = Literal["refuse", "move_up", "delete"]


# --------------------------------------------------------------------------- views


class FolderView(BaseModel):
    id: str
    name: str
    parent_id: str | None
    path: str = Field(description="The names from the root joined with ' / '")
    depth: int = Field(description="1 for a folder at the root")
    file_count: int = Field(description="Files directly in this folder")
    can_manage: bool = Field(description="Whether the caller may rename, move or delete it")
    created_by: str
    created_at: datetime
    updated_by: str | None = None
    updated_at: datetime | None = None


class FolderTree(BaseModel):
    """Every folder of the workspace (at most `limit`), A to Z by name; clients build the tree from `parent_id`."""

    items: list[FolderView]
    limit: int
    max_depth: int


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = Field(default=None, max_length=80)


class FolderUpdate(BaseModel):
    """Rename and/or move: `name` when given, `parent_id` when given (null moves to the root)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = Field(default=None, max_length=80)


class TagView(BaseModel):
    id: str
    name: str
    file_count: int
    can_manage: bool
    created_by: str
    created_at: datetime


class TagList(BaseModel):
    items: list[TagView]
    limit: int


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_TAG_NAME)


class TagIds(BaseModel):
    tag_ids: list[str] = Field(min_length=1, max_length=50)


class BulkFiles(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=BULK_MAX_FILES)


class BulkMove(BulkFiles):
    folder_id: str | None = Field(default=None, max_length=80, description="The target folder; null for the root")


class BulkTags(BulkFiles):
    tag_ids: list[str] = Field(min_length=1, max_length=50)


class BulkResult(BaseModel):
    count: int = Field(description="Files moved or deleted, or tag links added or removed")


# --------------------------------------------------------------------------- helpers


def _audit(store: FileMetadataStore, context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=target, outcome="success", correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata=metadata,
        )
    )


def _folder_view(folder: FileFolderRecord, paths: dict[str, str], depths: dict[str, int], counts: dict[str, int], context: TenantContext) -> FolderView:
    return FolderView(
        id=folder.id, name=folder.name, parent_id=folder.parent_id, path=paths.get(folder.id, folder.name),
        depth=depths.get(folder.id, 1), file_count=counts.get(folder.id, 0), can_manage=_owns(context, folder.created_by),
        created_by=folder.created_by, created_at=folder.created_at, updated_by=folder.updated_by, updated_at=folder.updated_at,
    )


def _owns(context: TenantContext, created_by: str) -> bool:
    if has_permission(context, Permission.FILE_MANAGE):
        return True
    return created_by == context.user_id and has_permission(context, Permission.FILE_UPLOAD)


def _tree(store: FileMetadataStore, context: TenantContext) -> list[FileFolderRecord]:
    return store.list_folders(context, MAX_FOLDERS)


def _folder_views(store: FileMetadataStore, context: TenantContext, folders: list[FileFolderRecord]) -> list[FolderView]:
    paths = folder_paths(folders)
    depths = {f.id: folder_depth(folders, f.id) for f in folders}
    counts = store.folder_file_counts(context)
    return [_folder_view(f, paths, depths, counts, context) for f in folders]


def _tag_name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > MAX_TAG_NAME or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise HTTPException(422, f"Tag names are 1 to {MAX_TAG_NAME} characters without control characters.")
    return name


def _folder_or_404(store: FileMetadataStore, context: TenantContext, folder_id: str) -> FileFolderRecord:
    folder = store.get_folder(context, folder_id)
    if folder is None:
        raise HTTPException(404, FOLDER_NOT_FOUND)
    return folder


def _tag_or_404(store: FileMetadataStore, context: TenantContext, tag_id: str) -> FileTagRecord:
    tag = store.get_tag(context, tag_id)
    if tag is None:
        raise HTTPException(404, TAG_NOT_FOUND)
    return tag


def _check_parent(folders: list[FileFolderRecord], parent_id: str | None, moving: str | None) -> None:
    """The parent exists, is not the moved folder or one below it, and keeps the tree within the depth cap."""
    if parent_id is not None:
        if parent_id not in {f.id for f in folders}:
            raise HTTPException(404, FOLDER_NOT_FOUND)
        if moving is not None and (parent_id == moving or parent_id in set(folder_descendants(folders, moving))):
            raise HTTPException(409, "A folder cannot be moved into itself or into one of its own subfolders.")
    # How many levels the moved subtree takes (1 for a new or leaf folder).
    height = 1
    if moving is not None:
        top = folder_depth(folders, moving)
        height = 1 + max((folder_depth(folders, below) - top for below in folder_descendants(folders, moving)), default=0)
    if folder_depth(folders, parent_id) + height > MAX_FOLDER_DEPTH:
        raise HTTPException(409, f"Folders can be at most {MAX_FOLDER_DEPTH} levels deep. Choose a folder closer to the root.")


def _taken(name: str, what: str) -> HTTPException:
    return HTTPException(409, f"A {what} named {name} already exists here. Choose another name.")


def _bulk_records(store: FileMetadataStore, context: TenantContext, file_ids: list[str]) -> list[FileRecord]:
    """Every listed file, or 404 naming how many are gone (all or nothing)."""
    wanted = list(dict.fromkeys(file_ids))
    records = {r.id: r for r in store.get_many(context, wanted)}
    missing = [one for one in wanted if one not in records]
    if missing:
        raise HTTPException(404, f"{len(missing)} of the {len(wanted)} selected files no longer exist in this workspace. Reload the list and try again.")
    return [records[one] for one in wanted]


def _all_manageable(context: TenantContext, records: list[FileRecord], verb: str) -> None:
    refused = [r for r in records if not _can_manage(context, r)]
    if refused:
        raise HTTPException(
            403,
            f"{len(refused)} of the {len(records)} selected files were uploaded by someone else, and only the uploader or a "
            f"workspace owner can {verb} them. Nothing was changed.",
        )


def _remove_files(context: TenantContext, records: list[FileRecord], store: FileMetadataStore) -> list[str]:
    """Delete the rows (in the open unit of work) and return every object key to delete after the commit."""
    keys: list[str] = []
    for record in records:
        keys += store.storage_keys(context, record.id)
    store.delete_files(context, [r.id for r in records])
    return list(dict.fromkeys(keys))


def _after_delete(context: TenantContext, keys: list[str], file_ids: list[str]) -> None:
    _delete_objects(keys)
    from .retrieval_api import forget_file

    for file_id in file_ids:
        forget_file(context, file_id)


# --------------------------------------------------------------------------- folders


@router.get("/folders", response_model=FolderTree)
def list_folders(context: TenantContext = Depends(tenant_context)) -> FolderTree:
    _require(context, Permission.FILE_READ, READ_DENIED)
    with open_file_metadata_store(context) as store:
        folders = _tree(store, context)
        items = _folder_views(store, context, folders)
    return FolderTree(items=items, limit=MAX_FOLDERS, max_depth=MAX_FOLDER_DEPTH)


@router.post("/folders", response_model=FolderView, status_code=status.HTTP_201_CREATED)
def create_folder(body: FolderCreate, context: TenantContext = Depends(tenant_context)) -> FolderView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    name = _valid_name(body.name)
    with open_file_metadata_store(context) as store:
        folders = _tree(store, context)
        if len(folders) >= MAX_FOLDERS:
            raise HTTPException(409, f"This workspace already has {MAX_FOLDERS} folders, the most it can hold. Delete folders you no longer need.")
        _check_parent(folders, body.parent_id, None)
        folder = FileFolderRecord(id=new_id("folder"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                  name=name, parent_id=body.parent_id, created_by=context.user_id, created_at=utc_now())
        try:
            store.add_folder(folder)
        except FileNameTaken as exc:
            raise _taken(name, "folder") from exc
        views = _folder_views(store, context, [*folders, folder])
    return next(v for v in views if v.id == folder.id)


@router.patch("/folders/{folder_id}", response_model=FolderView)
def update_folder(folder_id: str, body: FolderUpdate, context: TenantContext = Depends(tenant_context)) -> FolderView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        folder = _folder_or_404(store, context, folder_id)
        if not _owns(context, folder.created_by):
            raise HTTPException(403, FOLDER_MANAGE_DENIED)
        name = _valid_name(body.name) if body.name is not None else folder.name
        parent_id = body.parent_id if "parent_id" in body.model_fields_set else folder.parent_id
        folders = _tree(store, context)
        if parent_id != folder.parent_id:
            _check_parent(folders, parent_id, folder.id)
        try:
            updated = store.update_folder(context, folder_id, name=name, parent_id=parent_id)
        except FileNameTaken as exc:
            raise _taken(name, "folder") from exc
        if updated is None:
            raise HTTPException(404, FOLDER_NOT_FOUND)
        folders = [updated if f.id == updated.id else f for f in folders]
        views = _folder_views(store, context, folders)
    return next(v for v in views if v.id == folder_id)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(
    folder_id: str,
    contents: ContentsChoice = Query(default="refuse", description="What happens to files and subfolders inside"),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    keys: list[str] = []
    gone: list[str] = []
    with open_file_metadata_store(context) as store:
        folder = _folder_or_404(store, context, folder_id)
        if not _owns(context, folder.created_by):
            raise HTTPException(403, FOLDER_MANAGE_DENIED)
        folders = _tree(store, context)
        below = folder_descendants(folders, folder_id)
        subfolders = len(below)
        files = store.count_folder_files(context, [folder_id, *below])
        if (subfolders or files) and contents == "refuse":
            raise HTTPException(
                409,
                f"{folder.name} is not empty: it holds {files} file{'s' if files != 1 else ''} and {subfolders} "
                f"subfolder{'s' if subfolders != 1 else ''}. Move them out first, or delete the folder with its contents "
                "moved to the parent folder or deleted.",
            )
        if contents == "move_up":
            direct = store.file_ids_in_folders(context, [folder_id], FOLDER_DELETE_MAX_FILES + 1)
            if len(direct) > FOLDER_DELETE_MAX_FILES:
                raise HTTPException(409, f"{folder.name} holds more than {FOLDER_DELETE_MAX_FILES} files. Move them in parts first.")
            store.move_files(context, direct, folder.parent_id)
            for child in [f for f in folders if f.parent_id == folder_id]:
                try:
                    store.update_folder(context, child.id, name=child.name, parent_id=folder.parent_id)
                except FileNameTaken as exc:
                    raise HTTPException(
                        409, f"The parent folder already has a folder named {child.name}. Rename one of them first."
                    ) from exc
        elif contents == "delete":
            inside = store.file_ids_in_folders(context, [folder_id, *below], FOLDER_DELETE_MAX_FILES + 1)
            if len(inside) > FOLDER_DELETE_MAX_FILES:
                raise HTTPException(409, f"{folder.name} holds more than {FOLDER_DELETE_MAX_FILES} files. Delete them in parts first.")
            records = _bulk_records(store, context, inside)
            _all_manageable(context, records, "delete")
            keys = _remove_files(context, records, store)
            gone = [r.id for r in records]
        if not store.delete_folder(context, folder_id):
            raise HTTPException(404, FOLDER_NOT_FOUND)
        _audit(store, context, "file.folder_deleted", folder_id,
               {"name": folder.name, "contents": contents, "files": files, "subfolders": subfolders, "deleted_files": gone})
    _after_delete(context, keys, gone)
    return Response(status_code=204)


# --------------------------------------------------------------------------- tags


def _tag_views(store: FileMetadataStore, context: TenantContext) -> list[TagView]:
    return [
        TagView(id=tag.id, name=tag.name, file_count=count, can_manage=_owns(context, tag.created_by),
                created_by=tag.created_by, created_at=tag.created_at)
        for tag, count in store.list_tags(context, MAX_TAGS)
    ]


@router.get("/tags", response_model=TagList)
def list_tags(context: TenantContext = Depends(tenant_context)) -> TagList:
    _require(context, Permission.FILE_READ, READ_DENIED)
    with open_file_metadata_store(context) as store:
        items = _tag_views(store, context)
    return TagList(items=items, limit=MAX_TAGS)


@router.post("/tags", response_model=TagView, status_code=status.HTTP_201_CREATED)
def create_tag(body: TagCreate, context: TenantContext = Depends(tenant_context)) -> TagView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    name = _tag_name(body.name)
    with open_file_metadata_store(context) as store:
        if len(store.list_tags(context, MAX_TAGS)) >= MAX_TAGS:
            raise HTTPException(409, f"This workspace already has {MAX_TAGS} tags, the most it can hold. Delete tags you no longer use.")
        tag = FileTagRecord(id=new_id("tag"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                            name=name, created_by=context.user_id, created_at=utc_now())
        try:
            store.add_tag(tag)
        except FileNameTaken as exc:
            raise _taken(name, "tag") from exc
    return TagView(id=tag.id, name=tag.name, file_count=0, can_manage=True, created_by=tag.created_by, created_at=tag.created_at)


@router.patch("/tags/{tag_id}", response_model=TagView)
def rename_tag(tag_id: str, body: TagCreate, context: TenantContext = Depends(tenant_context)) -> TagView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    name = _tag_name(body.name)
    with open_file_metadata_store(context) as store:
        tag = _tag_or_404(store, context, tag_id)
        if not _owns(context, tag.created_by):
            raise HTTPException(403, TAG_MANAGE_DENIED)
        try:
            renamed = store.rename_tag(context, tag_id, name)
        except FileNameTaken as exc:
            raise _taken(name, "tag") from exc
        if renamed is None:
            raise HTTPException(404, TAG_NOT_FOUND)
        views = _tag_views(store, context)
    return next(v for v in views if v.id == tag_id)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        tag = _tag_or_404(store, context, tag_id)
        if not _owns(context, tag.created_by):
            raise HTTPException(403, TAG_MANAGE_DENIED)
        count = next((n for t, n in store.list_tags(context, MAX_TAGS) if t.id == tag_id), 0)
        if not store.delete_tag(context, tag_id):
            raise HTTPException(404, TAG_NOT_FOUND)
        _audit(store, context, "file.tag_deleted", tag_id, {"name": tag.name, "files": count})
    return Response(status_code=204)


@router.post("/{file_id}/tags", response_model=FileView)
def tag_file(file_id: str, body: TagIds, context: TenantContext = Depends(tenant_context)) -> FileView:
    """Add tags to one file (tags it already carries stay). Any member may tag any file."""
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        record = _files_get(store, context, file_id)
        if record is None:
            raise HTTPException(404, "File not found")
        for tag_id in body.tag_ids:
            _tag_or_404(store, context, tag_id)
        store.tag_files(context, [file_id], body.tag_ids)
    return _views(context, [record])[0]


@router.delete("/{file_id}/tags/{tag_id}", response_model=FileView)
def untag_file(file_id: str, tag_id: str, context: TenantContext = Depends(tenant_context)) -> FileView:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        record = _files_get(store, context, file_id)
        if record is None:
            raise HTTPException(404, "File not found")
        store.untag_files(context, [file_id], [tag_id])
    return _views(context, [record])[0]


# --------------------------------------------------------------------------- bulk actions


@router.post("/bulk/move", response_model=BulkResult)
def bulk_move(body: BulkMove, context: TenantContext = Depends(tenant_context)) -> BulkResult:
    """Move the files into a folder (null: the root). All or nothing: every file must exist and be the caller's or `file:manage`."""
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        if body.folder_id is not None:
            _folder_or_404(store, context, body.folder_id)
        records = _bulk_records(store, context, body.file_ids)
        _all_manageable(context, records, "move")
        moved = store.move_files(context, [r.id for r in records], body.folder_id)
        _audit(store, context, "file.bulk_moved", body.folder_id or "root", {"file_ids": [r.id for r in records], "moved": moved})
    return BulkResult(count=moved)


@router.post("/bulk/delete", response_model=BulkResult)
def bulk_delete(body: BulkFiles, context: TenantContext = Depends(tenant_context)) -> BulkResult:
    """Delete the files: rows in one unit of work, then every version's bytes and the retrieval chunks."""
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        records = _bulk_records(store, context, body.file_ids)
        _all_manageable(context, records, "delete")
        keys = _remove_files(context, records, store)
        _audit(store, context, "file.bulk_deleted", "files", {"file_ids": [r.id for r in records], "names": [r.name for r in records]})
    _after_delete(context, keys, [r.id for r in records])
    return BulkResult(count=len(records))


def _bulk_tags(store: FileMetadataStore, context: TenantContext, body: BulkTags) -> list[FileRecord]:
    for tag_id in dict.fromkeys(body.tag_ids):
        _tag_or_404(store, context, tag_id)
    return _bulk_records(store, context, body.file_ids)


@router.post("/bulk/tag", response_model=BulkResult)
def bulk_tag(body: BulkTags, context: TenantContext = Depends(tenant_context)) -> BulkResult:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        records = _bulk_tags(store, context, body)
        added = store.tag_files(context, [r.id for r in records], body.tag_ids)
        _audit(store, context, "file.bulk_tagged", "files", {"file_ids": [r.id for r in records], "tag_ids": body.tag_ids, "added": added})
    return BulkResult(count=added)


@router.post("/bulk/untag", response_model=BulkResult)
def bulk_untag(body: BulkTags, context: TenantContext = Depends(tenant_context)) -> BulkResult:
    _require(context, Permission.FILE_UPLOAD, UPLOAD_DENIED)
    with open_file_metadata_store(context) as store:
        records = _bulk_tags(store, context, body)
        removed = store.untag_files(context, [r.id for r in records], body.tag_ids)
        _audit(store, context, "file.bulk_untagged", "files", {"file_ids": [r.id for r in records], "tag_ids": body.tag_ids, "removed": removed})
    return BulkResult(count=removed)


def archive_entry_name(folder_path: str | None, name: str, used: set[str]) -> str:
    """``<folder>/<sub>/<name>`` inside the zip, with `` (2)`` and so on before the extension when taken."""
    prefix = "" if not folder_path else "/".join(part.strip() for part in folder_path.split(" / ")) + "/"
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    candidate = prefix + name
    n = 2
    while candidate.lower() in used:
        candidate = f"{prefix}{stem} ({n}){'.' + ext if ext else ''}"
        n += 1
    used.add(candidate.lower())
    return candidate


@router.post("/bulk/download")
def bulk_download(body: BulkFiles, context: TenantContext = Depends(tenant_context)) -> StreamingResponse:
    """The selected files as one zip, their folders as directories. Each file's bytes stream from
    object storage into a temporary file, which is then sent in chunks; the response never holds
    a whole file in memory. Quarantined files and more than ``BULK_DOWNLOAD_MAX_BYTES`` are refused."""
    _require(context, Permission.FILE_READ, READ_DENIED)
    with open_file_metadata_store(context) as store:
        records = _bulk_records(store, context, body.file_ids)
        paths = folder_paths(store.list_folders(context, MAX_FOLDERS)) if any(r.folder_id for r in records) else {}
    quarantined = [r for r in records if r.scan_status == "infected"]
    if quarantined:
        raise HTTPException(409, quarantine_message(quarantined[0]))
    total = sum(r.size_bytes for r in records)
    if total > BULK_DOWNLOAD_MAX_BYTES:
        raise HTTPException(
            413,
            f"The selected files add up to {human_bytes(total)}; one download can be at most "
            f"{human_bytes(BULK_DOWNLOAD_MAX_BYTES)}. Select fewer files.",
        )
    archive_file = tempfile.TemporaryFile()
    used: set[str] = set()
    try:
        with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            # Folder by folder, then by name; same-named files in one folder in upload order.
            for record in sorted(records, key=lambda r: ((paths.get(r.folder_id or "") or ""), r.name.lower(), r.created_at, r.id)):
                entry = zipfile.ZipInfo(archive_entry_name(paths.get(record.folder_id or ""), record.name, used), date_time=record.created_at.timetuple()[:6])
                kind = file_policy.kind_of(record.content_type, record.name)
                # Text compresses; images, PDFs, archives and video are already compressed.
                entry.compress_type = zipfile.ZIP_DEFLATED if kind in file_policy.TEXT_PREVIEW_KINDS else zipfile.ZIP_STORED
                try:
                    chunks = file_store.storage.open_stream(record.storage_key, STREAM_CHUNK_BYTES)
                except FileNotFoundError as exc:
                    raise HTTPException(404, f"The content of {record.name} is missing from storage. Nothing was downloaded.") from exc
                with archive.open(entry, "w", force_zip64=True) as target:
                    for chunk in chunks:
                        target.write(chunk)
        with open_file_metadata_store(context) as store:
            _audit(store, context, "file.bulk_downloaded", "files", {"file_ids": [r.id for r in records], "bytes": total})
    except BaseException:
        archive_file.close()
        raise
    size = archive_file.tell()
    archive_file.seek(0)
    name = f"files-{utc_now():%Y%m%d-%H%M}.zip"
    return StreamingResponse(
        _chunks(archive_file, STREAM_CHUNK_BYTES),
        media_type="application/zip",
        headers={
            "Content-Disposition": file_policy.content_disposition("attachment", name),
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
        },
    )
