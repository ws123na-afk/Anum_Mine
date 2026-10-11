"""What ANUM learns from one user: models and the pure learning logic (docs/learning.md).

Everything here is deterministic and free of I/O, so it is unit-tested directly:

- :func:`detect_language` and :func:`summarize_edit` describe one draft and how the user
  changed it before sending;
- :func:`derive_style` turns accepted and edited drafts into a style profile (tone,
  length, greeting, sign-off, language overall and per contact), and
  :func:`effective_style` lays the user's own overrides over it;
- :func:`render_learned_block` writes the labelled system block a drafting prompt gets;
- :func:`detect_routines` finds repeated weekly sequences and daily habits in the user's
  activity;
- :func:`improvement_suggestions` turns a week's failures, rejections, low ratings and
  edits into concrete, applicable suggestions.

Storage, the learning switch and the API live in ``learning_store`` and ``learning_api``.
No model is called: learning works the same with every provider and spends no budget.
"""

from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .prompt_provenance import _defang

# --------------------------------------------------------------------------- enums


class SignalKind(StrEnum):
    RATING_UP = "rating_up"
    RATING_DOWN = "rating_down"
    DRAFT_ACCEPTED = "draft_accepted"
    DRAFT_EDITED = "draft_edited"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    RETRY = "retry"


RATING_KINDS = frozenset({SignalKind.RATING_UP, SignalKind.RATING_DOWN})
DRAFT_KINDS = frozenset({SignalKind.DRAFT_ACCEPTED, SignalKind.DRAFT_EDITED})


class Surface(StrEnum):
    CHAT = "chat"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    TASK = "task"
    APPROVAL = "approval"
    AUTOMATION = "automation"


# Surfaces a client may post feedback for; approvals and automation retries are recorded
# by the server when they happen.
CLIENT_SURFACES = frozenset({Surface.CHAT, Surface.EMAIL, Surface.WHATSAPP, Surface.TASK})
DRAFT_SURFACES = ("chat", "email", "whatsapp", "task")


class LearningState(StrEnum):
    """Whether ANUM learns from this user now, and if not, why."""

    ON = "on"
    OFF_BY_USER = "off_by_user"
    OFF_CHAT_MEMORY = "off_chat_memory"
    OFF_ON_SERVER = "off_on_server"


class RoutineStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"  # the automation exists, its schedule paused until confirmed
    CONFIRMED = "confirmed"  # the schedule was enabled
    DISMISSED = "dismissed"


class SuggestionKind(StrEnum):
    INSTRUCTION = "instruction"
    TEMPLATE = "template"
    AUTONOMY = "autonomy"


class SuggestionStatus(StrEnum):
    OPEN = "open"
    APPLIED = "applied"
    DISMISSED = "dismissed"


Tone = Literal["formal", "neutral", "casual"]
Length = Literal["short", "medium", "long"]
Language = Literal["ar", "en", "mixed"]

MAX_DRAFT_CHARS = 20_000
MAX_PREFERENCE_CHARS = 300
STYLE_SAMPLE_LIMIT = 200
_STYLE_VALUE_CHARS = 80

# --------------------------------------------------------------------------- models


class EditSummary(BaseModel):
    """How the user changed a draft before sending (counts only, no text)."""

    words_before: int
    words_after: int
    words_added: int
    words_removed: int
    changed_ratio: float = Field(description="0: unchanged, 1: rewritten")
    greeting_before: str | None = None
    greeting_after: str | None = None
    sign_off_before: str | None = None
    sign_off_after: str | None = None


class LearningSignal(BaseModel):
    id: str
    kind: SignalKind
    surface: Surface
    subject_id: str | None = None
    skill: str | None = None
    tool: str | None = None
    contact: str | None = None
    language: Language | None = None
    original_text: str | None = None
    final_text: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class StyleProfile(BaseModel):
    """What the drafts say about the user's style; ``None`` means not enough evidence."""

    tone: Tone | None = None
    length: Length | None = None
    target_words: int | None = None
    greeting: str | None = None
    sign_off: str | None = None
    language: Language | None = None
    contact_languages: dict[str, Language] = Field(default_factory=dict)
    emoji: bool | None = None
    # Average words sent / words drafted over edited drafts (below 1: the user shortens).
    edit_length_ratio: float | None = None
    samples: int = 0


