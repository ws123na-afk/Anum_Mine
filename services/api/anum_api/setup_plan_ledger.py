"""What a setup plan already applied, per item (docs/setup-plan.md#idempotency).

One row per blueprint item key: the object it created or linked (``target_id``), a
fingerprint of what it set, and the object's version at that moment. Re-applying a plan
reads this ledger, so an item is never created twice and an object the owner changed after
it was applied is recognised and left alone.

Workspace-level items (agents, assistants, knowledge folders, automations, saved commands,
skill installations, the workspace budget) have the request's ``workspace_id``.
Tenant-level items (policy packs, approval rules, role templates, memory governance,
published skill versions, created workspaces) bind every workspace, so their rows carry
``workspace_id = ''`` and are seen from every workspace of the tenant: applying the plan in a
second workspace does not publish the tenant's governance twice.

``state`` is ``claimed`` while an apply is creating the object and ``applied`` once it is
recorded; a second apply that finds a fresh claim skips the item instead of creating a
duplicate.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .schemas import TenantContext, utc_now
from .scoped_store import open_scoped_store

TENANT_SCOPE = ""
# A claim older than this is from an apply that stopped half way: the next apply may retake it.
CLAIM_STALE_AFTER = timedelta(minutes=10)


class LedgerState(StrEnum):
    CLAIMED = "claimed"
    APPLIED = "applied"


class LedgerOrigin(StrEnum):
    # The plan created the object: later applies may update it while nobody else changed it.
    CREATED = "created"
    # The object already existed (same name): the plan links it and never changes it.
    LINKED = "linked"


class LedgerEntry(BaseModel):
    tenant_id: str
    workspace_id: str
    plan_id: str
    item_key: str
    kind: str
    state: LedgerState = LedgerState.APPLIED
    origin: LedgerOrigin = LedgerOrigin.CREATED
    target_id: str | None = None
    target_version: int | None = None
    fingerprint: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    plan_version: int = 1
    applied_by: str
    created_at: datetime
    updated_at: datetime

    def stale_claim(self, now: datetime) -> bool:
        return self.state == LedgerState.CLAIMED and now - self.updated_at > CLAIM_STALE_AFTER


class SetupPlanLedger(Protocol):
    def entries(self, context: TenantContext, plan_id: str) -> list[LedgerEntry]: ...
    def claim(self, entry: LedgerEntry) -> bool: ...
    def reclaim(self, entry: LedgerEntry, previous: LedgerEntry) -> bool: ...
    def save(self, entry: LedgerEntry) -> LedgerEntry: ...
    def release(self, entry: LedgerEntry) -> None: ...


def _scopes(context: TenantContext) -> tuple[str, str]:
    return (context.workspace_id, TENANT_SCOPE)


class InMemorySetupPlanLedger:
    """``ANUM_REPOSITORY_BACKEND=memory``: the same scoping rule as the RLS policy."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], LedgerEntry] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.rows.clear()

    @staticmethod
    def _key(entry: LedgerEntry) -> tuple[str, str, str, str]:
        return (entry.tenant_id, entry.workspace_id, entry.plan_id, entry.item_key)

    def entries(self, context: TenantContext, plan_id: str) -> list[LedgerEntry]:
        scopes = _scopes(context)
        with self._lock:
            return [
                row.model_copy(deep=True) for row in self.rows.values()
                if row.tenant_id == context.tenant_id and row.workspace_id in scopes and row.plan_id == plan_id
            ]

    def claim(self, entry: LedgerEntry) -> bool:
        with self._lock:
            existing = self.rows.get(self._key(entry))
            if existing is not None and not existing.stale_claim(utc_now()):
                return False
            self.rows[self._key(entry)] = entry.model_copy(deep=True)
            return True

    def reclaim(self, entry: LedgerEntry, previous: LedgerEntry) -> bool:
        """Turn the applied row ``previous`` into this claim, only if nobody changed it since."""
        with self._lock:
            row = self.rows.get(self._key(entry))
            if row is None or row.state != LedgerState.APPLIED or row.updated_at != previous.updated_at:
                return False
            self.rows[self._key(entry)] = entry.model_copy(deep=True)
            return True

    def save(self, entry: LedgerEntry) -> LedgerEntry:
        with self._lock:
            self.rows[self._key(entry)] = entry.model_copy(deep=True)
        return entry

    def release(self, entry: LedgerEntry) -> None:
        with self._lock:
            row = self.rows.get(self._key(entry))
            if row is not None and row.state == LedgerState.CLAIMED:
                del self.rows[self._key(entry)]


memory_setup_plan_ledger = InMemorySetupPlanLedger()


@contextmanager
def open_setup_plan_ledger(context: TenantContext) -> Iterator[SetupPlanLedger]:
    """One unit of work in the caller's tenant and workspace RLS scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.setup_plan_repository import SqlAlchemySetupPlanLedger

        return SqlAlchemySetupPlanLedger(session, context)

    with open_scoped_store(context, memory_setup_plan_ledger, sql_store) as store:
        yield store
