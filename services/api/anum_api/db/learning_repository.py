"""PostgreSQL store for what ANUM learned (migration ``0031_learning``, docs/learning.md).

The session's RLS context names the tenant, the workspace and the user
(``open_scoped_store(..., user_scoped=True)``, or the privacy unit of work); every
statement repeats all three, so another user's rows are neither read nor written even
if a policy were missing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from anum_api.learning import (
    ImprovementNote,
    LearnedPreference,
    LearningProfile,
    LearningSignal,
    NoteSummary,
    Routine,
    RoutineStatus,
    RoutineStep,
    SignalKind,
    StyleOverrides,
    StyleProfile,
    Suggestion,
    SuggestionStatus,
    Surface,
)
from anum_api.schemas import TenantContext, utc_now

from .models import (
    LearningNoteRecord,
    LearningPreferenceRecord,
    LearningProfileRecord,
    LearningRoutineRecord,
    LearningSignalRecord,
    LearningSuggestionRecord,
)
from .scope import require_workspace


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlAlchemyLearningStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _mine(self, model) -> list[Any]:  # type: ignore[no-untyped-def]
        return [
            model.tenant_id == self.context.tenant_id,
            model.workspace_id == self.context.workspace_id,
            model.user_id == self.context.user_id,
        ]

    def _scope(self) -> dict[str, str]:
        return {
            "tenant_id": self.context.tenant_id,
            "workspace_id": self.context.workspace_id,
            "user_id": self.context.user_id,
        }

    def _require_workspace(self) -> None:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)

    # -- profile ----------------------------------------------------------------------

    def get_profile(self) -> LearningProfile | None:
        record = self.session.scalar(select(LearningProfileRecord).where(*self._mine(LearningProfileRecord)))
        if record is None:
            return None
        return LearningProfile(
            enabled=record.enabled,
            derived=StyleProfile.model_validate(record.derived or {}),
            overrides=StyleOverrides.model_validate(record.overrides or {}),
            derived_at=record.derived_at,
            updated_at=record.updated_at,
        )

    def save_profile(self, profile: LearningProfile) -> LearningProfile:
        self._require_workspace()
        record = self.session.scalar(select(LearningProfileRecord).where(*self._mine(LearningProfileRecord)))
        if record is None:
            record = LearningProfileRecord(**self._scope())
            self.session.add(record)
        record.enabled = profile.enabled
        record.derived = profile.derived.model_dump(mode="json")
        record.overrides = profile.overrides.model_dump(mode="json", exclude_none=True)
        record.derived_at = profile.derived_at
        record.updated_at = profile.updated_at or utc_now()
        self.session.flush()
        return profile

    def delete_profile(self) -> int:
        return int(self.session.execute(delete(LearningProfileRecord).where(*self._mine(LearningProfileRecord))).rowcount or 0)

    # -- signals ----------------------------------------------------------------------

    @staticmethod
    def _signal(record: LearningSignalRecord) -> LearningSignal:
        return LearningSignal(
            id=record.id,
            kind=SignalKind(record.kind),
            surface=Surface(record.surface),
            subject_id=record.subject_id,
            skill=record.skill,
            tool=record.tool,
            contact=record.contact,
            language=record.language,  # type: ignore[arg-type]
            original_text=record.original_text,
            final_text=record.final_text,
            reason=record.reason,
            metadata=dict(record.signal_metadata or {}),
            created_at=record.created_at,
        )

    def add_signal(self, signal: LearningSignal) -> LearningSignal:
        self._require_workspace()
        self.session.add(
            LearningSignalRecord(
                id=signal.id,
                **self._scope(),
                kind=signal.kind.value,
                surface=signal.surface.value,
                subject_id=signal.subject_id,
                skill=signal.skill,
                tool=signal.tool,
                contact=signal.contact,
                language=signal.language,
                original_text=signal.original_text,
                final_text=signal.final_text,
                reason=signal.reason,
                signal_metadata=signal.metadata,
                created_at=signal.created_at,
            )
        )
        self.session.flush()
        return signal

    def _signal_filters(
        self,
        kinds: frozenset[SignalKind] | None = None,
        surface: Surface | None = None,
        since: datetime | None = None,
        query: str | None = None,
        subject_id: str | None = None,
        before: datetime | None = None,
    ) -> list[Any]:
        where = self._mine(LearningSignalRecord)
        if kinds is not None:
            where.append(LearningSignalRecord.kind.in_([kind.value for kind in kinds]))
        if surface is not None:
            where.append(LearningSignalRecord.surface == surface.value)
        if since is not None:
            where.append(LearningSignalRecord.created_at >= since)
        if before is not None:
            where.append(LearningSignalRecord.created_at < before)
        if subject_id is not None:
            where.append(LearningSignalRecord.subject_id == subject_id)
        if query:
            pattern = f"%{_escape_like(query)}%"
            where.append(
                or_(
                    *(
                        column.ilike(pattern, escape="\\")
                        for column in (
                            LearningSignalRecord.original_text,
                            LearningSignalRecord.final_text,
                            LearningSignalRecord.reason,
                            LearningSignalRecord.contact,
                            LearningSignalRecord.tool,
                            LearningSignalRecord.skill,
                        )
                    )
                )
            )
        return where

    def list_signals(self, *, kinds=None, surface=None, since=None, query=None, limit=None, offset=0) -> list[LearningSignal]:  # type: ignore[no-untyped-def]
        statement = (
            select(LearningSignalRecord)
            .where(*self._signal_filters(kinds, surface, since, query))
            .order_by(LearningSignalRecord.created_at.desc(), LearningSignalRecord.id.desc())
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return [self._signal(record) for record in self.session.scalars(statement)]

    def count_signals(self, *, kinds=None, surface=None, since=None, query=None) -> int:  # type: ignore[no-untyped-def]
        return int(
            self.session.scalar(
                select(func.count()).select_from(LearningSignalRecord).where(*self._signal_filters(kinds, surface, since, query))
            )
            or 0
        )

    def signal_counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(LearningSignalRecord.kind, func.count())
            .where(*self._mine(LearningSignalRecord))
            .group_by(LearningSignalRecord.kind)
        ).all()
        return {kind: int(count) for kind, count in rows}

    def get_signal(self, signal_id: str) -> LearningSignal | None:
        record = self.session.scalar(
            select(LearningSignalRecord).where(*self._mine(LearningSignalRecord), LearningSignalRecord.id == signal_id)
        )
        return self._signal(record) if record else None

    def delete_signal(self, signal_id: str) -> bool:
        result = self.session.execute(
            delete(LearningSignalRecord).where(*self._mine(LearningSignalRecord), LearningSignalRecord.id == signal_id)
        )
        return bool(result.rowcount)

    def delete_signals(self, *, kinds=None, surface=None, subject_id=None, before=None) -> int:  # type: ignore[no-untyped-def]
        result = self.session.execute(
            delete(LearningSignalRecord).where(
                *self._signal_filters(kinds, surface, None, None, subject_id=subject_id, before=before)
            )
        )
        return int(result.rowcount or 0)

    def prune_signals(self, keep: int) -> int:
        keep_ids = (
            select(LearningSignalRecord.id)
            .where(*self._mine(LearningSignalRecord))
            .order_by(LearningSignalRecord.created_at.desc(), LearningSignalRecord.id.desc())
            .limit(keep)
        )
        result = self.session.execute(
            delete(LearningSignalRecord).where(*self._mine(LearningSignalRecord), LearningSignalRecord.id.not_in(keep_ids))
        )
        return int(result.rowcount or 0)

    def ratings_for(self, surface: Surface, subject_ids: list[str]) -> list[LearningSignal]:
        if not subject_ids:
            return []
        statement = (
            select(LearningSignalRecord)
            .where(
                *self._mine(LearningSignalRecord),
                LearningSignalRecord.surface == surface.value,
                LearningSignalRecord.kind.in_([SignalKind.RATING_UP.value, SignalKind.RATING_DOWN.value]),
                LearningSignalRecord.subject_id.in_(subject_ids),
            )
            .order_by(LearningSignalRecord.created_at.desc())
        )
        return [self._signal(record) for record in self.session.scalars(statement)]

    # -- preferences ------------------------------------------------------------------

    @staticmethod
    def _preference(record: LearningPreferenceRecord) -> LearnedPreference:
        return LearnedPreference(
            id=record.id,
            scope=record.scope,
            text=record.text,
            source=record.source,  # type: ignore[arg-type]
            suggestion_id=record.suggestion_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _preference_filters(self, query: str | None) -> list[Any]:
        where = self._mine(LearningPreferenceRecord)
        if query:
            pattern = f"%{_escape_like(query)}%"
            where.append(
                or_(
                    LearningPreferenceRecord.text.ilike(pattern, escape="\\"),
                    LearningPreferenceRecord.scope.ilike(pattern, escape="\\"),
                )
            )
        return where

    def list_preferences(self, *, query=None, limit=None, offset=0) -> list[LearnedPreference]:  # type: ignore[no-untyped-def]
        statement = (
            select(LearningPreferenceRecord)
            .where(*self._preference_filters(query))
            .order_by(LearningPreferenceRecord.created_at.desc(), LearningPreferenceRecord.id.desc())
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return [self._preference(record) for record in self.session.scalars(statement)]

    def count_preferences(self, *, query=None) -> int:  # type: ignore[no-untyped-def]
        return int(
            self.session.scalar(select(func.count()).select_from(LearningPreferenceRecord).where(*self._preference_filters(query)))
            or 0
        )

    def get_preference(self, preference_id: str) -> LearnedPreference | None:
        record = self.session.scalar(
            select(LearningPreferenceRecord).where(
                *self._mine(LearningPreferenceRecord), LearningPreferenceRecord.id == preference_id
            )
        )
        return self._preference(record) if record else None

    def save_preference(self, preference: LearnedPreference) -> LearnedPreference:
        self._require_workspace()
        record = self.session.scalar(
            select(LearningPreferenceRecord).where(
                *self._mine(LearningPreferenceRecord), LearningPreferenceRecord.id == preference.id
            )
        )
        if record is None:
            record = LearningPreferenceRecord(id=preference.id, **self._scope(), created_at=preference.created_at)
            self.session.add(record)
        record.scope = preference.scope
        record.text = preference.text
        record.source = preference.source
        record.suggestion_id = preference.suggestion_id
        record.updated_at = preference.updated_at
        self.session.flush()
        return preference

    def delete_preference(self, preference_id: str) -> bool:
        result = self.session.execute(
            delete(LearningPreferenceRecord).where(
                *self._mine(LearningPreferenceRecord), LearningPreferenceRecord.id == preference_id
            )
        )
        return bool(result.rowcount)

    # -- routines ---------------------------------------------------------------------

    @staticmethod
    def _routine(record: LearningRoutineRecord) -> Routine:
        return Routine(
            id=record.id,
            fingerprint=record.fingerprint,
            status=RoutineStatus(record.status),
            title=record.title,
            cadence=record.cadence,  # type: ignore[arg-type]
            cron=record.cron,
            timezone=record.timezone,
            steps=[RoutineStep.model_validate(step) for step in record.steps or []],
            occurrences=record.occurrences,
            weeks_observed=record.weeks_observed,
            confidence=record.confidence,
            last_seen_at=record.last_seen_at,
            workflow_id=record.workflow_id,
            schedule_id=record.schedule_id,
            detected_at=record.detected_at,
            decided_at=record.decided_at,
        )

    def list_routines(self, *, status=None) -> list[Routine]:  # type: ignore[no-untyped-def]
        where = self._mine(LearningRoutineRecord)
        if status is not None:
            where.append(LearningRoutineRecord.status == status.value)
        statement = select(LearningRoutineRecord).where(*where).order_by(
            LearningRoutineRecord.confidence.desc(), LearningRoutineRecord.detected_at, LearningRoutineRecord.id
        )
        return [self._routine(record) for record in self.session.scalars(statement)]

    def _routine_record(self, *where: Any) -> LearningRoutineRecord | None:
        return self.session.scalar(select(LearningRoutineRecord).where(*self._mine(LearningRoutineRecord), *where))

    def get_routine(self, routine_id: str) -> Routine | None:
        record = self._routine_record(LearningRoutineRecord.id == routine_id)
        return self._routine(record) if record else None

    def get_routine_by_fingerprint(self, fingerprint: str) -> Routine | None:
        record = self._routine_record(LearningRoutineRecord.fingerprint == fingerprint)
        return self._routine(record) if record else None

    def save_routine(self, routine: Routine) -> Routine:
        self._require_workspace()
        record = self._routine_record(LearningRoutineRecord.id == routine.id)
        if record is None:
            record = LearningRoutineRecord(id=routine.id, **self._scope())
            self.session.add(record)
        record.fingerprint = routine.fingerprint
        record.status = routine.status.value
        record.title = routine.title
        record.cadence = routine.cadence
        record.cron = routine.cron
        record.timezone = routine.timezone
        record.steps = [step.model_dump(mode="json") for step in routine.steps]
        record.occurrences = routine.occurrences
        record.weeks_observed = routine.weeks_observed
        record.confidence = routine.confidence
        record.last_seen_at = routine.last_seen_at
        record.workflow_id = routine.workflow_id
        record.schedule_id = routine.schedule_id
        record.detected_at = routine.detected_at
        record.decided_at = routine.decided_at
        self.session.flush()
        return routine

    def delete_routine(self, routine_id: str) -> bool:
        result = self.session.execute(
            delete(LearningRoutineRecord).where(*self._mine(LearningRoutineRecord), LearningRoutineRecord.id == routine_id)
        )
        return bool(result.rowcount)

    # -- notes and suggestions --------------------------------------------------------

    @staticmethod
    def _note(record: LearningNoteRecord) -> ImprovementNote:
        return ImprovementNote(
            week=record.week,
            period_start=record.period_start,
            period_end=record.period_end,
            summary=NoteSummary.model_validate(record.summary or {}),
            generated_at=record.generated_at,
        )

    def get_note(self, week: str) -> ImprovementNote | None:
        record = self.session.scalar(
            select(LearningNoteRecord).where(*self._mine(LearningNoteRecord), LearningNoteRecord.week == week)
        )
        return self._note(record) if record else None

    def save_note(self, note: ImprovementNote) -> ImprovementNote:
        self._require_workspace()
        record = self.session.scalar(
            select(LearningNoteRecord).where(*self._mine(LearningNoteRecord), LearningNoteRecord.week == note.week)
        )
        if record is None:
            record = LearningNoteRecord(**self._scope(), week=note.week)
            self.session.add(record)
        record.period_start = note.period_start
        record.period_end = note.period_end
        record.summary = note.summary.model_dump(mode="json")
        record.generated_at = note.generated_at
        self.session.flush()
        return note

    def list_notes(self, *, limit=None, offset=0) -> list[ImprovementNote]:  # type: ignore[no-untyped-def]
        statement = (
            select(LearningNoteRecord)
            .where(*self._mine(LearningNoteRecord))
            .order_by(LearningNoteRecord.week.desc())
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return [self._note(record) for record in self.session.scalars(statement)]

    def count_notes(self) -> int:
        return int(
            self.session.scalar(select(func.count()).select_from(LearningNoteRecord).where(*self._mine(LearningNoteRecord))) or 0
        )

    @staticmethod
    def _suggestion(record: LearningSuggestionRecord) -> Suggestion:
        return Suggestion(
            id=record.id,
            week=record.week,
            fingerprint=record.fingerprint,
            rule=record.rule,
            kind=record.kind,  # type: ignore[arg-type]
            title=record.title,
            body=record.body,
            target=record.target,
            change=dict(record.change or {}),
            evidence=dict(record.evidence or {}),
            status=SuggestionStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
            decided_at=record.decided_at,
        )

    def list_suggestions(self, *, week=None, status=None) -> list[Suggestion]:  # type: ignore[no-untyped-def]
        where = self._mine(LearningSuggestionRecord)
        if week is not None:
            where.append(LearningSuggestionRecord.week == week)
        if status is not None:
            where.append(LearningSuggestionRecord.status == status.value)
        statement = select(LearningSuggestionRecord).where(*where).order_by(
            LearningSuggestionRecord.week.desc(), LearningSuggestionRecord.created_at.desc(), LearningSuggestionRecord.id.desc()
        )
        return [self._suggestion(record) for record in self.session.scalars(statement)]

    def _suggestion_record(self, *where: Any) -> LearningSuggestionRecord | None:
        return self.session.scalar(select(LearningSuggestionRecord).where(*self._mine(LearningSuggestionRecord), *where))

    def get_suggestion(self, suggestion_id: str) -> Suggestion | None:
        record = self._suggestion_record(LearningSuggestionRecord.id == suggestion_id)
        return self._suggestion(record) if record else None

    def get_suggestion_by_fingerprint(self, fingerprint: str) -> Suggestion | None:
        record = self._suggestion_record(LearningSuggestionRecord.fingerprint == fingerprint)
        return self._suggestion(record) if record else None

    def save_suggestion(self, suggestion: Suggestion) -> Suggestion:
        self._require_workspace()
        record = self._suggestion_record(LearningSuggestionRecord.id == suggestion.id)
        if record is None:
            record = LearningSuggestionRecord(id=suggestion.id, **self._scope(), created_at=suggestion.created_at)
            self.session.add(record)
        record.week = suggestion.week
        record.fingerprint = suggestion.fingerprint
        record.rule = suggestion.rule
        record.kind = suggestion.kind.value
        record.title = suggestion.title
        record.body = suggestion.body
        record.target = suggestion.target
        record.change = suggestion.change
        record.evidence = suggestion.evidence
        record.status = suggestion.status.value
        record.updated_at = suggestion.updated_at
        record.decided_at = suggestion.decided_at
        self.session.flush()
        return suggestion

    def delete_suggestion(self, suggestion_id: str) -> bool:
        result = self.session.execute(
            delete(LearningSuggestionRecord).where(
                *self._mine(LearningSuggestionRecord), LearningSuggestionRecord.id == suggestion_id
            )
        )
        return bool(result.rowcount)

    # -- privacy ----------------------------------------------------------------------

    _MODELS = (
        ("signals", LearningSignalRecord),
        ("profile", LearningProfileRecord),
        ("preferences", LearningPreferenceRecord),
        ("routines", LearningRoutineRecord),
        ("notes", LearningNoteRecord),
        ("suggestions", LearningSuggestionRecord),
    )

    def counts(self) -> dict[str, int]:
        return {
            name: int(self.session.scalar(select(func.count()).select_from(model).where(*self._mine(model))) or 0)
            for name, model in self._MODELS
        }

    def delete_all(self) -> dict[str, int]:
        counts = self.counts()
        for _, model in self._MODELS:
            self.session.execute(delete(model).where(*self._mine(model)))
        self.session.flush()
        return counts
