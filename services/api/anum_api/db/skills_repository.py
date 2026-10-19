from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.skill_signing import KeySource, PublisherKey, PublisherKeyStatus, SkillSignature
from anum_api.skills_api import (
    PublisherKeyExistsError,
    SigningSettings,
    SkillInstallation,
    SkillVersion,
    SkillVersionExistsError,
    skill_target,
)

from .models import (
    AuditRecordRow,
    SkillInstallationRecord,
    SkillPublisherKeyRecord,
    SkillSigningSettingsRecord,
    SkillVersionRecord,
)
from .scope import require_tenant, require_workspace


class SqlAlchemySkillStore:
    """PostgreSQL skill registry.

    The session must already carry the tenant and workspace RLS context; queries also
    filter on the scope explicitly. Versions are tenant-level, installations per workspace.
    """

    def __init__(self, session: Session) -> None:
        from .repository import SqlAlchemyRepository

        self.session = session
        self._audit = SqlAlchemyRepository(session)

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)

    def skill_history(self, context: TenantContext, skill_id: str, limit: int) -> list[AuditRecord]:
        rows = self.session.scalars(
            select(AuditRecordRow)
            .where(
                AuditRecordRow.tenant_id == context.tenant_id,
                AuditRecordRow.workspace_id == context.workspace_id,
                AuditRecordRow.target == skill_target(skill_id),
            )
            .order_by(AuditRecordRow.created_at.desc(), AuditRecordRow.id.desc())
            .limit(limit)
        ).all()
        return [
            AuditRecord(
                id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, actor=row.actor,
                action=row.action, target=row.target, outcome=row.outcome,
                correlation_id=row.correlation_id, created_at=row.created_at,
                metadata=dict(row.record_metadata or {}),
            )
            for row in rows
        ]

    def add_version(self, version: SkillVersion) -> SkillVersion:
        require_tenant(self.session, version.publisher_tenant_id)
        if self._version_record(version.publisher_tenant_id, version.skill_id, version.version) is not None:
            raise SkillVersionExistsError(version.skill_id)
        try:
            with self.session.begin_nested():
                self.session.add(
                    SkillVersionRecord(
                        id=version.id,
                        tenant_id=version.publisher_tenant_id,
                        skill_id=version.skill_id,
                        version=version.version,
                        name=version.name,
                        description=version.description,
                        instructions=version.instructions,
                        required_tools=list(version.required_tools),
                        risk_level=version.risk_level.value,
                        created_by=version.created_by,
                        created_at=version.created_at,
                        signature_key_id=version.signature.key_id if version.signature else None,
                        signature=version.signature.signature if version.signature else None,
                        signed_at=version.signed_at,
                        signed_by=version.signed_by,
                    )
                )
        except IntegrityError as exc:  # a concurrent publish of the same version won
            raise SkillVersionExistsError(version.skill_id) from exc
        return version

    def set_signature(
        self, context: TenantContext, skill_id: str, version: str, signature: SkillSignature,
        signed_at: datetime, signed_by: str,
    ) -> SkillVersion | None:
        record = self._version_record(context.tenant_id, skill_id, version)
        if record is None:
            return None
        record.signature_key_id = signature.key_id
        record.signature = signature.signature
        record.signed_at = signed_at
        record.signed_by = signed_by
        self.session.flush()
        return _version(record)

    def get_signing_settings(self, context: TenantContext) -> SigningSettings | None:
        record = self.session.get(SkillSigningSettingsRecord, context.tenant_id)
        if record is None:
            return None
        return SigningSettings(disable_flagged=record.disable_flagged, updated_by=record.updated_by, updated_at=record.updated_at)

    def save_signing_settings(self, context: TenantContext, value: SigningSettings) -> SigningSettings:
        require_tenant(self.session, context.tenant_id)
        record = self.session.get(SkillSigningSettingsRecord, context.tenant_id)
        if record is None:
            record = SkillSigningSettingsRecord(tenant_id=context.tenant_id)
            self.session.add(record)
        record.disable_flagged = value.disable_flagged
        record.updated_by = value.updated_by or context.user_id
        record.updated_at = value.updated_at or datetime.now().astimezone()
        self.session.flush()
        return value

    def list_publisher_keys(self, context: TenantContext) -> list[PublisherKey]:
        records = self.session.scalars(
            select(SkillPublisherKeyRecord)
            .where(SkillPublisherKeyRecord.tenant_id == context.tenant_id)
            .order_by(SkillPublisherKeyRecord.created_at, SkillPublisherKeyRecord.id)
        ).all()
        return [_key(record) for record in records]

    def get_publisher_key_for_update(self, context: TenantContext, key_id: str) -> PublisherKey | None:
        """The key row locked until the unit of work ends, so key state transitions serialize."""
        record = self.session.scalar(
            select(SkillPublisherKeyRecord)
            .where(SkillPublisherKeyRecord.tenant_id == context.tenant_id, SkillPublisherKeyRecord.id == key_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return None if record is None else _key(record)

    def add_publisher_key(self, key: PublisherKey) -> PublisherKey:
        tenant_id = key.tenant_id or ""
        require_tenant(self.session, tenant_id)
        if self.session.get(SkillPublisherKeyRecord, (tenant_id, key.id)) is not None:
            raise PublisherKeyExistsError(key.id)
        try:
            with self.session.begin_nested():
                self.session.add(_fill(SkillPublisherKeyRecord(tenant_id=tenant_id, id=key.id), key))
                self.session.flush()
        except IntegrityError as exc:  # a concurrent add of the same id won
            raise PublisherKeyExistsError(key.id) from exc
        return key

    def save_publisher_key(self, key: PublisherKey) -> PublisherKey:
        record = self.session.get(SkillPublisherKeyRecord, (key.tenant_id or "", key.id))
        if record is None:
            record = SkillPublisherKeyRecord(tenant_id=key.tenant_id or "", id=key.id)
            self.session.add(record)
        _fill(record, key)
        self.session.flush()
        return key

    def get_version(self, context: TenantContext, skill_id: str, version: str) -> SkillVersion | None:
        record = self._version_record(context.tenant_id, skill_id, version)
        return None if record is None else _version(record)

    def _version_record(self, tenant_id: str, skill_id: str, version: str) -> SkillVersionRecord | None:
        return self.session.scalar(
            select(SkillVersionRecord).where(
                SkillVersionRecord.tenant_id == tenant_id,
                SkillVersionRecord.skill_id == skill_id,
                SkillVersionRecord.version == version,
            )
        )

    def list_versions(self, context: TenantContext) -> list[SkillVersion]:
        records = self.session.scalars(
            select(SkillVersionRecord)
            .where(SkillVersionRecord.tenant_id == context.tenant_id)
            .order_by(SkillVersionRecord.created_at, SkillVersionRecord.id)
        ).all()
        return [_version(record) for record in records]

    def save_installation(self, installation: SkillInstallation) -> SkillInstallation:
        require_workspace(self.session, installation.tenant_id, installation.workspace_id)
        record = self._installation_record_for(
            installation.tenant_id, installation.workspace_id, installation.skill_id
        )
        if record is not None and record.id != installation.id:
            # Re-installing replaces the workspace's installation of that skill.
            self.session.delete(record)
            self.session.flush()
            record = None
        if record is None:
            record = SkillInstallationRecord(
                id=installation.id,
                tenant_id=installation.tenant_id,
                workspace_id=installation.workspace_id,
                skill_id=installation.skill_id,
            )
            self.session.add(record)
        record.skill_version_id = installation.skill_version_id
        record.version = installation.version
        record.approved_tools = list(installation.approved_tools)
        record.enabled = installation.enabled
        record.disabled_reason = installation.disabled_reason
        record.installed_by = installation.installed_by
        record.installed_at = installation.installed_at
        self.session.flush()
        return installation

    def get_installation(self, context: TenantContext, skill_id: str) -> SkillInstallation | None:
        record = self._installation_record(context, skill_id)
        return None if record is None else _installation(record)

    def list_installations(self, context: TenantContext) -> list[SkillInstallation]:
        records = self.session.scalars(
            select(SkillInstallationRecord)
            .where(
                SkillInstallationRecord.tenant_id == context.tenant_id,
                SkillInstallationRecord.workspace_id == context.workspace_id,
            )
            .order_by(SkillInstallationRecord.installed_at, SkillInstallationRecord.id)
        ).all()
        return [_installation(record) for record in records]

    def delete_installation(self, context: TenantContext, skill_id: str) -> bool:
        result = self.session.execute(
            delete(SkillInstallationRecord).where(
                SkillInstallationRecord.tenant_id == context.tenant_id,
                SkillInstallationRecord.workspace_id == context.workspace_id,
                SkillInstallationRecord.skill_id == skill_id,
            )
        )
        return bool(result.rowcount)

    def _installation_record(self, context: TenantContext, skill_id: str) -> SkillInstallationRecord | None:
        return self._installation_record_for(context.tenant_id, context.workspace_id, skill_id)

    def _installation_record_for(
        self, tenant_id: str, workspace_id: str, skill_id: str
    ) -> SkillInstallationRecord | None:
        return self.session.scalar(
            select(SkillInstallationRecord).where(
                SkillInstallationRecord.tenant_id == tenant_id,
                SkillInstallationRecord.workspace_id == workspace_id,
                SkillInstallationRecord.skill_id == skill_id,
            )
        )


def _version(record: SkillVersionRecord) -> SkillVersion:
    return SkillVersion(
        id=record.id,
        publisher_tenant_id=record.tenant_id,
        skill_id=record.skill_id,
        version=record.version,
        name=record.name,
        description=record.description,
        instructions=record.instructions,
        required_tools=list(record.required_tools),
        risk_level=RiskLevel(record.risk_level),
        created_by=record.created_by,
        created_at=record.created_at,
        signature=(
            SkillSignature(key_id=record.signature_key_id, signature=record.signature)
            if record.signature_key_id and record.signature else None
        ),
        signed_at=record.signed_at,
        signed_by=record.signed_by,
    )


def _fill(record: SkillPublisherKeyRecord, key: PublisherKey) -> SkillPublisherKeyRecord:
    record.publisher = key.publisher
    record.public_key = key.public_key
    record.fingerprint = key.fingerprint
    record.status = key.status.value
    record.created_by = key.created_by
    record.created_at = key.created_at
    record.rotated_at = key.rotated_at
    record.replaced_by = key.replaced_by
    record.revoked_at = key.revoked_at
    record.revoked_by = key.revoked_by
    record.revoke_reason = key.revoke_reason
    return record


def _key(record: SkillPublisherKeyRecord) -> PublisherKey:
    return PublisherKey(
        id=record.id,
        tenant_id=record.tenant_id,
        publisher=record.publisher,
        public_key=record.public_key,
        fingerprint=record.fingerprint,
        source=KeySource.TENANT,
        status=PublisherKeyStatus(record.status),
        created_by=record.created_by,
        created_at=record.created_at,
        rotated_at=record.rotated_at,
        replaced_by=record.replaced_by,
        revoked_at=record.revoked_at,
        revoked_by=record.revoked_by,
        revoke_reason=record.revoke_reason,
    )


def _installation(record: SkillInstallationRecord) -> SkillInstallation:
    return SkillInstallation(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        skill_version_id=record.skill_version_id,
        skill_id=record.skill_id,
        version=record.version,
        approved_tools=list(record.approved_tools),
        enabled=record.enabled,
        installed_by=record.installed_by,
        installed_at=record.installed_at,
        disabled_reason=record.disabled_reason,
    )
