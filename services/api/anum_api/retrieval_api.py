"""Retrieval index API and the indexing hooks used by memory and file routes.

* ``GET /api/v1/retrieval/status``: index state of the workspace's memories and files
  (status, error code, chunk count, embedding model; never text). Memory read permission.
* ``POST /api/v1/retrieval/index``: (re)index memories and files that are missing,
  failed, changed or embedded with another model, and drop index rows whose memory or
  file is gone or expired. Memory create permission, because it spends model budget.
  After an embedding model change it re-embeds sources still on the old model, at most
  ``limit`` per call, and reports ``reembedded`` and ``stale_remaining``.

Creating a memory indexes it in the same transaction; uploading a text file indexes it
after its metadata is committed. Indexing failures (provider down, budget used up) are
recorded on the source and never fail the create; ``POST /index`` retries them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import memory_repository_context, require_permission, tenant_context
from .memory import CONVERSATION_SOURCE_TYPE, MemoryListFilters, MemoryRepository
from .model_gateway import ModelGateway, build_default_model_gateway
from .retrieval import (
    Embedder,
    IndexOutcome,
    IndexStatus,
    RetrievalIndexer,
    RetrievalIndexStatus,
    RetrievalSource,
    RetrievalStore,
    Retriever,
    SourceType,
    build_embedder,
    index_status,
    is_stale,
    open_retrieval_store,
    retrieval_store_for_session,
    sha256_text,
)
from .schemas import TenantContext

logger = logging.getLogger("anum.retrieval")

_default_gateway: ModelGateway | None = None


def default_model_gateway() -> ModelGateway:
    """The server's default model (used when a workspace saved none), built once."""
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = build_default_model_gateway()
    return _default_gateway


def retriever_for(context: TenantContext, fallback_gateway: ModelGateway | None = None) -> Retriever:
    """The retriever the runtime uses for one tenant/workspace."""
    return Retriever(build_embedder(context, fallback_gateway or default_model_gateway()))


@contextmanager
def _savepoint(session: Any | None) -> Iterator[None]:
    """Keep an indexing failure from poisoning the caller's transaction."""
    if session is None:
        yield
        return
    nested = session.begin_nested()
    try:
        yield
        nested.commit()
    except BaseException:
        nested.rollback()
        raise


def _log_hook_failure(action: str, exc: BaseException) -> None:
    logger.warning(
        "retrieval_hook_failed action=%s error_class=%s",
        action,
        type(exc).__name__,
        extra={"anum_retrieval": {"event": "hook_failed", "action": action, "error_class": type(exc).__name__}},
    )


async def index_memory_note(
    context: TenantContext, note: Any, memories: MemoryRepository, *, embedder: Embedder | None = None
) -> IndexOutcome | None:
    """Index a just-created memory in the memory route's own transaction (best effort)."""
    session = getattr(memories, "session", None)
    try:
        with _savepoint(session):
            indexer = RetrievalIndexer(
                retrieval_store_for_session(session), embedder or build_embedder(context, default_model_gateway())
            )
            return await indexer.index_memory(context, note)
    except Exception as exc:
        _log_hook_failure("index_memory", exc)
        return None


def forget_memory(context: TenantContext, memory_id: str, memories: MemoryRepository) -> None:
    session = getattr(memories, "session", None)
    try:
        with _savepoint(session):
            retrieval_store_for_session(session).delete_source(context, SourceType.MEMORY, memory_id)
    except Exception as exc:
        _log_hook_failure("forget_memory", exc)


async def index_uploaded_file(context: TenantContext, record: Any, content: bytes) -> IndexOutcome | None:
    """Index an uploaded file after its metadata committed (best effort)."""
    try:
        with open_retrieval_store(context) as store:
            indexer = RetrievalIndexer(store, build_embedder(context, default_model_gateway()))
            return await indexer.index_file(context, record, content)
    except Exception as exc:
        _log_hook_failure("index_file", exc)
        return None


