"""PostgreSQL stores for favorites, the knowledge base and reports (migration ``0042_library``).

The session's RLS context names the tenant, the workspace and the user
(``open_scoped_store(..., user_scoped=True)``). Every statement repeats the tenant and
workspace, favorites also the user, and collections, items and reports the owner rule
(shared, or owned by the caller), so another user's or tenant's row is neither read nor
written even if a policy were missing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from anum_api.favorites import Favorite, FavoriteType
from anum_api.knowledge_base import Collection, Item, ItemKind
from anum_api.reports import (
    CreatedByKind,
    Report,
    ReportFilters,
    ReportMetric,
    ReportSource,
    ReportTable,
    ReportTypeCount,
    ReportVisibility,
)
from anum_api.schemas import TenantContext

from .models import FavoriteRecord, KnowledgeCollectionRecord, KnowledgeItemRecord, ReportRecord
from .scope import require_workspace


def _owned_by(column: Any, context: TenantContext) -> Any:
    return or_(column.is_(None), column == context.user_id)


# --------------------------------------------------------------------------- favorites


def _favorite(record: FavoriteRecord) -> Favorite:
    return Favorite(
        id=record.id,
        item_type=FavoriteType(record.item_type),
        item_id=record.item_id,
        label=record.label,
        created_at=record.created_at,
    )


class SqlAlchemyFavoriteStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _mine(statement: Select[Any], context: TenantContext) -> Select[Any]:
        return statement.where(
            FavoriteRecord.tenant_id == context.tenant_id,
            FavoriteRecord.workspace_id == context.workspace_id,
            FavoriteRecord.user_id == context.user_id,
        )

    def list(
        self, context: TenantContext, item_type: FavoriteType | None, limit: int, offset: int
    ) -> tuple[list[Favorite], int]:
        statement = self._mine(select(FavoriteRecord), context)
        counted = self._mine(select(func.count()).select_from(FavoriteRecord), context)
        if item_type is not None:
            statement = statement.where(FavoriteRecord.item_type == item_type.value)
            counted = counted.where(FavoriteRecord.item_type == item_type.value)
        rows = self.session.scalars(
            statement.order_by(FavoriteRecord.created_at.desc(), FavoriteRecord.id.desc()).limit(limit).offset(offset)
        ).all()
        return [_favorite(r) for r in rows], int(self.session.scalar(counted) or 0)

    def find(self, context: TenantContext, item_type: FavoriteType, item_id: str) -> Favorite | None:
        record = self.session.scalar(
            self._mine(select(FavoriteRecord), context).where(
                FavoriteRecord.item_type == item_type.value, FavoriteRecord.item_id == item_id
            )
        )
        return _favorite(record) if record is not None else None

    def count(self, context: TenantContext) -> int:
        return int(self.session.scalar(self._mine(select(func.count()).select_from(FavoriteRecord), context)) or 0)

    def add(self, context: TenantContext, favorite: Favorite) -> Favorite:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.add(
            FavoriteRecord(
                id=favorite.id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                item_type=favorite.item_type.value,
                item_id=favorite.item_id,
                label=favorite.label,
                created_at=favorite.created_at,
            )
        )
        self.session.flush()
        return favorite

    def _delete(self, context: TenantContext, *conditions: Any) -> bool:
        result = self.session.execute(
            delete(FavoriteRecord).where(
                FavoriteRecord.tenant_id == context.tenant_id,
                FavoriteRecord.workspace_id == context.workspace_id,
                FavoriteRecord.user_id == context.user_id,
                *conditions,
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def delete(self, context: TenantContext, favorite_id: str) -> bool:
        return self._delete(context, FavoriteRecord.id == favorite_id)

    def delete_item(self, context: TenantContext, item_type: FavoriteType, item_id: str) -> bool:
        return self._delete(context, FavoriteRecord.item_type == item_type.value, FavoriteRecord.item_id == item_id)


# --------------------------------------------------------------------------- knowledge base


def _collection(record: KnowledgeCollectionRecord) -> Collection:
    return Collection(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        name=record.name,
        description=record.description,
        owner_user_id=record.owner_user_id,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _item(record: KnowledgeItemRecord) -> Item:
    return Item(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        collection_id=record.collection_id,
        owner_user_id=record.owner_user_id,
        kind=ItemKind(record.kind),
        source_id=record.source_id,
        title=record.title,
        tags=list(record.tags or []),
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class SqlAlchemyKnowledgeStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _collections(context: TenantContext) -> Select[Any]:
        return select(KnowledgeCollectionRecord).where(
            KnowledgeCollectionRecord.tenant_id == context.tenant_id,
            KnowledgeCollectionRecord.workspace_id == context.workspace_id,
            _owned_by(KnowledgeCollectionRecord.owner_user_id, context),
        )

    @staticmethod
    def _items(context: TenantContext) -> Select[Any]:
        return select(KnowledgeItemRecord).where(
            KnowledgeItemRecord.tenant_id == context.tenant_id,
            KnowledgeItemRecord.workspace_id == context.workspace_id,
            _owned_by(KnowledgeItemRecord.owner_user_id, context),
        )

    def list_collections(self, context: TenantContext) -> list[Collection]:
        rows = self.session.scalars(
            self._collections(context).order_by(func.lower(KnowledgeCollectionRecord.name), KnowledgeCollectionRecord.id)
        ).all()
        return [_collection(r) for r in rows]

    def get_collection(self, context: TenantContext, collection_id: str) -> Collection | None:
        record = self.session.scalar(self._collections(context).where(KnowledgeCollectionRecord.id == collection_id))
        return _collection(record) if record is not None else None

    def save_collection(self, collection: Collection) -> Collection:
        require_workspace(self.session, collection.tenant_id, collection.workspace_id)
        record = self.session.scalar(
            select(KnowledgeCollectionRecord).where(
                KnowledgeCollectionRecord.tenant_id == collection.tenant_id,
                KnowledgeCollectionRecord.workspace_id == collection.workspace_id,
                KnowledgeCollectionRecord.id == collection.id,
            )
        )
        if record is None:
            record = KnowledgeCollectionRecord(
                id=collection.id,
                tenant_id=collection.tenant_id,
                workspace_id=collection.workspace_id,
                owner_user_id=collection.owner_user_id,
                created_by=collection.created_by,
                created_at=collection.created_at,
            )
            self.session.add(record)
        record.name = collection.name
        record.description = collection.description
        record.updated_at = collection.updated_at
        self.session.flush()
        return collection

    def delete_collection(self, context: TenantContext, collection_id: str) -> list[Item]:
        if self.get_collection(context, collection_id) is None:
            return []
        removed = self.list_items(context, collection_id)
        self.session.execute(
            delete(KnowledgeItemRecord).where(
                KnowledgeItemRecord.tenant_id == context.tenant_id,
                KnowledgeItemRecord.workspace_id == context.workspace_id,
                KnowledgeItemRecord.collection_id == collection_id,
            )
        )
        self.session.execute(
            delete(KnowledgeCollectionRecord).where(
                KnowledgeCollectionRecord.tenant_id == context.tenant_id,
                KnowledgeCollectionRecord.workspace_id == context.workspace_id,
                KnowledgeCollectionRecord.id == collection_id,
                _owned_by(KnowledgeCollectionRecord.owner_user_id, context),
            )
        )
        return removed

    def list_items(self, context: TenantContext, collection_id: str | None) -> list[Item]:
        statement = self._items(context)
        if collection_id is not None:
            statement = statement.where(KnowledgeItemRecord.collection_id == collection_id)
        rows = self.session.scalars(
            statement.order_by(KnowledgeItemRecord.created_at.desc(), KnowledgeItemRecord.id.desc())
        ).all()
        return [_item(r) for r in rows]

    def get_item(self, context: TenantContext, item_id: str) -> Item | None:
        record = self.session.scalar(self._items(context).where(KnowledgeItemRecord.id == item_id))
        return _item(record) if record is not None else None

    def find_item(self, context: TenantContext, collection_id: str, kind: ItemKind, source_id: str) -> Item | None:
        record = self.session.scalar(
            self._items(context).where(
                KnowledgeItemRecord.collection_id == collection_id,
                KnowledgeItemRecord.kind == kind.value,
                KnowledgeItemRecord.source_id == source_id,
            )
        )
        return _item(record) if record is not None else None

    def save_item(self, item: Item) -> Item:
        record = self.session.scalar(
            select(KnowledgeItemRecord).where(
                KnowledgeItemRecord.tenant_id == item.tenant_id,
                KnowledgeItemRecord.workspace_id == item.workspace_id,
                KnowledgeItemRecord.id == item.id,
            )
        )
        if record is None:
            record = KnowledgeItemRecord(
                id=item.id,
                tenant_id=item.tenant_id,
                workspace_id=item.workspace_id,
                collection_id=item.collection_id,
                owner_user_id=item.owner_user_id,
                kind=item.kind.value,
                source_id=item.source_id,
                created_by=item.created_by,
                created_at=item.created_at,
            )
            self.session.add(record)
        record.title = item.title
        record.tags = list(item.tags)
        record.updated_at = item.updated_at
        self.session.flush()
        return item

    def delete_item(self, context: TenantContext, item_id: str) -> bool:
        result = self.session.execute(
            delete(KnowledgeItemRecord).where(
                KnowledgeItemRecord.tenant_id == context.tenant_id,
                KnowledgeItemRecord.workspace_id == context.workspace_id,
                KnowledgeItemRecord.id == item_id,
                _owned_by(KnowledgeItemRecord.owner_user_id, context),
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def item_counts(self, context: TenantContext) -> dict[str, int]:
        rows = self.session.execute(
            select(KnowledgeItemRecord.collection_id, func.count())
            .where(
                KnowledgeItemRecord.tenant_id == context.tenant_id,
                KnowledgeItemRecord.workspace_id == context.workspace_id,
                _owned_by(KnowledgeItemRecord.owner_user_id, context),
            )
            .group_by(KnowledgeItemRecord.collection_id)
        ).all()
        return {collection_id: int(count) for collection_id, count in rows}


# --------------------------------------------------------------------------- reports


def _report(record: ReportRecord) -> Report:
    return Report(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        type=record.report_type,
        title=record.title,
        period_start=record.period_start,
        period_end=record.period_end,
        summary=record.summary,
        metrics=[ReportMetric.model_validate(m) for m in record.metrics or []],
        highlights=list(record.highlights or []),
        data=ReportTable.model_validate(record.data or {}),
        body_markdown=record.body_markdown,
        visibility=ReportVisibility.WORKSPACE if record.owner_user_id is None else ReportVisibility.PRIVATE,
        owner_user_id=record.owner_user_id,
        created_by=record.created_by,
        created_by_kind=CreatedByKind(record.created_by_kind),
        source=ReportSource(
            conversation_id=record.source_conversation_id,
            turn_id=record.source_turn_id,
            automation_run_id=record.source_automation_run_id,
        ),
        created_at=record.created_at,
        updated_at=record.updated_at,
        tabs=list(record.tabs or []),
        sections=list(record.sections or []),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlAlchemyReportStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _visible(statement: Select[Any], context: TenantContext) -> Select[Any]:
        return statement.where(
            ReportRecord.tenant_id == context.tenant_id,
            ReportRecord.workspace_id == context.workspace_id,
            _owned_by(ReportRecord.owner_user_id, context),
        )

    def add(self, report: Report) -> Report:
        require_workspace(self.session, report.tenant_id, report.workspace_id)
        self.session.add(
            ReportRecord(
                id=report.id,
                tenant_id=report.tenant_id,
                workspace_id=report.workspace_id,
                report_type=report.type,
                title=report.title,
                period_start=report.period_start,
                period_end=report.period_end,
                summary=report.summary,
                metrics=[m.model_dump(mode="json") for m in report.metrics],
                highlights=list(report.highlights),
                data=report.data.model_dump(mode="json"),
                body_markdown=report.body_markdown,
                owner_user_id=report.owner_user_id,
                created_by=report.created_by,
                created_by_kind=report.created_by_kind.value,
                source_conversation_id=report.source.conversation_id,
                source_turn_id=report.source.turn_id,
                source_automation_run_id=report.source.automation_run_id,
                created_at=report.created_at,
                updated_at=report.updated_at,
                tabs=list(report.tabs),
                sections=[section.model_dump(mode="json") for section in report.sections],
            )
        )
        self.session.flush()
        return report

    def get(self, context: TenantContext, report_id: str) -> Report | None:
        record = self.session.scalar(self._visible(select(ReportRecord), context).where(ReportRecord.id == report_id))
        return _report(record) if record is not None else None

    def _filtered(self, statement: Select[Any], context: TenantContext, filters: ReportFilters) -> Select[Any]:
        statement = self._visible(statement, context)
        if filters.type:
            statement = statement.where(ReportRecord.report_type == filters.type)
        if filters.created_by_kind:
            statement = statement.where(ReportRecord.created_by_kind == filters.created_by_kind.value)
        if filters.conversation_id:
            statement = statement.where(ReportRecord.source_conversation_id == filters.conversation_id)
        if filters.query:
            pattern = f"%{_escape_like(filters.query)}%"
            statement = statement.where(
                or_(ReportRecord.title.ilike(pattern, escape="\\"), ReportRecord.summary.ilike(pattern, escape="\\"))
            )
        return statement

    def list(
        self, context: TenantContext, filters: ReportFilters, limit: int, offset: int
    ) -> tuple[list[Report], int]:
        rows = self.session.scalars(
            self._filtered(select(ReportRecord), context, filters)
            .order_by(ReportRecord.created_at.desc(), ReportRecord.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        total = self.session.scalar(self._filtered(select(func.count()).select_from(ReportRecord), context, filters))
        return [_report(r) for r in rows], int(total or 0)

    def type_counts(self, context: TenantContext) -> list[ReportTypeCount]:
        rows = self.session.execute(
            self._visible(select(ReportRecord.report_type, func.count()), context)
            .group_by(ReportRecord.report_type)
            .order_by(ReportRecord.report_type)
        ).all()
        return [ReportTypeCount(type=t, count=int(n)) for t, n in rows]

    def save(self, report: Report) -> Report:
        # The caller read the report through get() first; RLS (and its owner policy, on
        # the new owner too, WITH CHECK) still decides what this statement may touch.
        record = self.session.scalar(
            select(ReportRecord).where(
                ReportRecord.tenant_id == report.tenant_id,
                ReportRecord.workspace_id == report.workspace_id,
                ReportRecord.id == report.id,
            )
        )
        if record is None:
            return self.add(report)
        record.title = report.title
        record.owner_user_id = report.owner_user_id
        record.updated_at = report.updated_at
        self.session.flush()
        return report

    def delete(self, context: TenantContext, report_id: str) -> bool:
        result = self.session.execute(
            delete(ReportRecord).where(
                ReportRecord.tenant_id == context.tenant_id,
                ReportRecord.workspace_id == context.workspace_id,
                ReportRecord.id == report_id,
                _owned_by(ReportRecord.owner_user_id, context),
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

