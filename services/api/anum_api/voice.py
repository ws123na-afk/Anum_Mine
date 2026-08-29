from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import repository_context, require_permission, tenant_context
from .repository import AnumRepository
from .schemas import Task, TaskStatus, TenantContext, new_id, utc_now


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
    status: VoiceSessionStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class VoiceCommandResult(BaseModel):
    session: VoiceSession
    task: Task
    transcript_segment_id: str


class VoiceStore:
    """Thread-safe ephemeral store; production adapters can preserve the same contract."""

    def __init__(self) -> None:
        self.sessions: dict[str, VoiceSession] = {}
        self.segments: dict[str, list[TranscriptSegment]] = {}
        self.consumed_segments: set[str] = set()
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.sessions.clear()
            self.segments.clear()
            self.consumed_segments.clear()

    def create_session(self, payload: VoiceSessionCreate, context: TenantContext) -> VoiceSession:
        now = utc_now()
        expires_at = now + timedelta(days=30) if payload.retention == TranscriptRetention.THIRTY_DAYS else None
        session = VoiceSession(
            id=new_id("voice"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            locale=payload.locale,
            retention=payload.retention,
            status=VoiceSessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        with self._lock:
            self.sessions[session.id] = session
            self.segments[session.id] = []
        return session

    def get_session(self, session_id: str, context: TenantContext) -> VoiceSession | None:
        session = self.sessions.get(session_id)
        if not session or (
            session.tenant_id,
            session.workspace_id,
            session.user_id,
        ) != (context.tenant_id, context.workspace_id, context.user_id):
            return None
        return session

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


voice_store = VoiceStore()
router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


def _session_or_404(session_id: str, context: TenantContext) -> VoiceSession:
    session = voice_store.get_session(session_id, context)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice session not found")
    return session


@router.post("/sessions", response_model=VoiceSession, status_code=status.HTTP_201_CREATED)
async def create_voice_session(
    payload: VoiceSessionCreate,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_CREATE)
    return voice_store.create_session(payload, context)


@router.get("/sessions/{session_id}", response_model=VoiceSession)
async def get_voice_session(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_READ)
    return _session_or_404(session_id, context)


@router.get("/sessions/{session_id}/transcript", response_model=list[TranscriptSegment])
async def get_voice_transcript(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> list[TranscriptSegment]:
    require_permission(context, Permission.TASK_READ)
    session = _session_or_404(session_id, context)
    return sorted(voice_store.segments[session.id], key=lambda item: item.client_sequence)


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
    session = _session_or_404(session_id, context)
    if session.status != VoiceSessionStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice session is closed")
    try:
        return voice_store.add_segment(session, payload)
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
    session = _session_or_404(session_id, context)
    if session.status != VoiceSessionStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice session is closed")
    segment = voice_store.get_segment(session.id, payload.transcript_segment_id)
    if not segment or segment.role != TranscriptRole.USER or not segment.is_final:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A final user transcript segment is required",
        )
    try:
        voice_store.consume_segment(segment.id)
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
    )
    repository.create_task(task)
    return VoiceCommandResult(session=session, task=task, transcript_segment_id=segment.id)


@router.post("/sessions/{session_id}/complete", response_model=VoiceSession)
async def complete_voice_session(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_CREATE)
    session = _session_or_404(session_id, context)
    if session.status != VoiceSessionStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice session is closed")
    return voice_store.close(session, VoiceSessionStatus.COMPLETED)


@router.delete("/sessions/{session_id}", response_model=VoiceSession)
async def cancel_voice_session(
    session_id: str,
    context: TenantContext = Depends(tenant_context),
) -> VoiceSession:
    require_permission(context, Permission.TASK_CREATE)
    session = _session_or_404(session_id, context)
    if session.status != VoiceSessionStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice session is closed")
    return voice_store.close(session, VoiceSessionStatus.CANCELLED)
