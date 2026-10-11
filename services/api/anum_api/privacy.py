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
  retrieval index rows, voice sessions (and transcripts), notification preferences and
  the phones registered for push in this workspace.
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
from .conversations import Conversation, ConversationTurn
from .dependencies import require_permission, tenant_context
from .files import FileRecord
from .memory import MemoryNote
from .onboarding import NotificationPreferences
from .push import PushDevice
from .request_context import current_correlation_id
from .schemas import AgentRun, Approval, ApprovalApproverRecord, Task, TenantContext, WorkspaceMembership, new_id, utc_now
from .settings import settings
from .user_preferences import ConversationMemoryState, UserPreferences, conversation_memory_state
from .voice import TRANSCRIPT_RETENTION_DAYS, TranscriptRetention, TranscriptSegment, VoiceSession

logger = logging.getLogger("anum.privacy")

EXPORT_FORMAT = "anum.privacy-export/v1"
DELETE_CONFIRMATION = "DELETE MY DATA"
ACCOUNT_NOTE = (
    "Your sign-in account is managed by your organization's identity provider, not by "
    "ANUM. To close it, ask your workspace owner or organization administrator."
)
SHARED_NOTE = (
    "Tasks, runs, approvals, shared memories and files belong to the workspace, so they "
    "are kept. Ask a workspace owner to remove them."
)


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

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class SharedCounts(BaseModel):
    """Workspace-shared records the caller created or decided: kept by a deletion."""

    tasks_created: int = 0
    approvals_decided: int = 0
    shared_memories_created: int = 0
    files_uploaded: int = 0


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
        from .conversations import conversation_store

        found = conversation_store.list(self.context, limit=len(conversation_store.conversations) + 1)
        return [(item, conversation_store.turns(item)) for item in reversed(found)]

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
        from .voice import voice_store

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
        )
        return private, self._shared_counts()

    def _shared_counts(self) -> SharedCounts:
        return SharedCounts(
            tasks_created=len(self.tasks_created()),
            approvals_decided=len(self.approvals_decided()),
            shared_memories_created=len(self.shared_memories_created()),
            files_uploaded=len(self.files_uploaded()),
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
        return deleted

    def record_audit(self, record: AuditRecord) -> None:
        from .dependencies import memory_repository

        memory_repository.record_audit(record)


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


NOT_INCLUDED = [
    "File contents: each uploaded file is listed with a download_path; download it while signed in.",
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


@router.get("", response_model=PrivacySummary)
async def privacy_summary(context: TenantContext = Depends(tenant_context)) -> PrivacySummary:
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
        retention=RetentionInfo(conversation_days=settings.conversation_retention_days),
        model_provider=ModelProviderInfo(
            deployment=settings.model_provider,
            workspace=_workspace_provider(context),
            conversation_memory_enabled=settings.conversation_memory_enabled,
            conversation_memory=memory,
        ),
        privacy_policy_url=settings.privacy_policy_url.strip() or None,
        export_max_bytes=settings.privacy_export_max_bytes,
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
async def delete_my_data(payload: DeleteMyData, context: TenantContext = Depends(tenant_context)) -> DeleteResult:
    _require_member(context)
    if payload.confirm.strip() != DELETE_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Type {DELETE_CONFIRMATION} exactly to confirm. Nothing was deleted.",
        )
    with open_privacy_store(context) as store:
        deleted = store.delete_private()
        _, kept = store.counts()
        _audit(store, context, "privacy.delete", "success", {"deleted": deleted.model_dump(), "kept": kept.model_dump()})
    return DeleteResult(deleted=deleted, kept=kept)
