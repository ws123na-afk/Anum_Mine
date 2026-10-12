"""Calls & Meetings: summaries from recordings and transcripts (docs/meetings.md).

There are no live meeting bots. A meeting enters ANUM in one of four ways:

* a recording upload (``POST /api/v1/meetings/recordings``), stored as a workspace file and
  transcribed by the configured speech-to-text provider (``anum_api.transcription``);
  refused with 409 and setup instructions when none is configured;
* a transcript file upload (``POST /api/v1/meetings/transcripts``: VTT, SRT, TXT, DOCX);
* a Zoom cloud-recording transcript or a Microsoft Teams meeting transcript, through the
  caller's own OAuth connection (``anum_api.meeting_connectors``).

Processing runs after the response (FastAPI background task): transcription when needed,
then one budgeted model call (``anum_api.meeting_analysis``) that writes the summary,
decisions, action items and a follow-up email draft, each labelled as AI generated with
the model that wrote it. The mock provider writes nothing: the meeting waits in
``needs_model`` until a model is connected and someone presses Summarize.

Nothing the model says acts on its own. An action item becomes a task only when a user
presses "Create task" (``task:create``), and the follow-up email is only *drafted*:
sending saves it as a reply draft in a connected mailbox and asks for approval of a
``send_email`` call (the email module's own path), so nothing leaves before an approver
approves exactly that text.

Meetings belong to the workspace (forced RLS on tenant and workspace): every member can
read them; owners and members import; only the importer or a workspace owner edits or
deletes one. A meeting, its transcript and its recording are the importer's personal data
for "Download my data" and "Delete my data" (docs/privacy.md).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import PurePath
from threading import RLock
from typing import Any, Protocol

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import meeting_connectors as connectors
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .meeting_analysis import AnalysisParseError, analysis_unavailable, analyze
from .meeting_transcripts import (
    ParsedSegment,
    TranscriptParseError,
    detect_language,
    parse_cues,
    parse_transcript,
)
from .model_budget import ModelBudgetExceededError
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings
from .transcription import RECORDING_EXTENSIONS, TranscriptionError, build_transcriber
from .transcription import setup_problem as transcription_setup_problem

logger = logging.getLogger("anum.meetings")

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])

# A meeting stuck in a working state this long (the API restarted mid-run) may be retried.
STALE_AFTER = timedelta(minutes=15)
MAX_PAGE = 100
MAX_RECIPIENTS = 20
_EMAIL = re.compile(r"^[^@\s<>,;]{1,64}@[^@\s<>,;]{1,255}\.[^@\s<>,;]{2,63}$")
_LANGUAGES = {"auto", "en", "ar"}

CREATE_DENIED = "Viewers can read meetings but not import them. Ask a workspace owner to make you a member."
EDIT_DENIED = "Only the person who imported this meeting or a workspace owner can change or delete it."


class MeetingSource(StrEnum):
    RECORDING = "recording"
    TRANSCRIPT = "transcript"
    ZOOM = "zoom"
    TEAMS = "teams"


class MeetingStatus(StrEnum):
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    NEEDS_MODEL = "needs_model"
    FAILED = "failed"


WORKING = {MeetingStatus.QUEUED, MeetingStatus.TRANSCRIBING, MeetingStatus.ANALYZING}


class ActionItem(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=120)
    due_date: date | None = None
    done: bool = False
    task_id: str | None = None


class FollowUpEmail(BaseModel):
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    # True once a user changed the draft: summarizing again keeps their text.
    edited: bool = False
    # Set when the draft was handed to the email module (docs/email.md): its reply draft,
    # the approval that must be granted before ``send_email`` runs, and that task.
    send_task_id: str | None = None
    email_draft_id: str | None = None
    approval_id: str | None = None
    sent_from: str | None = None


class AnalysisProvenance(BaseModel):
    label: str = "ai_generated"
    source: str = "meeting_transcript"
    provider: str
    model: str
    generated_at: datetime
    transcript_chars: int = 0
    truncated: bool = False


class Meeting(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    title: str
    source: MeetingSource
    status: MeetingStatus
    # Resolved language of the transcript and notes ("en" or "ar"); "auto" until known.
    language: str = "auto"
    requested_language: str = "auto"
    occurred_at: datetime | None = None
    duration_seconds: int | None = None
    agent_skill_id: str | None = None
    recording_file_id: str | None = None
    source_file_name: str | None = None
    external_id: str | None = None
    segment_count: int = 0
    error_code: str | None = None
    error: str | None = None
    summary: str | None = None
    decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    follow_up: FollowUpEmail | None = None
    provenance: AnalysisProvenance | None = None
    created_at: datetime
    updated_at: datetime


class TranscriptSegment(BaseModel):
    id: str
    meeting_id: str
    seq: int
    speaker: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text: str


class MeetingPage(BaseModel):
    items: list[Meeting]
    total: int
    offset: int
    limit: int


class TranscriptPage(BaseModel):
    items: list[TranscriptSegment]
    total: int
    offset: int
    limit: int
    speakers: list[str]


class TranscriptionSetup(BaseModel):
    configured: bool
    provider: str
    model: str | None = None
    target: str | None = None
    problem: str | None = None
    max_recording_bytes: int
    accepted_extensions: list[str]


class ConnectorStatus(BaseModel):
    provider: connectors.ConnectorProvider
    available: bool
    problem: str | None = None
    connected: bool = False
    account_label: str | None = None
    connected_at: datetime | None = None


class EmailAccountChoice(BaseModel):
    id: str
    address: str
    provider: str


class FollowUpSend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)


class MeetingSetup(BaseModel):
    transcription: TranscriptionSetup
    transcript_extensions: list[str] = Field(default_factory=lambda: [".vtt", ".srt", ".txt", ".docx"])
    max_transcript_bytes: int
    connectors: list[ConnectorStatus]
    model_ready: bool
    # Mailboxes the caller may send from (email module); empty when email is not
    # connected or the caller's role cannot act on email.
    email_accounts: list[EmailAccountChoice] = Field(default_factory=list)
    can_create: bool
    can_manage_all: bool


class MeetingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    agent_skill_id: str | None = Field(default=None, max_length=160)
    clear_agent: bool = False
    language: str | None = None

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        if value is not None and value not in _LANGUAGES:
            raise ValueError("language must be auto, en or ar")
        return value


class ActionItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=120)
    clear_owner: bool = False
    due_date: date | None = None
    clear_due_date: bool = False
    done: bool | None = None


class FollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=6000)

    @field_validator("to")
    @classmethod
    def _recipients(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        bad = [item for item in cleaned if not _EMAIL.match(item)]
        if bad:
            raise ValueError(f"not an email address: {bad[0][:80]}")
        return list(dict.fromkeys(cleaned))


class OAuthCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=4096)


class ConnectorImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=512)
    language: str = "auto"
    agent_skill_id: str | None = Field(default=None, max_length=160)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        if value not in _LANGUAGES:
            raise ValueError("language must be auto, en or ar")
        return value


class AuthorizeLink(BaseModel):
    authorize_url: str


# --------------------------------------------------------------------------- store


class DuplicateImportError(LookupError):
    """The same Zoom/Teams transcript is already a meeting in this workspace."""


class MeetingStore(Protocol):
    def add(self, meeting: Meeting) -> Meeting: ...
    def get(self, context: TenantContext, meeting_id: str, *, lock: bool = False) -> Meeting | None: ...
    def save(self, meeting: Meeting) -> Meeting: ...
    def delete(self, context: TenantContext, meeting_id: str) -> bool: ...
    def list_meetings(
        self, context: TenantContext, *, query: str | None, status: MeetingStatus | None, offset: int, limit: int
    ) -> tuple[list[Meeting], int]: ...
    def replace_segments(self, context: TenantContext, meeting_id: str, segments: list[TranscriptSegment]) -> None: ...
    def segments(
        self, context: TenantContext, meeting_id: str, *, query: str | None, speaker: str | None, offset: int, limit: int
    ) -> tuple[list[TranscriptSegment], int]: ...
    def all_segments(self, context: TenantContext, meeting_id: str) -> list[TranscriptSegment]: ...
    def speakers(self, context: TenantContext, meeting_id: str) -> list[str]: ...
    def external_ids(self, context: TenantContext, source: MeetingSource) -> dict[str, str]: ...


def _matches(text: str | None, needle: str) -> bool:
    return bool(text) and needle in text.casefold()  # type: ignore[union-attr]


class InMemoryMeetingStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self.meetings: dict[str, Meeting] = {}
        self.segment_rows: dict[str, list[TranscriptSegment]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.meetings.clear()
            self.segment_rows.clear()

    def _scoped(self, context: TenantContext, meeting: Meeting) -> bool:
        return meeting.tenant_id == context.tenant_id and meeting.workspace_id == context.workspace_id

    def add(self, meeting: Meeting) -> Meeting:
        with self._lock:
            if meeting.external_id and any(
                item.external_id == meeting.external_id
                and item.source == meeting.source
                and item.tenant_id == meeting.tenant_id
                and item.workspace_id == meeting.workspace_id
                for item in self.meetings.values()
            ):
                raise DuplicateImportError(meeting.external_id)
            self.meetings[meeting.id] = meeting.model_copy(deep=True)
        return meeting

    def get(self, context: TenantContext, meeting_id: str, *, lock: bool = False) -> Meeting | None:
        with self._lock:
            meeting = self.meetings.get(meeting_id)
            return meeting.model_copy(deep=True) if meeting is not None and self._scoped(context, meeting) else None

    def save(self, meeting: Meeting) -> Meeting:
        with self._lock:
            if meeting.id in self.meetings:
                self.meetings[meeting.id] = meeting.model_copy(deep=True)
        return meeting

    def delete(self, context: TenantContext, meeting_id: str) -> bool:
        with self._lock:
            if self.get(context, meeting_id) is None:
                return False
            self.meetings.pop(meeting_id, None)
            self.segment_rows.pop(meeting_id, None)
            return True

    def list_meetings(
        self, context: TenantContext, *, query: str | None, status: MeetingStatus | None, offset: int, limit: int
    ) -> tuple[list[Meeting], int]:
        needle = query.casefold() if query else None
        with self._lock:
            found = [
                meeting
                for meeting in self.meetings.values()
                if self._scoped(context, meeting)
                and (status is None or meeting.status == status)
                and (
                    needle is None
                    or _matches(meeting.title, needle)
                    or _matches(meeting.summary, needle)
                    or any(_matches(row.text, needle) for row in self.segment_rows.get(meeting.id, []))
                )
            ]
        found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return [item.model_copy(deep=True) for item in found[offset : offset + limit]], len(found)

    def replace_segments(self, context: TenantContext, meeting_id: str, segments: list[TranscriptSegment]) -> None:
        with self._lock:
            self.segment_rows[meeting_id] = list(segments)

    def segments(
        self, context: TenantContext, meeting_id: str, *, query: str | None, speaker: str | None, offset: int, limit: int
    ) -> tuple[list[TranscriptSegment], int]:
        needle = query.casefold() if query else None
        rows = [
            row
            for row in self.all_segments(context, meeting_id)
            if (needle is None or _matches(row.text, needle) or _matches(row.speaker, needle))
            and (speaker is None or row.speaker == speaker)
        ]
        return rows[offset : offset + limit], len(rows)

    def all_segments(self, context: TenantContext, meeting_id: str) -> list[TranscriptSegment]:
        if self.get(context, meeting_id) is None:
            return []
        with self._lock:
            return sorted(self.segment_rows.get(meeting_id, []), key=lambda row: row.seq)

    def speakers(self, context: TenantContext, meeting_id: str) -> list[str]:
        return sorted({row.speaker for row in self.all_segments(context, meeting_id) if row.speaker})

    def external_ids(self, context: TenantContext, source: MeetingSource) -> dict[str, str]:
        with self._lock:
            return {
                meeting.external_id: meeting.id
                for meeting in self.meetings.values()
                if self._scoped(context, meeting) and meeting.source == source and meeting.external_id
            }


meeting_store = InMemoryMeetingStore()
# Serializes "create task" and "send" per process for the memory backend; PostgreSQL
# also locks the meeting row (SELECT ... FOR UPDATE).
_action_lock = RLock()


@contextmanager
def open_meeting_store(context: TenantContext) -> Iterator[MeetingStore]:
    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.meetings_repository import SqlAlchemyMeetingStore

        return SqlAlchemyMeetingStore(session)

    with open_scoped_store(context, meeting_store, sql_store) as store:
        yield store


# --------------------------------------------------------------------------- helpers


def _default_gateway() -> Any:
    global _fallback_gateway
    if _fallback_gateway is None:
        from .model_gateway import build_default_model_gateway

        _fallback_gateway = build_default_model_gateway()
    return _fallback_gateway


_fallback_gateway: Any = None


def analysis_gateway(context: TenantContext) -> Any:
    """The workspace's model behind its monthly budget (tests replace this)."""
    from .onboarding import budgeted_model_gateway

    return budgeted_model_gateway(context, _default_gateway())


