"""Voice sessions, transcripts and the spoken assistant.

Sessions and transcript segments are private to the user who started them. With
``ANUM_REPOSITORY_BACKEND=memory`` they live in process memory (``VoiceStore``); with
``postgresql`` they live in the ``voice_sessions`` and ``voice_transcript_segments``
tables under tenant, workspace and user RLS (migration 0009,
``anum_api.db.voice_repository``). Transcript retention (docs/voice.md):

- ``session``: the transcript is deleted when the session is completed or cancelled.
- ``30_days``: hidden from every read once ``expires_at`` passes and deleted by
  ``python -m anum_api.voice_retention`` (run it at least daily).
- ``permanent``: kept until the session's workspace data is deleted.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import repository_context, require_permission, tenant_context
from .model_gateway import ModelGateway, ModelRoute, build_default_model_gateway, route_gateway
from .model_budget import ModelBudgetExceededError
from .onboarding import budgeted_model_gateway
from .repository import AnumRepository
from .schemas import Task, TaskStatus, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .sse import SSE_HEADERS, sse
from .tyre.tools import tyre_grounding
from .voice_assistant import (
    MAX_REPLY_CHARS,
    SMALL_TALK,
    VoiceIntent,
    VoiceRiskTier,
    WorkspaceSnapshot,
    answer_question,
    classify,
    confirm_reply,
    mock_answer,
    snapshot,
    voice_messages,
    status_reply,
    small_talk_reply,
    strip_wake_word,
    visual_only_reply,
)

logger = logging.getLogger("anum.voice")

MAX_ASKS_PER_SESSION = 60
# Spoken replies are two or three sentences; the cap keeps a slow model from rambling.
VOICE_MAX_TOKENS = 400
TRANSCRIPT_RETENTION_DAYS = 30


class VoiceSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TranscriptRetention(StrEnum):
    SESSION = "session"
    THIRTY_DAYS = "30_days"
    PERMANENT = "permanent"


class TranscriptRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class VoiceSessionCreate(BaseModel):
    locale: str = Field(default="en-US", min_length=2, max_length=35)
    retention: TranscriptRetention = TranscriptRetention.SESSION
    assistant_name: str = Field(default="Anum", min_length=1, max_length=40, pattern=r"^[\w .'-]+$")


class TranscriptSegmentCreate(BaseModel):
    role: TranscriptRole = TranscriptRole.USER
    text: str = Field(min_length=1, max_length=8000)
    is_final: bool = True
    client_sequence: int = Field(ge=0)


class VoiceCommandCreate(BaseModel):
    transcript_segment_id: str
    title: str | None = Field(default=None, min_length=1, max_length=160)


class TranscriptSegment(BaseModel):
    id: str
    session_id: str
    role: TranscriptRole
    text: str
    is_final: bool
    client_sequence: int
    created_at: datetime


class VoiceSession(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    locale: str
    retention: TranscriptRetention
    assistant_name: str = "Anum"
    status: VoiceSessionStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class VoiceCommandResult(BaseModel):
    session: VoiceSession
    task: Task
    transcript_segment_id: str


class VoiceAskCreate(BaseModel):
    transcript_segment_id: str


class VoiceAskResult(BaseModel):
    intent: VoiceIntent
    risk_tier: VoiceRiskTier
    reply: str
    proposed_task: str | None = None
    workspace: WorkspaceSnapshot
    assistant_segment: TranscriptSegment


def transcript_expiry(retention: TranscriptRetention, created_at: datetime) -> datetime | None:
    if retention == TranscriptRetention.THIRTY_DAYS:
        return created_at + timedelta(days=TRANSCRIPT_RETENTION_DAYS)
    return None


def transcript_expired(session: VoiceSession, now: datetime | None = None) -> bool:
    """A 30-day transcript is unreadable from ``expires_at`` on, purged or not."""
    return session.expires_at is not None and session.expires_at <= (now or utc_now())


class VoiceSessionStore(Protocol):
    def create_session(self, payload: VoiceSessionCreate, context: TenantContext) -> VoiceSession: ...
    def get_session(self, session_id: str, context: TenantContext, *, lock: bool = False) -> VoiceSession | None: ...
    def list_transcript(self, session: VoiceSession) -> list[TranscriptSegment]: ...
    def add_segment(self, session: VoiceSession, payload: TranscriptSegmentCreate) -> TranscriptSegment: ...
    def add_assistant_reply(self, session: VoiceSession, text: str, sequence: int) -> TranscriptSegment: ...
    def get_segment(self, session_id: str, segment_id: str) -> TranscriptSegment | None: ...
    def consume_segment(self, segment_id: str) -> None: ...
    def count_ask(self, session_id: str) -> int: ...
    def close(self, session: VoiceSession, final_status: VoiceSessionStatus) -> VoiceSession: ...


class VoiceStore:
    """Thread-safe in-process store for ``ANUM_REPOSITORY_BACKEND=memory``."""

    def __init__(self) -> None:
        self.sessions: dict[str, VoiceSession] = {}
        self.segments: dict[str, list[TranscriptSegment]] = {}
        self.consumed_segments: set[str] = set()
        self.ask_counts: dict[str, int] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.sessions.clear()
            self.segments.clear()
            self.consumed_segments.clear()
            self.ask_counts.clear()

    def count_ask(self, session_id: str) -> int:
        with self._lock:
            self.ask_counts[session_id] = self.ask_counts.get(session_id, 0) + 1
            return self.ask_counts[session_id]

    def add_assistant_reply(self, session: VoiceSession, text: str, sequence: int) -> TranscriptSegment:
        with self._lock:
            segment = TranscriptSegment(
                id=new_id("transcript"),
                session_id=session.id,
                role=TranscriptRole.ASSISTANT,
                text=text,
                is_final=True,
                client_sequence=sequence,
                created_at=utc_now(),
            )
            self.segments[session.id].append(segment)
            session.updated_at = segment.created_at
            return segment

    def create_session(self, payload: VoiceSessionCreate, context: TenantContext) -> VoiceSession:
        now = utc_now()
        session = VoiceSession(
            id=new_id("voice"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            locale=payload.locale,
            assistant_name=payload.assistant_name.strip(),
            retention=payload.retention,
            status=VoiceSessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=transcript_expiry(payload.retention, now),
        )
        with self._lock:
            self.sessions[session.id] = session
            self.segments[session.id] = []
        return session

    def get_session(self, session_id: str, context: TenantContext, *, lock: bool = False) -> VoiceSession | None:
        # ``lock`` matters to the PostgreSQL store only; this store serialises with _lock.
        session = self.sessions.get(session_id)
        if not session or (
            session.tenant_id,
            session.workspace_id,
            session.user_id,
        ) != (context.tenant_id, context.workspace_id, context.user_id):
            return None
        return session

    def list_transcript(self, session: VoiceSession) -> list[TranscriptSegment]:
        if transcript_expired(session):
            return []
        with self._lock:
            return sorted(self.segments.get(session.id, []), key=lambda item: item.client_sequence)

    def add_segment(
        self,
        session: VoiceSession,
        payload: TranscriptSegmentCreate,
    ) -> TranscriptSegment:
        with self._lock:
            segments = self.segments[session.id]
            if any(item.client_sequence == payload.client_sequence for item in segments):
                raise ValueError("Transcript sequence already exists")
            segment = TranscriptSegment(
                id=new_id("transcript"),
                session_id=session.id,
                role=payload.role,
                text=payload.text,
                is_final=payload.is_final,
                client_sequence=payload.client_sequence,
                created_at=utc_now(),
            )
            segments.append(segment)
            session.updated_at = segment.created_at
            return segment

    def get_segment(self, session_id: str, segment_id: str) -> TranscriptSegment | None:
        session = self.sessions.get(session_id)
        if session is None or transcript_expired(session):
            return None
        return next((item for item in self.segments.get(session_id, []) if item.id == segment_id), None)

    def consume_segment(self, segment_id: str) -> None:
        with self._lock:
            if segment_id in self.consumed_segments:
                raise ValueError("Transcript segment already submitted")
            self.consumed_segments.add(segment_id)

    def close(self, session: VoiceSession, final_status: VoiceSessionStatus) -> VoiceSession:
        with self._lock:
            session.status = final_status
            session.updated_at = utc_now()
            if session.retention == TranscriptRetention.SESSION:
                self.segments[session.id] = []
            return session

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete expired 30-day transcripts; returns the number of segments removed."""
        removed = 0
        with self._lock:
            for session in self.sessions.values():
                if transcript_expired(session, now) and self.segments.get(session.id):
                    removed += len(self.segments[session.id])
                    self.segments[session.id] = []
        return removed