class StyleOverrides(BaseModel):
    """The user's own choices; a field left ``None`` keeps what was learned.

    An empty greeting or sign-off means "none". ``notes`` is free-text guidance.
    """

    model_config = ConfigDict(extra="forbid")

    tone: Tone | None = None
    length: Length | None = None
    greeting: str | None = Field(default=None, max_length=_STYLE_VALUE_CHARS)
    sign_off: str | None = Field(default=None, max_length=_STYLE_VALUE_CHARS)
    language: Language | None = None
    contact_languages: dict[str, Language] | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _bounded_contacts(self) -> StyleOverrides:
        if self.contact_languages is not None:
            if len(self.contact_languages) > 100:
                raise ValueError("At most 100 contacts")
            if any(not key.strip() or len(key) > 160 for key in self.contact_languages):
                raise ValueError("Contact keys must be 1 to 160 characters")
        return self


class LearningProfile(BaseModel):
    enabled: bool = True
    derived: StyleProfile = Field(default_factory=StyleProfile)
    overrides: StyleOverrides = Field(default_factory=StyleOverrides)
    derived_at: datetime | None = None
    updated_at: datetime | None = None


class LearnedPreference(BaseModel):
    id: str
    # ``all``, ``surface:<chat|email|whatsapp|task>``, ``skill:<id>``, ``tool:<name>`` or
    # ``contact:<key>``.
    scope: str = "all"
    text: str
    source: Literal["user", "suggestion"] = "user"
    suggestion_id: str | None = None
    created_at: datetime
    updated_at: datetime


class RoutineStep(BaseModel):
    key: str
    label: str
    prompt: str
    source: Literal["task", "automation"] = "task"


class Routine(BaseModel):
    id: str
    fingerprint: str
    status: RoutineStatus = RoutineStatus.PROPOSED
    title: str
    cadence: Literal["daily", "weekly"]
    cron: str
    timezone: str = "UTC"
    steps: list[RoutineStep]
    occurrences: int = 0
    weeks_observed: int = 0
    confidence: float = 0.0
    last_seen_at: datetime | None = None
    workflow_id: str | None = None
    schedule_id: str | None = None
    detected_at: datetime
    decided_at: datetime | None = None


class Suggestion(BaseModel):
    id: str
    week: str
    fingerprint: str
    rule: str
    kind: SuggestionKind
    title: str
    body: str
    target: str | None = None
    change: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: SuggestionStatus = SuggestionStatus.OPEN
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None


class NoteSummary(BaseModel):
    ratings_up: int = 0
    ratings_down: int = 0
    drafts_accepted: int = 0
    drafts_edited: int = 0
    approvals_approved: int = 0
    approvals_rejected: int = 0
    retries: int = 0
    run_failures: int = 0


class ImprovementNote(BaseModel):
    week: str
    period_start: datetime
    period_end: datetime
    summary: NoteSummary
    generated_at: datetime


# --------------------------------------------------------------------------- text analysis

_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"\w+", re.UNICODE)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

# Greeting and sign-off phrases, longest first so "good morning" wins over "good".
_GREETINGS = sorted(
    [
        "assalamu alaikum", "good morning", "good afternoon", "good evening", "dear", "hello", "hi", "hey",
        "greetings", "السلام عليكم ورحمة الله", "السلام عليكم", "صباح الخير", "مساء الخير", "مرحبا", "مرحباً",
        "أهلا", "أهلاً", "هلا", "عزيزي", "عزيزتي", "الأستاذ", "السادة",
    ],
    key=len,
    reverse=True,
)
_SIGN_OFFS = sorted(
    [
        "best regards", "kind regards", "warm regards", "regards", "sincerely", "many thanks", "thank you",
        "thanks", "cheers", "best", "مع خالص التحية", "مع التحية", "تحياتي", "شكرا", "شكراً", "وتفضلوا بقبول فائق الاحترام",
        "مع الشكر", "دمتم بخير",
    ],
    key=len,
    reverse=True,
)
_FORMAL = (
    "dear", "regards", "sincerely", "kindly", "please find", "we would like", "i would like", "attached",
    "السادة", "تحية طيبة", "وتفضلوا", "مع خالص", "المحترم", "نفيدكم", "يرجى",
)
_CASUAL = ("hey", "cheers", "thanks!", "lol", "btw", "gonna", "هلا", "تمام", "يا هلا", "حبيبي", "ok", "okay")
_CONTRACTION = re.compile(r"\b\w+'(m|re|s|ll|ve|d|t)\b", re.IGNORECASE)


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def detect_language(text: str) -> Language | None:
    """``ar`` or ``en`` when one script clearly dominates the letters, ``mixed`` otherwise."""
    arabic = len(_ARABIC.findall(text))
    latin = len(_LATIN.findall(text))
    letters = arabic + latin
    if letters < 3:
        return None
    if arabic / letters >= 0.7:
        return "ar"
    if latin / letters >= 0.7:
        return "en"
    return "mixed"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.strip().splitlines() if line.strip()]