def email_accounts(context: TenantContext) -> list[EmailAccountChoice]:
    """Connected mailboxes the caller may send from (empty without ``email:act``)."""
    from .email_inbox import AccountStatus, open_email_store, visible_accounts

    if not has_permission(context, Permission.EMAIL_ACT):
        return []
    try:
        with open_email_store(context) as store:
            accounts = visible_accounts(store, context)
    except Exception as exc:  # setup still answers; the send button stays off
        logger.warning("meeting_email_accounts_failed error_class=%s", type(exc).__name__)
        return []
    return [
        EmailAccountChoice(id=account.id, address=account.address, provider=account.provider.value)
        for account in accounts
        if account.status == AccountStatus.CONNECTED
    ]


def _require_create(context: TenantContext) -> None:
    if not has_permission(context, Permission.MEETING_CREATE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CREATE_DENIED)


def _can_edit(context: TenantContext, meeting: Meeting) -> bool:
    if has_permission(context, Permission.MEETING_MANAGE):
        return True
    return has_permission(context, Permission.MEETING_CREATE) and meeting.created_by == context.user_id


def _require_edit(context: TenantContext, meeting: Meeting) -> None:
    if not _can_edit(context, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EDIT_DENIED)


def _get_or_404(store: MeetingStore, context: TenantContext, meeting_id: str, *, lock: bool = False) -> Meeting:
    meeting = store.get(context, meeting_id, lock=lock)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found in this workspace")
    return meeting