voice_store = VoiceStore()


@contextmanager
def open_voice_store(context: TenantContext) -> Iterator[VoiceSessionStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.voice_repository import SqlAlchemyVoiceStore

        return SqlAlchemyVoiceStore(session, context)

    with open_scoped_store(context, voice_store, sql_store, user_scoped=True) as store:
        yield store


router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
_gateway: ModelGateway | None = None


def _default_voice_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = build_default_model_gateway()
    return _gateway


def voice_model_gateway(context: TenantContext = Depends(tenant_context)) -> ModelGateway:
    """Answer with the model this workspace chose in Settings, else the server default."""
    return budgeted_model_gateway(context, _default_voice_gateway())


def _session_or_404(
    store: VoiceSessionStore,
    session_id: str,
    context: TenantContext,
    *,
    active: bool = False,
    lock: bool = False,
) -> VoiceSession:
    session = store.get_session(session_id, context, lock=lock)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice session not found")
    if active and session.status != VoiceSessionStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice session is closed")
    return session


def _final_user_segment(store: VoiceSessionStore, session: VoiceSession, segment_id: str) -> TranscriptSegment:
    segment = store.get_segment(session.id, segment_id)
    if not segment or segment.role != TranscriptRole.USER or not segment.is_final:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A final user transcript segment is required",
        )
    return segment


