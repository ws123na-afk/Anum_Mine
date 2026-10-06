"""PostgreSQL voice sessions and transcripts (migration 0009).

The session must carry the tenant, workspace *and user* RLS context
(``set_tenant_context(..., user_id=...)``): voice rows are private to the user who
started the session. Queries also filter on the scope explicitly.

Counters and one-shot markers are single atomic statements, so they hold across API
replicas: ``ask_count`` is incremented with ``UPDATE ... RETURNING`` (the per-session
question limit) and a segment is consumed by an ``UPDATE`` that only matches while
``consumed_at`` is null (a transcript becomes at most one task).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.maintenance import SessionFactory, discover, scoped_unit
from anum_api.schemas import TenantContext, new_id, utc_now
from anum_api.voice import (
    TranscriptRetention,
    TranscriptRole,
    TranscriptSegment,
    TranscriptSegmentCreate,
    VoiceSession,
    VoiceSessionCreate,
    VoiceSessionStatus,
    transcript_expired,
    transcript_expiry,
)

from .models import VoiceSessionRecord, VoiceTranscriptSegmentRecord
from .scope import require_workspace


def _session(record: VoiceSessionRecord) -> VoiceSession:
    return VoiceSession(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        user_id=record.user_id,
        locale=record.locale,
        retention=TranscriptRetention(record.retention),
        assistant_name=record.assistant_name,
        status=VoiceSessionStatus(record.status),
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
    )


def _segment(record: VoiceTranscriptSegmentRecord) -> TranscriptSegment:
    return TranscriptSegment(
        id=record.id,
        session_id=record.session_id,
        role=TranscriptRole(record.role),
        text=record.text,
        is_final=record.is_final,
        client_sequence=record.client_sequence,
        created_at=record.created_at,
    )


class SqlAlchemyVoiceStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scoped_session(self, session_id: str, *, lock: bool = False) -> VoiceSessionRecord | None:
        statement = select(VoiceSessionRecord).where(
            VoiceSessionRecord.tenant_id == self.context.tenant_id,
            VoiceSessionRecord.workspace_id == self.context.workspace_id,
            VoiceSessionRecord.user_id == self.context.user_id,
            VoiceSessionRecord.id == session_id,
        )
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def _touch(self, session_id: str, when: datetime) -> None:
        self.session.execute(
            update(VoiceSessionRecord)
            .where(VoiceSessionRecord.id == session_id, VoiceSessionRecord.tenant_id == self.context.tenant_id)
            .values(updated_at=when)
        )

    def create_session(self, payload: VoiceSessionCreate, context: TenantContext) -> VoiceSession:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        now = utc_now()
        record = VoiceSessionRecord(
            id=new_id("voice"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            locale=payload.locale,
            retention=payload.retention.value,
            assistant_name=payload.assistant_name.strip(),
            status=VoiceSessionStatus.ACTIVE.value,
            ask_count=0,
            created_at=now,
            updated_at=now,
            expires_at=transcript_expiry(payload.retention, now),
        )
        self.session.add(record)
        self.session.flush()
        return _session(record)

    def get_session(self, session_id: str, context: TenantContext, *, lock: bool = False) -> VoiceSession | None:
        # ``lock`` takes the row lock that orders appends, replies and closing.
        record = self._scoped_session(session_id, lock=lock)
        return _session(record) if record is not None else None

    def list_transcript(self, session: VoiceSession) -> list[TranscriptSegment]:
        if transcript_expired(session):
            return []
        records = self.session.scalars(
            select(VoiceTranscriptSegmentRecord)
            .where(
                VoiceTranscriptSegmentRecord.tenant_id == self.context.tenant_id,
                VoiceTranscriptSegmentRecord.session_id == session.id,
            )
            .order_by(VoiceTranscriptSegmentRecord.client_sequence, VoiceTranscriptSegmentRecord.created_at)
        ).all()
        return [_segment(record) for record in records]

    def _insert_segment(
        self, session: VoiceSession, *, role: TranscriptRole, text_value: str, is_final: bool, sequence: int
    ) -> TranscriptSegment:
        now = utc_now()
        record = VoiceTranscriptSegmentRecord(
            id=new_id("transcript"),
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            user_id=session.user_id,
            session_id=session.id,
            role=role.value,
            text=text_value,
            is_final=is_final,
            client_sequence=sequence,
            created_at=now,
        )
        self.session.add(record)
        self.session.flush()
        self._touch(session.id, now)
        return _segment(record)

    def add_segment(self, session: VoiceSession, payload: TranscriptSegmentCreate) -> TranscriptSegment:
        taken = self.session.scalar(
            select(VoiceTranscriptSegmentRecord.id).where(
                VoiceTranscriptSegmentRecord.tenant_id == self.context.tenant_id,
                VoiceTranscriptSegmentRecord.session_id == session.id,
                VoiceTranscriptSegmentRecord.client_sequence == payload.client_sequence,
            ).limit(1)
        )
        if taken is not None:
            raise ValueError("Transcript sequence already exists")
        try:
            with self.session.begin_nested():
                return self._insert_segment(
                    session,
                    role=payload.role,
                    text_value=payload.text,
                    is_final=payload.is_final,
                    sequence=payload.client_sequence,
                )
        except IntegrityError as exc:
            # A concurrent request on another replica used the same sequence number.
            raise ValueError("Transcript sequence already exists") from exc

    def add_assistant_reply(self, session: VoiceSession, text_value: str, sequence: int) -> TranscriptSegment:
        return self._insert_segment(
            session, role=TranscriptRole.ASSISTANT, text_value=text_value, is_final=True, sequence=sequence
        )

    def get_segment(self, session_id: str, segment_id: str) -> TranscriptSegment | None:
        session = self.get_session(session_id, self.context)
        if session is None or transcript_expired(session):
            return None
        record = self.session.scalar(
            select(VoiceTranscriptSegmentRecord).where(
                VoiceTranscriptSegmentRecord.tenant_id == self.context.tenant_id,
                VoiceTranscriptSegmentRecord.session_id == session_id,
                VoiceTranscriptSegmentRecord.id == segment_id,
            )
        )
        return _segment(record) if record is not None else None

    def consume_segment(self, segment_id: str) -> None:
        consumed = self.session.execute(
            update(VoiceTranscriptSegmentRecord)
            .where(
                VoiceTranscriptSegmentRecord.tenant_id == self.context.tenant_id,
                VoiceTranscriptSegmentRecord.id == segment_id,
                VoiceTranscriptSegmentRecord.consumed_at.is_(None),
            )
            .values(consumed_at=utc_now())
            .returning(VoiceTranscriptSegmentRecord.id)
        ).scalar_one_or_none()
        if consumed is None:
            raise ValueError("Transcript segment already submitted")

    def count_ask(self, session_id: str) -> int:
        count = self.session.execute(
            update(VoiceSessionRecord)
            .where(
                VoiceSessionRecord.tenant_id == self.context.tenant_id,
                VoiceSessionRecord.workspace_id == self.context.workspace_id,
                VoiceSessionRecord.user_id == self.context.user_id,
                VoiceSessionRecord.id == session_id,
            )
            .values(ask_count=VoiceSessionRecord.ask_count + 1)
            .returning(VoiceSessionRecord.ask_count)
        ).scalar_one_or_none()
        if count is None:
            raise KeyError(session_id)
        return int(count)

    def close(self, session: VoiceSession, final_status: VoiceSessionStatus) -> VoiceSession:
        now = utc_now()
        record = self._scoped_session(session.id)
        if record is None:
            raise KeyError(session.id)
        record.status = final_status.value
        record.updated_at = now
        if session.retention == TranscriptRetention.SESSION:
            # Session-only transcripts are deleted in the same transaction that closes it.
            self.session.execute(
                delete(VoiceTranscriptSegmentRecord).where(
                    VoiceTranscriptSegmentRecord.tenant_id == self.context.tenant_id,
                    VoiceTranscriptSegmentRecord.session_id == session.id,
                )
            )
            record.transcript_purged_at = now
        self.session.flush()
        return _session(record)


# Retention purge ---------------------------------------------------------------------

# Runs as anum_maintenance: its policy shows only sessions whose 30-day transcript has
# expired and was not purged yet; its grants cover these columns only.
_EXPIRED_SESSIONS = text(
    """
    select id, tenant_id, workspace_id, user_id
    from voice_sessions
    order by expires_at, id
    limit :limit
    """
)
_COUNT_EXPIRED_SEGMENTS = text(
    """
    select count(*) from voice_transcript_segments s
    join voice_sessions v on v.tenant_id = s.tenant_id and v.id = s.session_id
    where v.id = any(:ids) and v.expires_at <= now() and v.transcript_purged_at is null
    """
)
_DELETE_EXPIRED_SEGMENTS = text(
    """
    delete from voice_transcript_segments s
    using voice_sessions v
    where v.tenant_id = s.tenant_id and v.id = s.session_id
      and v.id = any(:ids) and v.expires_at <= now() and v.transcript_purged_at is null
    """
)
_MARK_PURGED = text(
    """
    update voice_sessions set transcript_purged_at = now()
    where id = any(:ids) and expires_at <= now() and transcript_purged_at is null
    """
)


@dataclass
class PurgeResult:
    sessions: int = 0
    segments: int = 0
    dry_run: bool = False


def purge_expired_transcripts(
    session_factory: SessionFactory,
    *,
    maintenance_session_factory: SessionFactory | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> PurgeResult:
    """Delete transcripts whose 30-day retention has passed, scope by scope.

    Discovery runs as ``anum_maintenance`` (session ids and scopes only); each delete runs
    as the application role inside that user's tenant, workspace and user RLS context.
    """
    result = PurgeResult(dry_run=dry_run)
    seen: set[str] = set()
    while True:
        rows = discover(maintenance_session_factory or session_factory, _EXPIRED_SESSIONS, {"limit": batch_size})
        fresh = [row for row in rows if row.id not in seen]
        if not fresh:
            return result
        scopes: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for row in fresh:
            seen.add(row.id)
            scopes[(row.tenant_id, row.workspace_id, row.user_id)].append(row.id)
        for (tenant_id, workspace_id, user_id), ids in scopes.items():
            with scoped_unit(session_factory, tenant_id, workspace_id, user_id=user_id) as session:
                if dry_run:
                    result.segments += int(session.execute(_COUNT_EXPIRED_SEGMENTS, {"ids": ids}).scalar_one())
                    result.sessions += len(ids)
                    session.rollback()
                    continue
                result.segments += session.execute(_DELETE_EXPIRED_SEGMENTS, {"ids": ids}).rowcount or 0
                result.sessions += session.execute(_MARK_PURGED, {"ids": ids}).rowcount or 0
        if len(rows) < batch_size:
            return result
