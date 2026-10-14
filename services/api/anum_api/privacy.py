"""Privacy rights: what ANUM stores about the caller, "Download my data" and "Delete my data".

Everything here acts on the caller's own data in the request's tenant and workspace,
never anyone else's (docs/privacy.md):

- ``GET /api/v1/privacy`` summarises what is stored about the caller (counts only, no
  content), the retention periods this deployment applies, the model provider their
  messages may be sent to, whether the Assistant remembers from and recalls into the
  caller's chats (``model_provider.conversation_memory``, the same state
  ``GET /api/v1/me/preferences`` returns) and the privacy policy link.
- ``GET /api/v1/privacy/export`` returns a JSON document (``anum.privacy-export/v1``)
  with the caller's identity in the workspace, conversations and turns, private
  memories and the shared memories they created, voice sessions and transcripts, tasks
  they created with their runs, approvals they decided, notification preferences, the
  phones they registered for push (platform, label and dates; never the push token) and
  the metadata of files they uploaded (file bytes are linked, not embedded). It is built
  in memory up to ``ANUM_PRIVACY_EXPORT_MAX_BYTES`` and refused with 413 beyond that,
  never truncated.
- ``POST /api/v1/privacy/delete`` with ``{"confirm": "DELETE MY DATA"}`` deletes the
  caller's user-private data: conversations (and turns), private memories and their
  retrieval index rows, voice sessions (and transcripts), notification preferences,
  the phones registered for push in this workspace, the
  meetings they imported (transcripts, notes and recording files: recordings are
  personal data, docs/meetings.md#privacy) and their Zoom/Teams connections.
  Workspace-shared records the caller created (tasks, runs, approvals, shared memories,
  files) are kept and counted; the sign-in account lives in the identity provider and
  is not deleted by ANUM.

With PostgreSQL every read and delete runs in one transaction whose RLS context names
the caller's tenant, workspace *and* user (``anum.user_id``), so the policies of the
user-private tables admit only the caller's rows; statements also name the scope
explicitly. Nothing bypasses RLS and no cross-tenant role is used.

Both actions are audited (``privacy.export``, ``privacy.delete``) with counts only,
never content.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .autonomy import AutonomousAction
from .email_inbox import EmailAccount, EmailDraft, EmailMessage
from .conversations import Conversation, ConversationTurn
from .dependencies import require_permission, tenant_context
from .files import FileRecord
from .commands import CommandStore
from .commands import privacy_counts as command_privacy_counts
from .commands import privacy_export as command_privacy_export
from .home import HomeStore, home_counts, home_export
from .learning_store import LearningStore
from .localization import PRIVACY_NOTES_AR, Language, localize_message, request_language
from .memory import MemoryNote
from .onboarding import NotificationPreferences
from .push import PushDevice
from .request_context import current_correlation_id
from .schemas import AgentRun, Approval, ApprovalApproverRecord, Task, TenantContext, WorkspaceMembership, new_id, utc_now
from .settings import settings
from .library_privacy import LibraryPrivacy, memory_library
from .user_preferences import ConversationMemoryState, UserPreferences, conversation_memory_state
from .meeting_connectors import StoredConnection
from .meetings import Meeting
from .meetings import TranscriptSegment as MeetingTranscriptSegment
from .voice import TRANSCRIPT_RETENTION_DAYS, TranscriptRetention, TranscriptSegment, VoiceSession
from .whatsapp_store import Message as WhatsAppMessage

logger = logging.getLogger("anum.privacy")

EXPORT_FORMAT = "anum.privacy-export/v1"
DELETE_CONFIRMATION = "DELETE MY DATA"
ACCOUNT_NOTE = (
    "Your sign-in account is managed by your organization's identity provider, not by "
    "ANUM. To close it, ask your workspace owner or organization administrator."
)
SHARED_NOTE = (
    "Tasks, runs, approvals, shared memories, files, workspace mailboxes you connected, "
    "emails you sent or sent for approval, WhatsApp messages you sent and actions ANUM took "
    "for you belong to the workspace, so they are kept. Ask a workspace owner to remove them."
)
# Drafts that never left ANUM: "Delete my data" removes them. Sent ones (or ones waiting for
# approval) are part of the workspace's correspondence and are kept.
UNSENT_DRAFT_STATUSES = frozenset({"draft", "failed", "discarded"})
# Never exported: credentials and the provider's sync state.
EMAIL_ACCOUNT_PRIVATE_FIELDS = {"access_token_enc", "refresh_token_enc", "sync_cursor", "sync_lease_until", "access_expires_at"}
# The undo token is a capability; undo arguments belong to the tool call, not the user.
AUTONOMY_ACTION_PRIVATE_FIELDS = {"undo_token", "undo_arguments"}


# --------------------------------------------------------------------------- models


class PrivateCounts(BaseModel):
    """User-private data: what "Delete my data" removes."""

    conversations: int = 0
    conversation_turns: int = 0
    private_memories: int = 0
    retrieval_index_entries: int = 0
    voice_sessions: int = 0
    voice_transcript_segments: int = 0
    notification_preferences: int = 0
    # Tenant-wide, not per workspace (migration 0025_user_preferences).
    user_preferences: int = 0
    # Phones registered for push in this workspace (migration 0027_push_devices).
    push_devices: int = 0
    # What ANUM learned (migration 0036_learning, docs/learning.md): feedback signals, and
    # the style profile, learned preferences, routines, notes and suggestions.
    learning_signals: int = 0
    learned_items: int = 0
    # Meetings the caller imported, with transcripts and recordings (migration 0043_meetings).
    meetings: int = 0
    meeting_transcript_segments: int = 0
    meeting_recordings: int = 0
    meeting_connections: int = 0
    # ANUM Command history and saved quick commands (migration 0044_commands,
    # docs/command.md); the tasks a command created stay with the workspace.
    commands: int = 0
    quick_commands: int = 0
    # Favorites, and collections and reports private to the caller (migration 0042_library).
    favorites: int = 0
    private_collections: int = 0
    private_reports: int = 0
    # Email (migration 0035, docs/email.md): private mailboxes the caller connected, with the
    # message metadata synced from them, and the caller's drafts that never left ANUM (plus
    # every draft in those private mailboxes). Message bodies are never stored.
    email_mailboxes: int = 0
    email_messages: int = 0
    email_drafts: int = 0
    # Team work (migration 0039_team_work, docs/team-work.md): the caller's in-app
    # notifications and their member profile (name, email, title, capacity) here.
    team_notifications: int = 0
    team_profile: int = 0
    # Home (migration 0046_home, docs/home.md): pinned apps, dismissed or snoozed insights,
    # and the greeting name (tenant-wide).
    home_items: int = 0
    # Custom agents (migration 0041_agent_profiles, docs/agents.md): the caller's private
    # agents and those agents' memories.
    private_agents: int = 0
    private_agent_memories: int = 0
    # Personal finance (migration 0056_finance_ledger, docs/finance.md): every row of the
    # caller's ledger (settings, accounts, transactions, imports, bills, debts, goals,
    # holdings). It is private to them, so all of it is exported and deleted.
    finance_records: int = 0

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class SharedCounts(BaseModel):
    """Workspace-shared records the caller created or decided: kept by a deletion."""

    tasks_created: int = 0
    approvals_decided: int = 0
    shared_memories_created: int = 0
    files_uploaded: int = 0
    # Workspace mailboxes the caller connected, and their emails sent or waiting for approval.
    email_mailboxes_connected: int = 0
    email_drafts_sent: int = 0
    # WhatsApp messages the caller sent or asked to send (docs/whatsapp.md).
    whatsapp_messages_sent: int = 0
    # Actions ANUM took on its own for the caller's requests (docs/autonomy.md).
    autonomy_actions_requested: int = 0
    # Team work items the caller created or that are assigned to them (docs/team-work.md).
    work_items_created: int = 0
    work_items_assigned: int = 0
    # Tyre planning (docs/tyre-planning.md): the source connection and reports they created.
    tyre_connections_created: int = 0
    tyre_reports_created: int = 0
    # Commerce (docs/commerce.md): sales-channel connectors they connected.
    commerce_connectors_created: int = 0
    # Marketing (docs/marketing.md): social accounts they connected, campaigns they created.
    marketing_accounts_connected: int = 0
    marketing_campaigns_created: int = 0
    # Calendar events they created (docs/reports-calendar.md) and automation triggers
    # they added (docs/automation.md#triggers).
    calendar_events_created: int = 0
    automation_triggers_created: int = 0
    # Agents the caller owns and shared with the workspace, with their memories.
    shared_agents_owned: int = 0
    shared_agent_memories: int = 0


class EmailCounts(BaseModel):
    """What the caller's email data splits into: deleted (private) and kept (shared)."""

    mailboxes: int = 0
    messages: int = 0
    drafts: int = 0
    mailboxes_connected: int = 0
    drafts_sent: int = 0


