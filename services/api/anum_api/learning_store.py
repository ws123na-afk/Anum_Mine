"""Storage, the learning switch and the hooks other modules call (docs/learning.md).

A learning store is always bound to one caller: tenant, workspace *and* user. With
``ANUM_REPOSITORY_BACKEND=memory`` it lives in process memory; with ``postgresql`` it is
the six ``learning_*`` tables of migration ``0036_learning`` under forced RLS on
``anum.tenant_id``, ``anum.workspace_id`` and ``anum.user_id`` (the unit of work sets all
three), and every statement repeats the scope.

The hooks (:func:`record_server_signal`, :func:`learned_instructions`) never raise: a
learning failure is logged by class name and the request carries on.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Protocol

from .learning import (
    DRAFT_KINDS,
    ImprovementNote,
    LearnedPreference,
    LearningProfile,
    LearningSignal,
    LearningState,
    Routine,
    RoutineStatus,
    SignalKind,
    Suggestion,
    SuggestionStatus,
    Surface,
    derive_style,
    detect_language,
    render_learned_block,
)
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings

logger = logging.getLogger("anum.learning")

# A user's signals are bounded: the newest are kept when a pass prunes.
MAX_SIGNALS_PER_USER = 5000


class LearningStore(Protocol):
    def get_profile(self) -> LearningProfile | None: ...
    def save_profile(self, profile: LearningProfile) -> LearningProfile: ...
    def delete_profile(self) -> int: ...

    def add_signal(self, signal: LearningSignal) -> LearningSignal: ...
    def list_signals(
        self,
        *,
        kinds: frozenset[SignalKind] | None = None,
        surface: Surface | None = None,
        since: datetime | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[LearningSignal]: ...
    def count_signals(
        self,
        *,
        kinds: frozenset[SignalKind] | None = None,
        surface: Surface | None = None,
        since: datetime | None = None,
        query: str | None = None,
    ) -> int: ...
    def signal_counts(self) -> dict[str, int]: ...
    def get_signal(self, signal_id: str) -> LearningSignal | None: ...
    def delete_signal(self, signal_id: str) -> bool: ...
    def delete_signals(
        self,
        *,
        kinds: frozenset[SignalKind] | None = None,
        surface: Surface | None = None,
        subject_id: str | None = None,
        before: datetime | None = None,
    ) -> int: ...
    def prune_signals(self, keep: int) -> int: ...
    def ratings_for(self, surface: Surface, subject_ids: list[str]) -> list[LearningSignal]: ...

    def list_preferences(self, *, query: str | None = None, limit: int | None = None, offset: int = 0) -> list[LearnedPreference]: ...
    def count_preferences(self, *, query: str | None = None) -> int: ...
    def get_preference(self, preference_id: str) -> LearnedPreference | None: ...
    def save_preference(self, preference: LearnedPreference) -> LearnedPreference: ...
    def delete_preference(self, preference_id: str) -> bool: ...

    def list_routines(self, *, status: RoutineStatus | None = None) -> list[Routine]: ...
    def get_routine(self, routine_id: str) -> Routine | None: ...
    def get_routine_by_fingerprint(self, fingerprint: str) -> Routine | None: ...
    def save_routine(self, routine: Routine) -> Routine: ...
    def delete_routine(self, routine_id: str) -> bool: ...

    def get_note(self, week: str) -> ImprovementNote | None: ...
    def save_note(self, note: ImprovementNote) -> ImprovementNote: ...
    def list_notes(self, *, limit: int | None = None, offset: int = 0) -> list[ImprovementNote]: ...
    def count_notes(self) -> int: ...

    def list_suggestions(self, *, week: str | None = None, status: SuggestionStatus | None = None) -> list[Suggestion]: ...
    def get_suggestion(self, suggestion_id: str) -> Suggestion | None: ...
    def get_suggestion_by_fingerprint(self, fingerprint: str) -> Suggestion | None: ...
    def save_suggestion(self, suggestion: Suggestion) -> Suggestion: ...
    def delete_suggestion(self, suggestion_id: str) -> bool: ...

    def counts(self) -> dict[str, int]: ...
    def delete_all(self) -> dict[str, int]: ...


def _matches(signal: LearningSignal, query: str) -> bool:
    needle = query.lower()
    return any(
        needle in (value or "").lower()
        for value in (signal.original_text, signal.final_text, signal.reason, signal.contact, signal.tool, signal.skill)
    )


# --------------------------------------------------------------------------- in memory


@dataclass
class _UserData:
    profile: LearningProfile | None = None
    signals: dict[str, LearningSignal] = field(default_factory=dict)
    preferences: dict[str, LearnedPreference] = field(default_factory=dict)
    routines: dict[str, Routine] = field(default_factory=dict)
    notes: dict[str, ImprovementNote] = field(default_factory=dict)
    suggestions: dict[str, Suggestion] = field(default_factory=dict)


class InMemoryLearningData:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._users: dict[tuple[str, str, str], _UserData] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self._users.clear()

    def bound(self, context: TenantContext) -> InMemoryLearningStore:
        with self.lock:
            data = self._users.setdefault((context.tenant_id, context.workspace_id, context.user_id), _UserData())
        return InMemoryLearningStore(data, self.lock)


class InMemoryLearningStore:
    def __init__(self, data: _UserData, lock: RLock) -> None:
        self.data = data
        self.lock = lock

    # profile
    def get_profile(self) -> LearningProfile | None:
        with self.lock:
            return self.data.profile.model_copy(deep=True) if self.data.profile else None

    def save_profile(self, profile: LearningProfile) -> LearningProfile:
        with self.lock:
            self.data.profile = profile.model_copy(deep=True)
        return profile

    def delete_profile(self) -> int:
        with self.lock:
            existed = self.data.profile is not None
            self.data.profile = None
        return int(existed)

    # signals
    def add_signal(self, signal: LearningSignal) -> LearningSignal:
        with self.lock:
            self.data.signals[signal.id] = signal.model_copy(deep=True)
        return signal

    def _filtered(self, kinds, surface, since, query, subject_id=None, before=None) -> list[LearningSignal]:  # type: ignore[no-untyped-def]
        with self.lock:
            items = list(self.data.signals.values())
        return sorted(
            (
                item
                for item in items
                if (kinds is None or item.kind in kinds)
                and (surface is None or item.surface == surface)
                and (since is None or item.created_at >= since)
                and (before is None or item.created_at < before)
                and (subject_id is None or item.subject_id == subject_id)
                and (not query or _matches(item, query))
            ),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def list_signals(self, *, kinds=None, surface=None, since=None, query=None, limit=None, offset=0) -> list[LearningSignal]:  # type: ignore[no-untyped-def]
        items = self._filtered(kinds, surface, since, query)[offset:]
        return [item.model_copy(deep=True) for item in (items if limit is None else items[:limit])]

    def count_signals(self, *, kinds=None, surface=None, since=None, query=None) -> int:  # type: ignore[no-untyped-def]
        return len(self._filtered(kinds, surface, since, query))

    def signal_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.lock:
            for item in self.data.signals.values():
                counts[item.kind.value] = counts.get(item.kind.value, 0) + 1
        return counts

    def get_signal(self, signal_id: str) -> LearningSignal | None:
        with self.lock:
            found = self.data.signals.get(signal_id)
            return found.model_copy(deep=True) if found else None

    def delete_signal(self, signal_id: str) -> bool:
        with self.lock:
            return self.data.signals.pop(signal_id, None) is not None

    def delete_signals(self, *, kinds=None, surface=None, subject_id=None, before=None) -> int:  # type: ignore[no-untyped-def]
        doomed = self._filtered(kinds, surface, None, None, subject_id=subject_id, before=before)
        with self.lock:
            for item in doomed:
                self.data.signals.pop(item.id, None)
        return len(doomed)

    def prune_signals(self, keep: int) -> int:
        doomed = self._filtered(None, None, None, None)[keep:]
        with self.lock:
            for item in doomed:
                self.data.signals.pop(item.id, None)
        return len(doomed)

    def ratings_for(self, surface: Surface, subject_ids: list[str]) -> list[LearningSignal]:
        wanted = set(subject_ids)
        return [
            item
            for item in self._filtered(frozenset({SignalKind.RATING_UP, SignalKind.RATING_DOWN}), surface, None, None)
            if item.subject_id in wanted
        ]

    # preferences
    def _preferences(self, query: str | None) -> list[LearnedPreference]:
        with self.lock:
            items = list(self.data.preferences.values())
        needle = (query or "").lower()
        return sorted(
            (item for item in items if not needle or needle in item.text.lower() or needle in item.scope.lower()),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def list_preferences(self, *, query=None, limit=None, offset=0) -> list[LearnedPreference]:  # type: ignore[no-untyped-def]
        items = self._preferences(query)[offset:]
        return [item.model_copy() for item in (items if limit is None else items[:limit])]

    def count_preferences(self, *, query=None) -> int:  # type: ignore[no-untyped-def]
        return len(self._preferences(query))

    def get_preference(self, preference_id: str) -> LearnedPreference | None:
        with self.lock:
            found = self.data.preferences.get(preference_id)
            return found.model_copy() if found else None

    def save_preference(self, preference: LearnedPreference) -> LearnedPreference:
        with self.lock:
            self.data.preferences[preference.id] = preference.model_copy()
        return preference

    def delete_preference(self, preference_id: str) -> bool:
        with self.lock:
            return self.data.preferences.pop(preference_id, None) is not None

    # routines
    def list_routines(self, *, status=None) -> list[Routine]:  # type: ignore[no-untyped-def]
        with self.lock:
            items = [item.model_copy(deep=True) for item in self.data.routines.values() if status is None or item.status == status]
        return sorted(items, key=lambda item: (-item.confidence, item.detected_at, item.id))

    def get_routine(self, routine_id: str) -> Routine | None:
        with self.lock:
            found = self.data.routines.get(routine_id)
            return found.model_copy(deep=True) if found else None

    def get_routine_by_fingerprint(self, fingerprint: str) -> Routine | None:
        with self.lock:
            found = next((item for item in self.data.routines.values() if item.fingerprint == fingerprint), None)
            return found.model_copy(deep=True) if found else None

    def save_routine(self, routine: Routine) -> Routine:
        with self.lock:
            self.data.routines[routine.id] = routine.model_copy(deep=True)
        return routine

    def delete_routine(self, routine_id: str) -> bool:
        with self.lock:
            return self.data.routines.pop(routine_id, None) is not None

    # notes and suggestions
    def get_note(self, week: str) -> ImprovementNote | None:
        with self.lock:
            found = self.data.notes.get(week)
            return found.model_copy(deep=True) if found else None

    def save_note(self, note: ImprovementNote) -> ImprovementNote:
        with self.lock:
            self.data.notes[note.week] = note.model_copy(deep=True)
        return note

    def list_notes(self, *, limit=None, offset=0) -> list[ImprovementNote]:  # type: ignore[no-untyped-def]
        with self.lock:
            items = sorted(self.data.notes.values(), key=lambda item: item.week, reverse=True)[offset:]
        return [item.model_copy(deep=True) for item in (items if limit is None else items[:limit])]

    def count_notes(self) -> int:
        with self.lock:
            return len(self.data.notes)

    def list_suggestions(self, *, week=None, status=None) -> list[Suggestion]:  # type: ignore[no-untyped-def]
        with self.lock:
            items = [
                item.model_copy(deep=True)
                for item in self.data.suggestions.values()
                if (week is None or item.week == week) and (status is None or item.status == status)
            ]
        return sorted(items, key=lambda item: (item.week, item.created_at, item.id), reverse=True)

    def get_suggestion(self, suggestion_id: str) -> Suggestion | None:
        with self.lock:
            found = self.data.suggestions.get(suggestion_id)
            return found.model_copy(deep=True) if found else None

    def get_suggestion_by_fingerprint(self, fingerprint: str) -> Suggestion | None:
        with self.lock:
            found = next((item for item in self.data.suggestions.values() if item.fingerprint == fingerprint), None)
            return found.model_copy(deep=True) if found else None

    def save_suggestion(self, suggestion: Suggestion) -> Suggestion:
        with self.lock:
            self.data.suggestions[suggestion.id] = suggestion.model_copy(deep=True)
        return suggestion

    def delete_suggestion(self, suggestion_id: str) -> bool:
        with self.lock:
            return self.data.suggestions.pop(suggestion_id, None) is not None

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {
                "signals": len(self.data.signals),
                "profile": int(self.data.profile is not None),
                "preferences": len(self.data.preferences),
                "routines": len(self.data.routines),
                "notes": len(self.data.notes),
                "suggestions": len(self.data.suggestions),
            }

    def delete_all(self) -> dict[str, int]:
        with self.lock:
            counts = self.counts()
            self.data.profile = None
            for bucket in (self.data.signals, self.data.preferences, self.data.routines, self.data.notes, self.data.suggestions):
                bucket.clear()
        return counts


learning_data = InMemoryLearningData()


@contextmanager
def open_learning_store(context: TenantContext) -> Iterator[LearningStore]:
    """The caller's learning data for one unit of work (tenant, workspace and user RLS)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.learning_repository import SqlAlchemyLearningStore

        return SqlAlchemyLearningStore(session, context)

    with open_scoped_store(context, learning_data.bound(context), sql_store, user_scoped=True) as store:
        yield store


