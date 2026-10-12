"""Favorites: the items one user starred in one workspace (docs/knowledge-base.md#favorites).

A favorite names an item by type and id. Starring checks, server side, that the caller
may read that type and that the item exists and is visible to them (a conversation is
the caller's own, a private collection or report is theirs), and stores the item's
current title as the label. Favorites are private to their user: there is no user id in
any path or body, the store is keyed by tenant, workspace and user, and with PostgreSQL
the ``favorites`` row is under forced RLS on ``anum.tenant_id``, ``anum.workspace_id`` and
``anum.user_id`` (migration ``0042_library``).

* ``GET /api/v1/favorites?item_type=&limit=&offset=``: newest first, with ``total``.
* ``POST /api/v1/favorites``: star (idempotent: starring again returns the same row).
* ``DELETE /api/v1/favorites/{id}`` or ``DELETE /api/v1/favorites?item_type=&item_id=``.

Every member may star what they can read; a viewer stars too, since nothing shared changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store

router = APIRouter(prefix="/api/v1/favorites", tags=["favorites"])

MAX_FAVORITES_PER_USER = 500
LABEL_CHARS = 200


class FavoriteType(StrEnum):
    CONVERSATION = "conversation"
    TASK = "task"
    AGENT = "agent"
    FILE = "file"
    REPORT = "report"
    MEMORY = "memory"
    AUTOMATION = "automation"
    COLLECTION = "collection"


# What the caller must hold to read (and so to star) each type.
READ_PERMISSION: dict[FavoriteType, Permission] = {
    FavoriteType.CONVERSATION: Permission.TASK_READ,
    FavoriteType.TASK: Permission.TASK_READ,
    FavoriteType.AGENT: Permission.MARKETPLACE_READ,
    FavoriteType.FILE: Permission.FILE_READ,
    FavoriteType.REPORT: Permission.TASK_READ,
    FavoriteType.MEMORY: Permission.MEMORY_READ,
    FavoriteType.AUTOMATION: Permission.AUTOMATION_READ,
    FavoriteType.COLLECTION: Permission.MEMORY_READ,
}


class Favorite(BaseModel):
    id: str
    item_type: FavoriteType
    item_id: str
    label: str
    created_at: datetime


class FavoriteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: FavoriteType
    item_id: str = Field(min_length=1, max_length=200)


class FavoritePage(BaseModel):
    items: list[Favorite]
    total: int = Field(description="Favorites matching the filter, across all pages")
    limit: int
    offset: int


class FavoriteStore(Protocol):
    def list(
        self, context: TenantContext, item_type: FavoriteType | None, limit: int, offset: int
    ) -> tuple[list[Favorite], int]: ...
    def find(self, context: TenantContext, item_type: FavoriteType, item_id: str) -> Favorite | None: ...
    def count(self, context: TenantContext) -> int: ...
    def add(self, context: TenantContext, favorite: Favorite) -> Favorite: ...
    def delete(self, context: TenantContext, favorite_id: str) -> bool: ...
    def delete_item(self, context: TenantContext, item_type: FavoriteType, item_id: str) -> bool: ...


Scope = tuple[str, str, str]


def _scope(context: TenantContext) -> Scope:
    return (context.tenant_id, context.workspace_id, context.user_id)


class InMemoryFavoriteStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._items: dict[Scope, dict[str, Favorite]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _mine(self, context: TenantContext) -> dict[str, Favorite]:
        return self._items.setdefault(_scope(context), {})

    def list(
        self, context: TenantContext, item_type: FavoriteType | None, limit: int, offset: int
    ) -> tuple[list[Favorite], int]:
        with self._lock:
            rows = [f for f in self._mine(context).values() if item_type is None or f.item_type == item_type]
        rows.sort(key=lambda f: (f.created_at, f.id), reverse=True)
        return [f.model_copy() for f in rows[offset : offset + limit]], len(rows)

    def find(self, context: TenantContext, item_type: FavoriteType, item_id: str) -> Favorite | None:
        with self._lock:
            return next(
                (f.model_copy() for f in self._mine(context).values() if (f.item_type, f.item_id) == (item_type, item_id)),
                None,
            )

    def count(self, context: TenantContext) -> int:
        with self._lock:
            return len(self._mine(context))

    def add(self, context: TenantContext, favorite: Favorite) -> Favorite:
        with self._lock:
            self._mine(context)[favorite.id] = favorite
        return favorite

    def delete(self, context: TenantContext, favorite_id: str) -> bool:
        with self._lock:
            return self._mine(context).pop(favorite_id, None) is not None

    def delete_item(self, context: TenantContext, item_type: FavoriteType, item_id: str) -> bool:
        found = self.find(context, item_type, item_id)
        return found is not None and self.delete(context, found.id)

    def delete_everywhere(self, tenant_id: str, workspace_id: str, item_type: FavoriteType, item_id: str) -> None:
        """Forget an item that was deleted, from every user's favorites in its workspace."""
        with self._lock:
            for (tenant, workspace, _), rows in self._items.items():
                if (tenant, workspace) != (tenant_id, workspace_id):
                    continue
                for key in [k for k, f in rows.items() if (f.item_type, f.item_id) == (item_type, item_id)]:
                    rows.pop(key, None)


