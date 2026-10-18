"""Business storage: one generic protocol, an in-memory store and the PostgreSQL store.

``ANUM_REPOSITORY_BACKEND=memory`` (local and tests) keeps everything in this process;
``postgresql`` uses ``anum_api.db.business_repository`` under forced RLS (migration
``0070_business``). Every method works inside the caller's tenant and workspace only:
a record of another workspace reads as missing, whatever id is asked for.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import (
    Account,
    Activity,
    BusinessSettings,
    Contact,
    Draft,
    Evidence,
    Inference,
    Lead,
    Milestone,
    Offer,
    Opportunity,
    Product,
    Project,
    Proposal,
    ProposalVersion,
    ResearchRun,
)
from .outreach_models import Observation, OutboxItem, OutreachSettings, PipelineRun, ReplyEvent, TargetProfile

MAX_PAGE = 100
# Rows read at most for a summary (the KPI window); more is reported as truncated.
MAX_SCAN = 5000

KINDS: dict[str, type[BaseModel]] = {
    "products": Product,
    "offers": Offer,
    "accounts": Account,
    "contacts": Contact,
    "leads": Lead,
    "evidence": Evidence,
    "inferences": Inference,
    "activities": Activity,
    "opportunities": Opportunity,
    "proposals": Proposal,
    "proposal_versions": ProposalVersion,
    "projects": Project,
    "milestones": Milestone,
    "drafts": Draft,
    "research_runs": ResearchRun,
    # The prospecting pipeline and the outbox (migration 0076_business_pipeline).
    "outreach_settings": OutreachSettings,
    "observations": Observation,
    "pipeline_runs": PipelineRun,
    "outbox": OutboxItem,
    "replies": ReplyEvent,
    "target_profiles": TargetProfile,
}

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass
class Query:
    """Equality (or membership, for a set) filters, a text search, a creation window,
    an order and a page."""

    where: dict[str, Any] = field(default_factory=dict)
    text: str | None = None
    text_fields: tuple[str, ...] = ()
    created_from: datetime | None = None
    created_to: datetime | None = None
    order_by: str = "updated_at"
    descending: bool = True
    limit: int = 50
    offset: int = 0


class BusinessStore(Protocol):
    def get(self, kind: str, item_id: str, *, for_update: bool = False) -> Any: ...
    def add(self, kind: str, item: ModelT) -> ModelT: ...
    def save(self, kind: str, item: ModelT) -> ModelT: ...
    def delete(self, kind: str, item_id: str) -> bool: ...
    def find(self, kind: str, query: Query) -> tuple[list[Any], int]: ...
    def first(self, kind: str, **where: Any) -> Any: ...
    def count(self, kind: str, query: Query) -> int: ...
    def get_settings(self) -> BusinessSettings: ...
    def save_settings(self, value: BusinessSettings) -> BusinessSettings: ...


def plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _matches(item: BaseModel, query: Query) -> bool:
    for name, wanted in query.where.items():
        actual = plain(getattr(item, name))
        if isinstance(wanted, (set, frozenset, list, tuple)):
            if actual not in {plain(value) for value in wanted}:
                return False
        elif actual != plain(wanted):
            return False
    created = getattr(item, "created_at", None)
    if query.created_from is not None and (created is None or created < query.created_from):
        return False
    if query.created_to is not None and (created is None or created >= query.created_to):
        return False
    if query.text:
        needle = query.text.casefold()
        if not any(needle in str(getattr(item, name) or "").casefold() for name in query.text_fields):
            return False
    return True


class InMemoryBusinessStore:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, BaseModel]] = {kind: {} for kind in KINDS}
        self._settings: dict[tuple[str, str], BusinessSettings] = {}
        self._lock = RLock()
        self.context: TenantContext | None = None

    def clear(self) -> None:
        with self._lock:
            for rows in self._rows.values():
                rows.clear()
            self._settings.clear()

    def scoped(self, context: TenantContext) -> "_ScopedMemoryStore":
        return _ScopedMemoryStore(self, context)


class _ScopedMemoryStore:
    def __init__(self, root: InMemoryBusinessStore, context: TenantContext) -> None:
        self.root = root
        self.context = context

    def _mine(self, item: BaseModel) -> bool:
        return (getattr(item, "tenant_id"), getattr(item, "workspace_id")) == (self.context.tenant_id, self.context.workspace_id)

    def get(self, kind: str, item_id: str, *, for_update: bool = False) -> Any:
        with self.root._lock:
            found = self.root._rows[kind].get(item_id)
        return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def add(self, kind: str, item: ModelT) -> ModelT:
        if not self._mine(item):
            raise PermissionError("record outside the caller's workspace")
        with self.root._lock:
            if item.id in self.root._rows[kind]:  # type: ignore[attr-defined]
                raise KeyError(item.id)  # type: ignore[attr-defined]
            self.root._rows[kind][item.id] = item.model_copy(deep=True)  # type: ignore[attr-defined]
        return item

    def save(self, kind: str, item: ModelT) -> ModelT:
        with self.root._lock:
            existing = self.root._rows[kind].get(item.id)  # type: ignore[attr-defined]
            if existing is None or not self._mine(existing) or not self._mine(item):
                raise KeyError(item.id)  # type: ignore[attr-defined]
            self.root._rows[kind][item.id] = item.model_copy(deep=True)  # type: ignore[attr-defined]
        return item

    def delete(self, kind: str, item_id: str) -> bool:
        with self.root._lock:
            existing = self.root._rows[kind].get(item_id)
            if existing is None or not self._mine(existing):
                return False
            del self.root._rows[kind][item_id]
        return True

    def _all(self, kind: str, query: Query) -> list[Any]:
        with self.root._lock:
            items = [item for item in self.root._rows[kind].values() if self._mine(item) and _matches(item, query)]
        items.sort(key=lambda item: (getattr(item, query.order_by) is not None, getattr(item, query.order_by) or 0, item.id), reverse=query.descending)  # type: ignore[attr-defined]
        return items

    def find(self, kind: str, query: Query) -> tuple[list[Any], int]:
        items = self._all(kind, query)
        page = items[query.offset : query.offset + query.limit]
        return [item.model_copy(deep=True) for item in page], len(items)

    def first(self, kind: str, **where: Any) -> Any:
        items, _ = self.find(kind, Query(where=where, limit=1, order_by="created_at", descending=False))
        return items[0] if items else None

    def count(self, kind: str, query: Query) -> int:
        return len(self._all(kind, query))

    def get_settings(self) -> BusinessSettings:
        with self.root._lock:
            found = self.root._settings.get((self.context.tenant_id, self.context.workspace_id))
        return found.model_copy() if found is not None else BusinessSettings()

    def save_settings(self, value: BusinessSettings) -> BusinessSettings:
        with self.root._lock:
            self.root._settings[(self.context.tenant_id, self.context.workspace_id)] = value.model_copy()
        return value


business_store = InMemoryBusinessStore()


@contextmanager
def open_business_store(context: TenantContext) -> Iterator[BusinessStore]:
    """One unit of work in the caller's tenant and workspace scope (RLS in PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.business_repository import SqlAlchemyBusinessStore

        return SqlAlchemyBusinessStore(session, context)

    with open_scoped_store(context, business_store.scoped(context), sql_store) as store:
        yield store