def _language(value: str | None) -> str:
    value = (value or "auto").strip().lower()
    if value not in _LANGUAGES:
        raise HTTPException(status_code=422, detail="language must be auto, en or ar")
    return value


def _title(value: str | None, fallback: str) -> str:
    title = re.sub(r"\s+", " ", (value or "").strip()) or fallback
    return title[:200]


def _occurred(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="occurred_at must be an ISO date and time") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=utc_now().tzinfo)


def _check_agent(context: TenantContext, agent_skill_id: str | None) -> str | None:
    if not agent_skill_id:
        return None
    from .skills_api import open_skill_store

    with open_skill_store(context) as skills:
        installed = {item.skill_id for item in skills.list_installations(context)}
    if agent_skill_id not in installed:
        raise HTTPException(status_code=422, detail="No agent with that id is installed in this workspace")
    return agent_skill_id


def _file_name(query_name: str | None, header_name: str | None) -> str:
    """``?name=`` (URL-encoded, as Files takes it) or ``X-File-Name`` (ASCII, may be percent-encoded)."""
    from urllib.parse import unquote

    from .files import valid_file_name

    if query_name:
        return valid_file_name(query_name)
    if header_name:
        return valid_file_name(unquote(header_name, errors="strict"))
    raise HTTPException(status_code=422, detail="Name the file: send ?name= (or the X-File-Name header).")


def _segments(meeting_id: str, parsed: list[ParsedSegment]) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            id=new_id("seg"),
            meeting_id=meeting_id,
            seq=index,
            speaker=item.speaker,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            text=item.text,
        )
        for index, item in enumerate(parsed)
    ]