def email_counts(accounts: list[Any], drafts: list[Any], private_messages: int) -> EmailCounts:
    """Split the caller's mailboxes (``connected_by``) and drafts (``created_by``)."""
    private_ids = {account.id for account in accounts if str(account.visibility) == "private"}
    deleted = [d for d in drafts if d.account_id in private_ids or str(d.status) in UNSENT_DRAFT_STATUSES]
    return EmailCounts(
        mailboxes=len(private_ids),
        messages=private_messages,
        drafts=len(deleted),
        mailboxes_connected=len(accounts) - len(private_ids),
        drafts_sent=len(drafts) - len(deleted),
    )


def module_counts(email: EmailCounts, whatsapp_sent: int, autonomy_requested: int) -> tuple[dict[str, int], dict[str, int]]:
    return (
        {"email_mailboxes": email.mailboxes, "email_messages": email.messages, "email_drafts": email.drafts},
        {
            "email_mailboxes_connected": email.mailboxes_connected,
            "email_drafts_sent": email.drafts_sent,
            "whatsapp_messages_sent": whatsapp_sent,
            "autonomy_actions_requested": autonomy_requested,
        },
    )


_PAGE = 200


def _team_items(team: Any, context: TenantContext, flt: Any) -> list[Any]:
    found: list[Any] = []
    offset = 0
    while True:
        page, total = team.query_items(context, flt, _PAGE, offset)
        found += page
        offset += len(page)
        if not page or offset >= total:
            return found


def team_data(store: "PrivacyStore", context: TenantContext) -> dict[str, Any]:
    """The caller's team work (docs/team-work.md): profile, notifications, items, comments."""
    from .team_models import ActorType, AssigneeType
    from .team_store import WorkFilter

    team = store.team()
    notifications: list[Any] = []
    before: str | None = None
    while True:
        page = team.list_notifications(context, limit=_PAGE, before=before, unread_only=False)
        notifications += page
        if len(page) < _PAGE:
            break
        before = page[-1].id
    created = _team_items(team, context, WorkFilter(created_by=context.user_id, sort="updated"))
    assigned = _team_items(
        team, context, WorkFilter(assignee_type=AssigneeType.USER, assignee_id=context.user_id, sort="updated")
    )
    seen = {item.id: item for item in [*created, *assigned]}
    comments = [
        {"work_item_id": item_id, **_dump(comment)}
        for item_id in seen
        for comment in team.list_comments(context, item_id)
        if comment.author_type == ActorType.USER and comment.author_id == context.user_id
    ]
    return {
        "profile": team.list_profiles(context).get(context.user_id),
        "notifications": notifications,
        "work_items_created": created,
        "work_items_assigned": assigned,
        "comments_written": comments,
    }


def tyre_data(store: "PrivacyStore", context: TenantContext) -> dict[str, Any]:
    """The tyre planning connection and reports the caller created (docs/tyre-planning.md)."""
    tyre = store.tyre()
    connection = tyre.get_connection(context)
    reports: list[Any] = []
    offset = 0
    while True:
        page, total = tyre.list_reports(context, kind=None, limit=_PAGE, offset=offset)
        reports += [report for report in page if report.created_by == context.user_id]
        offset += len(page)
        if not page or offset >= total:
            break
    return {
        "connection": connection if connection is not None and connection.created_by == context.user_id else None,
        "reports": reports,
    }


def commerce_connectors_created(store: "PrivacyStore", context: TenantContext) -> list[Any]:
    return [item for item in store.commerce().list_connectors(context) if item.created_by == context.user_id]