def _match_phrase(line: str, phrases: Sequence[str]) -> str | None:
    lowered = line.lower().strip(" ,.!،:")
    for phrase in phrases:
        if lowered == phrase or lowered.startswith(phrase + " ") or lowered.startswith(phrase + ","):
            return phrase
        if lowered.startswith(phrase + "،") or lowered.startswith(phrase + "!"):
            return phrase
    return None


def _original_case(line: str, phrase: str) -> str:
    """The phrase as the user wrote it ("Best regards", not "best regards")."""
    stripped = line.strip()
    return stripped[: len(phrase)] if stripped.lower().startswith(phrase) else phrase


def greeting_of(text: str) -> str | None:
    lines = _lines(text)
    if not lines:
        return None
    first = lines[0]
    phrase = _match_phrase(first, _GREETINGS)
    return _original_case(first, phrase) if phrase else None


def sign_off_of(text: str) -> str | None:
    lines = _lines(text)
    # The sign-off is one of the last two lines (the last may be a name).
    for line in reversed(lines[-2:]):
        if len(line.split()) > 6:
            continue
        phrase = _match_phrase(line, _SIGN_OFFS)
        if phrase:
            return _original_case(line, phrase)
    return None


def tone_of(text: str) -> Tone:
    lowered = text.lower()
    formal = sum(lowered.count(marker) for marker in _FORMAL)
    casual = sum(lowered.count(marker) for marker in _CASUAL)
    casual += len(_CONTRACTION.findall(text)) + len(_EMOJI.findall(text)) + text.count("!") // 2
    if formal > casual:
        return "formal"
    if casual > formal:
        return "casual"
    return "neutral"


def summarize_edit(original: str, final: str) -> EditSummary:
    before, after = _WORD.findall(original), _WORD.findall(final)
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    added = removed = 0
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op in {"replace", "delete"}:
            removed += a2 - a1
        if op in {"replace", "insert"}:
            added += b2 - b1
    return EditSummary(
        words_before=len(before),
        words_after=len(after),
        words_added=added,
        words_removed=removed,
        changed_ratio=round(1.0 - matcher.ratio(), 3) if before or after else 0.0,
        greeting_before=greeting_of(original),
        greeting_after=greeting_of(final),
        sign_off_before=sign_off_of(original),
        sign_off_after=sign_off_of(final),
    )


# --------------------------------------------------------------------------- style


def _most_common(values: Iterable[str | None], minimum: int = 2) -> str | None:
    counts = Counter(value for value in values if value)
    if not counts:
        return None
    value, count = counts.most_common(1)[0]
    return value if count >= minimum else None


def _length_band(words: int) -> Length:
    if words <= 40:
        return "short"
    if words <= 120:
        return "medium"
    return "long"


def derive_style(signals: Sequence[LearningSignal]) -> StyleProfile:
    """The style shown by the user's accepted and edited drafts (newest first, bounded).

    What the user *sent* counts (the final text), not what ANUM drafted. Greetings and
    sign-offs need at least two drafts that agree; per-contact language takes the
    contact's most common language.
    """
    drafts = [
        signal
        for signal in signals
        if signal.kind in DRAFT_KINDS and (signal.final_text or "").strip()
    ][:STYLE_SAMPLE_LIMIT]
    if not drafts:
        return StyleProfile()
    finals = [signal.final_text or "" for signal in drafts]
    words = [word_count(text) for text in finals]
    target = int(statistics.median(words))
    tones = Counter(tone_of(text) for text in finals)
    tone: Tone = tones.most_common(1)[0][0]
    languages = [signal.language or detect_language(signal.final_text or "") for signal in drafts]
    per_contact: dict[str, Counter[str]] = defaultdict(Counter)
    for signal, language in zip(drafts, languages, strict=True):
        if signal.contact and language in {"ar", "en"}:
            per_contact[signal.contact][language] += 1
    ratios = [
        signal.metadata["edit"]["words_after"] / signal.metadata["edit"]["words_before"]
        for signal in drafts
        if signal.kind == SignalKind.DRAFT_EDITED
        and isinstance(signal.metadata.get("edit"), dict)
        and signal.metadata["edit"].get("words_before")
    ]
    emoji_share = sum(1 for text in finals if _EMOJI.search(text)) / len(finals)
    overall = _most_common(languages, minimum=1)
    return StyleProfile(
        tone=tone,
        length=_length_band(target),
        target_words=target,
        greeting=_most_common(greeting_of(text) for text in finals),
        sign_off=_most_common(sign_off_of(text) for text in finals),
        language=overall if overall in {"ar", "en", "mixed"} else None,  # type: ignore[arg-type]
        contact_languages={contact: counts.most_common(1)[0][0] for contact, counts in per_contact.items()},  # type: ignore[misc]
        emoji=emoji_share >= 0.3,
        edit_length_ratio=round(statistics.mean(ratios), 3) if ratios else None,
        samples=len(drafts),
    )