# --------------------------------------------------------------------------- state


def learning_state(context: TenantContext, profile: LearningProfile | None) -> LearningState:
    """Whether ANUM learns from this user now; the server switch, theirs, then chat memory.

    "Remember things from my chats" off also stops learning: both are the user's say over
    what ANUM keeps from their work. A preference that cannot be read counts as off.
    """
    if not settings.learning_enabled:
        return LearningState.OFF_ON_SERVER
    if profile is not None and not profile.enabled:
        return LearningState.OFF_BY_USER
    from .user_preferences import remembers_conversations

    try:
        remembers = remembers_conversations(context)
    except Exception as exc:  # unknown: learn nothing
        logger.warning(
            "learning_failed stage=preference error_class=%s",
            type(exc).__name__,
            extra={"anum_learning": {"event": "failed", "stage": "preference", "error_class": type(exc).__name__}},
        )
        remembers = False
    if not remembers:
        return LearningState.OFF_CHAT_MEMORY
    return LearningState.ON


def current_state(context: TenantContext) -> tuple[LearningState, LearningProfile | None]:
    with open_learning_store(context) as store:
        profile = store.get_profile()
    return learning_state(context, profile), profile


def _log_failure(stage: str, exc: BaseException) -> None:
    logger.warning(
        "learning_failed stage=%s error_class=%s",
        stage,
        type(exc).__name__,
        extra={"anum_learning": {"event": "failed", "stage": stage, "error_class": type(exc).__name__}},
    )