def workspace_records_created(context: TenantContext) -> dict[str, list[Any]]:
    """Marketing, calendar and automation trigger records the caller created (each module's own
    unit of work in the caller's tenant and workspace; workspace records, kept by a deletion)."""
    from datetime import timezone

    from .automation import current_engine
    from .calendar_api import open_calendar_store
    from .marketing.store import open_marketing_store

    user = context.user_id
    with open_marketing_store(context) as marketing:
        accounts = [item for item in marketing.list_accounts(context) if item.connected_by == user]
        campaigns = [
            item
            for item in marketing.list_campaigns(context, status=None, text=None, limit=10_000, before=None)
            if item.created_by == user
        ]
    with open_calendar_store(context) as calendar:
        events = [
            item
            for item in calendar.between(
                datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(2200, 1, 1, tzinfo=timezone.utc),
                include_cancelled=True, limit=10_000,
            )
            if item.created_by == user
        ]
    triggers = [item for item in current_engine().list_triggers(context) if item.created_by == user]
    return {
        "marketing_accounts_connected": accounts,
        "marketing_campaigns_created": campaigns,
        "calendar_events_created": events,
        "automation_triggers_created": triggers,
    }


def agents_owned(store: "PrivacyStore", context: TenantContext) -> list[tuple[Any, list[Any]]]:
    """The agents the caller owns in this workspace (private and shared), each with its memories."""
    agents = store.agents()
    owned = [agent for agent in agents.list(context) if agent.owner_user_id == context.user_id]
    owned.sort(key=lambda agent: (agent.created_at, agent.id))
    return [(agent, agents.list_memories(context, agent.id)) for agent in owned]


def _is_private_agent(agent: Any) -> bool:
    return str(agent.sharing) != "workspace"


def delete_private_agents(store: "PrivacyStore", context: TenantContext) -> None:
    """A private agent is the caller's personal data: it goes with its memories (cascade).
    An agent shared with the workspace is a workspace record other members use: it stays."""
    agents = store.agents()
    for agent, _memories in agents_owned(store, context):
        if _is_private_agent(agent):
            agents.delete(context, agent.id)


def other_module_counts(store: "PrivacyStore", context: TenantContext) -> tuple[dict[str, int], dict[str, int]]:
    team = team_data(store, context)
    tyre = tyre_data(store, context)
    created = workspace_records_created(context)
    owned = agents_owned(store, context)
    private_agents = [(agent, memories) for agent, memories in owned if _is_private_agent(agent)]
    shared_agents = [(agent, memories) for agent, memories in owned if not _is_private_agent(agent)]
    return (
        {
            "team_notifications": len(team["notifications"]),
            "team_profile": int(team["profile"] is not None),
            "private_agents": len(private_agents),
            "private_agent_memories": sum(len(memories) for _, memories in private_agents),
        },
        {
            "work_items_created": len(team["work_items_created"]),
            "work_items_assigned": len(team["work_items_assigned"]),
            "tyre_connections_created": int(tyre["connection"] is not None),
            "tyre_reports_created": len(tyre["reports"]),
            "commerce_connectors_created": len(commerce_connectors_created(store, context)),
            **{name: len(items) for name, items in created.items()},
            "shared_agents_owned": len(shared_agents),
            "shared_agent_memories": sum(len(memories) for _, memories in shared_agents),
        },
    )


class RetentionInfo(BaseModel):
    # 0: kept until the user deletes them.
    conversation_days: int
    voice_transcript_days: int = TRANSCRIPT_RETENTION_DAYS
    voice_transcript_choices: list[str] = Field(default_factory=lambda: [item.value for item in TranscriptRetention])
    private_memories: str = "Kept until you delete them (Memory, or Delete my data)."
    task_memories: str = "Kept with their task, or until the expiry set when they were saved."
    audit_records: str = "Kept as the workspace's security record; they hold ids and counts, not your content."


class ModelProviderInfo(BaseModel):
    # The deployment's provider (ANUM_MODEL_PROVIDER) and this workspace's override.
    deployment: str
    workspace: str | None = None
    # The deployment-wide switch (ANUM_CONVERSATION_MEMORY_ENABLED) only; kept for older
    # clients. What applies to this caller is ``conversation_memory``.
    conversation_memory_enabled: bool
    conversation_memory: ConversationMemoryState = Field(
        description=(
            "Whether a post-reply model call remembers facts from the caller's chats, and if not, why: "
            "on; off_by_user; off_on_server; off_for_organization; not_for_role (as GET /api/v1/me/preferences)"
        )
    )


class PrivacySummary(BaseModel):
    tenant_id: str
    workspace_id: str
    user_id: str
    role: str | None
    private: PrivateCounts
    shared: SharedCounts
    retention: RetentionInfo
    model_provider: ModelProviderInfo
    privacy_policy_url: str | None
    delete_confirmation: str = DELETE_CONFIRMATION
    export_max_bytes: int
    account_note: str = ACCOUNT_NOTE
    shared_note: str = SHARED_NOTE


class DeleteMyData(BaseModel):
    confirm: str = Field(max_length=64)


class DeleteResult(BaseModel):
    deleted: PrivateCounts
    kept: SharedCounts
    account_note: str = ACCOUNT_NOTE
    shared_note: str = SHARED_NOTE
    # What still needs the caller, e.g. a mailbox provider ANUM could not revoke access at.
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- stores


class PrivacyStore(Protocol):
    """The caller's data in one tenant and workspace, read and deleted in one unit of work."""

    def membership(self) -> WorkspaceMembership | None: ...
    def conversations(self) -> list[tuple[Conversation, list[ConversationTurn]]]: ...
    def private_memories(self) -> list[MemoryNote]: ...
    def shared_memories_created(self) -> list[MemoryNote]: ...
    def voice_sessions(self) -> list[tuple[VoiceSession, list[TranscriptSegment]]]: ...
    def tasks_created(self) -> list[tuple[Task, list[AgentRun]]]: ...
    def approvals_decided(self) -> list[tuple[Approval, ApprovalApproverRecord | None]]: ...
    def notification_preferences(self) -> NotificationPreferences | None: ...
    def user_preferences(self) -> UserPreferences | None: ...
    def push_devices(self) -> list[PushDevice]: ...
    def files_uploaded(self) -> list[FileRecord]: ...
    def learning(self) -> LearningStore: ...
    def meetings_created(self) -> list[tuple[Meeting, list[MeetingTranscriptSegment]]]: ...
    def meeting_recordings(self) -> list[FileRecord]: ...
    def meeting_connections(self) -> list[StoredConnection]: ...
    def commands(self) -> CommandStore: ...
    def library(self) -> LibraryPrivacy: ...
    def finance(self) -> Any: ...
    # Email, WhatsApp and autonomy rows the caller connected, wrote, sent or requested.
    def email_accounts_connected(self) -> list[EmailAccount]: ...
    def email_drafts_written(self) -> list[EmailDraft]: ...
    def email_private_messages(self) -> list[EmailMessage]: ...
    def whatsapp_messages_sent(self) -> list[WhatsAppMessage]: ...
    def autonomy_actions_requested(self) -> list[AutonomousAction]: ...
    # Team work, tyre planning and commerce stores in this same unit of work.
    def team(self) -> Any: ...
    def tyre(self) -> Any: ...
    def commerce(self) -> Any: ...
    def delete_team_private(self) -> None: ...
    def home(self) -> HomeStore: ...
    def agents(self) -> Any: ...
    def counts(self) -> tuple[PrivateCounts, SharedCounts]: ...
    def delete_private(self) -> PrivateCounts: ...
    def record_audit(self, record: AuditRecord) -> None: ...


