"""Favorites, private collections and private reports in "Download my data" and
"Delete my data" (docs/privacy.md, docs/knowledge-base.md, docs/reports.md).

The caller's own rows only: every favorite is theirs (RLS on ``anum.user_id``); a
collection or report counts only when ``owner_user_id`` is the caller (shared ones belong
to the workspace and stay). The notes of a private collection are private memories, which
the privacy store already exports and deletes; here the collection and its items go.
"""

from __future__ import annotations

from typing import Any

from .favorites import MAX_FAVORITES_PER_USER, FavoriteStore
from .knowledge_base import KnowledgeStore
from .reports import MAX_LIST, ReportFilters, ReportStore
from .schemas import TenantContext


class LibraryPrivacy:
    def __init__(
        self, context: TenantContext, favorites: FavoriteStore, knowledge: KnowledgeStore, reports: ReportStore
    ) -> None:
        self.context = context
        self.favorites_store = favorites
        self.knowledge = knowledge
        self.reports_store = reports

    def favorites(self) -> list[Any]:
        items, _ = self.favorites_store.list(self.context, None, MAX_FAVORITES_PER_USER, 0)
        return items

    def private_collections(self) -> list[Any]:
        return [c for c in self.knowledge.list_collections(self.context) if c.owner_user_id == self.context.user_id]

    def private_reports(self) -> list[Any]:
        found: list[Any] = []
        offset = 0
        while True:
            page, total = self.reports_store.list(self.context, ReportFilters(), MAX_LIST, offset)
            found += [r for r in page if r.owner_user_id == self.context.user_id]
            offset += len(page)
            if not page or offset >= total:
                return found

    def counts(self) -> dict[str, int]:
        return {
            "favorites": len(self.favorites()),
            "private_collections": len(self.private_collections()),
            "private_reports": len(self.private_reports()),
        }

    def export(self) -> dict[str, Any]:
        collections = []
        for collection in self.private_collections():
            items = self.knowledge.list_items(self.context, collection.id)
            collections.append({
                **collection.model_dump(mode="json"),
                "items": [item.model_dump(mode="json") for item in items],
            })
        return {
            "favorites": [f.model_dump(mode="json") for f in self.favorites()],
            "private_collections": collections,
            "private_reports": [r.model_dump(mode="json") for r in self.private_reports()],
        }

    def delete_all(self) -> None:
        for favorite in self.favorites():
            self.favorites_store.delete(self.context, favorite.id)
        for collection in self.private_collections():
            self.knowledge.delete_collection(self.context, collection.id)
        for report in self.private_reports():
            self.reports_store.delete(self.context, report.id)


def memory_library(context: TenantContext) -> LibraryPrivacy:
    from .favorites import favorite_store
    from .knowledge_base import knowledge_store
    from .reports import report_store

    return LibraryPrivacy(context, favorite_store, knowledge_store, report_store)


def sql_library(session: Any, context: TenantContext) -> LibraryPrivacy:
    """In the privacy store's own transaction (tenant, workspace and user RLS context)."""
    from .db.library_repository import SqlAlchemyFavoriteStore, SqlAlchemyKnowledgeStore, SqlAlchemyReportStore

    return LibraryPrivacy(
        context, SqlAlchemyFavoriteStore(session), SqlAlchemyKnowledgeStore(session), SqlAlchemyReportStore(session)
    )