# --------------------------------------------------------------------------- maintenance


def prune(store: LearningStore, now: datetime | None = None) -> int:
    """Delete this user's signals past retention, and the oldest beyond the per-user cap."""
    now = now or utc_now()
    removed = store.delete_signals(before=now - timedelta(days=settings.learning_signal_retention_days))
    return removed + store.prune_signals(MAX_SIGNALS_PER_USER)


def rederive(store: LearningStore, now: datetime | None = None) -> LearningProfile:
    """Recompute the learned style from the user's draft signals (overrides are kept)."""
    now = now or utc_now()
    profile = store.get_profile() or LearningProfile(updated_at=now)
    drafts = store.list_signals(kinds=DRAFT_KINDS, limit=200)
    profile.derived = derive_style(drafts)
    profile.derived_at = now
    profile.updated_at = now
    return store.save_profile(profile)


# --------------------------------------------------------------------------- hooks


def new_signal(
    kind: SignalKind,
    surface: Surface,
    *,
    subject_id: str | None = None,
    skill: str | None = None,
    tool: str | None = None,
    contact: str | None = None,
    original: str | None = None,
    final: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> LearningSignal:
    text = final or original or ""
    return LearningSignal(
        id=new_id("signal"),
        kind=kind,
        surface=surface,
        subject_id=subject_id,
        skill=skill,
        tool=tool,
        contact=contact.strip() if contact else None,
        language=detect_language(text) if text else None,
        original_text=original,
        final_text=final,
        reason=" ".join(reason.split())[:500] if reason else None,
        metadata=metadata or {},
        created_at=now or utc_now(),
    )


def record_server_signal(context: TenantContext, signal: LearningSignal) -> bool:
    """Record a signal the server observed (an approval decision, a retry) if learning is on.

    Best effort, in its own unit of work: a failure is logged and never fails the caller.
    """
    try:
        with open_learning_store(context) as store:
            if learning_state(context, store.get_profile()) != LearningState.ON:
                return False
            store.add_signal(signal)
        return True
    except Exception as exc:  # never fail the decision or retry that produced it
        _log_failure("record", exc)
        return False


def learned_instructions(
    context: TenantContext,
    *,
    surface: str,
    skill_ids: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    contact: str | None = None,
) -> str | None:
    """The labelled "learned preferences" block for a drafting prompt, or ``None``.

    ``None`` when learning is off for the user or nothing was learned yet; a store that
    cannot be read also gives ``None`` (the prompt is built without it).
    """
    try:
        with open_learning_store(context) as store:
            profile = store.get_profile()
            if learning_state(context, profile) != LearningState.ON:
                return None
            preferences = store.list_preferences(limit=100)
    except Exception as exc:
        _log_failure("instructions", exc)
        return None
    return render_learned_block(profile, preferences, surface=surface, skill_ids=skill_ids, tools=tools, contact=contact)


__all__ = [
    "LearningStore",
    "RoutineStatus",
    "current_state",
    "learned_instructions",
    "learning_data",
    "learning_state",
    "new_signal",
    "open_learning_store",
    "prune",
    "record_server_signal",
    "rederive",
]