class InMemoryPrivacyStore:
    """``ANUM_REPOSITORY_BACKEND=memory``: reads the process's in-memory stores."""

    def __init__(self, context: TenantContext) -> None:
        self.context = context

    # Each store is imported where it is used: they are process singletons.
    def _scoped(self, tenant_id: str, workspace_id: str) -> bool:
        return (tenant_id, workspace_id) == (self.context.tenant_id, self.context.workspace_id)

    def membership(self) -> WorkspaceMembership | None:
        from .dependencies import memory_repository

        return memory_repository.get_membership(self.context)

    def conversations(self) -> list[tuple[Conversation, list[ConversationTurn]]]:
        from .conversations import _owned, conversation_store

        # Every thread, the Assistant's and those with agents, oldest first.
        found = sorted(
            (item for item in list(conversation_store.conversations.values()) if _owned(item, self.context)),
            key=lambda item: (item.updated_at, item.id),
        )
        return [(item, conversation_store.turns(item)) for item in found]

    def _notes(self) -> list[MemoryNote]:
        from .dependencies import memory_note_repository

        notes = [
            note.model_copy(deep=True)
            for note in list(memory_note_repository._notes.values())
            if self._scoped(note.tenant_id, note.workspace_id)
        ]
        return sorted(notes, key=lambda note: (note.created_at, note.id))

    def private_memories(self) -> list[MemoryNote]:
        return [note for note in self._notes() if note.owner_user_id == self.context.user_id]

    def shared_memories_created(self) -> list[MemoryNote]:
        return [
            note
            for note in self._notes()
            if note.owner_user_id is None and note.provenance.created_by_user_id == self.context.user_id
        ]

    def voice_sessions(self) -> list[tuple[VoiceSession, list[TranscriptSegment]]]:
        from .voice import voice_store

        sessions = sorted(
            (
                session
                for session in list(voice_store.sessions.values())
                if self._scoped(session.tenant_id, session.workspace_id) and session.user_id == self.context.user_id
            ),
            key=lambda session: (session.created_at, session.id),
        )
        return [(session.model_copy(), voice_store.list_transcript(session)) for session in sessions]

    def tasks_created(self) -> list[tuple[Task, list[AgentRun]]]:
        from .store import store

        tasks = sorted(
            (
                task
                for task in list(store.tasks.values())
                if self._scoped(task.tenant_id, task.workspace_id) and task.created_by == self.context.user_id
            ),
            key=lambda task: (task.created_at, task.id),
        )
        return [
            (
                task,
                sorted(
                    (run for run in list(store.runs.values()) if run.task_id == task.id),
                    key=lambda run: (run.created_at, run.id),
                ),
            )
            for task in tasks
        ]

    def approvals_decided(self) -> list[tuple[Approval, ApprovalApproverRecord | None]]:
        from .dependencies import memory_repository

        approvals = memory_repository.list_approvals(self.context)
        approvers = memory_repository.list_approval_approvers([item.id for item in approvals], self.context)
        return _decided_by(self.context.user_id, approvals, approvers)

    def notification_preferences(self) -> NotificationPreferences | None:
        from .onboarding import _notifications

        return _notifications.get(self.context)

    def user_preferences(self) -> UserPreferences | None:
        from .user_preferences import user_preference_store

        return user_preference_store.get(self.context)

    def push_devices(self) -> list[PushDevice]:
        from .push import push_device_store

        return [device.view() for device in push_device_store.list(self.context)]

    def files_uploaded(self) -> list[FileRecord]:
        from .files import file_store

        records = [
            record
            for record in list(file_store.records.values())
            if self._scoped(record.tenant_id, record.workspace_id) and record.created_by == self.context.user_id
        ]
        return sorted(records, key=lambda record: (record.created_at, record.id))

    def learning(self) -> LearningStore:
        from .learning_store import learning_data

        return learning_data.bound(self.context)
    def meetings_created(self) -> list[tuple[Meeting, list[MeetingTranscriptSegment]]]:
        from .meetings import meeting_store

        mine = sorted(
            (
                meeting
                for meeting in list(meeting_store.meetings.values())
                if self._scoped(meeting.tenant_id, meeting.workspace_id) and meeting.created_by == self.context.user_id
            ),
            key=lambda meeting: (meeting.created_at, meeting.id),
        )
        return [(meeting.model_copy(deep=True), meeting_store.all_segments(self.context, meeting.id)) for meeting in mine]

    def meeting_recordings(self) -> list[FileRecord]:
        ids = {meeting.recording_file_id for meeting, _ in self.meetings_created() if meeting.recording_file_id}
        return [record for record in self.files_uploaded() if record.id in ids]

    def meeting_connections(self) -> list[StoredConnection]:
        from .meeting_connectors import connection_store

        return sorted(connection_store.list_connections(self.context), key=lambda item: item.provider.value)

    def commands(self) -> CommandStore:
        from .commands import memory_command_store

        return memory_command_store.bind(self.context)

    def library(self) -> LibraryPrivacy:
        return memory_library(self.context)

    def finance(self) -> Any:
        from .finance.store import finance_store

        return finance_store

    def team(self) -> Any:
        from .team_store import memory_team_store

        return memory_team_store

    def tyre(self) -> Any:
        from .tyre.store import memory_tyre_store

        return memory_tyre_store

    def commerce(self) -> Any:
        from .commerce.store import commerce_store

        return commerce_store

    def agents(self) -> Any:
        from .agent_profiles import agent_store

        return agent_store

    def delete_team_private(self) -> None:
        from .team_store import memory_team_store

        with memory_team_store._lock:
            workspace = memory_team_store._ws(self.context)
            for key, notification in list(workspace.notifications.items()):
                if notification.user_id == self.context.user_id:
                    workspace.notifications.pop(key, None)
            workspace.profiles.pop(self.context.user_id, None)

    def email_accounts_connected(self) -> list[EmailAccount]:
        from .email_inbox import email_backend

        with email_backend.lock:
            found = [
                item.model_copy(deep=True)
                for item in email_backend.accounts.values()
                if self._scoped(item.tenant_id, item.workspace_id) and item.connected_by == self.context.user_id
            ]
        return sorted(found, key=lambda item: (item.created_at, item.id))

    def email_drafts_written(self) -> list[EmailDraft]:
        from .email_inbox import email_backend

        with email_backend.lock:
            found = [
                item.model_copy(deep=True)
                for item in email_backend.drafts.values()
                if self._scoped(item.tenant_id, item.workspace_id) and item.created_by == self.context.user_id
            ]
        return sorted(found, key=lambda item: (item.created_at, item.id))

    def _private_mailbox_ids(self) -> set[str]:
        return {item.id for item in self.email_accounts_connected() if str(item.visibility) == "private"}

    def email_private_messages(self) -> list[EmailMessage]:
        from .email_inbox import email_backend

        ids = self._private_mailbox_ids()
        with email_backend.lock:
            found = [item.model_copy(deep=True) for item in email_backend.messages.values() if item.account_id in ids]
        return sorted(found, key=lambda item: (item.received_at, item.id))

    def whatsapp_messages_sent(self) -> list[WhatsAppMessage]:
        from .whatsapp_store import whatsapp_store

        with whatsapp_store._lock:
            found = [
                item.model_copy(deep=True)
                for item in whatsapp_store._messages.values()
                if self._scoped(item.tenant_id, item.workspace_id)
                and item.direction == "outbound"
                and item.sent_by == self.context.user_id
            ]
        return sorted(found, key=lambda item: (item.timestamp, item.id))

    def autonomy_actions_requested(self) -> list[AutonomousAction]:
        from .autonomy import autonomy_memory_store

        with autonomy_memory_store._lock:
            found = [
                item.model_copy(deep=True)
                for item in autonomy_memory_store.actions.values()
                if self._scoped(item.tenant_id, item.workspace_id) and item.requested_by == self.context.user_id
            ]
        return sorted(found, key=lambda item: (item.created_at, item.id))

    def _module_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        email = email_counts(
            self.email_accounts_connected(), self.email_drafts_written(), len(self.email_private_messages())
        )
        private, shared = module_counts(email, len(self.whatsapp_messages_sent()), len(self.autonomy_actions_requested()))
        other_private, other_shared = other_module_counts(self, self.context)
        return {**private, **other_private}, {**shared, **other_shared}

    def _delete_email(self) -> None:
        from .email_inbox import email_backend

        private = self._private_mailbox_ids()
        with email_backend.lock:
            for key, draft in list(email_backend.drafts.items()):
                mine = self._scoped(draft.tenant_id, draft.workspace_id) and draft.created_by == self.context.user_id
                if draft.account_id in private or (mine and str(draft.status) in UNSENT_DRAFT_STATUSES):
                    email_backend.drafts.pop(key, None)
            for key, message in list(email_backend.messages.items()):
                if message.account_id in private:
                    email_backend.messages.pop(key, None)
            for account_id in private:
                email_backend.accounts.pop(account_id, None)
            for key, pending in list(email_backend.pending.items()):
                if self._scoped(pending.tenant_id, pending.workspace_id) and pending.user_id == self.context.user_id:
                    email_backend.pending.pop(key, None)

    def home(self) -> HomeStore:
        from .home import home_store

        return home_store

    def _private_sources(self) -> list[Any]:
        from .retrieval import memory_retrieval_store

        return [
            source
            for source in memory_retrieval_store.list_sources(self.context)
            if source.owner_user_id == self.context.user_id
        ]

    def counts(self) -> tuple[PrivateCounts, SharedCounts]:
        conversations = self.conversations()
        voice = self.voice_sessions()
        meetings = self.meetings_created()
        from .voice import voice_store

        module_private, module_shared = self._module_counts()
        private = PrivateCounts(
            conversations=len(conversations),
            conversation_turns=sum(len(turns) for _, turns in conversations),
            private_memories=len(self.private_memories()),
            retrieval_index_entries=len(self._private_sources()),
            voice_sessions=len(voice),
            # Stored segments, including expired ones not yet purged.
            voice_transcript_segments=sum(len(voice_store.segments.get(session.id, [])) for session, _ in voice),
            notification_preferences=int(self.notification_preferences() is not None),
            user_preferences=int(self.user_preferences() is not None),
            push_devices=len(self.push_devices()),
            **learning_counts(self.learning()),
            meetings=len(meetings),
            meeting_transcript_segments=sum(len(rows) for _, rows in meetings),
            meeting_recordings=len(self.meeting_recordings()),
            meeting_connections=len(self.meeting_connections()),
            **command_privacy_counts(self.commands()),
            **self.library().counts(),
            **module_private,
            home_items=home_counts(self.home(), self.context),
            finance_records=self.finance().count_everything(self.context),
        )
        return private, self._shared_counts(module_shared)

    def _shared_counts(self, module_shared: dict[str, int]) -> SharedCounts:
        return SharedCounts(
            tasks_created=len(self.tasks_created()),
            approvals_decided=len(self.approvals_decided()),
            shared_memories_created=len(self.shared_memories_created()),
            files_uploaded=len(self.files_uploaded()),
            **module_shared,
        )

    def delete_private(self) -> PrivateCounts:
        from .conversations import conversation_store
        from .dependencies import memory_note_repository
        from .onboarding import _notifications
        from .retrieval import SourceType, memory_retrieval_store
        from .voice import voice_store

        deleted, _ = self.counts()
        for conversation, _turns in self.conversations():
            conversation_store.delete(conversation)
        for source in self._private_sources():
            memory_retrieval_store.delete_source(self.context, SourceType(source.source_type), source.source_id)
        for note in self.private_memories():
            memory_note_repository.delete(note.id, self.context)
        with voice_store._lock:
            for session, _segments in self.voice_sessions():
                for segment in voice_store.segments.pop(session.id, []):
                    voice_store.consumed_segments.discard(segment.id)
                voice_store.ask_counts.pop(session.id, None)
                voice_store.sessions.pop(session.id, None)
        with _notifications._lock:
            _notifications._items.pop((self.context.tenant_id, self.context.workspace_id, self.context.user_id), None)
        from .user_preferences import user_preference_store

        with user_preference_store._lock:
            user_preference_store._items.pop((self.context.tenant_id, self.context.user_id), None)
        from .push import push_device_store

        for device in push_device_store.list(self.context):
            push_device_store.delete(self.context, device.id)
        self.learning().delete_all()
        from .files import file_store
        from .meeting_connectors import connection_store
        from .meetings import meeting_store

        recordings = self.meeting_recordings()
        for meeting, _rows in self.meetings_created():
            meeting_store.delete(self.context, meeting.id)
        with file_store._lock:
            for record in recordings:
                file_store.records.pop(record.id, None)
        for connection in self.meeting_connections():
            connection_store.delete(self.context, connection.provider)
        self.commands().delete_all()
        self.library().delete_all()
        self.finance().delete_everything(self.context)
        self._delete_email()
        self.delete_team_private()
        self.home().delete_all(self.context)
        delete_private_agents(self, self.context)
        return deleted

    def record_audit(self, record: AuditRecord) -> None:
        from .dependencies import memory_repository

        memory_repository.record_audit(record)


