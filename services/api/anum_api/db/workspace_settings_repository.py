"""PostgreSQL stores for workspace-level settings: integration configurations, file
metadata and notification preferences.

Each store's session must already carry the tenant and workspace RLS context; queries
also filter on the scope explicitly.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anum_api.files import FileRecord
from anum_api.integrations import IntegrationConfiguration
from anum_api.onboarding import NotificationPreferences
from anum_api.schemas import TenantContext, utc_now

from .models import IntegrationConfigurationRecord, NotificationPreferenceRecord, WorkspaceFileRecord
from .scope import require_workspace


class SqlAlchemyIntegrationConfigurationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_configurations(self, context: TenantContext) -> dict[str, IntegrationConfiguration]:
        records = self.session.scalars(
            select(IntegrationConfigurationRecord).where(
                IntegrationConfigurationRecord.tenant_id == context.tenant_id,
                IntegrationConfigurationRecord.workspace_id == context.workspace_id,
            )
        ).all()
        return {record.integration_id: _configuration(record) for record in records}

    def get(self, context: TenantContext, integration_id: str) -> IntegrationConfiguration | None:
        record = self.session.get(
            IntegrationConfigurationRecord, (context.tenant_id, context.workspace_id, integration_id)
        )
        return None if record is None else _configuration(record)

    def save(
        self, context: TenantContext, integration_id: str, configuration: IntegrationConfiguration
    ) -> IntegrationConfiguration:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        record = self.session.get(
            IntegrationConfigurationRecord, (context.tenant_id, context.workspace_id, integration_id)
        )
        if record is None:
            record = IntegrationConfigurationRecord(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                integration_id=integration_id,
            )
            self.session.add(record)
        record.enabled = configuration.enabled
        record.endpoint = configuration.endpoint
        record.updated_by = context.user_id
        record.updated_at = utc_now()
        self.session.flush()
        return configuration


class SqlAlchemyFileMetadataStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: FileRecord) -> FileRecord:
        require_workspace(self.session, record.tenant_id, record.workspace_id)
        self.session.add(
            WorkspaceFileRecord(
                id=record.id,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                name=record.name,
                content_type=record.content_type,
                size_bytes=record.size_bytes,
                sha256=record.sha256,
                storage_key=record.storage_key,
                created_by=record.created_by,
                created_at=record.created_at,
            )
        )
        self.session.flush()
        return record

    def get(self, context: TenantContext, file_id: str) -> FileRecord | None:
        row = self.session.scalar(
            select(WorkspaceFileRecord).where(
                WorkspaceFileRecord.tenant_id == context.tenant_id,
                WorkspaceFileRecord.workspace_id == context.workspace_id,
                WorkspaceFileRecord.id == file_id,
            )
        )
        return None if row is None else _file(row)

    def list_files(self, context: TenantContext, limit: int) -> list[FileRecord]:
        rows = self.session.scalars(
            select(WorkspaceFileRecord)
            .where(
                WorkspaceFileRecord.tenant_id == context.tenant_id,
                WorkspaceFileRecord.workspace_id == context.workspace_id,
            )
            .order_by(WorkspaceFileRecord.created_at, WorkspaceFileRecord.id)
            .limit(limit)
        ).all()
        return [_file(row) for row in rows]

    def delete(self, context: TenantContext, file_id: str) -> bool:
        result = self.session.execute(
            delete(WorkspaceFileRecord).where(
                WorkspaceFileRecord.tenant_id == context.tenant_id,
                WorkspaceFileRecord.workspace_id == context.workspace_id,
                WorkspaceFileRecord.id == file_id,
            )
        )
        return bool(result.rowcount)


class SqlAlchemyNotificationPreferenceStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, context: TenantContext) -> NotificationPreferences | None:
        record = self._record(context)
        if record is None:
            return None
        return NotificationPreferences(
            task_completed=record.task_completed,
            approval_required=record.approval_required,
            run_failed=record.run_failed,
            automation_failed=record.automation_failed,
            email_enabled=record.email_enabled,
            desktop_enabled=record.desktop_enabled,
        )

    def save(self, context: TenantContext, preferences: NotificationPreferences) -> NotificationPreferences:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        record = self._record(context)
        if record is None:
            record = NotificationPreferenceRecord(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
            )
            self.session.add(record)
        record.task_completed = preferences.task_completed
        record.approval_required = preferences.approval_required
        record.run_failed = preferences.run_failed
        record.automation_failed = preferences.automation_failed
        record.email_enabled = preferences.email_enabled
        record.desktop_enabled = preferences.desktop_enabled
        record.updated_at = utc_now()
        self.session.flush()
        return preferences

    def _record(self, context: TenantContext) -> NotificationPreferenceRecord | None:
        return self.session.get(
            NotificationPreferenceRecord, (context.tenant_id, context.workspace_id, context.user_id)
        )


def _configuration(record: IntegrationConfigurationRecord) -> IntegrationConfiguration:
    return IntegrationConfiguration(enabled=record.enabled, endpoint=record.endpoint)


def _file(row: WorkspaceFileRecord) -> FileRecord:
    return FileRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        name=row.name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_key=row.storage_key,
        created_by=row.created_by,
        created_at=row.created_at,
    )