def _duration(parsed: list[ParsedSegment]) -> int | None:
    ends = [item.end_ms or item.start_ms for item in parsed if (item.end_ms or item.start_ms) is not None]
    return int(max(ends) // 1000) if ends else None  # type: ignore[type-var]


def _resolved_language(requested: str, parsed: list[ParsedSegment], hint: str | None = None) -> str:
    if requested in {"en", "ar"}:
        return requested
    if hint in {"en", "ar"}:
        return hint  # type: ignore[return-value]
    return detect_language(" ".join(item.text for item in parsed[:400]))


def _emit(context: TenantContext, event: Any, meeting: Meeting, **extra: Any) -> None:
    """Record a meeting event (feeds the activity stream); never fails the caller."""
    from .dependencies import _unit_of_work
    from .events import create_event

    try:
        with _unit_of_work(context, require_membership=False) as repository:
            repository.record_event(
                create_event(
                    event,
                    context,
                    meeting.id,
                    {"title": meeting.title, "source": meeting.source.value, "agent_skill_id": meeting.agent_skill_id, **extra},
                    correlation_id=meeting.id,
                ).event
            )
    except Exception as exc:
        logger.warning("meeting_event_failed error_class=%s", type(exc).__name__)


def _new_meeting(
    context: TenantContext,
    *,
    title: str,
    source: MeetingSource,
    language: str,
    occurred_at: datetime | None,
    agent_skill_id: str | None,
    status_: MeetingStatus,
    **fields: Any,
) -> Meeting:
    now = utc_now()
    return Meeting(
        id=new_id("meeting"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_by=context.user_id,
        title=title,
        source=source,
        status=status_,
        language=language if language != "auto" else "auto",
        requested_language=language,
        occurred_at=occurred_at,
        agent_skill_id=agent_skill_id,
        created_at=now,
        updated_at=now,
        **fields,
    )


def _update(context: TenantContext, meeting_id: str, **changes: Any) -> Meeting | None:
    """Apply ``changes`` to the stored meeting (None when it was deleted meanwhile)."""
    with open_meeting_store(context) as store:
        meeting = store.get(context, meeting_id, lock=True)
        if meeting is None:
            return None
        updated = meeting.model_copy(update={**changes, "updated_at": utc_now()})
        store.save(updated)
        return updated


def _fail(context: TenantContext, meeting_id: str, code: str, message: str) -> None:
    meeting = _update(context, meeting_id, status=MeetingStatus.FAILED, error_code=code, error=message)
    if meeting is not None:
        _emit(context, _event("MEETING_FAILED"), meeting, error_code=code)


def _event(name: str) -> Any:
    from .events import CanonicalEventName

    return CanonicalEventName[name]


# --------------------------------------------------------------------------- processing


async def _transcribe(context: TenantContext, meeting_id: str) -> bool:
    transcriber = build_transcriber(settings)
    if transcriber is None:
        _fail(context, meeting_id, "transcription_not_configured", transcription_setup_problem(settings) or "No transcription provider.")
        return False
    meeting = _update(context, meeting_id, status=MeetingStatus.TRANSCRIBING, error_code=None, error=None)
    if meeting is None:
        return False
    from .files import file_store, open_file_metadata_store

    record = None
    if meeting.recording_file_id:
        with open_file_metadata_store(context) as files:
            record = files.get(context, meeting.recording_file_id)
    try:
        content = file_store.storage.get(record.storage_key) if record is not None else None
    except FileNotFoundError:
        content = None
    if record is None or content is None:
        _fail(context, meeting_id, "recording_missing", "The recording was deleted from Files. Upload it again.")
        return False
    try:
        result = await transcriber.transcribe(
            content,
            file_name=record.name,
            content_type=record.content_type,
            language=meeting.requested_language if meeting.requested_language != "auto" else None,
        )
    except TranscriptionError as exc:
        _fail(context, meeting_id, "transcription_failed", exc.detail)
        return False
    if not result.segments:
        _fail(context, meeting_id, "empty_transcript", "No speech was recognised in the recording.")
        return False
    segments = _segments(meeting_id, result.segments)
    language = _resolved_language(meeting.requested_language, result.segments, result.language)
    with open_meeting_store(context) as store:
        current = store.get(context, meeting_id, lock=True)
        if current is None:
            return False
        store.replace_segments(context, meeting_id, segments)
        store.save(
            current.model_copy(
                update={
                    "status": MeetingStatus.ANALYZING,
                    "segment_count": len(segments),
                    "language": language,
                    "duration_seconds": result.duration_seconds or _duration(result.segments),
                    "updated_at": utc_now(),
                }
            )
        )
    return True


def _merge_items(existing: list[ActionItem], fresh: list[dict[str, Any]]) -> list[ActionItem]:
    """Keep items already turned into tasks or edited to done; replace the rest."""
    kept = [item for item in existing if item.task_id or item.done]
    seen = {item.text.casefold() for item in kept}
    for value in fresh:
        if value["text"].casefold() in seen:
            continue
        kept.append(
            ActionItem(
                id=new_id("act"),
                text=value["text"],
                owner=value.get("owner"),
                due_date=date.fromisoformat(value["due_date"]) if value.get("due_date") else None,
            )
        )
    return kept


async def _analyze(context: TenantContext, meeting_id: str) -> None:
    with open_meeting_store(context) as store:
        meeting = store.get(context, meeting_id)
        rows = store.all_segments(context, meeting_id) if meeting is not None else []
    if meeting is None:
        return
    if not rows:
        _fail(context, meeting_id, "empty_transcript", "The transcript has no text to summarise.")
        return
    gateway = analysis_gateway(context)
    if analysis_unavailable(gateway):
        _update(
            context,
            meeting_id,
            status=MeetingStatus.NEEDS_MODEL,
            error_code="no_model",
            error="Connect a model in Settings > Model, then press Summarize. The transcript is saved.",
        )
        return
    _update(context, meeting_id, status=MeetingStatus.ANALYZING, error_code=None, error=None)
    language = meeting.language if meeting.language in {"en", "ar"} else detect_language(" ".join(row.text for row in rows[:400]))
    parsed = [ParsedSegment(text=row.text, speaker=row.speaker, start_ms=row.start_ms, end_ms=row.end_ms) for row in rows]
    try:
        result, provenance = await analyze(
            gateway,
            title=meeting.title,
            language=language,
            meeting_date=(meeting.occurred_at or meeting.created_at).date(),
            segments=parsed,
            max_chars=settings.meeting_analysis_max_chars,
            max_tokens=settings.meeting_analysis_max_tokens,
        )
    except ModelBudgetExceededError as exc:
        _fail(context, meeting_id, "budget_exceeded", f"{exc.message} The transcript is saved; press Summarize after the reset or once the budget is raised.")
        return
    except AnalysisParseError:
        _fail(
            context,
            meeting_id,
            "analysis_failed",
            "The model's answer could not be read as meeting notes. Press Summarize to try again, or choose a more capable model in Settings > Model.",
        )
        return
    except Exception as exc:
        logger.warning("meeting_analysis_failed error_class=%s", type(exc).__name__)
        _fail(
            context,
            meeting_id,
            "model_failed",
            "The model could not be reached. Check Settings > Model, then press Summarize to try again.",
        )
        return
    with open_meeting_store(context) as store:
        current = store.get(context, meeting_id, lock=True)
        if current is None:
            return
        follow_up = current.follow_up or FollowUpEmail()
        if not follow_up.edited:
            follow_up = follow_up.model_copy(update={"subject": result.email_subject, "body": result.email_body})
        updated = current.model_copy(
            update={
                "status": MeetingStatus.READY,
                "error_code": None,
                "error": None,
                "language": language,
                "summary": result.summary,
                "decisions": result.decisions,
                "action_items": _merge_items(current.action_items, result.action_items),
                "follow_up": follow_up,
                "provenance": AnalysisProvenance(generated_at=utc_now(), **provenance),
                "updated_at": utc_now(),
            }
        )
        store.save(updated)
    _emit(context, _event("MEETING_SUMMARIZED"), updated, action_items=len(updated.action_items))


async def process_meeting(context: TenantContext, meeting_id: str, *, transcribe: bool) -> None:
    """Transcribe (recordings) then summarise; every failure ends in a visible status."""
    try:
        if transcribe and not await _transcribe(context, meeting_id):
            return
        await _analyze(context, meeting_id)
    except Exception as exc:  # the background task must never die silently
        logger.exception("meeting_processing_failed error_class=%s", type(exc).__name__)
        try:
            _fail(context, meeting_id, "processing_failed", "Processing stopped unexpectedly. Press Retry.")
        except Exception:
            logger.warning("meeting_fail_mark_failed")


# --------------------------------------------------------------------------- setup


@router.get("/setup", response_model=MeetingSetup)
async def meeting_setup(context: TenantContext = Depends(tenant_context)) -> MeetingSetup:
    """What this deployment and the caller can do: transcription, connectors, model, email."""
    require_permission(context, Permission.MEETING_READ)
    transcriber = build_transcriber(settings)
    with connectors.open_connection_store(context) as store:
        mine = {item.provider: item for item in store.list_connections(context)}
    statuses = []
    for provider in connectors.ConnectorProvider:
        problem = connectors.setup_problem(provider, settings)
        connection = mine.get(provider)
        statuses.append(
            ConnectorStatus(
                provider=provider,
                available=problem is None,
                problem=problem,
                connected=connection is not None,
                account_label=connection.account_label if connection else None,
                connected_at=connection.connected_at if connection else None,
            )
        )
    try:
        model_ready = not analysis_unavailable(analysis_gateway(context))
    except Exception:
        model_ready = False
    return MeetingSetup(
        transcription=TranscriptionSetup(
            configured=transcriber is not None,
            provider=settings.transcription_provider,
            model=transcriber.model if transcriber else None,
            target=transcriber.target if transcriber else None,
            problem=transcription_setup_problem(settings),
            max_recording_bytes=settings.meeting_recording_max_bytes,
            accepted_extensions=sorted(RECORDING_EXTENSIONS),
        ),
        max_transcript_bytes=settings.meeting_transcript_max_bytes,
        connectors=statuses,
        model_ready=model_ready,
        email_accounts=email_accounts(context),
        can_create=has_permission(context, Permission.MEETING_CREATE),
        can_manage_all=has_permission(context, Permission.MEETING_MANAGE),
    )


# --------------------------------------------------------------------------- list and import


@router.get("", response_model=MeetingPage)
def list_meetings(
    q: str | None = Query(default=None, max_length=200),
    status_filter: MeetingStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE),
    context: TenantContext = Depends(tenant_context),
) -> MeetingPage:
    """Newest first; ``q`` searches titles, summaries and transcript text."""
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        items, total = store.list_meetings(context, query=(q or "").strip() or None, status=status_filter, offset=offset, limit=limit)
    return MeetingPage(items=items, total=total, offset=offset, limit=limit)


@router.post("/recordings", response_model=Meeting, status_code=status.HTTP_202_ACCEPTED)
async def import_recording(
    request: Request,
    background: BackgroundTasks,
    name_param: str | None = Query(default=None, alias="name", min_length=1, max_length=255),
    x_file_name: str | None = Header(default=None, min_length=1, max_length=255),
    title: str | None = Query(default=None, max_length=200),
    language: str = Query(default="auto"),
    agent_skill_id: str | None = Query(default=None, max_length=160),
    occurred_at: str | None = Query(default=None, max_length=40),
    context: TenantContext = Depends(tenant_context),
) -> Meeting:
    """Store an audio or video recording as a workspace file and transcribe it."""
    _require_create(context)
    problem = transcription_setup_problem(settings)
    if problem or build_transcriber(settings) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem or "Recording transcription is not set up.")
    name = _file_name(name_param, x_file_name)
    if PurePath(name).suffix.lower() not in RECORDING_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Upload an audio or video recording (" + ", ".join(sorted(RECORDING_EXTENSIONS)) + ").")
    requested = _language(language)
    when = _occurred(occurred_at)
    agent = _check_agent(context, agent_skill_id)
    content = await request.body()
    limit = settings.meeting_recording_max_bytes
    if not content:
        raise HTTPException(status_code=422, detail="The recording is empty.")
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"Recordings are limited to {limit // (1024 * 1024)} MB on this server.")
    content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip() or "application/octet-stream"
    from .files import remove_workspace_file, store_workspace_file

    record = store_workspace_file(context, name, content, content_type)
    meeting = _new_meeting(
        context,
        title=_title(title, PurePath(name).stem),
        source=MeetingSource.RECORDING,
        language=requested,
        occurred_at=when,
        agent_skill_id=agent,
        status_=MeetingStatus.QUEUED,
        recording_file_id=record.id,
        source_file_name=name,
    )
    try:
        with open_meeting_store(context) as store:
            store.add(meeting)
    except BaseException:
        remove_workspace_file(context, record.id)
        raise
    _emit(context, _event("MEETING_IMPORTED"), meeting)
    background.add_task(process_meeting, context, meeting.id, transcribe=True)
    return meeting


