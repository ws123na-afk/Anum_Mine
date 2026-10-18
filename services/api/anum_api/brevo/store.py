"""Brevo storage: an in-memory store and the PostgreSQL store (migration ``0071_brevo_campaigns``).

``ANUM_REPOSITORY_BACKEND=memory`` (local and tests) keeps everything in this process;
``postgresql`` uses ``anum_api.db.brevo_repository`` under forced RLS. Every method works
inside the caller's tenant and workspace only: a record of another workspace reads as
missing, whatever id is asked for.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import BrevoConnection, CampaignDraft, DraftStatus

MAX_PAGE = 50


class BrevoStore(Protocol):
    def get_connection(self, *, for_update: bool = False) -> BrevoConnection | None: ...
    def save_connection(self, connection: BrevoConnection) -> BrevoConnection: ...
    def delete_connection(self) -> bool: ...
    def get_draft(self, draft_id: str, *, for_update: bool = False) -> CampaignDraft | None: ...
    def add_draft(self, draft: CampaignDraft) -> CampaignDraft: ...
    def save_draft(self, draft: CampaignDraft) -> CampaignDraft: ...
    def delete_draft(self, draft_id: str) -> bool: ...
    def list_drafts(self, *, text: str | None, status: DraftStatus | None, limit: int, offset: int) -> tuple[list[CampaignDraft], int]: ...


class InMemoryBrevoStore:
    def __init__(self) -> None:
        self.connections: dict[tuple[str, str], BrevoConnection] = {}
        self.drafts: dict[str, CampaignDraft] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.connections.clear()
            self.drafts.clear()

    def scoped(self, context: TenantContext) -> _ScopedMemoryStore:
        return _ScopedMemoryStore(self, context)


class _ScopedMemoryStore:
    def __init__(self, root: InMemoryBrevoStore, context: TenantContext) -> None:
        self.root = root
        self.scope = (context.tenant_id, context.workspace_id)

    def _mine(self, draft: CampaignDraft) -> bool:
        return (draft.tenant_id, draft.workspace_id) == self.scope

    def get_connection(self, *, for_update: bool = False) -> BrevoConnection | None:
        with self.root.lock:
            found = self.root.connections.get(self.scope)
        return found.model_copy(deep=True) if found is not None else None

    def save_connection(self, connection: BrevoConnection) -> BrevoConnection:
        if (connection.tenant_id, connection.workspace_id) != self.scope:
            raise PermissionError("record outside the caller's workspace")
        with self.root.lock:
            self.root.connections[self.scope] = connection.model_copy(deep=True)
        return connection

    def delete_connection(self) -> bool:
        with self.root.lock:
            return self.root.connections.pop(self.scope, None) is not None

    def get_draft(self, draft_id: str, *, for_update: bool = False) -> CampaignDraft | None:
        with self.root.lock:
            found = self.root.drafts.get(draft_id)
        return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def add_draft(self, draft: CampaignDraft) -> CampaignDraft:
        if not self._mine(draft):
            raise PermissionError("record outside the caller's workspace")
        with self.root.lock:
            if draft.id in self.root.drafts:
                raise KeyError(draft.id)
            self.root.drafts[draft.id] = draft.model_copy(deep=True)
        return draft

    def save_draft(self, draft: CampaignDraft) -> CampaignDraft:
        with self.root.lock:
            existing = self.root.drafts.get(draft.id)
            if existing is None or not self._mine(existing) or not self._mine(draft):
                raise KeyError(draft.id)
            if draft.brevo_campaign_id is not None and any(
                other.id != draft.id and other.brevo_campaign_id == draft.brevo_campaign_id and self._mine(other)
                for other in self.root.drafts.values()
            ):
                raise ValueError("another draft owns this Brevo campaign")
            self.root.drafts[draft.id] = draft.model_copy(deep=True)
        return draft

    def delete_draft(self, draft_id: str) -> bool:
        with self.root.lock:
            existing = self.root.drafts.get(draft_id)
            if existing is None or not self._mine(existing):
                return False
            del self.root.drafts[draft_id]
        return True

    def list_drafts(self, *, text: str | None, status: DraftStatus | None, limit: int, offset: int) -> tuple[list[CampaignDraft], int]:
        needle = (text or "").casefold().strip()
        with self.root.lock:
            items = [
                draft for draft in self.root.drafts.values()
                if self._mine(draft)
                and (status is None or draft.status == status)
                and (not needle or needle in draft.name.casefold() or needle in draft.subject.casefold())
            ]
        items.sort(key=lambda draft: (draft.updated_at, draft.id), reverse=True)
        return [draft.model_copy(deep=True) for draft in items[offset : offset + limit]], len(items)


brevo_store = InMemoryBrevoStore()


@contextmanager
def open_brevo_store(context: TenantContext) -> Iterator[BrevoStore]:
    """One unit of work in the caller's tenant and workspace scope (RLS in PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.brevo_repository import SqlAlchemyBrevoStore

        return SqlAlchemyBrevoStore(session, context)

    with open_scoped_store(context, brevo_store.scoped(context), sql_store) as store:
        yield store
