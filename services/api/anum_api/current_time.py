"""The current date and time for model prompts (docs/model-gateway.md#current-date-and-time).

A model has no clock: asked "what day is it?", it can only guess or say it does not know.
Every prompt ANUM sends for a user (chat, the answer above a tool card, voice, task planning,
knowledge base answers) therefore carries one short line such as::

    Current date and time: Wednesday 4 November 2026, 14:32 (Asia/Dubai, UTC+04:00).
    Use this for today, tomorrow, deadlines and relative dates.

in the user's language (Arabic weekday and month names for Arabic, Gregorian calendar).

* **Time zone**, first that is a valid IANA name: the user's own team profile (Team, the
  person's profile; ``UTC``, the old form default, counts as not set), the workspace's first
  office building, then ``ANUM_DEFAULT_TIME_ZONE`` (Asia/Dubai), then Asia/Dubai.
* **Clock**: :func:`now` reads one injectable clock; tests freeze it with :func:`frozen`.
* **Prompt caching**: callers put the line last in the system text (:func:`with_time_line`).
  The Anthropic gateway takes it out of the cached system prefix and sends it after the last
  cache breakpoint (``anthropic_gateway.build_messages_request``), so a line that changes
  every minute never invalidates the cached instructions or conversation.

:func:`time_line_for` reads the database: call it in a worker thread, never on the event loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import settings

if TYPE_CHECKING:
    from .schemas import TenantContext

logger = logging.getLogger(__name__)

FALLBACK_TIME_ZONE = "Asia/Dubai"
# A profile saved before anyone chose a zone says UTC: it is not the user's choice.
_UNSET_PROFILE_ZONES = frozenset({"UTC", "Etc/UTC", "Etc/UCT", "UCT", "Universal", "Zulu"})

PREFIX = {"en": "Current date and time:", "ar": "التاريخ والوقت الآن:"}
RULE = {
    "en": "Use this for today, tomorrow, deadlines and relative dates.",
    "ar": "استخدم هذا لليوم والغد والمواعيد النهائية والتواريخ النسبية.",
}
_WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "ar": ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"),
}
_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"),
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر",
           "نوفمبر", "ديسمبر"),
}


def _system_clock() -> datetime:
    return datetime.now(UTC)


_clock: Callable[[], datetime] = _system_clock


def now() -> datetime:
    """The current instant (timezone-aware UTC) from the injectable clock."""
    value = _clock()
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def set_clock(clock: Callable[[], datetime] | None) -> Callable[[], datetime]:
    """Replace the clock (``None`` restores the system clock); returns the previous one."""
    global _clock
    previous = _clock
    _clock = clock or _system_clock
    return previous


@contextmanager
def frozen(at: datetime) -> Iterator[datetime]:
    """Freeze :func:`now` at ``at`` (tests)."""
    previous = set_clock(lambda: at)
    try:
        yield at
    finally:
        set_clock(previous)


def _zone(name: str | None) -> ZoneInfo | None:
    if not name or not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def resolve_zone(user_zone: str | None = None, workspace_zone: str | None = None) -> str:
    """The first valid zone of the user's profile, the workspace and the default setting."""
    if user_zone and user_zone.strip() not in _UNSET_PROFILE_ZONES and _zone(user_zone):
        return user_zone.strip()
    if _zone(workspace_zone):
        return str(workspace_zone).strip()
    if _zone(settings.default_time_zone):
        return settings.default_time_zone.strip()
    logger.warning("default_time_zone_invalid", extra={"anum_time": {"event": "default_zone_invalid"}})
    return FALLBACK_TIME_ZONE


def _offset(moment: datetime) -> str:
    delta = moment.utcoffset()
    minutes = int(delta.total_seconds() // 60) if delta is not None else 0
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{rest:02d}"


def format_time_line(at: datetime, zone_name: str, language: str = "en") -> str:
    """The prompt line for ``at`` in ``zone_name``, in English or Arabic."""
    lang = "ar" if language == "ar" else "en"
    zone = _zone(zone_name) or ZoneInfo(FALLBACK_TIME_ZONE)
    local = (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(zone)
    weekday, month = _WEEKDAYS[lang][local.weekday()], _MONTHS[lang][local.month - 1]
    sep = "،" if lang == "ar" else ","
    stamp = f"{weekday} {local.day} {month} {local.year}{sep} {local:%H:%M}"
    return f"{PREFIX[lang]} {stamp} ({zone.key}{sep} {_offset(local)}). {RULE[lang]}"


def stored_zones(context: TenantContext) -> tuple[str | None, str | None]:
    """(the user's profile zone, the workspace's first office building zone); blocking.

    Reads in its own short transaction in the caller's tenant scope. Never raises: a failed
    read leaves the line on the default zone.
    """
    try:
        if settings.repository_backend == "postgresql":
            return _stored_zones_sql(context)
        from .team_store import memory_team_store

        profile = memory_team_store.list_profiles(context).get(context.user_id)
        return (profile.timezone if profile else None), None
    except Exception as exc:  # the date line is a hint: never fail the reply for it
        logger.warning(
            "time_zone_lookup_failed error_class=%s",
            type(exc).__name__,
            extra={"anum_time": {"event": "zone_lookup_failed", "error_class": type(exc).__name__}},
        )
        return None, None


def _stored_zones_sql(context: TenantContext) -> tuple[str | None, str | None]:
    from sqlalchemy import text

    from .db.session import SessionLocal, set_tenant_context

    scope = {"t": context.tenant_id, "w": context.workspace_id, "u": context.user_id}
    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
        user = session.execute(
            text("select timezone from team_member_profiles where tenant_id = :t and workspace_id = :w and user_id = :u"),
            scope,
        ).scalar()
        building = session.execute(
            text(
                "select timezone from office_buildings where tenant_id = :t and workspace_id = :w "
                "order by created_at, id limit 1"
            ),
            scope,
        ).scalar()
        session.rollback()
        return user, building
    finally:
        session.close()


def time_line_for(context: TenantContext, language: str = "en") -> str:
    """The date line for this user now (blocking: run it in a worker thread)."""
    return format_time_line(now(), resolve_zone(*stored_zones(context)), language)


def is_time_line(text: str) -> bool:
    return text.startswith(tuple(PREFIX.values()))


def with_time_line(text: str, line: str | None) -> str:
    """``text`` with the date line as its last paragraph (where the gateway looks for it)."""
    return f"{text}\n\n{line}" if line else text


def split_time_line(text: str) -> tuple[str, str | None]:
    """(``text`` without a trailing date line, the line or ``None``)."""
    head, sep, tail = text.rpartition("\n\n")
    if sep and is_time_line(tail):
        return head, tail
    if not sep and is_time_line(text):
        return "", text
    return text, None