def _save_transcript_meeting(
    context: TenantContext,
    parsed: list[ParsedSegment],
    *,
    title: str,
    source: MeetingSource,
    requested: str,
    occurred_at: datetime | None,
    agent: str | None,
    duration: int | None,
    **fields: Any,
) -> Meeting:
    meeting = _new_meeting(
        context,
        title=title,
        source=source,
        language=requested,
        occurred_at=occurred_at,
        agent_skill_id=agent,
        status_=MeetingStatus.ANALYZING,
        **fields,
    )
    meeting.language = _resolved_language(requested, parsed)
    meeting.segment_count = len(parsed)
    meeting.duration_seconds = duration or _duration(parsed)
    try:
        with open_meeting_store(context) as store:
            store.add(meeting)
            store.replace_segments(context, meeting.id, _segments(meeting.id, parsed))
    except DuplicateImportError:
        raise HTTPException(status_code=409, detail="This transcript is already imported in this workspace.") from None
    _emit(context, _event("MEETING_IMPORTED"), meeting)
    return meeting


@router.post("/transcripts", response_model=Meeting, status_code=status.HTTP_201_CREATED)
async def import_transcript(
    request: Request,
    background: BackgroundTasks,
    name_param: str | None = Query(default=None, alias="name", min_length=1, max_length=255),
    x_file_name: str | None = Header(default=None, min_length=1, max_length=255),
    title: str | None = Query(default=None, max_length=200),
    language: str = Query(default="auto"),
    agent_skill_id: str | None = Query(default=None, max_length=160),
    occurred_at: str | None = Query(default=None, max_length=40),
    context: TenantContext = Depends(tenant_context),
) -> Meeting:
    """A VTT, SRT, TXT or DOCX transcript; summarised in the background."""
    _require_create(context)
    name = _file_name(name_param, x_file_name)
    requested = _language(language)
    when = _occurred(occurred_at)
    agent = _check_agent(context, agent_skill_id)
    content = await request.body()
    limit = settings.meeting_transcript_max_bytes
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"Transcript files are limited to {limit // (1024 * 1024)} MB on this server.")
    try:
        parsed = parse_transcript(name, content, max_bytes=limit)
    except TranscriptParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    meeting = _save_transcript_meeting(
        context,
        parsed,
        title=_title(title, PurePath(name).stem),
        source=MeetingSource.TRANSCRIPT,
        requested=requested,
        occurred_at=when,
        agent=agent,
        duration=None,
        source_file_name=name,
    )
    background.add_task(process_meeting, context, meeting.id, transcribe=False)
    return meeting