@router.post("/sessions", response_model=VoiceSession, status_code=status.HTTP_201_CREATED)
async def create_voice_session(
    payload: VoiceSessionCreate,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_CREATE)
    with open_voice_store(context) as store:
        return store.create_session(payload, context)


@router.get("/sessions/{session_id}", response_model=VoiceSession)
async def get_voice_session(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_READ)
    with open_voice_store(context) as store:
        return _session_or_404(store, session_id, context)


@router.get("/sessions/{session_id}/transcript", response_model=list[TranscriptSegment])
async def get_voice_transcript(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> list[TranscriptSegment]:
    require_permission(context, Permission.TASK_READ)
    with open_voice_store(context) as store:
        return store.list_transcript(_session_or_404(store, session_id, context))


@router.post(
    "/sessions/{session_id}/transcript",
    response_model=TranscriptSegment,
    status_code=status.HTTP_201_CREATED,
)
async def append_voice_transcript(
    session_id: str,
    payload: TranscriptSegmentCreate,
    context: TenantContext = Depends(tenant_context),
) -> TranscriptSegment:
    require_permission(context, Permission.TASK_CREATE)
    with open_voice_store(context) as store:
        # The row lock orders this append against a concurrent complete/cancel, so a
        # session-only transcript cannot gain a segment after it was erased.
        session = _session_or_404(store, session_id, context, active=True, lock=True)
        try:
            return store.add_segment(session, payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/commands", response_model=VoiceCommandResult)
async def submit_voice_command(
    session_id: str,
    payload: VoiceCommandCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> VoiceCommandResult:
    require_permission(context, Permission.TASK_CREATE)
    with open_voice_store(context) as store:
        session = _session_or_404(store, session_id, context, active=True, lock=True)
        segment = _final_user_segment(store, session, payload.transcript_segment_id)
        try:
            store.consume_segment(segment.id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=payload.title or segment.text[:160],
        prompt=segment.text,
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
        created_by=context.user_id,
    )
    repository.create_task(task)
    return VoiceCommandResult(session=session, task=task, transcript_segment_id=segment.id)


@router.post("/sessions/{session_id}/ask", response_model=VoiceAskResult)
async def ask_voice_assistant(
    session_id: str,
    payload: VoiceAskCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    gateway: ModelGateway = Depends(voice_model_gateway),
) -> VoiceAskResult:
    """Answer a spoken question. Read-only: it never changes tasks or approvals."""
    asked = _prepare_ask(session_id, payload, context, repository)
    reply = asked.reply
    grounding = await tyre_grounding(context, asked.text) if reply is None else None
    if grounding is not None and getattr(gateway, "provider", "") == "mock":
        # No model to phrase it: say the tool's own answer, numbers included.
        reply = grounding.result.summary[:MAX_REPLY_CHARS]
    if reply is None:
        try:
            reply = await answer_question(
                gateway, asked.text, asked.facts, asked.name, asked.arabic,
                data_blocks=[grounding.block] if grounding else (),
            )
        except ModelBudgetExceededError as exc:
            # Said aloud instead of an error: the monthly model budget is used up.
            reply = exc.spoken(asked.arabic)

    assistant_segment = _store_voice_reply(context, session_id, reply, asked.segment.client_sequence)
    if assistant_segment is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice session is closed")
    return VoiceAskResult(
        intent=asked.intent,
        risk_tier=asked.tier,
        reply=reply,
        proposed_task=asked.proposal,
        workspace=asked.facts,
        assistant_segment=assistant_segment,
    )


@dataclass
class _PreparedAsk:
    session: VoiceSession
    segment: TranscriptSegment
    history: list[tuple[str, str]]
    facts: WorkspaceSnapshot
    text: str
    name: str
    arabic: bool
    intent: VoiceIntent
    tier: VoiceRiskTier
    proposal: str | None
    # A fixed reply (status, small talk, refusals); None when the model must answer.
    reply: str | None


def _marketing_reply(context: TenantContext, text: str, arabic: bool) -> str:
    """The marketing summary tool's answer, spoken (read-only, from the last pulled numbers)."""
    from .authorization import Permission
    from .dependencies import has_permission
    from .marketing.tools import days_in, platform_in, summary_for

    if not has_permission(context, Permission.MARKETING_READ):
        return "أرقام التسويق متاحة لأعضاء مساحة العمل فقط." if arabic else "Marketing numbers are for members of this workspace."
    try:
        return summary_for(context, platform=platform_in(text), days=days_in(text), arabic=arabic)[:MAX_REPLY_CHARS]
    except Exception:
        logger.warning("voice_marketing_summary_failed", extra={"anum_voice": {"event": "marketing_summary_failed"}})
        return "لم أستطع قراءة أرقام التسويق الآن. افتح صفحة التسويق." if arabic else "I couldn't read the marketing numbers just now. Open Marketing to see them."


def _prepare_ask(
    session_id: str,
    payload: VoiceAskCreate,
    context: TenantContext,
    repository: AnumRepository,
) -> _PreparedAsk:
    require_permission(context, Permission.TASK_READ)
    # No transaction stays open while the model answers: the checks and the counter
    # commit first, the reply is stored in a second unit of work.
    with open_voice_store(context) as store:
        session = _session_or_404(store, session_id, context, active=True)
        segment = _final_user_segment(store, session, payload.transcript_segment_id)
        history = [
            (item.role.value, item.text)
            for item in store.list_transcript(session)
            if item.is_final and item.client_sequence < segment.client_sequence
        ]
        # Atomic per-session counter (a single UPDATE in PostgreSQL), shared by replicas.
        asked = store.count_ask(session.id)
    if asked > MAX_ASKS_PER_SESSION:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Voice question limit reached")

    arabic = session.locale.lower().startswith("ar")
    name = session.assistant_name
    facts = snapshot(
        repository.list_tasks(context),
        [approval.status for approval in repository.list_approvals(context)],
    )
    text = strip_wake_word(segment.text, name)
    intent, proposal = classify(text, name)
    reply: str | None = None
    tier = VoiceRiskTier.READ
    if intent == VoiceIntent.VISUAL_ONLY:
        tier, reply = VoiceRiskTier.VISUAL_ONLY, visual_only_reply(arabic)
    elif intent == VoiceIntent.CREATE_TASK:
        tier, reply = VoiceRiskTier.CONFIRM, confirm_reply(proposal, arabic)
    elif intent == VoiceIntent.STATUS:
        reply = status_reply(facts, arabic)
    elif intent == VoiceIntent.EMAIL:
        # Read-only inbox facts from the synced metadata (docs/email.md#voice).
        from .email_inbox import voice_email_reply

        reply = voice_email_reply(context, text, arabic)
    elif intent == VoiceIntent.MARKETING_SUMMARY:
        reply = _marketing_reply(context, text, arabic)
    elif intent in SMALL_TALK:
        reply = small_talk_reply(intent, name, facts, arabic)
    elif intent == VoiceIntent.QUESTION:
        # "How were Amazon sales today?" is answered from the synced data (docs/commerce.md).
        from .commerce.voice import commerce_voice_reply

        commerce = commerce_voice_reply(context, text, arabic)
        if commerce is not None:
            tier_value, reply, proposal = commerce
            tier = VoiceRiskTier(tier_value)
            if proposal is not None:
                intent = VoiceIntent.CREATE_TASK
    return _PreparedAsk(
        session=session,
        segment=segment,
        history=history,
        facts=facts,
        text=text,
        name=name,
        arabic=arabic,
        intent=intent,
        tier=tier,
        proposal=proposal,
        reply=reply,
    )


def _store_voice_reply(
    context: TenantContext, session_id: str, reply: str, sequence: int
) -> TranscriptSegment | None:
    """Store the reply in its own unit of work; None when the session closed meanwhile
    (a session-only transcript is already erased, so nothing is written back)."""
    with open_voice_store(context) as store:
        session = store.get_session(session_id, context, lock=True)
        if session is None or session.status != VoiceSessionStatus.ACTIVE:
            return None
        return store.add_assistant_reply(session, reply, sequence)


@router.post("/sessions/{session_id}/ask/stream")
async def stream_voice_assistant(
    session_id: str,
    payload: VoiceAskCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    gateway: ModelGateway = Depends(voice_model_gateway),
) -> StreamingResponse:
    """The same answer as ``/ask``, streamed as server-sent events so speech can start
    with the first sentence: ``meta`` (intent, risk tier, proposal, workspace), ``delta``
    (reply text), then ``done`` (the stored assistant segment). Read-only, like ``/ask``.

    The body reads nothing through the request's repository session: everything it
    needs is gathered before the response starts and the reply is stored in its own
    unit of work (a partial reply too, when the client stops listening).
    """
    asked = _prepare_ask(session_id, payload, context, repository)
    events = stream_prepared_answer(asked, context, session_id, gateway)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def stream_prepared_answer(
    asked: _PreparedAsk,
    context: TenantContext,
    session_id: str,
    gateway: ModelGateway,
    *,
    vision: ModelGateway | None = None,
    extra_rules: str = "",
) -> AsyncIterator[str]:
    """The server-sent events of one spoken answer (``/ask/stream`` and the call's
    ``/look/stream``): ``meta``, ``delta`` pieces, then ``done`` or ``error``.

    ``vision`` is a gateway with a camera frame attached (voice_call.py). Only a
    question reaches it: a fixed reply (status, small talk, refusals, task proposals)
    never sends the frame anywhere.
    """
    sequence = asked.segment.client_sequence

    async def answer() -> AsyncIterator[str]:
        looking = vision is not None and asked.reply is None
        # Tyre data for a tyre question (its own store session, never the request's).
        grounding = await tyre_grounding(context, asked.text) if asked.reply is None else None
        yield sse(
            "meta",
            {
                "intent": asked.intent.value,
                "risk_tier": asked.tier.value,
                "proposed_task": asked.proposal,
                "workspace": asked.facts.model_dump(mode="json"),
                "vision": looking,
            },
        )
        fixed = asked.reply
        if fixed is None and not looking and getattr(gateway, "provider", "") == "mock":
            fixed = grounding.result.summary[:MAX_REPLY_CHARS] if grounding else mock_answer(asked.facts, asked.arabic)
        parts: list[str] = []
        if fixed is not None:
            parts.append(fixed)
            yield sse("delta", {"text": fixed})
        else:
            try:
                messages = voice_messages(
                    asked.text, asked.facts, asked.name, asked.history,
                    data_blocks=[grounding.block] if grounding else (),
                )
                if extra_rules:
                    messages[0] = {"role": "system", "content": f"{messages[0]['content']}\n{extra_rules}"}
                answering = vision if looking and vision is not None else route_gateway(gateway, ModelRoute.FAST)
                async for chunk in answering.stream_chat(messages, max_tokens=VOICE_MAX_TOKENS):
                    room = MAX_REPLY_CHARS - sum(len(part) for part in parts)
                    if room <= 0:
                        break
                    parts.append(chunk[:room])
                    yield sse("delta", {"text": chunk[:room]})
            except (asyncio.CancelledError, GeneratorExit):
                if parts:
                    _store_voice_reply(context, session_id, "".join(parts).strip(), sequence)
                raise
            except ModelBudgetExceededError as exc:
                spoken = exc.spoken(asked.arabic)
                parts.append(spoken)
                yield sse("delta", {"text": spoken})
            except Exception as exc:  # only the class name is logged
                logger.warning("voice_stream_failed error_class=%s", type(exc).__name__)
                if not parts:
                    yield sse("error", {"code": "model_unavailable", "message": "I could not answer. Try again in a moment."})
                    return
        segment = _store_voice_reply(context, session_id, "".join(parts).strip(), sequence)
        yield sse("done", {"assistant_segment": segment.model_dump(mode="json") if segment else None})

    return answer()


@router.post("/sessions/{session_id}/complete", response_model=VoiceSession)
async def complete_voice_session(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_CREATE)
    with open_voice_store(context) as store:
        session = _session_or_404(store, session_id, context, active=True, lock=True)
        return store.close(session, VoiceSessionStatus.COMPLETED)


@router.delete("/sessions/{session_id}", response_model=VoiceSession)
async def cancel_voice_session(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_CREATE)
    with open_voice_store(context) as store:
        session = _session_or_404(store, session_id, context, active=True, lock=True)
        return store.close(session, VoiceSessionStatus.CANCELLED)