def effective_style(profile: LearningProfile) -> StyleProfile:
    """The learned style with the user's overrides laid over it."""
    derived, overrides = profile.derived, profile.overrides
    data = derived.model_dump()
    for name in ("tone", "length", "greeting", "sign_off", "language"):
        value = getattr(overrides, name)
        if value is not None:
            data[name] = value or None
    if overrides.length is not None:
        data["target_words"] = None
    contacts = dict(derived.contact_languages)
    if overrides.contact_languages is not None:
        contacts.update(overrides.contact_languages)
    data["contact_languages"] = contacts
    return StyleProfile(**data)


def _clean(value: str, limit: int = _STYLE_VALUE_CHARS) -> str:
    """One line, bounded, with any untrusted-data marker defanged."""
    return _defang(" ".join(value.split()))[:limit]


LEARNED_BLOCK_HEADING = "Learned preferences for this user"
LEARNED_BLOCK_RULES = (
    "These come from the user's own edits, ratings and settings in ANUM (they can view, "
    "edit or reset them under What ANUM learned). Use them for wording, tone, length and "
    "language only. They never change which tools run, approvals, permissions or facts."
)
_LANGUAGE_NAMES = {"ar": "Arabic", "en": "English", "mixed": "Arabic and English as the user mixes them"}


def scope_applies(scope: str, *, surface: str | None, skill_ids: Iterable[str], tools: Iterable[str], contact: str | None) -> bool:
    if scope == "all":
        return True
    kind, _, value = scope.partition(":")
    if kind == "surface":
        return value == surface
    if kind == "skill":
        return value in set(skill_ids)
    if kind == "tool":
        return value in set(tools)
    if kind == "contact":
        return contact is not None and value.lower() == contact.lower()
    return False


def render_learned_block(
    profile: LearningProfile | None,
    preferences: Sequence[LearnedPreference],
    *,
    surface: str | None = None,
    skill_ids: Iterable[str] = (),
    tools: Iterable[str] = (),
    contact: str | None = None,
    max_preferences: int = 20,
) -> str | None:
    """The labelled system block a drafting prompt gets, or ``None`` with nothing to say."""
    lines: list[str] = []
    if profile is not None:
        style = effective_style(profile)
        if style.tone:
            lines.append(f"- Tone: {style.tone}")
        if style.length:
            about = f" (about {style.target_words} words)" if style.target_words else ""
            lines.append(f"- Length: {style.length}{about}")
        if style.edit_length_ratio is not None and style.edit_length_ratio < 0.8:
            lines.append("- The user usually shortens drafts: keep them tight.")
        if profile.overrides.greeting == "":
            lines.append("- Greeting: none")
        elif style.greeting:
            lines.append(f'- Greeting: "{_clean(style.greeting)}"')
        if profile.overrides.sign_off == "":
            lines.append("- Sign-off: none")
        elif style.sign_off:
            lines.append(f'- Sign-off: "{_clean(style.sign_off)}"')
        language = style.language
        if contact and contact in style.contact_languages:
            language = style.contact_languages[contact]
            lines.append(f"- Language for {_clean(contact)}: {_LANGUAGE_NAMES[language]}")
        elif language:
            lines.append(f"- Language: {_LANGUAGE_NAMES[language]}, unless the user writes in another")
        if style.emoji is False and style.samples:
            lines.append("- No emoji.")
        if profile.overrides.notes:
            lines.append(f"- Notes from the user: {_clean(profile.overrides.notes, 500)}")
    chosen = [
        item
        for item in preferences
        if scope_applies(item.scope, surface=surface, skill_ids=skill_ids, tools=tools, contact=contact)
    ][:max_preferences]
    for item in chosen:
        lines.append(f"- {_clean(item.text, MAX_PREFERENCE_CHARS)}")
    if not lines:
        return None
    return f"{LEARNED_BLOCK_HEADING} ({LEARNED_BLOCK_RULES})\n" + "\n".join(lines)