def learning_counts(store: LearningStore) -> dict[str, int]:
    counts = store.counts()
    return {"learning_signals": counts["signals"], "learned_items": sum(counts.values()) - counts["signals"]}


def learning_export(store: LearningStore) -> dict[str, Any]:
    """Everything ANUM learned from the caller in this workspace, full texts included."""
    profile = store.get_profile()
    return {
        "profile": _dump(profile) if profile is not None else None,
        "signals": [_dump(signal) for signal in store.list_signals()],
        "preferences": [_dump(item) for item in store.list_preferences()],
        "routines": [_dump(item) for item in store.list_routines()],
        "notes": [_dump(item) for item in store.list_notes()],
        "suggestions": [_dump(item) for item in store.list_suggestions()],
    }


def _decided_by(
    user_id: str,
    approvals: list[Approval],
    approvers: dict[str, list[ApprovalApproverRecord]],
) -> list[tuple[Approval, ApprovalApproverRecord | None]]:
    """Approvals the user decided (approved or rejected) or approved as one of a chain."""
    decided = []
    for approval in sorted(approvals, key=lambda item: (item.created_at, item.id)):
        mine = next((row for row in approvers.get(approval.id, []) if row.user_id == user_id), None)
        if approval.decided_by == user_id or mine is not None:
            decided.append((approval, mine))
    return decided


