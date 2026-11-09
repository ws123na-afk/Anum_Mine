"""Storage for finance links, business allocations and iPhone device events (docs/finance-links.md#storage).

Like ``sms_store``: a protocol, an in-memory store for local development and tests, and
``open_link_stores`` which opens the finance store, the SMS and journal store and this store in
one owner-scoped unit of work. With PostgreSQL the store is
``anum_api.db.finance_links_repository.SqlAlchemyLinkStore`` on the same session, over the
tables of migration ``0134_finance_links``.

Business allocation rows live in the **business** workspace. The SQL store writes them in the
same transaction as the owner's link by switching the transaction-local workspace setting to the
business workspace for that one statement and back (both workspaces are in the caller's tenant;
the caller's membership there was checked first), so a link and its allocation row commit or
roll back together.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from .sms_store import SmsStore, open_sms_stores
from .store import FinanceStore

DIMENSIONS = ("vehicle", "purchase", "document", "business", "trip", "project", "bill", "debt", "person_loan", "payslip", "remittance")
ROLES = ("payment", "refund", "deposit", "evidence")
EVENT_OUTCOMES = ("ok", "failed", "denied", "test")


class LinkStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"  # the transaction changed (amount or currency): the owner confirms or removes it
    VOID = "void"  # removed, or the transaction was undone


class FinanceLink(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    canonical_id: str
    dimension: str
    role: str
    target_type: str
    target_id: str
    target_workspace_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    status: LinkStatus = LinkStatus.ACTIVE
    status_reason: str | None = None
    source_event_ids: list[str] = Field(default_factory=list)
    document_id: str | None = None
    idem_key: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    voided_at: datetime | None = None
    version: int = 1


class BusinessAllocation(BaseModel):
    """What the business workspace sees: never the bank, the account, the card or the message."""

    id: str
    tenant_id: str
    workspace_id: str  # the business workspace
    source_link_id: str
    source_workspace_id: str
    allocated_by: str
    source_ref: str  # the last six characters of the canonical id, to quote to the owner
    posted_on: date
    amount: Decimal
    currency: str
    category: str
    project_id: str | None = None
    memo: str | None = None
    treatment: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class DeviceEvent(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    device_id: str
    integration: str
    scope: str
    outcome: str
    code: str | None = None
    created_at: datetime


class EventSummary(BaseModel):
    integration: str
    last_at: datetime | None = None
    last_ok_at: datetime | None = None
    failed_7d: int = 0


class LinkStore(Protocol):
    def add_link(self, link: FinanceLink) -> None: ...
    def save_link(self, link: FinanceLink, *, expected_version: int) -> bool: ...
    def get_link(self, context: TenantContext, link_id: str) -> FinanceLink | None: ...
    def link_by_idem(self, context: TenantContext, idem_key: str) -> FinanceLink | None: ...
    def live_links(self, context: TenantContext, canonical_id: str) -> list[FinanceLink]: ...
    def list_links(self, context: TenantContext, *, canonical_id: str | None, dimension: str | None, target_type: str | None,
                   target_id: str | None, status: str | None, limit: int, offset: int) -> tuple[list[FinanceLink], int]: ...
    def put_allocation(self, context: TenantContext, allocation: BusinessAllocation) -> None: ...
    def set_allocation(self, context: TenantContext, workspace_id: str, link_id: str, *, status: str, posted_on: date, now: datetime) -> None: ...
    def retarget_allocation(self, context: TenantContext, workspace_id: str, link_id: str, *, source_ref: str, now: datetime) -> None: ...
    def delete_allocation(self, context: TenantContext, workspace_id: str, link_id: str) -> None: ...
    def list_allocations(self, context: TenantContext, *, currency: str | None, limit: int, offset: int) -> tuple[list[BusinessAllocation], int]: ...
    def allocation_totals(self, context: TenantContext, *, currency: str | None) -> dict[str, Decimal]: ...
    def add_event(self, event: DeviceEvent) -> None: ...
    def count_events(self, context: TenantContext, device_id: str, scope: str, since: datetime) -> int: ...
    def purge_events(self, context: TenantContext, before: datetime) -> int: ...
    def event_summary(self, context: TenantContext, since: datetime) -> list[EventSummary]: ...
    def list_events(self, context: TenantContext, *, integration: str, outcome: str | None, limit: int, offset: int) -> tuple[list[DeviceEvent], int]: ...
    def export_everything(self, context: TenantContext) -> dict[str, list[dict[str, Any]]]: ...
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: Any, context: TenantContext) -> bool:
    return (getattr(item, "tenant_id", None) == context.tenant_id and getattr(item, "workspace_id", None) == context.workspace_id
            and getattr(item, "owner_user_id", None) == context.user_id)


class InMemoryLinkStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.clear()

    def clear(self) -> None:
        self.links: dict[str, FinanceLink] = {}
        self.allocations: dict[str, BusinessAllocation] = {}
        self.events: dict[str, DeviceEvent] = {}

    def add_link(self, link: FinanceLink) -> None:
        with self.lock:
            if any(_mine(other, _ctx(link)) and other.idem_key == link.idem_key and other.status != LinkStatus.VOID for other in self.links.values()):
                raise ValueError("a live link with this idempotency key exists")
            self.links[link.id] = link.model_copy(deep=True)

    def save_link(self, link: FinanceLink, *, expected_version: int) -> bool:
        with self.lock:
            current = self.links.get(link.id)
            if current is None or current.version != expected_version:
                return False
            self.links[link.id] = link.model_copy(deep=True)
            return True

    def get_link(self, context: TenantContext, link_id: str) -> FinanceLink | None:
        found = self.links.get(link_id)
        return found.model_copy(deep=True) if found and _mine(found, context) else None

    def link_by_idem(self, context: TenantContext, idem_key: str) -> FinanceLink | None:
        found = next((l for l in self.links.values() if _mine(l, context) and l.idem_key == idem_key and l.status != LinkStatus.VOID), None)
        return found.model_copy(deep=True) if found else None

    def live_links(self, context: TenantContext, canonical_id: str) -> list[FinanceLink]:
        rows = [l for l in self.links.values() if _mine(l, context) and l.canonical_id == canonical_id and l.status != LinkStatus.VOID]
        return [l.model_copy(deep=True) for l in sorted(rows, key=lambda l: (l.created_at, l.id))]

    def list_links(self, context: TenantContext, *, canonical_id: str | None, dimension: str | None, target_type: str | None,
                   target_id: str | None, status: str | None, limit: int, offset: int) -> tuple[list[FinanceLink], int]:
        rows = [l for l in self.links.values() if _mine(l, context) and (canonical_id is None or l.canonical_id == canonical_id)
                and (dimension is None or l.dimension == dimension) and (target_type is None or l.target_type == target_type)
                and (target_id is None or l.target_id == target_id) and (status is None or l.status == status)]
        rows.sort(key=lambda l: (l.created_at, l.id), reverse=True)
        return [l.model_copy(deep=True) for l in rows[offset:offset + limit]], len(rows)

    def put_allocation(self, context: TenantContext, allocation: BusinessAllocation) -> None:
        with self.lock:
            if any(a.tenant_id == allocation.tenant_id and a.source_link_id == allocation.source_link_id for a in self.allocations.values()):
                raise ValueError("this link already has an allocation row")
            self.allocations[allocation.id] = allocation.model_copy(deep=True)

    def set_allocation(self, context: TenantContext, workspace_id: str, link_id: str, *, status: str, posted_on: date, now: datetime) -> None:
        with self.lock:
            for a in self.allocations.values():
                if a.tenant_id == context.tenant_id and a.workspace_id == workspace_id and a.source_link_id == link_id:
                    a.status, a.posted_on, a.updated_at = status, posted_on, now

    def retarget_allocation(self, context: TenantContext, workspace_id: str, link_id: str, *, source_ref: str, now: datetime) -> None:
        with self.lock:
            for a in self.allocations.values():
                if a.tenant_id == context.tenant_id and a.workspace_id == workspace_id and a.source_link_id == link_id:
                    a.source_ref, a.updated_at = source_ref, now

    def delete_allocation(self, context: TenantContext, workspace_id: str, link_id: str) -> None:
        with self.lock:
            for key in [k for k, a in self.allocations.items()
                        if a.tenant_id == context.tenant_id and a.workspace_id == workspace_id and a.source_link_id == link_id]:
                del self.allocations[key]

    def list_allocations(self, context: TenantContext, *, currency: str | None, limit: int, offset: int) -> tuple[list[BusinessAllocation], int]:
        rows = [a for a in self.allocations.values() if a.tenant_id == context.tenant_id and a.workspace_id == context.workspace_id
                and (currency is None or a.currency == currency)]
        rows.sort(key=lambda a: (a.posted_on, a.created_at, a.id), reverse=True)
        return [a.model_copy(deep=True) for a in rows[offset:offset + limit]], len(rows)

    def allocation_totals(self, context: TenantContext, *, currency: str | None) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for a in list(self.allocations.values()):
            if a.tenant_id == context.tenant_id and a.workspace_id == context.workspace_id and (currency is None or a.currency == currency):
                out[a.currency] = out.get(a.currency, Decimal(0)) + a.amount
        return out

    def add_event(self, event: DeviceEvent) -> None:
        with self.lock:
            self.events[event.id] = event.model_copy()

    def count_events(self, context: TenantContext, device_id: str, scope: str, since: datetime) -> int:
        """Accepted requests only: a refusal never keeps a device locked out."""
        return sum(1 for e in list(self.events.values()) if _mine(e, context) and e.device_id == device_id and e.scope == scope
                   and e.outcome in ("ok", "test") and e.created_at >= since)

    def purge_events(self, context: TenantContext, before: datetime) -> int:
        with self.lock:
            old = [k for k, e in self.events.items() if _mine(e, context) and e.created_at < before]
            for key in old:
                del self.events[key]
            return len(old)

    def event_summary(self, context: TenantContext, since: datetime) -> list[EventSummary]:
        out: dict[str, EventSummary] = {}
        for e in list(self.events.values()):
            if not _mine(e, context):
                continue
            row = out.setdefault(e.integration, EventSummary(integration=e.integration))
            row.last_at = max(filter(None, (row.last_at, e.created_at)))
            if e.outcome in ("ok", "test"):
                row.last_ok_at = max(filter(None, (row.last_ok_at, e.created_at)))
            if e.outcome in ("failed", "denied") and e.created_at >= since:
                row.failed_7d += 1
        return list(out.values())

    def list_events(self, context: TenantContext, *, integration: str, outcome: str | None, limit: int, offset: int) -> tuple[list[DeviceEvent], int]:
        rows = [e for e in list(self.events.values()) if _mine(e, context) and e.integration == integration
                and (outcome is None or e.outcome == outcome or (outcome == "problem" and e.outcome in ("failed", "denied")))]
        rows.sort(key=lambda e: (e.created_at, e.id), reverse=True)
        return [e.model_copy() for e in rows[offset:offset + limit]], len(rows)

    def export_everything(self, context: TenantContext) -> dict[str, list[dict[str, Any]]]:
        return {
            "finance_links": [l.model_dump(mode="json", exclude={"idem_key"}) for l in self.links.values() if _mine(l, context)],
            "business_allocations_made": [a.model_dump(mode="json") for a in self.allocations.values() if _made_by(a, context)],
            "device_events": [e.model_dump(mode="json") for e in self.events.values() if _mine(e, context)],
        }

    def count_everything(self, context: TenantContext) -> int:
        return (sum(1 for l in self.links.values() if _mine(l, context)) + sum(1 for e in self.events.values() if _mine(e, context))
                + sum(1 for a in self.allocations.values() if _made_by(a, context)))

    def delete_everything(self, context: TenantContext) -> int:
        """The person's links and events, and the allocation rows they made in business workspaces."""
        with self.lock:
            removed = self.count_everything(context)
            for table, test in ((self.links, _mine), (self.events, _mine), (self.allocations, _made_by)):
                for key in [k for k, v in table.items() if test(v, context)]:  # type: ignore[operator]
                    del table[key]
            return removed