# --------------------------------------------------------------------------- routines

_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "my", "our", "for", "to", "of", "and", "please", "today", "now", "this", "that", "me",
        "in", "on", "at", "with", "all", "any", "new", "latest", "can", "you", "من", "في", "على", "إلى",
        "عن", "مع", "لو", "سمحت", "اليوم", "الآن", "هذا", "هذه",
    }
)


def activity_key(prompt: str) -> str:
    """A short normalized signature, so "Check Amazon orders" and "check amazon orders today" match."""
    tokens = [token for token in _WORD.findall(prompt.lower()) if not token.isdigit() and token not in _STOP_WORDS]
    return " ".join(tokens[:3])


@dataclass(frozen=True)
class Activity:
    key: str
    label: str
    prompt: str
    at: datetime
    source: Literal["task", "automation"] = "task"


@dataclass
class RoutineCandidate:
    fingerprint: str
    title: str
    cadence: Literal["daily", "weekly"]
    cron: str
    timezone: str
    steps: list[RoutineStep]
    occurrences: int
    weeks_observed: int
    confidence: float
    last_seen_at: datetime
    keys: tuple[str, ...] = field(default_factory=tuple)


_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_SEQUENCE_WINDOW = timedelta(hours=3)


def _cron_weekday(python_weekday: int) -> int:
    """Python's Monday=0 to cron's Sunday=0."""
    return (python_weekday + 1) % 7


def _week_of(moment: datetime) -> tuple[int, int]:
    iso = moment.isocalendar()
    return iso[0], iso[1]


