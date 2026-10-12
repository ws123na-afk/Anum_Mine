"""PostgreSQL stores for Calls & Meetings (migration ``0043_meetings``, docs/meetings.md).

Sessions come from ``open_scoped_store``, so the tenant and workspace RLS context (and
for connector accounts the user) is already set; every statement still names the scope.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.meeting_connectors import ConnectorProvider, StoredConnection
from anum_api.meetings import (
    ActionItem,
    AnalysisProvenance,
    DuplicateImportError,
    FollowUpEmail,
    Meeting,
    MeetingSource,
    MeetingStatus,
    TranscriptSegment,
)
from anum_api.schemas import TenantContext

from .models import MeetingConnectorAccountRecord, MeetingRecord, MeetingTranscriptSegmentRecord
from .scope import require_workspace


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _meeting(record: MeetingRecord) -> Meeting:
    return Meeting(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        created_by=record.created_by,
        title=record.title,
        source=MeetingSource(record.source),
        status=MeetingStatus(record.status),
        language=record.language,
        requested_language=record.requested_language,
        occurred_at=record.occurred_at,
        duration_seconds=record.duration_seconds,
        agent_skill_id=record.agent_skill_id,
        recording_file_id=record.recording_file_id,
        source_file_name=record.source_file_name,
        external_id=record.external_id,
        segment_count=record.segment_count,
        error_code=record.error_code,
        error=record.error,
        summary=record.summary,
        decisions=list(record.decisions or []),
        action_items=[ActionItem.model_validate(item) for item in record.action_items or []],
        follow_up=FollowUpEmail.model_validate(record.follow_up) if record.follow_up else None,
        provenance=AnalysisProvenance.model_validate(record.provenance) if record.provenance else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _apply(record: MeetingRecord, meeting: Meeting) -> None:
    record.title = meeting.title
    record.source = meeting.source.value
    record.status = meeting.status.value
    record.language = meeting.language
    record.requested_language = meeting.requested_language
    record.occurred_at = meeting.occurred_at
    record.duration_seconds = meeting.duration_seconds
    record.agent_skill_id = meeting.agent_skill_id
    record.recording_file_id = meeting.recording_file_id
    record.source_file_name = meeting.source_file_name
    record.external_id = meeting.external_id
    record.segment_count = meeting.segment_count
    record.error_code = meeting.error_code
    record.error = meeting.error
    record.summary = meeting.summary
    record.decisions = list(meeting.decisions)
    record.action_items = [item.model_dump(mode="json") for item in meeting.action_items]
    record.follow_up = meeting.follow_up.model_dump(mode="json") if meeting.follow_up else None
    record.provenance = meeting.provenance.model_dump(mode="json") if meeting.provenance else None
    record.updated_at = meeting.updated_at


def _segment(record: MeetingTranscriptSegmentRecord) -> TranscriptSegment:
    return TranscriptSegment(
        id=record.id,
        meeting_id=record.meeting_id,
        seq=record.seq,
        speaker=record.speaker,
        start_ms=record.start_ms,
        end_ms=record.end_ms,
        text=record.text,
    )


class SqlAlchemyMeetingStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _scope(context: TenantContext) -> list[Any]:
        return [MeetingRecord.tenant_id == context.tenant_id, MeetingRecord.workspace_id == context.workspace_id]

    @staticmethod
    def _segment_scope(context: TenantContext, meeting_id: str) -> list[Any]:
        return [
            MeetingTranscriptSegmentRecord.tenant_id == context.tenant_id,
            MeetingTranscriptSegmentRecord.workspace_id == context.workspace_id,
            MeetingTranscriptSegmentRecord.meeting_id == meeting_id,
        ]

    def _record(self, context: TenantContext, meeting_id: str, *, lock: bool = False) -> MeetingRecord | None:
        statement = select(MeetingRecord).where(*self._scope(context), MeetingRecord.id == meeting_id)
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def add(self, meeting: Meeting) -> Meeting:
        require_workspace(self.session, meeting.tenant_id, meeting.workspace_id)
        record = MeetingRecord(
            id=meeting.id,
            tenant_id=meeting.tenant_id,
            workspace_id=meeting.workspace_id,
            created_by=meeting.created_by,
            created_at=meeting.created_at,
        )
        _apply(record, meeting)
        self.session.add(record)
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError as exc:
            if meeting.external_id:
                raise DuplicateImportError(meeting.external_id) from exc
            raise
        return meeting

    def get(self, context: TenantContext, meeting_id: str, *, lock: bool = False) -> Meeting | None:
        record = self._record(context, meeting_id, lock=lock)
        return _meeting(record) if record is not None else None

    def save(self, meeting: Meeting) -> Meeting:
        record = self.session.scalar(
            select(MeetingRecord).where(
                MeetingRecord.tenant_id == meeting.tenant_id,
                MeetingRecord.workspace_id == meeting.workspace_id,
                MeetingRecord.id == meeting.id,
            )
        )
        if record is not None:
            _apply(record, meeting)
            self.session.flush()
        return meeting

    def delete(self, context: TenantContext, meeting_id: str) -> bool:
        self.session.execute(delete(MeetingTranscriptSegmentRecord).where(*self._segment_scope(context, meeting_id)))
        deleted = self.session.execute(delete(MeetingRecord).where(*self._scope(context), MeetingRecord.id == meeting_id))
        return bool(deleted.rowcount)

    def list_meetings(
        self, context: TenantContext, *, query: str | None, status: MeetingStatus | None, offset: int, limit: int
    ) -> tuple[list[Meeting], int]:
        conditions = self._scope(context)
        if status is not None:
            conditions.append(MeetingRecord.status == status.value)
        if query:
            pattern = _like(query)
            in_transcript = exists().where(
                MeetingTranscriptSegmentRecord.tenant_id == MeetingRecord.tenant_id,
                MeetingTranscriptSegmentRecord.workspace_id == MeetingRecord.workspace_id,
                MeetingTranscriptSegmentRecord.meeting_id == MeetingRecord.id,
                MeetingTranscriptSegmentRecord.text.ilike(pattern, escape="\\"),
            )
            conditions.append(
                or_(
                    MeetingRecord.title.ilike(pattern, escape="\\"),
                    MeetingRecord.summary.ilike(pattern, escape="\\"),
                    in_transcript,
                )
            )
        total = int(self.session.scalar(select(func.count()).select_from(MeetingRecord).where(*conditions)) or 0)
        rows = self.session.scalars(
            select(MeetingRecord)
            .where(*conditions)
            .order_by(MeetingRecord.created_at.desc(), MeetingRecord.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [_meeting(row) for row in rows], total

    def replace_segments(self, context: TenantContext, meeting_id: str, segments: list[TranscriptSegment]) -> None:
        self.session.execute(delete(MeetingTranscriptSegmentRecord).where(*self._segment_scope(context, meeting_id)))
        self.session.add_all(
            MeetingTranscriptSegmentRecord(
                id=segment.id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                meeting_id=meeting_id,
                seq=segment.seq,
                speaker=segment.speaker,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
            )
            for segment in segments
        )
        self.session.flush()

    def segments(
        self, context: TenantContext, meeting_id: str, *, query: str | None, speaker: str | None, offset: int, limit: int
    ) -> tuple[list[TranscriptSegment], int]:
        conditions = self._segment_scope(context, meeting_id)
        if query:
            pattern = _like(query)
            conditions.append(
                or_(
                    MeetingTranscriptSegmentRecord.text.ilike(pattern, escape="\\"),
                    MeetingTranscriptSegmentRecord.speaker.ilike(pattern, escape="\\"),
                )
            )
        if speaker:
            conditions.append(MeetingTranscriptSegmentRecord.speaker == speaker)
        total = int(
            self.session.scalar(select(func.count()).select_from(MeetingTranscriptSegmentRecord).where(*conditions)) or 0
        )
        rows = self.session.scalars(
            select(MeetingTranscriptSegmentRecord)
            .where(*conditions)
            .order_by(MeetingTranscriptSegmentRecord.seq)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_segment(row) for row in rows], total

    def all_segments(self, context: TenantContext, meeting_id: str) -> list[TranscriptSegment]:
        rows = self.session.scalars(
            select(MeetingTranscriptSegmentRecord)
            .where(*self._segment_scope(context, meeting_id))
            .order_by(MeetingTranscriptSegmentRecord.seq)
        ).all()
        return [_segment(row) for row in rows]

    def speakers(self, context: TenantContext, meeting_id: str) -> list[str]:
        rows = self.session.scalars(
            select(MeetingTranscriptSegmentRecord.speaker)
            .where(*self._segment_scope(context, meeting_id), MeetingTranscriptSegmentRecord.speaker.is_not(None))
            .distinct()
        ).all()
        return sorted(str(row) for row in rows if row)

    def external_ids(self, context: TenantContext, source: MeetingSource) -> dict[str, str]:
        rows = self.session.execute(
            select(MeetingRecord.external_id, MeetingRecord.id).where(
                *self._scope(context), MeetingRecord.source == source.value, MeetingRecord.external_id.is_not(None)
            )
        ).all()
        return {str(external): str(meeting_id) for external, meeting_id in rows}


class SqlAlchemyConnectionStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _scope(context: TenantContext) -> list[Any]:
        return [
            MeetingConnectorAccountRecord.tenant_id == context.tenant_id,
            MeetingConnectorAccountRecord.workspace_id == context.workspace_id,
            MeetingConnectorAccountRecord.user_id == context.user_id,
        ]

    @staticmethod
    def _connection(record: MeetingConnectorAccountRecord) -> StoredConnection:
        return StoredConnection(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            provider=ConnectorProvider(record.provider),
            account_label=record.account_label,
            external_user_id=record.external_user_id,
            scopes=list(record.scopes or []),
            token_ciphertext=record.token_ciphertext,
            expires_at=record.expires_at,
            connected_at=record.connected_at,
            updated_at=record.updated_at,
        )

    def get(self, context: TenantContext, provider: ConnectorProvider) -> StoredConnection | None:
        record = self.session.scalar(
            select(MeetingConnectorAccountRecord).where(*self._scope(context), MeetingConnectorAccountRecord.provider == provider.value)
        )
        return self._connection(record) if record is not None else None

    def list_connections(self, context: TenantContext) -> list[StoredConnection]:
        rows = self.session.scalars(select(MeetingConnectorAccountRecord).where(*self._scope(context))).all()
        return [self._connection(row) for row in rows]

    def save(self, connection: StoredConnection) -> StoredConnection:
        require_workspace(self.session, connection.tenant_id, connection.workspace_id)
        record = self.session.get(
            MeetingConnectorAccountRecord,
            (connection.tenant_id, connection.workspace_id, connection.user_id, connection.provider.value),
        )
        if record is None:
            record = MeetingConnectorAccountRecord(
                tenant_id=connection.tenant_id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
                provider=connection.provider.value,
            )
            self.session.add(record)
        record.account_label = connection.account_label
        record.external_user_id = connection.external_user_id
        record.scopes = list(connection.scopes)
        record.token_ciphertext = connection.token_ciphertext
        record.expires_at = connection.expires_at
        record.connected_at = connection.connected_at
        record.updated_at = connection.updated_at
        self.session.flush()
        return connection

    def delete(self, context: TenantContext, provider: ConnectorProvider) -> bool:
        result = self.session.execute(
            delete(MeetingConnectorAccountRecord).where(*self._scope(context), MeetingConnectorAccountRecord.provider == provider.value)
        )
        return bool(result.rowcount)


