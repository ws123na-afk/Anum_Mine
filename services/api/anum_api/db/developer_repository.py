"""PostgreSQL developer mode: requests (migrations 0022, 0023, 0031) and projects (0032_developer_projects).

The session carries the tenant and workspace RLS context (``open_scoped_store``); the
queries filter on the scope explicitly as well. Rows are shared by the workspace's owners.

Admission (``admit``) serialises creates per tenant and workspace with a transaction-scoped
advisory lock (the same key as before projects, so an older release and this one wait for
each other during a rollout), then checks the project's active request and the workspace's
daily count and inserts in the same transaction; the partial unique index
``uq_developer_requests_one_active_project`` backs the "one queued or coding request per
project" rule.

Project tokens are stored only as Fernet ciphertext (``token_ciphertext``); this store
never decrypts them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.developer import (
    ACTIVE,
    TERMINAL,
    ChecksSummary,
    DeveloperAdmissionError,
    DeveloperRequest,
    DevRequestSource,
    DevRequestStatus,
)
from anum_api.developer_projects import (
    DeveloperProject,
    ProjectCheck,
    ProjectConflictError,
    TokenSource,
)
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class DeveloperRequestRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "developer_requests"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    allow_protected_paths: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    branch: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(80))
    project_name: Mapped[str | None] = mapped_column(String(120))
    repository: Mapped[str | None] = mapped_column(String(200))
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    run_url: Mapped[str | None] = mapped_column(String(500))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(String(500))
    pr_title: Mapped[str | None] = mapped_column(String(300))
    pr_draft: Mapped[bool | None] = mapped_column(Boolean)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    checks: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    merge_error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    merge_task_id: Mapped[str | None] = mapped_column(String(80))
    merge_commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (_workspace_fk("developer_requests"),)


class DeveloperProjectRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "developer_projects"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    github_owner: Mapped[str] = mapped_column(String(100), nullable=False)
    github_repo: Mapped[str] = mapped_column(String(100), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    protected_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    workflow_file: Mapped[str] = mapped_column(String(110), nullable=False)
    token_source: Mapped[str] = mapped_column(String(20), nullable=False)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    install_pr_number: Mapped[int | None] = mapped_column(Integer)
    install_pr_url: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("developer_projects"),)


# pg_advisory_xact_lock(key1, key2): key1 names developer-mode admission, key2 is the scope's
# hash. A hash collision only makes two scopes wait for each other, never admits more.
ADMISSION_LOCK_CLASS = 0x414E4456  # "ANDV"
ONE_ACTIVE_INDEXES = ("uq_developer_requests_one_active_project", "uq_developer_requests_one_active")
PROJECT_NAME_INDEX = "uq_developer_projects_name"
PROJECT_REPOSITORY_INDEX = "uq_developer_projects_repository"

_FIELDS = (
    "created_by", "text", "source", "status", "allow_protected_paths", "branch", "project_id", "project_name",
    "repository", "run_id", "run_url", "pr_number", "pr_url", "pr_title", "pr_draft", "head_sha", "failure_reason",
    "merge_error", "approval_id", "merge_task_id", "merge_commit_sha", "created_at", "updated_at", "merged_at",
    "synced_at",
)
_PROJECT_FIELDS = (
    "name", "github_owner", "github_repo", "default_branch", "description", "enabled", "protected_paths",
    "workflow_file", "token_ciphertext", "token_updated_at", "install_pr_number", "install_pr_url", "created_by",
    "created_at", "updated_at",
)


def _model(record: DeveloperRequestRecord) -> DeveloperRequest:
    return DeveloperRequest(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        created_by=record.created_by,
        text=record.text,
        source=DevRequestSource(record.source),
        status=DevRequestStatus(record.status),
        allow_protected_paths=record.allow_protected_paths,
        branch=record.branch,
        project_id=record.project_id,
        project_name=record.project_name,
        repository=record.repository,
        run_id=record.run_id,
        run_url=record.run_url,
        pr_number=record.pr_number,
        pr_url=record.pr_url,
        pr_title=record.pr_title,
        pr_draft=record.pr_draft,
        head_sha=record.head_sha,
        checks=ChecksSummary(**record.checks) if record.checks else None,
        failure_reason=record.failure_reason,
        merge_error=record.merge_error,
        approval_id=record.approval_id,
        merge_task_id=record.merge_task_id,
        merge_commit_sha=record.merge_commit_sha,
        created_at=record.created_at,
        updated_at=record.updated_at,
        merged_at=record.merged_at,
        synced_at=record.synced_at,
    )


def _copy(request: DeveloperRequest, record: DeveloperRequestRecord) -> None:
    for name in _FIELDS:
        value = getattr(request, name)
        setattr(record, name, value.value if isinstance(value, (DevRequestSource, DevRequestStatus)) else value)
    record.checks = request.checks.model_dump() if request.checks else None


def _project_model(record: DeveloperProjectRecord) -> DeveloperProject:
    return DeveloperProject(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        name=record.name,
        github_owner=record.github_owner,
        github_repo=record.github_repo,
        default_branch=record.default_branch,
        description=record.description,
        enabled=record.enabled,
        protected_paths=list(record.protected_paths or []),
        workflow_file=record.workflow_file,
        token_source=TokenSource(record.token_source),
        token_ciphertext=record.token_ciphertext,
        token_updated_at=record.token_updated_at,
        last_check=ProjectCheck(**record.last_check) if record.last_check else None,
        install_pr_number=record.install_pr_number,
        install_pr_url=record.install_pr_url,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _project_copy(project: DeveloperProject, record: DeveloperProjectRecord) -> None:
    for name in _PROJECT_FIELDS:
        setattr(record, name, getattr(project, name))
    record.protected_paths = list(project.protected_paths)
    record.token_source = project.token_source.value
    record.last_check = project.last_check.model_dump(mode="json") if project.last_check else None


def _escape_like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlAlchemyDeveloperStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self):  # type: ignore[no-untyped-def]
        return (
            DeveloperRequestRecord.tenant_id == self.context.tenant_id,
            DeveloperRequestRecord.workspace_id == self.context.workspace_id,
        )

    def add(self, request: DeveloperRequest) -> DeveloperRequest:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = DeveloperRequestRecord(
            id=request.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id
        )
        _copy(request, record)
        self.session.add(record)
        self.session.flush()
        return request

    def admit(self, request: DeveloperRequest, *, since: datetime, limit: int) -> DeveloperRequest:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        scope_key = f"{self.context.tenant_id}\x1f{self.context.workspace_id}"
        # Held until this unit of work commits or rolls back: a concurrent create for the same
        # scope waits here and then sees this request (READ COMMITTED re-reads per statement).
        self.session.execute(
            text("select pg_advisory_xact_lock(cast(:lock_class as integer), hashtext(:scope_key))"),
            {"lock_class": ADMISSION_LOCK_CLASS, "scope_key": scope_key},
        )
        busy = self.session.scalar(
            select(func.count())
            .select_from(DeveloperRequestRecord)
            .where(
                *self._scope(),
                DeveloperRequestRecord.status.in_([status.value for status in ACTIVE]),
                func.coalesce(DeveloperRequestRecord.project_id, "") == (request.project_id or ""),
            )
        )
        if busy:
            raise DeveloperAdmissionError("active")
        if self.count_since(self.context, since) >= limit:
            raise DeveloperAdmissionError("daily_limit")
        try:
            return self.add(request)
        except IntegrityError as exc:
            # A writer that does not take the lock.
            if any(index in str(exc.orig) for index in ONE_ACTIVE_INDEXES):
                raise DeveloperAdmissionError("active") from None
            raise

    def _record(self, request_id: str, *, for_update: bool = False) -> DeveloperRequestRecord | None:
        statement = select(DeveloperRequestRecord).where(*self._scope(), DeveloperRequestRecord.id == request_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get(self, request_id: str, context: TenantContext) -> DeveloperRequest | None:
        record = self._record(request_id)
        return _model(record) if record is not None else None

    def save(self, request: DeveloperRequest) -> DeveloperRequest:
        record = self._record(request.id, for_update=True)
        if record is None:
            raise KeyError(request.id)
        _copy(request, record)
        self.session.flush()
        return request

    def list(
        self,
        context: TenantContext,
        *,
        limit: int,
        before: DeveloperRequest | None,
        query: str | None,
        project_id: str | None = None,
    ) -> list[DeveloperRequest]:
        statement = select(DeveloperRequestRecord).where(*self._scope())
        if project_id is not None:
            statement = statement.where(DeveloperRequestRecord.project_id == project_id)
        if before is not None:
            statement = statement.where(
                or_(
                    DeveloperRequestRecord.created_at < before.created_at,
                    and_(
                        DeveloperRequestRecord.created_at == before.created_at,
                        DeveloperRequestRecord.id < before.id,
                    ),
                )
            )
        if query:
            pattern = _escape_like(query)
            statement = statement.where(
                or_(
                    DeveloperRequestRecord.text.ilike(pattern, escape="\\"),
                    DeveloperRequestRecord.pr_title.ilike(pattern, escape="\\"),
                )
            )
        records = self.session.scalars(
            statement.order_by(DeveloperRequestRecord.created_at.desc(), DeveloperRequestRecord.id.desc()).limit(limit)
        ).all()
        return [_model(record) for record in records]

    def count_since(self, context: TenantContext, since: datetime) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(DeveloperRequestRecord)
                .where(*self._scope(), DeveloperRequestRecord.created_at >= since)
            )
            or 0
        )

    def active(self, context: TenantContext) -> list[DeveloperRequest]:
        records = self.session.scalars(
            select(DeveloperRequestRecord).where(
                *self._scope(), DeveloperRequestRecord.status.in_([status.value for status in ACTIVE])
            )
        ).all()
        return [_model(record) for record in records]

    def unfinished(self, context: TenantContext, project_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(DeveloperRequestRecord)
                .where(
                    *self._scope(),
                    DeveloperRequestRecord.project_id == project_id,
                    DeveloperRequestRecord.status.not_in([status.value for status in TERMINAL]),
                )
            )
            or 0
        )


class SqlAlchemyProjectStore:
    """Projects of one tenant and workspace. Name and repository are unique per workspace
    (case-insensitive unique indexes); a clash raises ``ProjectConflictError``."""

    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self):  # type: ignore[no-untyped-def]
        return (
            DeveloperProjectRecord.tenant_id == self.context.tenant_id,
            DeveloperProjectRecord.workspace_id == self.context.workspace_id,
        )

    def _record(self, project_id: str, *, for_update: bool = False) -> DeveloperProjectRecord | None:
        statement = select(DeveloperProjectRecord).where(*self._scope(), DeveloperProjectRecord.id == project_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def _flush(self) -> None:
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError as exc:
            message = str(exc.orig)
            if PROJECT_NAME_INDEX in message:
                raise ProjectConflictError("name") from None
            if PROJECT_REPOSITORY_INDEX in message:
                raise ProjectConflictError("repository") from None
            raise

    def list(self, context: TenantContext, *, query: str | None = None) -> list[DeveloperProject]:
        statement = select(DeveloperProjectRecord).where(*self._scope())
        if query:
            pattern = _escape_like(query)
            statement = statement.where(
                or_(
                    DeveloperProjectRecord.name.ilike(pattern, escape="\\"),
                    (DeveloperProjectRecord.github_owner + "/" + DeveloperProjectRecord.github_repo).ilike(pattern, escape="\\"),
                    DeveloperProjectRecord.description.ilike(pattern, escape="\\"),
                )
            )
        records = self.session.scalars(
            statement.order_by(func.lower(DeveloperProjectRecord.name), DeveloperProjectRecord.id)
        ).all()
        return [_project_model(record) for record in records]

    def get(self, project_id: str, context: TenantContext) -> DeveloperProject | None:
        record = self._record(project_id)
        return _project_model(record) if record is not None else None

    def add(self, project: DeveloperProject) -> DeveloperProject:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = DeveloperProjectRecord(
            id=project.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id
        )
        _project_copy(project, record)
        self.session.add(record)
        self._flush()
        return project

    def save(self, project: DeveloperProject) -> DeveloperProject:
        record = self._record(project.id, for_update=True)
        if record is None:
            raise KeyError(project.id)
        _project_copy(project, record)
        self._flush()
        return project

    def delete(self, project_id: str, context: TenantContext) -> bool:
        record = self._record(project_id, for_update=True)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def count(self, context: TenantContext) -> int:
        return int(
            self.session.scalar(select(func.count()).select_from(DeveloperProjectRecord).where(*self._scope())) or 0
        )
