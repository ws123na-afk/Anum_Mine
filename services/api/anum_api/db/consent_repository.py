"""PostgreSQL consent records for campaigns (migration ``0129_setup_plan_gaps``).

``contact_consents`` and ``contact_consent_settings`` are organisation-wide under forced RLS on
``anum.tenant_id``; every statement also filters on the tenant. Recording is one
``insert ... on conflict do update`` on (tenant, contact hash, channel), so two people
recording the same consent at once never make two rows. Audit records go to
``audit_records`` in the same transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.contact_consent import ConsentChannel, ConsentRecord, ConsentSettings, LawfulBasis
from anum_api.schemas import TenantContext

from .repository import SqlAlchemyRepository
from .scope import require_tenant

COLUMNS = (
    "id, tenant_id, recipient_hash, masked, kind, channel, lawful_basis, source, consented_at, note, "
    "withdrawn_at, withdrawn_reason, withdrawn_by, created_by, created_at, updated_at"
)


def _record(row: Any) -> ConsentRecord:
    return ConsentRecord(
        id=row.id, tenant_id=row.tenant_id, recipient_hash=row.recipient_hash, masked=row.masked, kind=row.kind,
        channel=ConsentChannel(row.channel), lawful_basis=LawfulBasis(row.lawful_basis), source=row.source,
        consented_at=row.consented_at, note=row.note, withdrawn_at=row.withdrawn_at, withdrawn_reason=row.withdrawn_reason,
        withdrawn_by=row.withdrawn_by, created_by=row.created_by, created_at=row.created_at, updated_at=row.updated_at,
    )


class SqlAlchemyConsentStore:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._audit = SqlAlchemyRepository(session)

    def list(self, context: TenantContext) -> list[ConsentRecord]:
        rows = self.session.execute(
            text(f"select {COLUMNS} from contact_consents where tenant_id = :t order by updated_at desc, id"),  # nosec B608
            {"t": context.tenant_id},
        ).all()
        return [_record(row) for row in rows]

    def find(self, context: TenantContext, hashes: list[str]) -> list[ConsentRecord]:
        if not hashes:
            return []
        rows = self.session.execute(
            text(f"select {COLUMNS} from contact_consents where tenant_id = :t and recipient_hash = any(:h)"),  # nosec B608
            {"t": context.tenant_id, "h": list(hashes)},
        ).all()
        return [_record(row) for row in rows]

    def get(self, context: TenantContext, record_id: str) -> ConsentRecord | None:
        row = self.session.execute(
            text(f"select {COLUMNS} from contact_consents where tenant_id = :t and id = :id"),  # nosec B608
            {"t": context.tenant_id, "id": record_id},
        ).one_or_none()
        return _record(row) if row is not None else None

    def upsert(self, context: TenantContext, record: ConsentRecord) -> tuple[ConsentRecord, bool]:
        require_tenant(self.session, context.tenant_id)
        row = self.session.execute(
            text(
                f"""
                insert into contact_consents (id, tenant_id, recipient_hash, masked, kind, channel, lawful_basis, source,
                    consented_at, note, withdrawn_at, withdrawn_reason, withdrawn_by, created_by, created_at, updated_at)
                values (:id, :t, :hash, :masked, :kind, :channel, :basis, :source, :consented_at, :note, null, null, null,
                        :by, :now, :now)
                on conflict on constraint uq_contact_consents_contact_channel do update set
                    masked = excluded.masked, lawful_basis = excluded.lawful_basis, source = excluded.source,
                    consented_at = excluded.consented_at, note = excluded.note, withdrawn_at = null,
                    withdrawn_reason = null, withdrawn_by = null, updated_at = excluded.updated_at
                returning {COLUMNS}, (xmax = 0) as inserted
                """  # nosec B608 - fixed column list
            ),
            {"id": record.id, "t": context.tenant_id, "hash": record.recipient_hash, "masked": record.masked, "kind": record.kind,
             "channel": record.channel.value, "basis": record.lawful_basis.value, "source": record.source,
             "consented_at": record.consented_at, "note": record.note, "by": record.created_by, "now": record.created_at},
        ).one()
        return _record(row), bool(row.inserted)

    def withdraw(self, context: TenantContext, record_id: str, reason: str, by: str, at: datetime) -> ConsentRecord | None:
        row = self.session.execute(
            text(
                f"""
                update contact_consents set withdrawn_at = :at, withdrawn_reason = :reason, withdrawn_by = :by, updated_at = :at
                where tenant_id = :t and id = :id and withdrawn_at is null
                returning {COLUMNS}
                """  # nosec B608 - fixed column list
            ),
            {"t": context.tenant_id, "id": record_id, "at": at, "reason": reason, "by": by},
        ).one_or_none()
        return _record(row) if row is not None else None

    def get_settings(self, context: TenantContext) -> ConsentSettings:
        row = self.session.execute(
            text("select require_for_marketing, updated_by, updated_at from contact_consent_settings where tenant_id = :t"),
            {"t": context.tenant_id},
        ).one_or_none()
        if row is None:
            return ConsentSettings()
        return ConsentSettings(require_for_marketing=bool(row.require_for_marketing), updated_by=row.updated_by, updated_at=row.updated_at)

    def save_settings(self, context: TenantContext, settings: ConsentSettings) -> ConsentSettings:
        require_tenant(self.session, context.tenant_id)
        self.session.execute(
            text(
                """
                insert into contact_consent_settings (tenant_id, require_for_marketing, updated_by, updated_at)
                values (:t, :req, :by, :at)
                on conflict (tenant_id) do update set require_for_marketing = excluded.require_for_marketing,
                    updated_by = excluded.updated_by, updated_at = excluded.updated_at
                """
            ),
            {"t": context.tenant_id, "req": settings.require_for_marketing, "by": settings.updated_by, "at": settings.updated_at},
        )
        return settings

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)
