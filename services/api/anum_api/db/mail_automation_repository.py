"""PostgreSQL store of mailbox settings, forwarding rules and the background sync state
(migration ``0068_mail_rules_retention``, docs/email.md#mailbox-settings).

The session carries the tenant, workspace and user RLS context (``open_automation_store``
opens it with ``user_scoped=True``). Settings and sync state are readable by the mailbox's
owner and the people it is shared with; rules and their log are the owner's alone, and every
query here repeats the scope. Settings change by compare-and-set on ``version``, the sync is
claimed by compare-and-set on its lease, and a forwarded message is claimed by a unique row
(rule, UID), so two replicas never act on one thing twice.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKeyConstraint, Integer, String, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.mailbox.automation import COUNTED, ForwardLog, ForwardRule, LogStatus, MailboxSettings, SyncState
from anum_api.schemas import TenantContext, utc_now

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


def _account_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "account_id", "owner_user_id"],
        ["mail_accounts.tenant_id", "mail_accounts.workspace_id", "mail_accounts.id", "mail_accounts.owner_user_id"],
        name=f"fk_{table}_account", ondelete="CASCADE",
    )


class MailSettingsRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_account_settings"

    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sent_retention_days: Mapped[int | None] = mapped_column(Integer)
    draft_retention_days: Mapped[int | None] = mapped_column(Integer)
    label_retention_days: Mapped[int | None] = mapped_column(Integer)
    background_sync: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(160))
    pending_approval_id: Mapped[str | None] = mapped_column(String(80))
    pending_task_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_account_settings"), _account_fk("mail_account_settings"))


class MailSyncRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_sync_state"

    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    inbox_unread: Mapped[int | None] = mapped_column(Integer)
    inbox_total: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (_workspace_fk("mail_sync_state"), _account_fk("mail_sync_state"))


class MailRuleRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_forward_rules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    from_contains: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_contains: Mapped[str] = mapped_column(String(200), nullable=False)
    forward_to: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approval_id: Mapped[str | None] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(80))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_uid: Mapped[int | None] = mapped_column(BigInteger)
    last_uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    forwarded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_forward_rules"), _account_fk("mail_forward_rules"))


class MailForwardLogRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_forward_log"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("mail_forward_log"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "rule_id", "owner_user_id"],
            ["mail_forward_rules.tenant_id", "mail_forward_rules.workspace_id", "mail_forward_rules.id", "mail_forward_rules.owner_user_id"],
            name="fk_mail_forward_log_rule", ondelete="CASCADE",
        ),
    )


def _settings(row: MailSettingsRecord) -> MailboxSettings:
    return MailboxSettings(
        account_id=row.account_id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
        sent_retention_days=row.sent_retention_days, draft_retention_days=row.draft_retention_days, label_retention_days=row.label_retention_days,
        background_sync=row.background_sync, version=row.version, updated_by=row.updated_by, pending_approval_id=row.pending_approval_id,
        pending_task_id=row.pending_task_id, created_at=row.created_at, updated_at=row.updated_at,
    )


def _sync(row: MailSyncRecord) -> SyncState:
    return SyncState(
        account_id=row.account_id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
        inbox_unread=row.inbox_unread, inbox_total=row.inbox_total, status=row.status, last_error=row.last_error,  # type: ignore[arg-type]
        checked_at=row.checked_at, next_sync_at=row.next_sync_at, lease_until=row.lease_until,
    )


def _rule(row: MailRuleRecord) -> ForwardRule:
    return ForwardRule(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id, account_id=row.account_id,
        name=row.name, from_contains=row.from_contains, subject_contains=row.subject_contains, forward_to=list(row.forward_to or []),
        status=row.status, rule_hash=row.rule_hash, approved_hash=row.approved_hash, approval_id=row.approval_id, task_id=row.task_id,  # type: ignore[arg-type]
        approved_at=row.approved_at, last_uid=row.last_uid, last_uidvalidity=row.last_uidvalidity, last_run_at=row.last_run_at, last_error=row.last_error,
        forwarded_count=row.forwarded_count, created_at=row.created_at, updated_at=row.updated_at,
    )


RULE_COLUMNS = (
    "name", "from_contains", "subject_contains", "forward_to", "status", "rule_hash", "approved_hash", "approval_id", "task_id",
    "approved_at", "last_uid", "last_uidvalidity", "last_run_at", "last_error", "forwarded_count", "updated_at",
)


class SqlAlchemyAutomationStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.tenant_id = context.tenant_id
        self.workspace_id = context.workspace_id
        self.owner = context.user_id

    def _workspace(self, model):  # type: ignore[no-untyped-def]
        return (model.tenant_id == self.tenant_id, model.workspace_id == self.workspace_id)

    def _mine(self, model):  # type: ignore[no-untyped-def]
        return (*self._workspace(model), model.owner_user_id == self.owner)

    def _check(self, item: MailboxSettings | SyncState | ForwardRule | ForwardLog) -> None:
        if (item.tenant_id, item.workspace_id, item.owner_user_id) != (self.tenant_id, self.workspace_id, self.owner):
            raise PermissionError("row outside the caller's scope")

    # -- settings (read by the owner and grantees: the row policy decides) -----------------------

    def get_settings(self, account_id: str) -> MailboxSettings | None:
        row = self.session.scalar(select(MailSettingsRecord).where(*self._workspace(MailSettingsRecord), MailSettingsRecord.account_id == account_id))
        return _settings(row) if row else None

    def save_settings(self, item: MailboxSettings, expected_version: int | None) -> bool:
        self._check(item)
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        values = {key: getattr(item, key) for key in (
            "sent_retention_days", "draft_retention_days", "label_retention_days", "background_sync", "version", "updated_by", "updated_at",
            "pending_approval_id", "pending_task_id",
        )}
        if expected_version is None:
            statement = insert(MailSettingsRecord).values(
                account_id=item.account_id, tenant_id=item.tenant_id, workspace_id=item.workspace_id, owner_user_id=item.owner_user_id,
                created_at=item.created_at, **values,
            ).on_conflict_do_nothing(index_elements=["account_id"]).returning(MailSettingsRecord.account_id)
            return self.session.execute(statement).first() is not None
        result = self.session.execute(
            update(MailSettingsRecord)
            .where(*self._mine(MailSettingsRecord), MailSettingsRecord.account_id == item.account_id, MailSettingsRecord.version == expected_version)
            .values(**values)
        )
        return (result.rowcount or 0) == 1  # type: ignore[attr-defined]

    def set_pending(self, account_id: str, approval_id: str | None, task_id: str | None) -> None:
        self.session.execute(
            update(MailSettingsRecord)
            .where(*self._mine(MailSettingsRecord), MailSettingsRecord.account_id == account_id)
            .values(pending_approval_id=approval_id, pending_task_id=task_id)
        )

    # -- sync state ------------------------------------------------------------------------------

    def get_sync(self, account_id: str) -> SyncState | None:
        row = self.session.scalar(select(MailSyncRecord).where(*self._workspace(MailSyncRecord), MailSyncRecord.account_id == account_id))
        return _sync(row) if row else None

    def sync_for(self, account_ids: list[str]) -> dict[str, SyncState]:
        if not account_ids:
            return {}
        rows = self.session.scalars(select(MailSyncRecord).where(*self._workspace(MailSyncRecord), MailSyncRecord.account_id.in_(account_ids)))
        return {row.account_id: _sync(row) for row in rows}

    def claim_sync(self, state: SyncState, now: datetime, lease_until: datetime) -> bool:
        """Take the mailbox's sync for this replica: free (no lease or an expired one) and due."""
        self._check(state)
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        statement = insert(MailSyncRecord).values(
            account_id=state.account_id, tenant_id=state.tenant_id, workspace_id=state.workspace_id, owner_user_id=state.owner_user_id,
            status=state.status, next_sync_at=now, lease_until=lease_until, created_at=now, updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["account_id"],
            set_={"lease_until": lease_until, "updated_at": now},
            where=and_(
                MailSyncRecord.owner_user_id == self.owner,
                or_(MailSyncRecord.lease_until.is_(None), MailSyncRecord.lease_until < now),
                MailSyncRecord.next_sync_at <= now,
            ),
        ).returning(MailSyncRecord.account_id)
        return self.session.execute(statement).first() is not None

    def save_sync(self, state: SyncState) -> None:
        self._check(state)
        self.session.execute(
            update(MailSyncRecord)
            .where(*self._mine(MailSyncRecord), MailSyncRecord.account_id == state.account_id)
            .values(
                inbox_unread=state.inbox_unread, inbox_total=state.inbox_total, status=state.status, last_error=state.last_error,
                checked_at=state.checked_at, next_sync_at=state.next_sync_at, lease_until=state.lease_until, updated_at=utc_now(),
            )
        )

    # -- rules (owner only) ----------------------------------------------------------------------

    def list_rules(self, account_id: str) -> list[ForwardRule]:
        rows = self.session.scalars(
            select(MailRuleRecord).where(*self._mine(MailRuleRecord), MailRuleRecord.account_id == account_id).order_by(MailRuleRecord.created_at, MailRuleRecord.id)
        )
        return [_rule(row) for row in rows]

    def all_rules(self) -> list[ForwardRule]:
        rows = self.session.scalars(select(MailRuleRecord).where(*self._mine(MailRuleRecord)).order_by(MailRuleRecord.created_at, MailRuleRecord.id))
        return [_rule(row) for row in rows]

    def get_rule(self, rule_id: str) -> ForwardRule | None:
        row = self.session.scalar(select(MailRuleRecord).where(*self._mine(MailRuleRecord), MailRuleRecord.id == rule_id))
        return _rule(row) if row else None

    def save_rule(self, rule: ForwardRule) -> ForwardRule:
        self._check(rule)
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        row = self.session.scalar(select(MailRuleRecord).where(*self._mine(MailRuleRecord), MailRuleRecord.id == rule.id))
        if row is None:
            row = MailRuleRecord(
                id=rule.id, tenant_id=rule.tenant_id, workspace_id=rule.workspace_id, owner_user_id=rule.owner_user_id,
                account_id=rule.account_id, created_at=rule.created_at,
            )
            self.session.add(row)
        for column in RULE_COLUMNS:
            setattr(row, column, getattr(rule, column))
        self.session.flush()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        result = self.session.execute(delete(MailRuleRecord).where(*self._mine(MailRuleRecord), MailRuleRecord.id == rule_id))
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    def claim_forward(self, log: ForwardLog) -> bool:
        self._check(log)
        statement = (
            insert(MailForwardLogRecord).values(**log.model_dump())
            .on_conflict_do_nothing(index_elements=["rule_id", "uid"]).returning(MailForwardLogRecord.id)
        )
        return self.session.execute(statement).first() is not None

    def finish_forward(self, rule_id: str, uid: int, status: LogStatus, error: str | None) -> None:
        self.session.execute(
            update(MailForwardLogRecord)
            .where(*self._mine(MailForwardLogRecord), MailForwardLogRecord.rule_id == rule_id, MailForwardLogRecord.uid == uid)
            .values(status=status, error=error)
        )

    def count_forwards_since(self, rule_ids: list[str], since: datetime) -> int:
        if not rule_ids:
            return 0
        return int(self.session.scalar(
            select(func.count()).select_from(MailForwardLogRecord)
            .where(*self._mine(MailForwardLogRecord), MailForwardLogRecord.rule_id.in_(rule_ids), MailForwardLogRecord.created_at >= since,
                   MailForwardLogRecord.status.in_(COUNTED))
        ) or 0)

    def record_run(self, rule_id: str, approved_hash: str, last_uid: int | None, forwarded: int, error: str | None, now: datetime, uidvalidity: int | None = None) -> bool:
        values: dict[str, object] = {"last_uid": last_uid, "last_run_at": now, "last_error": error, "forwarded_count": MailRuleRecord.forwarded_count + forwarded}
        if uidvalidity is not None:
            values["last_uidvalidity"] = uidvalidity
        result = self.session.execute(
            update(MailRuleRecord)
            .where(*self._mine(MailRuleRecord), MailRuleRecord.id == rule_id, MailRuleRecord.status == "active", MailRuleRecord.approved_hash == approved_hash)
            .values(**values)
        )
        return (result.rowcount or 0) == 1  # type: ignore[attr-defined]

    def restart_rule(self, rule_id: str, approved_hash: str, last_uid: int, uidvalidity: int | None, now: datetime) -> bool:
        if not self.record_run(rule_id, approved_hash, last_uid, 0, None, now, uidvalidity):
            return False
        # Forward records above the new start belong to the old UID numbering.
        self.session.execute(
            delete(MailForwardLogRecord).where(*self._mine(MailForwardLogRecord), MailForwardLogRecord.rule_id == rule_id, MailForwardLogRecord.uid > last_uid)
        )
        return True

    def refuse_rule(self, rule_id: str, error: str, now: datetime) -> None:
        self.session.execute(
            update(MailRuleRecord)
            .where(*self._mine(MailRuleRecord), MailRuleRecord.id == rule_id)
            .values(status="paused", approved_hash=None, last_error=error[:500], updated_at=now)
        )