def forget_file(context: TenantContext, file_id: str) -> None:
    try:
        with open_retrieval_store(context) as store:
            store.delete_source(context, SourceType.FILE, file_id)
    except Exception as exc:
        _log_hook_failure("forget_file", exc)


router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


class ReindexResult(BaseModel):
    indexed: int = 0
    reused: int = 0
    failed: int = 0
    skipped: int = 0
    removed: int = Field(default=0, description="Index rows whose memory or file is gone or expired")
    remaining: int = Field(default=0, description="Sources left for another call (limit reached)")
    reembedded: int = Field(
        default=0, description="Of `indexed`: sources re-embedded because their stored embedding model was not the current one"
    )
    stale_remaining: int = Field(default=0, description="Of `remaining`: sources still on another embedding model")
    status: RetrievalIndexStatus


def _workspace_store(memories: MemoryRepository) -> RetrievalStore:
    return retrieval_store_for_session(getattr(memories, "session", None))


@router.get("/status", response_model=RetrievalIndexStatus)
async def retrieval_status(
    context: TenantContext = Depends(tenant_context),
    memories: MemoryRepository = Depends(memory_repository_context),
) -> RetrievalIndexStatus:
    require_permission(context, Permission.MEMORY_READ)
    store = _workspace_store(memories)
    embedder = build_embedder(context, default_model_gateway())
    return index_status(store.list_sources(context), embedder.model_name)


@router.post("/index", response_model=ReindexResult)
async def reindex(
    limit: int = Query(default=100, ge=1, le=500),
    context: TenantContext = Depends(tenant_context),
    memories: MemoryRepository = Depends(memory_repository_context),
) -> ReindexResult:
    require_permission(context, Permission.MEMORY_CREATE)
    from .files import file_store, open_file_metadata_store

    now = datetime.now(timezone.utc)
    store: RetrievalStore = _workspace_store(memories)
    embedder = build_embedder(context, default_model_gateway())
    indexer = RetrievalIndexer(store, embedder)
    result = ReindexResult(status=index_status([], embedder.model_name))

    notes = [
        note
        for note in memories.list(context, MemoryListFilters(), now)
        if note.tenant_id == context.tenant_id and note.workspace_id == context.workspace_id
    ]
    with open_file_metadata_store(context) as files:
        records = files.list_files(context, 500)
    existing = {(source.source_type, source.source_id): source for source in store.list_sources(context)}

    live = {(SourceType.MEMORY, note.id) for note in notes} | {(SourceType.FILE, record.id) for record in records}
    for key in existing:
        if key not in live:
            store.delete_source(context, key[0], key[1])
            result.removed += 1

    model = embedder.model_name
    budget = limit
    for note in notes:
        source = existing.get((SourceType.MEMORY, note.id))
        if (
            source is not None
            and source.status == IndexStatus.INDEXED
            and source.embedding_model == model
            and source.content_sha256 == sha256_text(note.content)
        ):
            result.reused += 1
            continue
        if budget <= 0:
            result.remaining += 1
            result.stale_remaining += int(source is not None and is_stale(source, model))
            continue
        outcome = await indexer.index_memory(context, note)
        budget -= 0 if outcome.reused else 1
        _count(result, outcome, source, model)
    for record in records:
        source = existing.get((SourceType.FILE, record.id))
        if source is not None and source.content_sha256 == record.sha256 and (
            source.status == IndexStatus.SKIPPED
            or (source.status == IndexStatus.INDEXED and source.embedding_model == model)
        ):
            result.reused += 1
            continue
        if budget <= 0:
            result.remaining += 1
            result.stale_remaining += int(source is not None and is_stale(source, model))
            continue
        try:
            content = file_store.storage.get(record.storage_key)
        except FileNotFoundError:
            result.failed += 1
            continue
        outcome = await indexer.index_file(context, record, content)
        budget -= 1
        _count(result, outcome, source, model)
    result.status = index_status(store.list_sources(context), model)
    if result.reembedded or result.stale_remaining:
        logger.info(
            "retrieval_reembedded count=%d remaining=%d",
            result.reembedded,
            result.stale_remaining,
            extra={
                "anum_retrieval": {
                    "event": "reembedded",
                    "embedding_model": model,
                    "count": result.reembedded,
                    "remaining": result.stale_remaining,
                }
            },
        )
    return result