@contextmanager
def open_privacy_store(context: TenantContext) -> Iterator[PrivacyStore]:
    """One unit of work in the caller's tenant, workspace *and* user RLS context.

    PostgreSQL: commits when the block ends normally, rolls back otherwise.
    """
    if settings.repository_backend == "memory":
        yield InMemoryPrivacyStore(context)
        return
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db import session as db_session
    from .db.privacy_repository import SqlAlchemyPrivacyStore

    session = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
        yield SqlAlchemyPrivacyStore(session, context)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------- export


class ExportTooLargeError(ValueError):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"export exceeds {limit} bytes")
        self.size = size
        self.limit = limit


def _dump(model: BaseModel, *, exclude: set[str] | None = None) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude=exclude)


def _iso(value: datetime) -> str:
    return value.isoformat()


def finance_export(finance: Any, context: TenantContext) -> dict[str, Any]:
    from .finance.store import TransactionFilter

    hidden = {"tenant_id", "workspace_id", "owner_user_id"}
    transactions: list[Any] = []
    while True:
        page, total = finance.list_transactions(context, TransactionFilter(), limit=1000, offset=len(transactions))
        transactions += page
        if not page or len(transactions) >= total:
            break
    imports: list[Any] = []
    while True:
        page, total = finance.list_imports(context, limit=200, offset=len(imports))
        imports += page
        if not page or len(imports) >= total:
            break
    settings = finance.get_settings(context)
    plan = finance.get_plan(context)
    versions: list[Any] = []
    while True:
        page, total = finance.list_policies(context, limit=100, offset=len(versions))
        versions += page
        if not page or len(versions) >= total:
            break
    return {
        "settings": settings.model_dump(mode="json") if settings is not None else None,
        "accounts": [a.model_dump(mode="json", exclude=hidden) for a in finance.list_accounts(context)],
        "transactions": [t.model_dump(mode="json", exclude=hidden) for t in transactions],
        "imports": [i.model_dump(mode="json", exclude=hidden) for i in imports],
        **{
            f"{kind}s": [item.model_dump(mode="json", exclude=hidden) for item in finance.list_items(context, kind)]
            for kind in ("bill", "debt", "goal", "holding")
        },
        # Stages 3 to 5 (docs/finance.md#privacy). A connection's credentials are never
        # exported, not even encrypted: only whether some are saved.
        "plan": plan.model_dump(mode="json") if plan is not None else None,
        "guardian_versions": [p.model_dump(mode="json", exclude=hidden) for p in versions],
        "guardian_changes": [c.model_dump(mode="json", exclude=hidden) for c in finance.list_changes(context, status=None, limit=1000)],
        "connections": [
            {**c.model_dump(mode="json", exclude=hidden | {"credentials_encrypted", "credential_hint"}), "has_credentials": bool(c.credentials_encrypted)}
            for c in finance.list_connections(context)
        ],
        "fx_rates": [r.model_dump(mode="json", exclude=hidden) for r in finance.list_fx_rates(context, limit=10_000)],
    }