def _ctx(item: Any) -> TenantContext:
    return TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.owner_user_id)


def _made_by(allocation: BusinessAllocation, context: TenantContext) -> bool:
    return (allocation.tenant_id == context.tenant_id and allocation.source_workspace_id == context.workspace_id
            and allocation.allocated_by == context.user_id)


links_memory_store = InMemoryLinkStore()


def links_store_for(sms: Any) -> LinkStore:
    """The link store on the same unit of work as an SMS store (the same SQL session)."""
    session = getattr(sms, "session", None)
    if session is not None:
        from ..db.finance_links_repository import SqlAlchemyLinkStore

        return SqlAlchemyLinkStore(session)
    return links_memory_store


@contextmanager
def open_link_stores(context: TenantContext) -> Iterator[tuple[FinanceStore, SmsStore, LinkStore]]:
    """The finance store, the SMS and journal store and the link store in one owner-scoped unit of work."""
    with open_sms_stores(context) as (finance, sms):
        yield finance, sms, links_store_for(sms)


@contextmanager
def open_workspace_allocations(context: TenantContext) -> Iterator[LinkStore]:
    """A business workspace's allocation rows, read in that workspace's own RLS scope (no owner filter)."""
    from ..scoped_store import open_scoped_store

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.finance_links_repository import SqlAlchemyLinkStore

        return SqlAlchemyLinkStore(session)

    with open_scoped_store(context, links_memory_store, sql_store) as store:
        yield store


__all__ = [
    "BusinessAllocation",
    "DIMENSIONS",
    "DeviceEvent",
    "EventSummary",
    "FinanceLink",
    "InMemoryLinkStore",
    "LinkStatus",
    "LinkStore",
    "ROLES",
    "links_memory_store",
    "links_store_for",
    "open_link_stores",
    "open_workspace_allocations",
]
