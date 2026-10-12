"""PostgreSQL store behind "Download my data" and "Delete my data" (docs/privacy.md).

The session carries the caller's tenant, workspace *and* user RLS context
(``set_tenant_context(..., user_id=...)``): the user-private tables (conversations,
voice, private memories and their index rows, and what ANUM learned) admit only the caller's rows, and the
shared tables only the caller's workspace. Every statement also names the scope and the
caller explicitly, so a missing context finds nothing. No other role is used.
"""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.conversations import Conversation, ConversationTurn
from anum_api.files import FileRecord
from anum_api.memory import MemoryNote
from anum_api.onboarding import NotificationPreferences
from anum_api.commands import privacy_counts as command_privacy_counts
from anum_api.autonomy import AutonomousAction
from anum_api.email_inbox import EmailAccount, EmailDraft, EmailMessage
from anum_api.privacy import (
    UNSENT_DRAFT_STATUSES,
    PrivateCounts,
    SharedCounts,
    _decided_by,
    email_counts,
    learning_counts,
    module_counts,
    other_module_counts,
)
from anum_api.whatsapp_store import Message as WhatsAppMessage
from anum_api.home import home_counts
from anum_api.schemas import AgentRun, Approval, ApprovalApproverRecord, Task, TenantContext, WorkspaceMembership
from anum_api.push import PushDevice
from anum_api.library_privacy import LibraryPrivacy, sql_library
from anum_api.user_preferences import UserPreferences
from anum_api.meeting_connectors import StoredConnection
from anum_api.meetings import Meeting
from anum_api.meetings import TranscriptSegment as MeetingTranscriptSegment
from anum_api.voice import TranscriptSegment, VoiceSession, transcript_expired

from .autonomy_repository import SqlAlchemyAutonomyStore
from .commerce_repository import SqlAlchemyCommerceStore
from .team_repository import SqlAlchemyTeamStore
from .tyre_repository import SqlAlchemyTyreStore
from .conversation_repository import _conversation, _turn
from .command_repository import SqlAlchemyCommandStore
from .email_repository import EmailAccountRecord, EmailDraftRecord, EmailMessageRecord, EmailOAuthRequestRecord
from .email_repository import _account as _email_account
from .email_repository import _draft as _email_draft
from .email_repository import _message as _email_message
from .home_repository import SqlAlchemyHomeStore
from .learning_repository import SqlAlchemyLearningStore
from .meetings_repository import SqlAlchemyConnectionStore, SqlAlchemyMeetingStore, _meeting, _segment as _meeting_segment
from .memory_repository import SqlAlchemyMemoryRepository
from .models import (
    AgentRunRecord,
    AutonomousActionRecord,
    ApprovalApproverRow,
    ApprovalRecord,
    ConversationRecord,
    ConversationTurnRecord,
    MeetingConnectorAccountRecord,
    MeetingRecord,
    MeetingTranscriptSegmentRecord,
    MemoryRecord,
    NotificationPreferenceRecord,
    PushDeviceRecord,
    TaskRecord,
    UserPreferenceRecord,
    VoiceSessionRecord,
    VoiceTranscriptSegmentRecord,
    WorkspaceFileRecord,
)
from .repository import SqlAlchemyRepository
from .voice_repository import _segment, _session
from .whatsapp_repository import WhatsAppMessageRecord
from .whatsapp_repository import _message as _whatsapp_message
from .workspace_settings_repository import SqlAlchemyNotificationPreferenceStore, _file


class SqlAlchemyPrivacyStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.repository = SqlAlchemyRepository(session, created_by_user_id=context.user_id)

    def _in_workspace(self, model):  # type: ignore[no-untyped-def]
        return (model.tenant_id == self.context.tenant_id, model.workspace_id == self.context.workspace_id)

    def _mine(self, model):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(model), model.user_id == self.context.user_id)

    def _count(self, model, *where) -> int:  # type: ignore[no-untyped-def]
        return int(self.session.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    # -- reads ---------------------------------------------------------------------------

    def membership(self) -> WorkspaceMembership | None:
        return self.repository.get_membership(self.context)

    def conversations(self) -> list[tuple[Conversation, list[ConversationTurn]]]:
        conversations = self.session.scalars(
            select(ConversationRecord)
            .where(*self._mine(ConversationRecord))
            .order_by(ConversationRecord.created_at, ConversationRecord.id)
        ).all()
        turns: dict[str, list[ConversationTurn]] = {record.id: [] for record in conversations}
        for record in self.session.scalars(
            select(ConversationTurnRecord)
            .where(*self._mine(ConversationTurnRecord))
            .order_by(ConversationTurnRecord.conversation_id, ConversationTurnRecord.seq)
        ):
            if record.conversation_id in turns:
                turns[record.conversation_id].append(_turn(record))
        return [(_conversation(record), turns[record.id]) for record in conversations]

    def _memories(self, *where) -> list[MemoryNote]:  # type: ignore[no-untyped-def]
        records = self.session.scalars(
            select(MemoryRecord)
            .where(*self._in_workspace(MemoryRecord), *where)
            .order_by(MemoryRecord.created_at, MemoryRecord.id)
        ).all()
        return [SqlAlchemyMemoryRepository._from_record(record) for record in records]

    def _private_memory_filter(self):  # type: ignore[no-untyped-def]
        return (MemoryRecord.owner_user_id == self.context.user_id,)

    def _shared_memory_filter(self):  # type: ignore[no-untyped-def]
        return (
            MemoryRecord.owner_user_id.is_(None),
            MemoryRecord.provenance["created_by_user_id"].astext == self.context.user_id,
        )

    def private_memories(self) -> list[MemoryNote]:
        return self._memories(*self._private_memory_filter())

    def shared_memories_created(self) -> list[MemoryNote]:
        return self._memories(*self._shared_memory_filter())

    def voice_sessions(self) -> list[tuple[VoiceSession, list[TranscriptSegment]]]:
        sessions = [
            _session(record)
            for record in self.session.scalars(
                select(VoiceSessionRecord)
                .where(*self._mine(VoiceSessionRecord))
                .order_by(VoiceSessionRecord.created_at, VoiceSessionRecord.id)
            )
        ]
        segments: dict[str, list[TranscriptSegment]] = {session.id: [] for session in sessions}
        for record in self.session.scalars(
            select(VoiceTranscriptSegmentRecord)
            .where(*self._mine(VoiceTranscriptSegmentRecord))
            .order_by(
                VoiceTranscriptSegmentRecord.session_id,
                VoiceTranscriptSegmentRecord.client_sequence,
                VoiceTranscriptSegmentRecord.created_at,
            )
        ):
            if record.session_id in segments:
                segments[record.session_id].append(_segment(record))
        # An expired 30-day transcript is unreadable even before the purge removes it.
        return [(session, [] if transcript_expired(session) else segments[session.id]) for session in sessions]

    def _task_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(TaskRecord), TaskRecord.created_by_user_id == self.context.user_id)

    def tasks_created(self) -> list[tuple[Task, list[AgentRun]]]:
        tasks = self.session.scalars(
            select(TaskRecord).where(*self._task_filter()).order_by(TaskRecord.created_at, TaskRecord.id)
        ).all()
        runs: dict[str, list[AgentRun]] = {task.id: [] for task in tasks}
        if tasks:
            for record in self.session.scalars(
                select(AgentRunRecord)
                .where(*self._in_workspace(AgentRunRecord), AgentRunRecord.task_id.in_(list(runs)))
                .order_by(AgentRunRecord.created_at, AgentRunRecord.id)
            ):
                runs[record.task_id].append(self.repository._run_from_record(record))
        return [(SqlAlchemyRepository._task_from_record(task), runs[task.id]) for task in tasks]

    def _approval_filter(self):  # type: ignore[no-untyped-def]
        approved_by_me = (
            select(ApprovalApproverRow.approval_id)
            .where(
                *self._in_workspace(ApprovalApproverRow),
                ApprovalApproverRow.user_id == self.context.user_id,
            )
            .scalar_subquery()
        )
        return (
            *self._in_workspace(ApprovalRecord),
            or_(ApprovalRecord.decided_by == self.context.user_id, ApprovalRecord.id.in_(approved_by_me)),
        )

    def approvals_decided(self) -> list[tuple[Approval, ApprovalApproverRecord | None]]:
        records = self.session.scalars(
            select(ApprovalRecord).where(*self._approval_filter()).order_by(ApprovalRecord.created_at, ApprovalRecord.id)
        ).all()
        approvals = [SqlAlchemyRepository._approval_from_record(record) for record in records]
        approvers = self.repository.list_approval_approvers([item.id for item in approvals], self.context)
        return _decided_by(self.context.user_id, approvals, approvers)

    def notification_preferences(self) -> NotificationPreferences | None:
        return SqlAlchemyNotificationPreferenceStore(self.session).get(self.context)

    def _user_preference_filter(self):  # type: ignore[no-untyped-def]
        # Tenant-wide row (no workspace), under forced RLS on tenant and user (0025).
        return (
            UserPreferenceRecord.tenant_id == self.context.tenant_id,
            UserPreferenceRecord.user_id == self.context.user_id,
        )

    def user_preferences(self) -> UserPreferences | None:
        record = self.session.scalar(select(UserPreferenceRecord).where(*self._user_preference_filter()))
        if record is None:
            return None
        return UserPreferences(remember_conversations=record.remember_conversations, updated_at=record.updated_at)

    def _push_device_filter(self):  # type: ignore[no-untyped-def]
        # Under forced RLS on tenant, workspace and user (0027_push_devices).
        return (*self._in_workspace(PushDeviceRecord), PushDeviceRecord.user_id == self.context.user_id)

    def push_devices(self) -> list[PushDevice]:
        from anum_api.db.push_repository import SqlAlchemyPushDeviceStore

        return [device.view() for device in SqlAlchemyPushDeviceStore(self.session).list(self.context)]
    def home(self) -> SqlAlchemyHomeStore:
        """The caller's Home rows, in this same transaction and RLS context."""
        return SqlAlchemyHomeStore(self.session)

    def learning(self) -> SqlAlchemyLearningStore:
        """The caller's learning rows, in this same transaction and RLS context."""
        return SqlAlchemyLearningStore(self.session, self.context)

    def commands(self) -> SqlAlchemyCommandStore:
        """The caller's commands and quick commands, in this same transaction."""
        return SqlAlchemyCommandStore(self.session, self.context)
    def library(self) -> LibraryPrivacy:
        """Favorites, private collections and private reports, in this same transaction."""
        return sql_library(self.session, self.context)

    # -- email, WhatsApp, autonomy (migrations 0033 to 0035) ----------------------------

    def _mailbox_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(EmailAccountRecord), EmailAccountRecord.connected_by == self.context.user_id)

    def _private_mailbox_ids(self):  # type: ignore[no-untyped-def]
        return (
            select(EmailAccountRecord.id)
            .where(*self._mailbox_filter(), EmailAccountRecord.visibility == "private")
            .scalar_subquery()
        )

    def email_accounts_connected(self) -> list[EmailAccount]:
        rows = self.session.scalars(
            select(EmailAccountRecord)
            .where(*self._mailbox_filter())
            .order_by(EmailAccountRecord.created_at, EmailAccountRecord.id)
        ).all()
        return [_email_account(row) for row in rows]

    def _draft_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(EmailDraftRecord), EmailDraftRecord.created_by == self.context.user_id)

    def email_drafts_written(self) -> list[EmailDraft]:
        rows = self.session.scalars(
            select(EmailDraftRecord).where(*self._draft_filter()).order_by(EmailDraftRecord.created_at, EmailDraftRecord.id)
        ).all()
        return [_email_draft(row) for row in rows]

    def _private_message_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(EmailMessageRecord), EmailMessageRecord.account_id.in_(self._private_mailbox_ids()))

    def email_private_messages(self) -> list[EmailMessage]:
        rows = self.session.scalars(
            select(EmailMessageRecord)
            .where(*self._private_message_filter())
            .order_by(EmailMessageRecord.received_at, EmailMessageRecord.id)
        ).all()
        return [_email_message(row) for row in rows]

    def _whatsapp_filter(self):  # type: ignore[no-untyped-def]
        return (
            *self._in_workspace(WhatsAppMessageRecord),
            WhatsAppMessageRecord.direction == "outbound",
            WhatsAppMessageRecord.sent_by == self.context.user_id,
        )

    def whatsapp_messages_sent(self) -> list[WhatsAppMessage]:
        rows = self.session.scalars(
            select(WhatsAppMessageRecord)
            .where(*self._whatsapp_filter())
            .order_by(WhatsAppMessageRecord.timestamp, WhatsAppMessageRecord.id)
        ).all()
        return [_whatsapp_message(row) for row in rows]

    def _autonomy_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(AutonomousActionRecord), AutonomousActionRecord.requested_by == self.context.user_id)

    def autonomy_actions_requested(self) -> list[AutonomousAction]:
        rows = self.session.scalars(
            select(AutonomousActionRecord)
            .where(*self._autonomy_filter())
            .order_by(AutonomousActionRecord.created_at, AutonomousActionRecord.id)
        ).all()
        return [SqlAlchemyAutonomyStore._action(row) for row in rows]

    def _module_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        email = email_counts(
            self.email_accounts_connected(),
            self.email_drafts_written(),
            self._count(EmailMessageRecord, *self._private_message_filter()),
        )
        private, shared = module_counts(
            email,
            self._count(WhatsAppMessageRecord, *self._whatsapp_filter()),
            self._count(AutonomousActionRecord, *self._autonomy_filter()),
        )
        other_private, other_shared = other_module_counts(self, self.context)
        return {**private, **other_private}, {**shared, **other_shared}

    # -- team work, tyre planning, commerce (migrations 0037 to 0039) --------------------

    def team(self) -> SqlAlchemyTeamStore:
        return SqlAlchemyTeamStore(self.session)

    def tyre(self) -> SqlAlchemyTyreStore:
        return SqlAlchemyTyreStore(self.session)

    def commerce(self) -> SqlAlchemyCommerceStore:
        return SqlAlchemyCommerceStore(self.session, self.context)

    def delete_team_private(self) -> None:
        """The caller's notifications (RLS admits only the recipient) and member profile."""
        params = self._scope_params()
        self.session.execute(
            text(
                "delete from user_notifications where tenant_id = :tenant_id "
                "and workspace_id = :workspace_id and user_id = :user_id"
            ),
            params,
        )
        self.session.execute(
            text(
                "delete from team_member_profiles where tenant_id = :tenant_id "
                "and workspace_id = :workspace_id and user_id = :user_id"
            ),
            params,
        )

    def _file_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(WorkspaceFileRecord), WorkspaceFileRecord.created_by == self.context.user_id)

    def files_uploaded(self) -> list[FileRecord]:
        rows = self.session.scalars(
            select(WorkspaceFileRecord)
            .where(*self._file_filter())
            .order_by(WorkspaceFileRecord.created_at, WorkspaceFileRecord.id)
        ).all()
        return [_file(row) for row in rows]

    # -- meetings (migration 0026) -------------------------------------------------------

    def _meeting_filter(self):  # type: ignore[no-untyped-def]
        return (*self._in_workspace(MeetingRecord), MeetingRecord.created_by == self.context.user_id)

    def _my_meeting_ids(self):  # type: ignore[no-untyped-def]
        return select(MeetingRecord.id).where(*self._meeting_filter())

    def _meeting_segment_filter(self):  # type: ignore[no-untyped-def]
        return (
            *self._in_workspace(MeetingTranscriptSegmentRecord),
            MeetingTranscriptSegmentRecord.meeting_id.in_(self._my_meeting_ids()),
        )

    def meetings_created(self) -> list[tuple[Meeting, list[MeetingTranscriptSegment]]]:
        rows = self.session.scalars(
            select(MeetingRecord).where(*self._meeting_filter()).order_by(MeetingRecord.created_at, MeetingRecord.id)
        ).all()
        found = []
        for row in rows:
            segments = self.session.scalars(
                select(MeetingTranscriptSegmentRecord)
                .where(*self._in_workspace(MeetingTranscriptSegmentRecord), MeetingTranscriptSegmentRecord.meeting_id == row.id)
                .order_by(MeetingTranscriptSegmentRecord.seq)
            ).all()
            found.append((_meeting(row), [_meeting_segment(segment) for segment in segments]))
        return found

    def _recording_filter(self):  # type: ignore[no-untyped-def]
        recording_ids = select(MeetingRecord.recording_file_id).where(
            *self._meeting_filter(), MeetingRecord.recording_file_id.is_not(None)
        )
        return (*self._file_filter(), WorkspaceFileRecord.id.in_(recording_ids))

    def meeting_recordings(self) -> list[FileRecord]:
        rows = self.session.scalars(
            select(WorkspaceFileRecord).where(*self._recording_filter()).order_by(WorkspaceFileRecord.created_at)
        ).all()
        return [_file(row) for row in rows]

    def meeting_connections(self) -> list[StoredConnection]:
        return sorted(SqlAlchemyConnectionStore(self.session).list_connections(self.context), key=lambda item: item.provider.value)

    # -- counts --------------------------------------------------------------------------

    def _private_source_count(self) -> int:
        return int(
            self.session.execute(
                text(
                    "select count(*) from retrieval_sources where tenant_id = :tenant_id "
                    "and workspace_id = :workspace_id and owner_user_id = :user_id"
                ),
                self._scope_params(),
            ).scalar_one()
        )

    def _scope_params(self) -> dict[str, str]:
        return {
            "tenant_id": self.context.tenant_id,
            "workspace_id": self.context.workspace_id,
            "user_id": self.context.user_id,
        }

    def counts(self) -> tuple[PrivateCounts, SharedCounts]:
        module_private, module_shared = self._module_counts()
        private = PrivateCounts(
            conversations=self._count(ConversationRecord, *self._mine(ConversationRecord)),
            conversation_turns=self._count(ConversationTurnRecord, *self._mine(ConversationTurnRecord)),
            private_memories=self._count(MemoryRecord, *self._in_workspace(MemoryRecord), *self._private_memory_filter()),
            retrieval_index_entries=self._private_source_count(),
            voice_sessions=self._count(VoiceSessionRecord, *self._mine(VoiceSessionRecord)),
            voice_transcript_segments=self._count(VoiceTranscriptSegmentRecord, *self._mine(VoiceTranscriptSegmentRecord)),
            notification_preferences=self._count(NotificationPreferenceRecord, *self._mine(NotificationPreferenceRecord)),
            user_preferences=self._count(UserPreferenceRecord, *self._user_preference_filter()),
            push_devices=self._count(PushDeviceRecord, *self._push_device_filter()),
            **learning_counts(self.learning()),
            meetings=self._count(MeetingRecord, *self._meeting_filter()),
            meeting_transcript_segments=self._count(MeetingTranscriptSegmentRecord, *self._meeting_segment_filter()),
            meeting_recordings=self._count(WorkspaceFileRecord, *self._recording_filter()),
            meeting_connections=self._count(MeetingConnectorAccountRecord, *self._mine(MeetingConnectorAccountRecord)),
            **command_privacy_counts(self.commands()),
            **self.library().counts(),
            **module_private,
            home_items=home_counts(self.home(), self.context),
        )
        shared = SharedCounts(
            tasks_created=self._count(TaskRecord, *self._task_filter()),
            approvals_decided=self._count(ApprovalRecord, *self._approval_filter()),
            shared_memories_created=self._count(MemoryRecord, *self._in_workspace(MemoryRecord), *self._shared_memory_filter()),
            files_uploaded=self._count(WorkspaceFileRecord, *self._file_filter()),
            **module_shared,
        )
        return private, shared

    # -- delete --------------------------------------------------------------------------

    def delete_private(self) -> PrivateCounts:
        """Delete the caller's user-private rows; returns what was removed."""
        deleted, _ = self.counts()
        # Index rows of private memories first (their chunks cascade); migration 0015's
        # trigger would also remove them with the memory.
        self.session.execute(
            text(
                "delete from retrieval_sources where tenant_id = :tenant_id "
                "and workspace_id = :workspace_id and owner_user_id = :user_id"
            ),
            self._scope_params(),
        )
        self.session.execute(delete(MemoryRecord).where(*self._in_workspace(MemoryRecord), *self._private_memory_filter()))
        # Turns and transcript segments cascade from their conversation or session.
        self.session.execute(delete(ConversationRecord).where(*self._mine(ConversationRecord)))
        self.session.execute(delete(VoiceSessionRecord).where(*self._mine(VoiceSessionRecord)))
        self.session.execute(delete(NotificationPreferenceRecord).where(*self._mine(NotificationPreferenceRecord)))
        self.session.execute(delete(UserPreferenceRecord).where(*self._user_preference_filter()))
        self.session.execute(delete(PushDeviceRecord).where(*self._push_device_filter()))
        self.learning().delete_all()
        # Meetings the caller imported: recording file rows first (the route deletes the
        # bytes after commit), then transcript lines and the meetings themselves.
        self.session.execute(delete(WorkspaceFileRecord).where(*self._recording_filter()))
        self.session.execute(delete(MeetingTranscriptSegmentRecord).where(*self._meeting_segment_filter()))
        self.session.execute(delete(MeetingRecord).where(*self._meeting_filter()))
        self.session.execute(delete(MeetingConnectorAccountRecord).where(*self._mine(MeetingConnectorAccountRecord)))
        self.commands().delete_all()
        # Before the private memories go: a private collection's notes are among them.
        self.library().delete_all()
        # The caller's unsent drafts anywhere, then their private mailboxes (synced message
        # metadata and every draft in them cascade), then their pending connections.
        self.session.execute(
            delete(EmailDraftRecord).where(
                *self._draft_filter(), EmailDraftRecord.status.in_(sorted(UNSENT_DRAFT_STATUSES))
            )
        )
        self.session.execute(
            delete(EmailAccountRecord).where(*self._mailbox_filter(), EmailAccountRecord.visibility == "private")
        )
        self.session.execute(
            delete(EmailOAuthRequestRecord).where(
                *self._in_workspace(EmailOAuthRequestRecord), EmailOAuthRequestRecord.user_id == self.context.user_id
            )
        )
        self.delete_team_private()
        self.home().delete_all(self.context)
        self.session.flush()
        return deleted

    def record_audit(self, record: AuditRecord) -> None:
        self.repository.record_audit(record)
