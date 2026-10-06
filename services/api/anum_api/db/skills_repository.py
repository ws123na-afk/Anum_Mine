from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.schemas import RiskLevel, TenantContext
from anum_api.skills_api import SkillInstallation, SkillVersion, SkillVersionExistsError

from .models import SkillInstallationRecord, SkillVersionRecord
from .scope import require_tenant, require_workspace


class SqlAlchemySkillStore:
    """PostgreSQL skill registry.

    The session must already carry the tenant and workspace RLS context; queries also
    filter on the scope explicitly. Versions are tenant-level, installations per workspace.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

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
                    )
                )
        except IntegrityError as exc:  # a concurrent publish of the same version won
            raise SkillVersionExistsError(version.skill_id) from exc
        return version

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
    )