def detect_routines(
    activities: Sequence[Activity],
    *,
    now: datetime,
    zone: Any,
    timezone_name: str = "UTC",
    min_weeks: int = 3,
    min_days: int = 5,
    lookback_weeks: int = 8,
) -> list[RoutineCandidate]:
    """Repeated weekly sequences and daily habits in the user's own activity.

    - **Weekly**: an activity (or an ordered sequence of up to three, each within three
      hours of the previous) on the same weekday around the same hour in at least
      ``min_weeks`` different weeks. A sequence replaces the single-step routines of its
      first step.
    - **Daily**: an activity around the same hour on at least ``min_days`` different days
      of the last two weeks, not already part of a weekly routine.

    Times are read in ``zone`` (the user's timezone); the cron fires on the hour the
    routine usually starts.
    """
    since = now - timedelta(weeks=lookback_weeks)
    local = sorted(
        (
            Activity(item.key, item.label, item.prompt, item.at.astimezone(zone), item.source)
            for item in activities
            if item.key and since <= item.at <= now
        ),
        key=lambda item: item.at,
    )
    if not local:
        return []
    weeks_observed = max(1, min(lookback_weeks, ((now - min(item.at for item in local)).days // 7) + 1))
    latest: dict[str, Activity] = {}
    for item in local:
        latest[item.key] = item
    by_day: dict[tuple[int, int, int], list[Activity]] = defaultdict(list)  # (iso year, week, weekday)
    for item in local:
        year, week = _week_of(item.at)
        by_day[(year, week, item.at.weekday())].append(item)

    candidates: list[RoutineCandidate] = []
    covered: set[str] = set()
    for weekday in range(7):
        days = {key: items for key, items in by_day.items() if key[2] == weekday}
        # Keys seen on this weekday, with the weeks they were seen in and their hours.
        seen: dict[str, dict[tuple[int, int], Activity]] = defaultdict(dict)
        for (year, week, _), items in days.items():
            for item in items:
                seen[item.key].setdefault((year, week), item)
        starters = [
            key
            for key, weeks in seen.items()
            if len(weeks) >= min_weeks and _clustered(item.at for item in weeks.values())
        ]
        for start in sorted(starters, key=lambda key: -len(seen[key])):
            if start in covered:
                continue
            sequence = [start]
            weeks = set(seen[start])
            while len(sequence) < 3:
                follower = _best_follower(sequence, weeks, days, min_weeks)
                if follower is None:
                    break
                next_key, next_weeks = follower
                sequence.append(next_key)
                weeks = next_weeks
            hours = [seen[start][week].at for week in weeks]
            hour = int(statistics.median(moment.hour for moment in hours))
            steps = [_step(latest[key]) for key in sequence]
            last = max(seen[sequence[-1]][week].at for week in weeks if week in seen[sequence[-1]])
            day_name = _DAY_NAMES[weekday]
            title = f"Every {day_name} around {hour:02d}:00: " + ", then ".join(step.label for step in steps)
            candidates.append(
                RoutineCandidate(
                    fingerprint=f"weekly:{weekday}:{hour}:" + ">".join(sequence),
                    title=title[:200],
                    cadence="weekly",
                    cron=f"0 {hour} * * {_cron_weekday(weekday)}",
                    timezone=timezone_name,
                    steps=steps,
                    occurrences=len(weeks),
                    weeks_observed=weeks_observed,
                    confidence=round(min(1.0, len(weeks) / weeks_observed), 2),
                    last_seen_at=last.astimezone(timezone.utc),
                    keys=tuple(sequence),
                )
            )
            covered.update(sequence)

    recent = [item for item in local if item.at >= now.astimezone(zone) - timedelta(days=14)]
    daily: dict[str, dict[Any, Activity]] = defaultdict(dict)
    for item in recent:
        daily[item.key].setdefault(item.at.date(), item)
    for key, by_date in sorted(daily.items()):
        if key in covered or len(by_date) < min_days or not _clustered(item.at for item in by_date.values()):
            continue
        moments = list(by_date.values())
        hour = int(statistics.median(item.at.hour for item in moments))
        weekdays = sorted({_cron_weekday(item.at.weekday()) for item in moments})
        days_field = "*" if len(weekdays) >= 6 else ",".join(str(day) for day in weekdays)
        step = _step(latest[key])
        candidates.append(
            RoutineCandidate(
                fingerprint=f"daily:{hour}:{days_field}:{key}",
                title=f"Daily around {hour:02d}:00: {step.label}"[:200],
                cadence="daily",
                cron=f"0 {hour} * * {days_field}",
                timezone=timezone_name,
                steps=[step],
                occurrences=len(by_date),
                weeks_observed=weeks_observed,
                confidence=round(min(1.0, len(by_date) / 14), 2),
                last_seen_at=max(item.at for item in moments).astimezone(timezone.utc),
                keys=(key,),
            )
        )
    return candidates


def _clustered(moments: Iterable[datetime], spread_hours: int = 2) -> bool:
    hours = [moment.hour for moment in moments]
    return bool(hours) and max(hours) - min(hours) <= spread_hours


def _best_follower(
    sequence: list[str],
    weeks: set[tuple[int, int]],
    days: dict[tuple[int, int, int], list[Activity]],
    min_weeks: int,
) -> tuple[str, set[tuple[int, int]]] | None:
    """The activity that most often follows the sequence's last step within the window."""
    followers: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for (year, week, _), items in days.items():
        if (year, week) not in weeks:
            continue
        last = next((item for item in reversed(items) if item.key == sequence[-1]), None)
        if last is None:
            continue
        for item in items:
            if item.key in sequence or item.at <= last.at or item.at - last.at > _SEQUENCE_WINDOW:
                continue
            followers[item.key].add((year, week))
    best = max(followers.items(), key=lambda pair: (len(pair[1]), pair[0]), default=None)
    if best is None or len(best[1]) < min_weeks:
        return None
    return best[0], best[1]


def _step(item: Activity) -> RoutineStep:
    label = " ".join(item.prompt.split())
    label = label if len(label) <= 80 else label[:79].rstrip() + "…"
    return RoutineStep(key=item.key, label=label, prompt=item.prompt[:2000], source=item.source)


# --------------------------------------------------------------------------- suggestions


@dataclass(frozen=True)
class RunFailure:
    tool: str | None
    skill_ids: tuple[str, ...]
    reason: str
    risk_level: str | None = None
    approved: bool = False


@dataclass
class SuggestionDraft:
    rule: str
    kind: SuggestionKind
    title: str
    body: str
    target: str | None
    change: dict[str, Any]
    evidence: dict[str, Any]

    def fingerprint(self, week: str) -> str:
        return f"{week}:{self.rule}:{self.target or '-'}"[:200]


FAILURE_THRESHOLD = 2
REJECTION_THRESHOLD = 2
NEGATIVE_THRESHOLD = 2
EDIT_THRESHOLD = 3
LOOSEN_THRESHOLD = 5


def iso_week(moment: datetime) -> str:
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def note_summary(signals: Sequence[LearningSignal], failures: Sequence[RunFailure]) -> NoteSummary:
    counts = Counter(signal.kind for signal in signals)
    return NoteSummary(
        ratings_up=counts[SignalKind.RATING_UP],
        ratings_down=counts[SignalKind.RATING_DOWN],
        drafts_accepted=counts[SignalKind.DRAFT_ACCEPTED],
        drafts_edited=counts[SignalKind.DRAFT_EDITED],
        approvals_approved=counts[SignalKind.APPROVAL_APPROVED],
        approvals_rejected=counts[SignalKind.APPROVAL_REJECTED],
        retries=counts[SignalKind.RETRY],
        run_failures=len(failures),
    )


def _top_reason(reasons: Iterable[str | None]) -> str | None:
    found = Counter(" ".join(reason.split())[:160] for reason in reasons if reason and reason.strip())
    return found.most_common(1)[0][0] if found else None


def _preference_change(scope: str, text: str) -> dict[str, Any]:
    return {"type": "preference", "scope": scope, "text": text[:MAX_PREFERENCE_CHARS]}


def improvement_suggestions(
    week_signals: Sequence[LearningSignal],
    month_signals: Sequence[LearningSignal],
    failures: Sequence[RunFailure],
    *,
    medium_risk_requires_approval: bool,
) -> list[SuggestionDraft]:
    """Concrete changes for this week, from the user's own signals and failed runs.

    ``week_signals`` are the last 7 days, ``month_signals`` the last 28 (for autonomy,
    which needs a longer record). Applying is always a separate, explicit step.
    """
    drafts: list[SuggestionDraft] = []

    by_tool: dict[str, list[RunFailure]] = defaultdict(list)
    for failure in failures:
        if failure.tool:
            by_tool[failure.tool].append(failure)
    for tool, items in sorted(by_tool.items()):
        if len(items) < FAILURE_THRESHOLD:
            continue
        reason = _top_reason(item.reason for item in items)
        drafts.append(
            SuggestionDraft(
                rule="tool_failures",
                kind=SuggestionKind.INSTRUCTION,
                title=f"{tool} failed {len(items)} times this week",
                body=(
                    f"Runs that used {tool} failed {len(items)} times"
                    + (f', most often with: "{reason}"' if reason else "")
                    + ". Applying adds an instruction to check its inputs first and ask you for anything missing."
                ),
                target=f"tool:{tool}",
                change=_preference_change(
                    f"tool:{tool}",
                    f"Before using {tool}, confirm the inputs it needs and ask me for anything missing instead of guessing.",
                ),
                evidence={"failures": len(items), "example_error": reason},
            )
        )

    rejected: dict[str, list[LearningSignal]] = defaultdict(list)
    for signal in week_signals:
        if signal.kind == SignalKind.APPROVAL_REJECTED and signal.tool:
            rejected[signal.tool].append(signal)
    for tool, items in sorted(rejected.items()):
        if len(items) < REJECTION_THRESHOLD:
            continue
        reason = _top_reason(item.reason for item in items)
        text = (
            f'When proposing {tool}, account for why I rejected it before: "{reason}".'
            if reason
            else f"Ask me to confirm the details before proposing {tool}."
        )
        drafts.append(
            SuggestionDraft(
                rule="approval_rejections",
                kind=SuggestionKind.INSTRUCTION,
                title=f"You rejected {tool} {len(items)} times",
                body=f"You rejected {len(items)} proposed {tool} actions this week. Applying adds this instruction: {text}",
                target=f"tool:{tool}",
                change=_preference_change(f"tool:{tool}", text),
                evidence={"rejections": len(items), "top_reason": reason},
            )
        )

    for surface in DRAFT_SURFACES:
        mine = [signal for signal in week_signals if signal.surface == surface]
        down = [signal for signal in mine if signal.kind == SignalKind.RATING_DOWN]
        retries = [signal for signal in mine if signal.kind == SignalKind.RETRY]
        up = [signal for signal in mine if signal.kind == SignalKind.RATING_UP]
        negative = len(down) + len(retries)
        if negative >= NEGATIVE_THRESHOLD and negative >= len(up):
            reason = _top_reason(item.reason for item in down)
            shortened = [
                signal.metadata["edit"]
                for signal in mine
                if signal.kind == SignalKind.DRAFT_EDITED and isinstance(signal.metadata.get("edit"), dict)
            ]
            shorter = bool(shortened) and statistics.mean(
                edit["words_after"] / max(1, edit["words_before"]) for edit in shortened
            ) < 0.8
            text = (
                f"Keep {surface} answers short and lead with the answer in the first sentence."
                if shorter or (reason and any(word in reason.lower() for word in ("long", "short", "verbose", "طويل")))
                else f"When a {surface} request is unclear, ask one clarifying question before answering."
            )
            drafts.append(
                SuggestionDraft(
                    rule="low_ratings",
                    kind=SuggestionKind.TEMPLATE,
                    title=f"{surface.capitalize()} answers missed {negative} times",
                    body=(
                        f"This week {len(down)} {surface} answers were rated down and {len(retries)} were retried"
                        + (f' (reason given: "{reason}")' if reason else "")
                        + f". Applying adds this to your {surface} template: {text}"
                    ),
                    target=f"surface:{surface}",
                    change=_preference_change(f"surface:{surface}", text),
                    evidence={"ratings_down": len(down), "retries": len(retries), "ratings_up": len(up), "top_reason": reason},
                )
            )
        edited = [signal for signal in mine if signal.kind == SignalKind.DRAFT_EDITED and isinstance(signal.metadata.get("edit"), dict)]
        if len(edited) >= EDIT_THRESHOLD:
            edits = [signal.metadata["edit"] for signal in edited]
            changed = statistics.mean(edit.get("changed_ratio", 0) for edit in edits)
            if changed < 0.25:
                continue
            ratio = statistics.mean(edit["words_after"] / max(1, edit["words_before"]) for edit in edits)
            details: list[str] = []
            if ratio < 0.8:
                details.append(f"about {round((1 - ratio) * 100)}% shorter")
            elif ratio > 1.25:
                details.append(f"about {round((ratio - 1) * 100)}% longer")
            greeting = _most_common((edit.get("greeting_after") for edit in edits if edit.get("greeting_after") != edit.get("greeting_before")))
            if greeting:
                details.append(f'opening with "{_clean(greeting)}"')
            sign_off = _most_common((edit.get("sign_off_after") for edit in edits if edit.get("sign_off_after") != edit.get("sign_off_before")))
            if sign_off:
                details.append(f'signing off with "{_clean(sign_off)}"')
            text = (
                f"Write {surface} drafts " + ", ".join(details) + "."
                if details
                else f"Follow my style profile closely for {surface} drafts; I rewrite much of them."
            )
            drafts.append(
                SuggestionDraft(
                    rule="heavy_edits",
                    kind=SuggestionKind.TEMPLATE,
                    title=f"You rewrote {len(edited)} {surface} drafts",
                    body=(
                        f"You changed about {round(changed * 100)}% of the words in {len(edited)} {surface} drafts this week. "
                        f"Applying adds this to your {surface} template: {text}"
                    ),
                    target=f"surface:{surface}:edits",
                    change=_preference_change(f"surface:{surface}", text),
                    evidence={"drafts_edited": len(edited), "changed_ratio": round(changed, 2), "length_ratio": round(ratio, 2)},
                )
            )

    medium = [signal for signal in month_signals if signal.metadata.get("risk_level") == "medium"]
    approved = [signal for signal in medium if signal.kind == SignalKind.APPROVAL_APPROVED]
    rejected_medium = [signal for signal in medium if signal.kind == SignalKind.APPROVAL_REJECTED]
    if medium_risk_requires_approval and len(approved) >= LOOSEN_THRESHOLD and not rejected_medium:
        drafts.append(
            SuggestionDraft(
                rule="autonomy_loosen",
                kind=SuggestionKind.AUTONOMY,
                title="Let medium-risk actions run without asking",
                body=(
                    f"You approved all {len(approved)} medium-risk actions in the last four weeks and rejected none. "
                    "Applying turns off the workspace setting that asks for approval of medium-risk actions; high-risk "
                    "actions still need approval. Only a workspace owner can apply it, and it is audited."
                ),
                target="approval_policy:medium_risk",
                change={"type": "approval_policy", "medium_risk_requires_approval": False},
                evidence={"approved": len(approved), "rejected": 0, "days": 28},
            )
        )
    unapproved = [failure for failure in failures if failure.risk_level == "medium" and not failure.approved]
    if not medium_risk_requires_approval and len(unapproved) >= FAILURE_THRESHOLD:
        drafts.append(
            SuggestionDraft(
                rule="autonomy_tighten",
                kind=SuggestionKind.AUTONOMY,
                title="Ask before medium-risk actions",
                body=(
                    f"{len(unapproved)} medium-risk actions that ran without approval failed this week. Applying makes the "
                    "workspace ask for approval before medium-risk actions. Only a workspace owner can apply it, and it is audited."
                ),
                target="approval_policy:medium_risk",
                change={"type": "approval_policy", "medium_risk_requires_approval": True},
                evidence={"failures": len(unapproved)},
            )
        )
    return drafts