# --------------------------------------------------------------------------- connectors


def _connector_error(exc: connectors.ConnectorError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _provider(value: str) -> connectors.ConnectorProvider:
    try:
        return connectors.ConnectorProvider(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown meeting connector") from None


def _require_available(provider: connectors.ConnectorProvider) -> None:
    problem = connectors.setup_problem(provider, settings)
    if problem:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)


@router.get("/connectors/{provider}/authorize", response_model=AuthorizeLink)
async def connector_authorize(provider: str, context: TenantContext = Depends(tenant_context)) -> AuthorizeLink:
    """The provider's sign-in page; the browser comes back to the web app's callback page."""
    _require_create(context)
    chosen = _provider(provider)
    _require_available(chosen)
    return AuthorizeLink(authorize_url=connectors.authorize_url(chosen, context, settings))


@router.post("/connectors/callback", response_model=ConnectorStatus)
async def connector_callback(payload: OAuthCallback, context: TenantContext = Depends(tenant_context)) -> ConnectorStatus:
    """Finish connecting: the state must name the caller, then the code becomes tokens."""
    _require_create(context)
    try:
        provider = connectors.decode_state(payload.state, context)
        _require_available(provider)
        tokens, label, external_user_id = await connectors.exchange_code(provider, payload.code, settings)
    except connectors.ConnectorError as exc:
        raise _connector_error(exc) from None
    now = utc_now()
    connection = connectors.StoredConnection(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        provider=provider,
        account_label=label,
        external_user_id=external_user_id,
        scopes=tokens.scopes,
        token_ciphertext=connectors.seal(tokens),
        expires_at=tokens.expires_at,
        connected_at=now,
        updated_at=now,
    )
    with connectors.open_connection_store(context) as store:
        store.save(connection)
    return ConnectorStatus(provider=provider, available=True, connected=True, account_label=label, connected_at=now)


@router.delete("/connectors/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def connector_disconnect(provider: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Forget the caller's tokens (revoke ANUM's access in Zoom or Microsoft as well)."""
    require_permission(context, Permission.MEETING_READ)
    chosen = _provider(provider)
    with connectors.open_connection_store(context) as store:
        if not store.delete(context, chosen):
            raise HTTPException(status_code=404, detail="Not connected")
    return Response(status_code=204)


@router.get("/connectors/{provider}/meetings", response_model=list[connectors.ExternalMeeting])
async def connector_meetings(
    provider: str,
    days: int = Query(default=30, ge=1, le=30),
    context: TenantContext = Depends(tenant_context),
) -> list[connectors.ExternalMeeting]:
    """The caller's recent meetings that have a transcript, marked when already imported."""
    _require_create(context)
    chosen = _provider(provider)
    _require_available(chosen)
    try:
        token, connection = await connectors.fresh_access_token(context, chosen, settings)
        found = await connectors.list_meetings(chosen, token, connection, days=days)
    except connectors.ConnectorError as exc:
        raise _connector_error(exc) from None
    source = MeetingSource(chosen.value)
    with open_meeting_store(context) as store:
        imported = store.external_ids(context, source)
    return [item.model_copy(update={"imported_meeting_id": imported.get(item.external_id)}) for item in found]


@router.post("/connectors/{provider}/import", response_model=Meeting, status_code=status.HTTP_201_CREATED)
async def connector_import(
    provider: str,
    payload: ConnectorImport,
    background: BackgroundTasks,
    context: TenantContext = Depends(tenant_context),
) -> Meeting:
    """Fetch one meeting's transcript (WebVTT) and summarise it in the background."""
    _require_create(context)
    chosen = _provider(provider)
    _require_available(chosen)
    agent = _check_agent(context, payload.agent_skill_id)
    source = MeetingSource(chosen.value)
    with open_meeting_store(context) as store:
        if payload.external_id in store.external_ids(context, source):
            raise HTTPException(status_code=409, detail="This transcript is already imported in this workspace.")
    try:
        token, connection = await connectors.fresh_access_token(context, chosen, settings)
        fetched = await connectors.fetch_transcript(
            chosen, token, connection, payload.external_id, max_bytes=settings.meeting_transcript_max_bytes
        )
    except connectors.ConnectorError as exc:
        raise _connector_error(exc) from None
    try:
        parsed = parse_cues(fetched.vtt)
    except TranscriptParseError as exc:
        raise HTTPException(status_code=422, detail=f"The {chosen.value.title()} transcript could not be read: {exc}") from None
    meeting = _save_transcript_meeting(
        context,
        parsed,
        title=_title(fetched.title, "Meeting"),
        source=source,
        requested=payload.language,
        occurred_at=fetched.started_at,
        agent=agent,
        duration=fetched.duration_seconds,
        external_id=payload.external_id,
    )
    background.add_task(process_meeting, context, meeting.id, transcribe=False)
    return meeting


# --------------------------------------------------------------------------- one meeting


@router.get("/{meeting_id}", response_model=Meeting)
def get_meeting(meeting_id: str, context: TenantContext = Depends(tenant_context)) -> Meeting:
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        return _get_or_404(store, context, meeting_id)


@router.patch("/{meeting_id}", response_model=Meeting)
def update_meeting(meeting_id: str, payload: MeetingUpdate, context: TenantContext = Depends(tenant_context)) -> Meeting:
    """Rename, link or unlink an agent, or set the language for the next Summarize."""
    require_permission(context, Permission.MEETING_READ)
    agent = _check_agent(context, payload.agent_skill_id) if payload.agent_skill_id else None
    with open_meeting_store(context) as store:
        meeting = _get_or_404(store, context, meeting_id, lock=True)
        _require_edit(context, meeting)
        changes: dict[str, Any] = {"updated_at": utc_now()}
        if payload.title is not None:
            changes["title"] = _title(payload.title, meeting.title)
        if payload.clear_agent:
            changes["agent_skill_id"] = None
        elif agent is not None:
            changes["agent_skill_id"] = agent
        if payload.language is not None:
            changes["requested_language"] = payload.language
            if payload.language in {"en", "ar"}:
                changes["language"] = payload.language
        updated = meeting.model_copy(update=changes)
        store.save(updated)
    return updated


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """The meeting, its transcript and its recording file. Tasks created from it stay."""
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        meeting = _get_or_404(store, context, meeting_id, lock=True)
        _require_edit(context, meeting)
        store.delete(context, meeting_id)
    if meeting.recording_file_id:
        from .files import remove_workspace_file

        try:
            remove_workspace_file(context, meeting.recording_file_id)
        except Exception as exc:  # the meeting is gone; the file stays listed in Files
            logger.warning("meeting_recording_delete_failed error_class=%s", type(exc).__name__)
    return Response(status_code=204)


@router.get("/{meeting_id}/transcript", response_model=TranscriptPage)
def meeting_transcript(
    meeting_id: str,
    q: str | None = Query(default=None, max_length=200),
    speaker: str | None = Query(default=None, max_length=120),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    limit: int = Query(default=100, ge=1, le=500),
    context: TenantContext = Depends(tenant_context),
) -> TranscriptPage:
    """Timed, speaker-labelled lines; ``q`` searches text and speaker names."""
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        _get_or_404(store, context, meeting_id)
        items, total = store.segments(
            context, meeting_id, query=(q or "").strip() or None, speaker=speaker or None, offset=offset, limit=limit
        )
        speakers = store.speakers(context, meeting_id)
    return TranscriptPage(items=items, total=total, offset=offset, limit=limit, speakers=speakers)


@router.post("/{meeting_id}/process", response_model=Meeting, status_code=status.HTTP_202_ACCEPTED)
def reprocess_meeting(meeting_id: str, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> Meeting:
    """Summarize again (or retry a failed transcription) in the background."""
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        meeting = _get_or_404(store, context, meeting_id, lock=True)
        _require_edit(context, meeting)
        if meeting.status in WORKING and utc_now() - meeting.updated_at < STALE_AFTER:
            raise HTTPException(status_code=409, detail="This meeting is still being processed. Wait for it to finish.")
        transcribe = meeting.source == MeetingSource.RECORDING and meeting.segment_count == 0
        if transcribe and build_transcriber(settings) is None:
            raise HTTPException(status_code=409, detail=transcription_setup_problem(settings) or "Recording transcription is not set up.")
        updated = meeting.model_copy(
            update={"status": MeetingStatus.QUEUED, "error_code": None, "error": None, "updated_at": utc_now()}
        )
        store.save(updated)
    background.add_task(process_meeting, context, meeting_id, transcribe=transcribe)
    return updated


@router.patch("/{meeting_id}/action-items/{item_id}", response_model=Meeting)
def update_action_item(
    meeting_id: str, item_id: str, payload: ActionItemUpdate, context: TenantContext = Depends(tenant_context)
) -> Meeting:
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        meeting = _get_or_404(store, context, meeting_id, lock=True)
        _require_edit(context, meeting)
        items = []
        found = False
        for item in meeting.action_items:
            if item.id == item_id:
                found = True
                changes: dict[str, Any] = {}
                if payload.text is not None:
                    changes["text"] = payload.text.strip()
                if payload.clear_owner:
                    changes["owner"] = None
                elif payload.owner is not None:
                    changes["owner"] = payload.owner.strip() or None
                if payload.clear_due_date:
                    changes["due_date"] = None
                elif payload.due_date is not None:
                    changes["due_date"] = payload.due_date
                if payload.done is not None:
                    changes["done"] = payload.done
                item = item.model_copy(update=changes)
            items.append(item)
        if not found:
            raise HTTPException(status_code=404, detail="Action item not found")
        updated = meeting.model_copy(update={"action_items": items, "updated_at": utc_now()})
        store.save(updated)
    return updated


def _create_task(context: TenantContext, title: str, prompt: str) -> str:
    from .dependencies import _unit_of_work
    from .events import CanonicalEventName, create_event
    from .schemas import Task, TaskStatus

    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=title[:160],
        prompt=prompt[:8000],
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
        created_by=context.user_id,
    )
    with _unit_of_work(context, require_membership=True) as repository:
        repository.create_task(task)
        repository.record_event(
            create_event(
                CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now
            ).event
        )
    return task.id


def _require_task_create(context: TenantContext) -> None:
    if not has_permission(context, Permission.TASK_CREATE):
        raise HTTPException(
            status_code=403, detail="Creating tasks needs the owner or member role in this workspace."
        )


@router.post("/{meeting_id}/action-items/{item_id}/task", response_model=Meeting)
def action_item_task(meeting_id: str, item_id: str, context: TenantContext = Depends(tenant_context)) -> Meeting:
    """Turn one action item into a workspace task (runs follow the approval policy)."""
    require_permission(context, Permission.MEETING_READ)
    _require_task_create(context)
    with _action_lock, open_meeting_store(context) as store:
        meeting = _get_or_404(store, context, meeting_id, lock=True)
        _require_edit(context, meeting)
        item = next((value for value in meeting.action_items if value.id == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Action item not found")
        if item.task_id:
            raise HTTPException(status_code=409, detail="This action item already has a task.")
        when = (meeting.occurred_at or meeting.created_at).date().isoformat()
        prompt = "\n".join(
            line
            for line in (
                f'Action item from the meeting "{meeting.title}" ({when}).',
                f"Owner: {item.owner}" if item.owner else "Owner: not assigned",
                f"Due: {item.due_date.isoformat()}" if item.due_date else "Due: no date",
                "",
                item.text,
            )
        )
        task_id = _create_task(context, item.text, prompt)
        items = [value.model_copy(update={"task_id": task_id}) if value.id == item_id else value for value in meeting.action_items]
        updated = meeting.model_copy(update={"action_items": items, "updated_at": utc_now()})
        store.save(updated)
    return updated


@router.put("/{meeting_id}/follow-up", response_model=Meeting)
def update_follow_up(meeting_id: str, payload: FollowUpUpdate, context: TenantContext = Depends(tenant_context)) -> Meeting:
    """Edit the follow-up draft (recipients, subject, body). Nothing is sent."""
    require_permission(context, Permission.MEETING_READ)
    with open_meeting_store(context) as store:
        meeting = _get_or_404(store, context, meeting_id, lock=True)
        _require_edit(context, meeting)
        current = meeting.follow_up or FollowUpEmail()
        follow_up = current.model_copy(
            update={"to": payload.to, "subject": payload.subject.strip(), "body": payload.body.strip(), "edited": True}
        )
        updated = meeting.model_copy(update={"follow_up": follow_up, "updated_at": utc_now()})
        store.save(updated)
    return updated


_send_lock = asyncio.Lock()
NO_MAILBOX = (
    "Connect a mailbox under Inbox first (Gmail, Outlook or Zoho), then send from it. "
    "Until then, copy the draft or open it in your mail app."
)


@router.post("/{meeting_id}/follow-up/send", response_model=Meeting)
async def send_follow_up(
    meeting_id: str, payload: FollowUpSend, context: TenantContext = Depends(tenant_context)
) -> Meeting:
    """Turn the saved draft into an email reply draft and ask to send it.

    Sending goes through the email module's own path (docs/email.md): a reply draft in the
    chosen mailbox, then a paused ``send_email`` call that an approver must approve with
    the payload hash shown. Only then is exactly this text sent to these recipients.
    """
    from .dependencies import _unit_of_work
    from .email_api import request_send_approval
    from .email_inbox import EmailDraft, open_email_store, visible_accounts

    require_permission(context, Permission.MEETING_READ)
    if not has_permission(context, Permission.EMAIL_ACT):
        raise HTTPException(status_code=403, detail="Sending email needs the owner or member role in this workspace.")
    async with _send_lock:
        with open_meeting_store(context) as store:
            meeting = _get_or_404(store, context, meeting_id)
        _require_edit(context, meeting)
        draft = meeting.follow_up
        if draft is None or not draft.to or not draft.subject or not draft.body:
            raise HTTPException(status_code=422, detail="Add at least one recipient, a subject and a body first.")
        if draft.approval_id:
            raise HTTPException(status_code=409, detail="This follow-up is already waiting for approval or was sent.")
        with open_email_store(context) as emails:
            account = next((item for item in visible_accounts(emails, context) if item.id == payload.account_id), None)
        if account is None:
            raise HTTPException(status_code=409, detail=NO_MAILBOX)
        now = utc_now()
        from_model = meeting.provenance is not None and not draft.edited
        email_draft = EmailDraft(
            id=new_id("emd"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            account_id=account.id,
            to=list(draft.to),
            subject=draft.subject,
            body=draft.body,
            source="model" if from_model else "person",
            model_provider=meeting.provenance.provider if from_model and meeting.provenance else None,
            model_name=meeting.provenance.model if from_model and meeting.provenance else None,
            created_by=context.user_id,
            created_at=now,
            updated_at=now,
        )
        with open_email_store(context) as emails:
            emails.add_draft(email_draft)
        with _unit_of_work(context, require_membership=True) as repository:
            result = await request_send_approval(email_draft.id, context, repository)
        with open_meeting_store(context) as store:
            current = _get_or_404(store, context, meeting_id, lock=True)
            follow_up = (current.follow_up or draft).model_copy(
                update={
                    "email_draft_id": email_draft.id,
                    "approval_id": result.approval.id,
                    "send_task_id": result.approval.task_id,
                    "sent_from": account.address,
                }
            )
            updated = current.model_copy(update={"follow_up": follow_up, "updated_at": utc_now()})
            store.save(updated)
    return updated