def export_sections(store: PrivacyStore, context: TenantContext) -> Iterator[tuple[str, Any]]:
    """The export, one top-level section at a time (so the size cap can stop early)."""
    membership = store.membership()
    yield (
        "identity",
        {
            "user_id": context.user_id,
            "tenant_id": context.tenant_id,
            "workspace_id": context.workspace_id,
            "workspace_roles": list(context.roles),
            "organization_roles": list(context.organization_roles),
            "membership": _dump(membership) if membership is not None else None,
            "note": ACCOUNT_NOTE,
        },
    )
    yield (
        "conversations",
        [
            {**_dump(conversation), "turns": [_dump(turn) for turn in turns]}
            for conversation, turns in store.conversations()
        ],
    )
    yield (
        "memories",
        {
            "private": [_dump(note) for note in store.private_memories()],
            "shared_created_by_you": [_dump(note) for note in store.shared_memories_created()],
        },
    )
    yield (
        "voice_sessions",
        [{**_dump(session), "transcript": [_dump(segment) for segment in segments]} for session, segments in store.voice_sessions()],
    )
    yield (
        "tasks_created",
        [
            {
                **_dump(task),
                # The checkpoint is the runtime's internal resume state, not the user's data.
                "runs": [_dump(run, exclude={"checkpoint"}) for run in runs],
            }
            for task, runs in store.tasks_created()
        ],
    )
    yield (
        "approvals_decided",
        [
            {
                # Tool arguments belong to the run (and may hold redacted values); the
                # decision is what the user did.
                **_dump(approval, exclude={"arguments"}),
                "your_approval": _dump(mine) if mine is not None else None,
            }
            for approval, mine in store.approvals_decided()
        ],
    )
    preferences = store.notification_preferences()
    yield ("notification_preferences", _dump(preferences) if preferences is not None else None)
    # Tenant-wide (every workspace of this organization), e.g. "Remember things from my chats".
    mine = store.user_preferences()
    yield ("user_preferences", _dump(mine) if mine is not None else None)
    # Platform, label and dates only: the push token is never exported.
    yield ("push_devices", [_dump(device) for device in store.push_devices()])
    yield ("learning", learning_export(store.learning()))
    yield ("commands", command_privacy_export(store.commands()))
    # Favorites, private knowledge base collections (with their items) and private reports.
    yield ("library", store.library().export())
    yield ("home", home_export(store.home(), context))
    # The caller's whole finance ledger (docs/finance.md#privacy).
    yield ("finance", finance_export(store.finance(), context))
    yield (
        "email",
        {
            # Never a token or the provider's sync state.
            "mailboxes_connected": [
                _dump(account, exclude=EMAIL_ACCOUNT_PRIVATE_FIELDS) for account in store.email_accounts_connected()
            ],
            "drafts_written": [_dump(draft) for draft in store.email_drafts_written()],
            # Metadata synced from your private mailboxes (bodies are never stored by ANUM).
            "private_mailbox_messages": [_dump(message) for message in store.email_private_messages()],
        },
    )
    yield ("whatsapp_messages_sent", [_dump(message) for message in store.whatsapp_messages_sent()])
    yield (
        "autonomy_actions_requested",
        [_dump(action, exclude=AUTONOMY_ACTION_PRIVATE_FIELDS) for action in store.autonomy_actions_requested()],
    )
    team = team_data(store, context)
    yield (
        "team_work",
        {
            "profile": _dump(team["profile"]) if team["profile"] is not None else None,
            "notifications": [_dump(item) for item in team["notifications"]],
            "work_items_created": [_dump(item) for item in team["work_items_created"]],
            "work_items_assigned": [_dump(item) for item in team["work_items_assigned"]],
            "comments_written": team["comments_written"],
        },
    )
    tyre = tyre_data(store, context)
    yield (
        "tyre_planning",
        {
            # Never the source's secret; the planning data itself is the workspace's.
            "connection_created": _dump(tyre["connection"], exclude={"secret_ciphertext"}) if tyre["connection"] else None,
            "reports_created": [_dump(report, exclude={"content"}) for report in tyre["reports"]],
        },
    )
    yield (
        "commerce_connectors_created",
        [_dump(item, exclude={"cursors"}) for item in commerce_connectors_created(store, context)],
    )
    created = workspace_records_created(context)
    # Never a token or a webhook secret.
    excluded = {
        "marketing_accounts_connected": {"access_token_ciphertext", "refresh_token_ciphertext"},
        "automation_triggers_created": {"secret_ciphertext"},
    }
    yield (
        "agents_owned",
        [{**_dump(agent), "memories": [_dump(memory) for memory in memories]} for agent, memories in agents_owned(store, context)],
    )
    yield (
        "workspace_records_created",
        {name: [_dump(item, exclude=excluded.get(name)) for item in items] for name, items in created.items()},
    )
    yield (
        "files_uploaded",
        [
            {
                **_dump(record, exclude={"storage_key"}),
                # Bytes are not embedded: download them, signed in, from this path.
                "download_path": f"/api/v1/files/{record.id}/content",
            }
            for record in store.files_uploaded()
        ],
    )
    yield from _meeting_sections(store)


def _meeting_sections(store: PrivacyStore) -> Iterator[tuple[str, Any]]:
    recordings = {record.id: record for record in store.meeting_recordings()}
    yield (
        "meetings_imported",
        [
            {
                **_dump(meeting),
                "transcript": [_dump(row) for row in rows],
                "recording": (
                    {
                        **_dump(recordings[meeting.recording_file_id], exclude={"storage_key"}),
                        "download_path": f"/api/v1/files/{meeting.recording_file_id}/content",
                    }
                    if meeting.recording_file_id in recordings
                    else None
                ),
            }
            for meeting, rows in store.meetings_created()
        ],
    )
    yield (
        "meeting_connections",
        # Which accounts are connected; the OAuth tokens themselves are never exported.
        [_dump(item, exclude={"token_ciphertext", "tenant_id", "workspace_id"}) for item in store.meeting_connections()],
    )


NOT_INCLUDED = [
    "File contents: each uploaded file (and meeting recording) is listed with a download_path; download it while signed in.",
    "Zoom and Microsoft sign-in tokens: connected accounts are listed, the tokens are not exported.",
    "Audit records: the workspace's security log (ids and counts, not content); ask a workspace owner for an audit export.",
    "Workspace records created by others, and other users' private data.",
    "Your sign-in account (name, email, password, two-factor): held by the identity provider; ask your administrator.",
    "Data in other workspaces: switch workspace and export again.",
]


def build_export(store: PrivacyStore, context: TenantContext, limit: int) -> tuple[bytes, dict[str, int]]:
    """Serialize the export; raises :class:`ExportTooLargeError` past ``limit`` bytes."""
    header = {"format": EXPORT_FORMAT, "generated_at": _iso(utc_now()), "not_included": NOT_INCLUDED}
    parts: list[bytes] = []
    size = 0
    sections: dict[str, int] = {}
    for name, value in export_sections(store, context):
        encoded = json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8")
        size += len(encoded) + len(name) + 8
        if size > limit:
            raise ExportTooLargeError(size, limit)
        parts.append(json.dumps(name).encode("utf-8") + b": " + encoded)
        sections[name] = len(value) if isinstance(value, list) else int(value is not None)
    head = json.dumps(header, ensure_ascii=False, indent=1).encode("utf-8")
    body = head[:-1].rstrip() + b",\n" + b",\n".join(parts) + b"\n}\n"
    if len(body) > limit:
        raise ExportTooLargeError(len(body), limit)
    return body, sections


# --------------------------------------------------------------------------- routes


router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])


def _require_member(context: TenantContext) -> None:
    # Every workspace role (viewer included) holds task:read; a caller without an
    # active role in this workspace gets 403 from require_permission.
    require_permission(context, Permission.TASK_READ)


