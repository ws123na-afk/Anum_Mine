"""PostgreSQL ledger of applied setup plan items (migration ``0052_setup_plans``).

The session's RLS context names the tenant and the workspace; the policy admits rows of
that workspace and the tenant-level rows (``workspace_id = ''``). Every statement repeats
the tenant and the scope explicitly. A claim is an ``INSERT ... ON CONFLICT DO NOTHING``
(or the takeover of a stale claim with a compare-and-set update), so two concurrent applies
never both create the same item.
"""

from __future__ import annotations

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from anum_api.schemas import TenantContext, utc_now
from anum_api.setup_plan_ledger import CLAIM_STALE_AFTER, TENANT_SCOPE, LedgerEntry, LedgerOrigin, LedgerState

from .models import SetupPlanItemRecord
from .scope import require_workspace


def _entry(record: SetupPlanItemRecord) -> LedgerEntry:
    return LedgerEntry(
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        plan_id=record.plan_id,
        item_key=record.item_key,
        kind=record.kind,
        state=LedgerState(record.state),
        origin=LedgerOrigin(record.origin),
        target_id=record.target_id,
        target_version=record.target_version,
        fingerprint=record.fingerprint,
        details=dict(record.details or {}),
        plan_version=record.plan_version,
        applied_by=record.applied_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _values(entry: LedgerEntry) -> dict[str, object]:
    return {
        "tenant_id": entry.tenant_id,
        "workspace_id": entry.workspace_id,
        "plan_id": entry.plan_id,
        "item_key": entry.item_key,
        "kind": entry.kind,
        "state": entry.state.value,
        "origin": entry.origin.value,
        "target_id": entry.target_id,
        "target_version": entry.target_version,
        "fingerprint": entry.fingerprint,
        "details": entry.details,
        "plan_version": entry.plan_version,
        "applied_by": entry.applied_by,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


class SqlAlchemySetupPlanLedger:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _row(self, entry: LedgerEntry):  # type: ignore[no-untyped-def]
        return and_(
            SetupPlanItemRecord.tenant_id == entry.tenant_id,
            SetupPlanItemRecord.workspace_id == entry.workspace_id,
            SetupPlanItemRecord.plan_id == entry.plan_id,
            SetupPlanItemRecord.item_key == entry.item_key,
        )

    def _check_scope(self, entry: LedgerEntry) -> None:
        if entry.tenant_id != self.context.tenant_id or entry.workspace_id not in (self.context.workspace_id, TENANT_SCOPE):
            raise PermissionError("A setup plan ledger row must belong to the request's tenant and workspace")

    def entries(self, context: TenantContext, plan_id: str) -> list[LedgerEntry]:
        rows = self.session.scalars(
            select(SetupPlanItemRecord).where(
                SetupPlanItemRecord.tenant_id == context.tenant_id,
                SetupPlanItemRecord.workspace_id.in_((context.workspace_id, TENANT_SCOPE)),
                SetupPlanItemRecord.plan_id == plan_id,
            )
        ).all()
        return [_entry(row) for row in rows]

    def claim(self, entry: LedgerEntry) -> bool:
        self._check_scope(entry)
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        inserted = self.session.execute(
            insert(SetupPlanItemRecord).values(**_values(entry)).on_conflict_do_nothing().returning(SetupPlanItemRecord.item_key)
        ).first()
        if inserted is not None:
            return True
        # Take over a claim left behind by an apply that stopped half way.
        taken = self.session.execute(
            update(SetupPlanItemRecord)
            .where(
                self._row(entry),
                SetupPlanItemRecord.state == LedgerState.CLAIMED.value,
                SetupPlanItemRecord.updated_at < utc_now() - CLAIM_STALE_AFTER,
            )
            .values(**_values(entry))
            .returning(SetupPlanItemRecord.item_key)
        ).first()
        return taken is not None

    def save(self, entry: LedgerEntry) -> LedgerEntry:
        self._check_scope(entry)
        values = _values(entry)
        statement = insert(SetupPlanItemRecord).values(**values)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["tenant_id", "workspace_id", "plan_id", "item_key"],
                set_={key: value for key, value in values.items() if key not in {"tenant_id", "workspace_id", "plan_id", "item_key", "created_at"}},
            )
        )
        return entry

    def release(self, entry: LedgerEntry) -> None:
        self._check_scope(entry)
        self.session.execute(
            delete(SetupPlanItemRecord).where(self._row(entry), SetupPlanItemRecord.state == LedgerState.CLAIMED.value)
        )