# A run lists at most ANUM_RETRIEVAL_TOP_K (<= 20) passages; this bounds one lookup.
MAX_SOURCE_LOOKUP = 50
_TITLE_MAX_CHARS = 160


class SourceName(BaseModel):
    """What the caller may see of one memory or file a run used: never more than its list
    routes return (a memory's first line, a file's metadata)."""

    source_type: SourceType
    source_id: str
    found: bool = Field(description="False when the source is gone, expired or not visible to the caller")
    title: str | None = Field(default=None, description="A memory's first non-empty line or a file's name")
    kind: str | None = Field(
        default=None,
        description="`task_note` or `chat` for a memory (a chat memory is private to the caller); the file's content type",
    )
    task_id: str | None = None
    size_bytes: int | None = None


class SourceNames(BaseModel):
    sources: list[SourceName]


def memory_title(content: str) -> str:
    """The first non-empty line of a memory, shortened for a list row."""
    line = next((part.strip() for part in content.splitlines() if part.strip()), "")
    return line if len(line) <= _TITLE_MAX_CHARS else line[: _TITLE_MAX_CHARS - 1].rstrip() + "\u2026"


@router.get("/sources", response_model=SourceNames)
async def source_names(
    memory_id: list[str] = Query(default_factory=list, max_length=MAX_SOURCE_LOOKUP),
    file_id: list[str] = Query(default_factory=list, max_length=MAX_SOURCE_LOOKUP),
    context: TenantContext = Depends(tenant_context),
    memories: MemoryRepository = Depends(memory_repository_context),
) -> SourceNames:
    require_permission(context, Permission.MEMORY_READ)
    memory_ids = list(dict.fromkeys(memory_id))
    file_ids = list(dict.fromkeys(file_id))
    if len(memory_ids) + len(file_ids) > MAX_SOURCE_LOOKUP:
        raise HTTPException(status_code=422, detail=f"Ask for at most {MAX_SOURCE_LOOKUP} sources at once")
    from .files import open_file_metadata_store

    now = datetime.now(timezone.utc)
    names: list[SourceName] = []
    for note_id in memory_ids:
        # The repository scopes by tenant and workspace and hides another user's private
        # memory (and RLS does too in PostgreSQL mode); an expired memory reads as gone.
        note = memories.get(note_id, context)
        if note is None or note.is_expired(now):
            names.append(SourceName(source_type=SourceType.MEMORY, source_id=note_id, found=False))
            continue
        # Private (owned) memories are the ones remembered from the caller's chats; a shared
        # note is a task note whatever source label it was saved with.
        chat = note.owner_user_id is not None and note.provenance.source_type == CONVERSATION_SOURCE_TYPE
        names.append(
            SourceName(
                source_type=SourceType.MEMORY,
                source_id=note_id,
                found=True,
                title=memory_title(note.content) or None,
                kind="chat" if chat else "task_note",
                task_id=note.task_id,
            )
        )
    if file_ids:
        with open_file_metadata_store(context) as files:
            for one in file_ids:
                record = files.get(context, one)
                if record is None:
                    names.append(SourceName(source_type=SourceType.FILE, source_id=one, found=False))
                    continue
                names.append(
                    SourceName(
                        source_type=SourceType.FILE,
                        source_id=one,
                        found=True,
                        title=record.name,
                        kind=record.content_type,
                        size_bytes=record.size_bytes,
                    )
                )
    return SourceNames(sources=names)


def _count(result: ReindexResult, outcome: IndexOutcome, before: RetrievalSource | None, model: str) -> None:
    if outcome.reused:
        result.reused += 1
    elif outcome.status == IndexStatus.INDEXED:
        result.indexed += 1
        if before is not None and is_stale(before, model):
            result.reembedded += 1
    elif outcome.status == IndexStatus.FAILED:
        result.failed += 1
    else:
        result.skipped += 1