def _audit(store: PrivacyStore, context: TenantContext, action: str, outcome: str, metadata: dict[str, Any]) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=context.user_id,
            outcome=outcome,
            correlation_id=current_correlation_id() or new_id("privacy"),
            created_at=utc_now(),
            metadata=metadata,
        )
    )


def _workspace_provider(context: TenantContext) -> str | None:
    from .model_config_store import open_model_config_store

    try:
        with open_model_config_store(context) as configs:
            config = configs.get(context, include_secret=False)
    except Exception as exc:  # the summary still answers; only the class name is logged
        logger.warning(
            "privacy_model_config_unavailable error_class=%s",
            type(exc).__name__,
            extra={"anum_privacy": {"event": "model_config_unavailable", "error_class": type(exc).__name__}},
        )
        return None
    return config.provider if config is not None else None


def _retention(language: Language) -> RetentionInfo:
    info = RetentionInfo(conversation_days=settings.conversation_retention_days)
    if language == "ar":
        # The notes follow Accept-Language (docs/web-localization.md); the numbers and codes do not.
        info = info.model_copy(update={key: PRIVACY_NOTES_AR[key] for key in ("private_memories", "task_memories", "audit_records")})
    return info


def _notes(language: Language) -> dict[str, str]:
    if language == "ar":
        return {"account_note": PRIVACY_NOTES_AR["account_note"], "shared_note": PRIVACY_NOTES_AR["shared_note"]}
    return {"account_note": ACCOUNT_NOTE, "shared_note": SHARED_NOTE}


@router.get("", response_model=PrivacySummary)
async def privacy_summary(
    context: TenantContext = Depends(tenant_context),
    language: Language = Depends(request_language),
) -> PrivacySummary:
    _require_member(context)
    with open_privacy_store(context) as store:
        membership = store.membership()
        private, shared = store.counts()
        preferences = store.user_preferences() or UserPreferences()
    # Outside the privacy transaction: the governance read opens its own unit of work.
    memory = conversation_memory_state(context, preferences)
    return PrivacySummary(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        role=membership.role if membership is not None else (context.roles[0] if context.roles else None),
        private=private,
        shared=shared,
        retention=_retention(language),
        model_provider=ModelProviderInfo(
            deployment=settings.model_provider,
            workspace=_workspace_provider(context),
            conversation_memory_enabled=settings.conversation_memory_enabled,
            conversation_memory=memory,
        ),
        privacy_policy_url=settings.privacy_policy_url.strip() or None,
        export_max_bytes=settings.privacy_export_max_bytes,
        **_notes(language),
    )


@router.get(
    "/export",
    responses={200: {"content": {"application/json": {}}, "description": "The caller's data (anum.privacy-export/v1)"}},
)
async def export_my_data(context: TenantContext = Depends(tenant_context)) -> Response:
    _require_member(context)
    limit = settings.privacy_export_max_bytes
    with open_privacy_store(context) as store:
        try:
            body, sections = build_export(store, context, limit)
        except ExportTooLargeError as exc:
            _audit(store, context, "privacy.export", "refused_too_large", {"limit_bytes": limit})
            too_large = exc
        else:
            too_large = None
            _audit(store, context, "privacy.export", "success", {"format": EXPORT_FORMAT, "bytes": len(body), "sections": sections})
    if too_large is not None:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Your data is larger than this server's export limit ({limit // (1024 * 1024)} MB). "
                "Delete conversations you no longer need, or ask your administrator to raise "
                "ANUM_PRIVACY_EXPORT_MAX_BYTES, then try again."
            ),
        )
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "content-disposition": f'attachment; filename="anum-my-data-{stamp}.json"',
            "cache-control": "no-store",
        },
    )


@router.post("/delete", response_model=DeleteResult)
async def delete_my_data(
    payload: DeleteMyData,
    context: TenantContext = Depends(tenant_context),
    language: Language = Depends(request_language),
) -> DeleteResult:
    _require_member(context)
    if payload.confirm.strip() != DELETE_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Type {DELETE_CONFIRMATION} exactly to confirm. Nothing was deleted.",
        )
    with open_privacy_store(context) as store:
        private_mailboxes = [item for item in store.email_accounts_connected() if str(item.visibility) == "private"]
    # Withdraw ANUM's access at the provider first (as disconnecting a mailbox does), outside
    # the database transaction; a failure never stops the deletion.
    notes, revoked = await revoke_mailboxes(private_mailboxes)
    with open_privacy_store(context) as store:
        recording_keys = [record.storage_key for record in store.meeting_recordings()]
        deleted = store.delete_private()
        _, kept = store.counts()
        _audit(
            store, context, "privacy.delete", "success",
            {"deleted": deleted.model_dump(), "kept": kept.model_dump(), "mailboxes_revoked_at_provider": revoked},
        )
    # Recording bytes go only after their rows are gone (as a file delete does).
    from .files import file_store

    for key in recording_keys:
        try:
            file_store.storage.delete(key)
        except Exception as exc:  # the metadata is gone, so the bytes are unreachable
            logger.warning("privacy_recording_delete_failed error_class=%s", type(exc).__name__)
    return DeleteResult(
        deleted=deleted, kept=kept, notes=[localize_message(note, language) for note in notes], **_notes(language)
    )


async def revoke_mailboxes(accounts: list[EmailAccount]) -> tuple[list[str], int]:
    """Revoke each private mailbox's grant at its provider; notes for what the caller must do."""
    import httpx

    from .email_inbox import PROVIDER_NAMES, provider_client
    from .email_providers import MailProviderError
    from .secret_box import SecretDecryptionError, secret_cipher

    notes: list[str] = []
    revoked = 0
    for account in accounts:
        name = PROVIDER_NAMES.get(account.provider, str(account.provider))
        done = False
        try:
            if account.refresh_token_enc:
                done = await provider_client(account.provider).revoke(secret_cipher().decrypt(account.refresh_token_enc))
        except (MailProviderError, SecretDecryptionError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning(
                "privacy_mailbox_revoke_failed error_class=%s",
                type(exc).__name__,
                extra={"anum_privacy": {"event": "mailbox_revoke_failed", "error_class": type(exc).__name__}},
            )
        if done:
            revoked += 1
        else:
            notes.append(
                f"ANUM deleted {account.address} and its tokens, but could not withdraw its access at {name}. "
                f"Remove ANUM in your {name} account's security settings (third-party apps)."
            )
    return notes, revoked
