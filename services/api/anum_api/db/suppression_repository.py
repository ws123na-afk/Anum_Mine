"""PostgreSQL contact suppression list (migration ``0054_routing_suppression``).

The session's RLS context names the tenant; ``contact_suppressions`` admits only that
tenant's rows (forced RLS). Every statement also filters on the tenant explicitly. An add
is ``INSERT ... ON CONFLICT DO NOTHING`` on (tenant, contact hash, channel), so two
concurrent opt-outs never create two rows. Audit records go to ``audit_records`` in the
same transaction.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.schemas import TenantContext
from anum_api.suppression import (
    SuppressionChannel,
    SuppressionEntry,
    SuppressionPurpose,
    SuppressionSource,
)

from .models import ContactSuppressionRecord
from .repository import SqlAlchemyRepository
from .scope import require_tenant


def _entry(record: ContactSuppressionRecord) -> SuppressionEntry:
    return SuppressionEntry(
        id=record.id,
        tenant_id=record.tenant_id,
        recipient_hash=record.recipient_hash,
        masked=record.masked,
        kind=record.kind,  # type: ignore[arg-type]
        channel=SuppressionChannel(record.channel),
        purpose=SuppressionPurpose(record.purpose),
        source=SuppressionSource(record.source),
        reason=record.reason,
        created_by=record.created_by,
        created_at=record.created_at,
    )


class SqlAlchemySuppressionStore:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._audit = SqlAlchemyRepository(session)

    def list(self, context: TenantContext) -> list[SuppressionEntry]:
        records = self.session.scalars(
            select(ContactSuppressionRecord)
            .where(ContactSuppressionRecord.tenant_id == context.tenant_id)
            .order_by(ContactSuppressionRecord.created_at.desc(), ContactSuppressionRecord.id)
        ).all()
        return [_entry(record) for record in records]

    def find(self, context: TenantContext, hashes: list[str]) -> list[SuppressionEntry]:
        if not hashes:
            return []
        records = self.session.scalars(
            select(ContactSuppressionRecord).where(
                ContactSuppressionRecord.tenant_id == context.tenant_id,
                ContactSuppressionRecord.recipient_hash.in_(hashes),
            )
        ).all()
        return [_entry(record) for record in records]

    def get(self, context: TenantContext, entry_id: str) -> SuppressionEntry | None:
        record = self.session.scalar(
            select(ContactSuppressionRecord).where(
                ContactSuppressionRecord.tenant_id == context.tenant_id, ContactSuppressionRecord.id == entry_id
            )
        )
        return _entry(record) if record is not None else None

    def add(self, context: TenantContext, entry: SuppressionEntry) -> tuple[SuppressionEntry, bool]:
        require_tenant(self.session, context.tenant_id)
        inserted = self.session.execute(
            insert(ContactSuppressionRecord)
            .values(
                tenant_id=context.tenant_id,
                id=entry.id,
                recipient_hash=entry.recipient_hash,
                masked=entry.masked,
                kind=entry.kind,
                channel=entry.channel.value,
                purpose=entry.purpose.value,
                source=entry.source.value,
                reason=entry.reason,
                created_by=entry.created_by,
                created_at=entry.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_contact_suppressions_contact")
            .returning(ContactSuppressionRecord.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return entry, True
        key = (
            ContactSuppressionRecord.tenant_id == context.tenant_id,
            ContactSuppressionRecord.recipient_hash == entry.recipient_hash,
            ContactSuppressionRecord.channel == entry.channel.value,
        )
        changed = False
        if entry.purpose == SuppressionPurpose.ALL:
            # Widening a marketing-only entry to every message (never the other way).
            changed = bool(
                self.session.execute(
                    update(ContactSuppressionRecord)
                    .where(*key, ContactSuppressionRecord.purpose == SuppressionPurpose.MARKETING.value)
                    .values(purpose=SuppressionPurpose.ALL.value)
                ).rowcount
            )
        existing = self.session.scalar(select(ContactSuppressionRecord).where(*key))
        return (_entry(existing), changed) if existing is not None else (entry, False)

    def remove(self, context: TenantContext, entry_id: str) -> bool:
        result = self.session.execute(
            delete(ContactSuppressionRecord).where(
                ContactSuppressionRecord.tenant_id == context.tenant_id, ContactSuppressionRecord.id == entry_id
            )
        )
        return bool(result.rowcount)

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)