favorite_store = InMemoryFavoriteStore()


@contextmanager
def open_favorite_store(context: TenantContext) -> Iterator[FavoriteStore]:
    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.library_repository import SqlAlchemyFavoriteStore

        return SqlAlchemyFavoriteStore(session)

    with open_scoped_store(context, favorite_store, sql_store, user_scoped=True) as store:
        yield store


def _clip(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= LABEL_CHARS else line[: LABEL_CHARS - 1].rstrip() + "…"


def resolve_label(context: TenantContext, item_type: FavoriteType, item_id: str) -> str | None:
    """The item's current title when it exists and the caller may see it, else None."""
    if item_type == FavoriteType.CONVERSATION:
        from .conversations import open_conversation_store

        with open_conversation_store(context) as conversations:
            conversation = conversations.get(item_id, context)
        return None if conversation is None else (conversation.title or "Conversation")
    if item_type == FavoriteType.TASK:
        from .dependencies import _unit_of_work

        with _unit_of_work(context, require_membership=False) as repository:
            task = repository.get_task(item_id, context)
        return None if task is None else task.title
    if item_type == FavoriteType.FILE:
        from .files import open_file_metadata_store

        with open_file_metadata_store(context) as files:
            record = files.get(context, item_id)
        return None if record is None else record.name
    if item_type == FavoriteType.MEMORY:
        from .dependencies import open_memory_repository
        from .retrieval_api import memory_title

        with open_memory_repository(context) as memories:
            note = memories.get(item_id, context)
        if note is None or note.is_expired(utc_now()):
            return None
        return memory_title(note.content) or "Memory"
    if item_type == FavoriteType.AGENT:
        from .skills_api import open_skill_store

        with open_skill_store(context) as skills:
            installation = skills.get_installation(context, item_id)
        return None if installation is None else installation.skill_id
    if item_type == FavoriteType.AUTOMATION:
        from .automation import current_engine

        workflow = next((w for w in current_engine().list_workflows(context) if w.id == item_id), None)
        return None if workflow is None else workflow.name
    if item_type == FavoriteType.REPORT:
        from .reports import open_report_store

        with open_report_store(context) as reports:
            report = reports.get(context, item_id)
        return None if report is None else report.title
    from .knowledge_base import open_knowledge_store

    with open_knowledge_store(context) as knowledge:
        collection = knowledge.get_collection(context, item_id)
    return None if collection is None else collection.name


def forget_item(context: TenantContext, item_type: FavoriteType, item_id: str) -> None:
    """Drop a deleted item from favorites. With PostgreSQL only the caller's own rows are
    visible (RLS), so other users' stars of it stay and read as "no longer available"."""
    try:
        with open_favorite_store(context) as store:
            store.delete_item(context, item_type, item_id)
        favorite_store.delete_everywhere(context.tenant_id, context.workspace_id, item_type, item_id)
    # Best effort: a stale star is harmless, it reads as gone when opened.
    except Exception:  # nosec B110
        pass


@router.get("", response_model=FavoritePage)
async def list_favorites(
    item_type: FavoriteType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=MAX_FAVORITES_PER_USER),
    context: TenantContext = Depends(tenant_context),
) -> FavoritePage:
    """The caller's own favorites, newest first. Open to every member."""
    with open_favorite_store(context) as store:
        items, total = store.list(context, item_type, limit, offset)
    return FavoritePage(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=Favorite, status_code=status.HTTP_201_CREATED)
async def add_favorite(
    payload: FavoriteCreate,
    response: Response,
    context: TenantContext = Depends(tenant_context),
) -> Favorite:
    """Star an item the caller can read. 200 with the existing favorite when already starred."""
    require_permission(context, READ_PERMISSION[payload.item_type])
    with open_favorite_store(context) as store:
        existing = store.find(context, payload.item_type, payload.item_id)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return existing
        if store.count(context) >= MAX_FAVORITES_PER_USER:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"You can keep at most {MAX_FAVORITES_PER_USER} favorites. Remove some first.",
            )
    label = resolve_label(context, payload.item_type, payload.item_id)
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {payload.item_type.value} with that id is visible to you")
    favorite = Favorite(
        id=new_id("fav"),
        item_type=payload.item_type,
        item_id=payload.item_id,
        label=_clip(label),
        created_at=utc_now(),
    )
    with open_favorite_store(context) as store:
        return store.add(context, favorite)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite_item(
    item_type: FavoriteType = Query(),
    item_id: str = Query(min_length=1, max_length=200),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    """Unstar an item (204 whether or not it was starred: the end state is the same)."""
    with open_favorite_store(context) as store:
        store.delete_item(context, item_type, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(favorite_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    with open_favorite_store(context) as store:
        if not store.delete(context, favorite_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Favorite not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

