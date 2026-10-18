"""PostgreSQL stores for workspace-level settings: integration configurations, file
metadata and notification preferences.

Each store's session must already carry the tenant and workspace RLS context; queries
also filter on the scope explicitly.
"""

from sqlalchemy import delete, func, select, text, tuple_, update
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.files import FilePageQuery, FileRecord, FileSort, FileUsage, StorageQuotaExceeded
from anum_api.integrations import IntegrationCheck, IntegrationConfiguration, IntegrationStatus, StoredCredential
from anum_api.onboarding import NotificationPreferences
from anum_api.schemas import TenantContext, utc_now

from .models import (
    IntegrationConfigurationRecord,
    IntegrationCredentialRecord,
    IntegrationHealthCheckRecord,
    NotificationPreferenceRecord,
    WorkspaceFileRecord,
)
from .scope import require_workspace


class SqlAlchemyIntegrationConfigurationStore:
    """Switches, health history and encrypted workspace credentials; audits in the same transaction."""

    def __init__(self, session: Session) -> None:
        from .repository import SqlAlchemyRepository

        self.session = session
        self._audit = SqlAlchemyRepository(session)

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)

    def list_checks(self, context: TenantContext) -> dict[str, IntegrationCheck]:
        records = self.session.scalars(
            select(IntegrationHealthCheckRecord).where(
                IntegrationHealthCheckRecord.tenant_id == context.tenant_id,
                IntegrationHealthCheckRecord.workspace_id == context.workspace_id,
            )
        ).all()
        return {record.integration_id: _check(record) for record in records}

    def save_check(self, context: TenantContext, integration_id: str, check: IntegrationCheck) -> IntegrationCheck:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        key = (context.tenant_id, context.workspace_id, integration_id)
        record = self.session.get(IntegrationHealthCheckRecord, key)
        if record is None:
            record = IntegrationHealthCheckRecord(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, integration_id=integration_id
            )
            self.session.add(record)
        record.status = check.status.value
        record.detail = check.detail
        record.latency_ms = check.latency_ms
        record.checked_by = check.checked_by
        record.checked_at = check.checked_at
        record.last_error = check.last_error
        record.last_error_at = check.last_error_at
        self.session.flush()
        return check

    def list_credentials(self, context: TenantContext) -> dict[str, StoredCredential]:
        records = self.session.scalars(
            select(IntegrationCredentialRecord).where(
                IntegrationCredentialRecord.tenant_id == context.tenant_id,
                IntegrationCredentialRecord.workspace_id == context.workspace_id,
            )
        ).all()
        return {record.integration_id: _credential(record) for record in records}

    def get_credential(self, context: TenantContext, integration_id: str) -> StoredCredential | None:
        record = self.session.get(
            IntegrationCredentialRecord, (context.tenant_id, context.workspace_id, integration_id)
        )
        return None if record is None else _credential(record)

    def save_credential(
        self, context: TenantContext, integration_id: str, credential: StoredCredential
    ) -> StoredCredential:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        key = (context.tenant_id, context.workspace_id, integration_id)
        record = self.session.get(IntegrationCredentialRecord, key)
        if record is None:
            record = IntegrationCredentialRecord(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, integration_id=integration_id
            )
            self.session.add(record)
        record.ciphertext = credential.ciphertext
        record.credential_hint = credential.hint
        record.updated_by = credential.updated_by
        record.updated_at = credential.updated_at
        self.session.flush()
        return credential

    def delete_credential(self, context: TenantContext, integration_id: str) -> bool:
        result = self.session.execute(
            delete(IntegrationCredentialRecord).where(
                IntegrationCredentialRecord.tenant_id == context.tenant_id,
                IntegrationCredentialRecord.workspace_id == context.workspace_id,
                IntegrationCredentialRecord.integration_id == integration_id,
            )
        )
        return bool(result.rowcount)

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

    def _scope(self, context: TenantContext):  # type: ignore[no-untyped-def]
        return (
            WorkspaceFileRecord.tenant_id == context.tenant_id,
            WorkspaceFileRecord.workspace_id == context.workspace_id,
        )

    def add(self, record: FileRecord, quota_bytes: int | None = None) -> FileRecord:
        require_workspace(self.session, record.tenant_id, record.workspace_id)
        if quota_bytes is not None:
            # One upload at a time per workspace decides against the quota; the lock is
            # released when this unit of work commits or rolls back.
            self.session.execute(
                text("select pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"anum.files.quota:{record.tenant_id}:{record.workspace_id}"},
            )
            scope = TenantContext(tenant_id=record.tenant_id, workspace_id=record.workspace_id, user_id=record.created_by)
            current = self.usage(scope)
            if current.used_bytes + record.size_bytes > quota_bytes:
                raise StorageQuotaExceeded(current)
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
                scan_status=record.scan_status,
                scan_signature=record.scan_signature,
                scanned_at=record.scanned_at,
            )
        )
        self.session.flush()
        return record

    def _row(self, context: TenantContext, file_id: str) -> WorkspaceFileRecord | None:
        return self.session.scalar(
            select(WorkspaceFileRecord).where(*self._scope(context), WorkspaceFileRecord.id == file_id)
        )

    def get(self, context: TenantContext, file_id: str) -> FileRecord | None:
        row = self._row(context, file_id)
        return None if row is None else _file(row)

    def list_files(self, context: TenantContext, limit: int) -> list[FileRecord]:
        rows = self.session.scalars(
            select(WorkspaceFileRecord)
            .where(*self._scope(context))
            .order_by(WorkspaceFileRecord.created_at, WorkspaceFileRecord.id)
            .limit(limit)
        ).all()
        return [_file(row) for row in rows]

    def list_page(self, context: TenantContext, query: FilePageQuery) -> list[FileRecord]:
        column = {
            FileSort.NAME: func.lower(WorkspaceFileRecord.name).collate("C"),
            FileSort.SIZE: WorkspaceFileRecord.size_bytes,
        }.get(query.sort, WorkspaceFileRecord.created_at)
        statement = select(WorkspaceFileRecord).where(*self._scope(context))
        if query.search:
            escaped = query.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(WorkspaceFileRecord.name.ilike(f"%{escaped}%", escape="\\"))
        if query.after is not None:
            after = query.after
            boundary = {
                FileSort.NAME: after.name.lower(),
                FileSort.SIZE: after.size_bytes,
            }.get(query.sort, after.created_at)
            key = tuple_(column, WorkspaceFileRecord.id)
            statement = statement.where(
                key < tuple_(boundary, after.id) if query.descending else key > tuple_(boundary, after.id)
            )
        if query.descending:
            statement = statement.order_by(column.desc(), WorkspaceFileRecord.id.desc())
        else:
            statement = statement.order_by(column.asc(), WorkspaceFileRecord.id.asc())
        rows = self.session.scalars(statement.limit(query.limit)).all()
        return [_file(row) for row in rows]

    def usage(self, context: TenantContext) -> FileUsage:
        used, count = self.session.execute(
            select(func.coalesce(func.sum(WorkspaceFileRecord.size_bytes), 0), func.count()).where(
                *self._scope(context)
            )
        ).one()
        return FileUsage(used_bytes=int(used), file_count=int(count))

    def rename(self, context: TenantContext, file_id: str, name: str) -> FileRecord | None:
        row = self._row(context, file_id)
        if row is None:
            return None
        row.name = name
        row.updated_by = context.user_id
        row.updated_at = utc_now()
        self.session.flush()
        return _file(row)

    def delete(self, context: TenantContext, file_id: str) -> bool:
        result = self.session.execute(
            delete(WorkspaceFileRecord).where(*self._scope(context), WorkspaceFileRecord.id == file_id)
        )
        return bool(result.rowcount)

    def record_scan(self, context: TenantContext, file_id: str, sha256: str, status: str, signature: str | None) -> bool:
        result = self.session.execute(
            update(WorkspaceFileRecord)
            .where(
                *self._scope(context),
                WorkspaceFileRecord.id == file_id,
                WorkspaceFileRecord.sha256 == sha256,
                WorkspaceFileRecord.scan_status.is_(None),
            )
            .values(scan_status=status, scan_signature=signature, scanned_at=utc_now())
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
            push_enabled=record.push_enabled,
            commerce_alerts=record.commerce_alerts,
            tyre_alerts=record.tyre_alerts,
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
        record.push_enabled = preferences.push_enabled
        record.commerce_alerts = preferences.commerce_alerts
        record.tyre_alerts = preferences.tyre_alerts
        record.updated_at = utc_now()
        self.session.flush()
        return preferences

    def _record(self, context: TenantContext) -> NotificationPreferenceRecord | None:
        return self.session.get(
            NotificationPreferenceRecord, (context.tenant_id, context.workspace_id, context.user_id)
        )


def _configuration(record: IntegrationConfigurationRecord) -> IntegrationConfiguration:
    return IntegrationConfiguration(enabled=record.enabled, endpoint=record.endpoint)


def _check(record: IntegrationHealthCheckRecord) -> IntegrationCheck:
    return IntegrationCheck(
        status=IntegrationStatus(record.status),
        detail=record.detail,
        latency_ms=record.latency_ms,
        checked_by=record.checked_by,
        checked_at=record.checked_at,
        last_error=record.last_error,
        last_error_at=record.last_error_at,
    )


def _credential(record: IntegrationCredentialRecord) -> StoredCredential:
    return StoredCredential(
        ciphertext=record.ciphertext,
        hint=record.credential_hint,
        updated_by=record.updated_by,
        updated_at=record.updated_at,
    )


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
        updated_by=row.updated_by,
        updated_at=row.updated_at,
        scan_status=row.scan_status,  # type: ignore[arg-type]
        scan_signature=row.scan_signature,
        scanned_at=row.scanned_at,
    )
