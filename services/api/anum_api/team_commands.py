"""Turn "ANUM, ask Ahmed to call the supplier by Thursday" into an assignment.

Deterministic on purpose (docs/team-work.md#assigning-in-words): the person, the work and
the due date come from the user's own sentence by these rules, never from model output or
retrieved text (threat model G5), and the result is shown before anything is created when
the request comes from a screen. Supported shapes, case-insensitive, an optional leading
"ANUM," is ignored:

* ``ask|tell|remind|get <person> to <work> [by|before|on|due|until <when>]``
* ``assign <work> to <person> [by ... <when>]``
* Arabic: ``اطلب من|اطلبي من|ذكّر|كلف <first name> [أن] <work> [قبل|بحلول|يوم|في <when>]``

``<when>``: today, tonight, tomorrow, a weekday (``Thursday`` is the nearest one, today
included while 17:00 has not passed; ``next Thursday`` is a week after that), ``in N
days|weeks``, ``end of (the) week`` (the coming Thursday, the end of the Gulf work week),
``next week`` (Sunday of next week), ``YYYY-MM-DD`` or ``DD/MM[/YYYY]`` (day first), and an
optional time ``at 5pm`` / ``at 17:30``. Without a time the work is due at 17:00 in the
creator's time zone (their team profile, else UTC).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_DUE_TIME = time(17, 0)

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5,
    "sat": 5, "sunday": 6, "sun": 6,
    "الاثنين": 0, "الإثنين": 0, "الثلاثاء": 1, "الأربعاء": 2, "الاربعاء": 2, "الخميس": 3,
    "الجمعة": 4, "السبت": 5, "الأحد": 6, "الاحد": 6,
}
# Every pattern runs on normalised text (:func:`_normalise`: at most MAX_COMMAND_CHARS,
# whitespace collapsed to single spaces), so separators are one literal space and no
# whitespace run can overlap a ``.+`` run: matching stays linear on hostile input (ReDoS).
MAX_COMMAND_CHARS = 2000
_LEAD = re.compile(r"^(?:hey |ok )?anum ?[,:]? ?", re.IGNORECASE)
# The lead-in only; who and what are split with plain string searches below, so no two
# unbounded runs can overlap on hostile input (CodeQL polynomial-regex).
_ASK_LEAD = re.compile(r"^(?:please )?(?:ask|tell|remind|get|have) ", re.IGNORECASE)
_ASSIGN_LEAD = re.compile(r"^(?:please )?assign ", re.IGNORECASE)
_ARABIC = re.compile(
    r"^(?:من فضلك )?(?:اطلب من|اطلبي من|ذكّر|ذكر|كلّف|كلف) (?P<person>\S+) (?:أن |ان |ب)?(?P<work>.+)$"
)
_TO = re.compile(r"(?= to )", re.IGNORECASE)  # every " to " position, overlapping ones included
_DUE_WORD = re.compile(r" (?:by|before|on|due|until|no later than) ", re.IGNORECASE)
_DUE_BARE = re.compile(
    r" (?P<bare>(?:today|tonight|tomorrow|this week|next week|in \d{1,3} (?:days?|weeks?))(?: at \d{1,2}(?::\d{2})? ?(?:am|pm)?)?)$",
    re.IGNORECASE,
)
_ARABIC_DUE_WORD = re.compile(r" (?:قبل|بحلول|يوم|في|خلال) ")
_ARABIC_DUE_BARE = re.compile(r" (?P<bare>اليوم|غدا|غداً|بكرة|بكره)$")
_TIME_TAIL = re.compile(r" (?:at|الساعة) \d{1,2}(?::\d{2})? ?(?:am|pm|ص|م)?$", re.IGNORECASE)
_TIME = re.compile(r" ?(?:at |الساعة )?(?P<h>\d{1,2})(?::(?P<m>\d{2}))? ?(?P<ap>am|pm|ص|م)?$", re.IGNORECASE)


@dataclass
class _Parts:
    person: str
    work: str
    kind: str  # "ask", "assign" or "arabic"


def _split_parts(sentence: str) -> _Parts | None:
    """Who and what in ``sentence``, matching the old ``(.+?) to (.+)`` / ``(.+) to (.+)`` rules."""
    lead = _ASK_LEAD.match(sentence)
    if lead:
        rest = sentence[lead.end():]
        # The first " to " after at least one character: the person's name is short.
        cuts = [m.start() for m in _TO.finditer(rest) if 0 < m.start() and m.start() + 4 < len(rest)]
        if cuts:
            return _Parts(person=rest[: cuts[0]], work=rest[cuts[0] + 4:], kind="ask")
    lead = _ASSIGN_LEAD.match(sentence)
    if lead:
        rest = sentence[lead.end():]
        # The last such " to ": the work itself may contain "to".
        cuts = [m.start() for m in _TO.finditer(rest) if 0 < m.start() and m.start() + 4 < len(rest)]
        if cuts:
            return _Parts(person=rest[cuts[-1] + 4:], work=rest[: cuts[-1]], kind="assign")
    match = _ARABIC.match(sentence)
    if match:
        return _Parts(person=match.group("person"), work=match.group("work"), kind="arabic")
    return None


def _split_due(tail: str, arabic: bool) -> tuple[str, str] | None:
    """(text before the due date, the due date) for the leftmost due phrase in ``tail``."""
    word, bare = (_ARABIC_DUE_WORD, _ARABIC_DUE_BARE) if arabic else (_DUE_WORD, _DUE_BARE)
    best: tuple[int, str] | None = None
    for found in word.finditer(tail):
        if found.end() < len(tail):
            best = (found.start(), tail[found.end():])
            break
    trailing = bare.search(tail)
    if trailing and (best is None or trailing.start() < best[0]):
        best = (trailing.start(), trailing.group("bare"))
    if best is None:
        return None
    return tail[: best[0]], best[1]


def _normalise(text: str) -> str:
    """At most :data:`MAX_COMMAND_CHARS` characters, whitespace collapsed to single spaces."""
    return " ".join(text[:MAX_COMMAND_CHARS].split())


@dataclass
class ParsedCommand:
    person: str
    work: str
    due_text: str | None
    due_at: datetime | None


class CommandError(ValueError):
    """The sentence is not an assignment ANUM understands; the message says how to phrase it."""


def _title(work: str) -> str:
    work = " ".join(work.strip().rstrip(".!؟?").split())
    if not work:
        raise CommandError("Say what needs to be done, for example: ask Ahmed to call the supplier by Thursday.")
    return (work[0].upper() + work[1:])[:200]


def _split_time(text: str) -> tuple[str, time | None]:
    match = _TIME_TAIL.search(text)
    if not match:
        return text, None
    parsed = _TIME.match(match.group(0))
    if not parsed:
        return text, None
    hour, minute = int(parsed.group("h")), int(parsed.group("m") or 0)
    marker = (parsed.group("ap") or "").lower()
    if marker in {"pm", "م"} and hour < 12:
        hour += 12
    if marker in {"am", "ص"} and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise CommandError("That time is not valid; use for example 5pm or 17:30.")
    return text[: match.start()], time(hour, minute)


def resolve_when(text: str, now: datetime, zone: ZoneInfo) -> datetime:
    """``text`` as an aware datetime in ``zone``; raises :class:`CommandError` if unknown."""
    original = text
    text, at = _split_time(" " + _normalise(text))
    text = " ".join(text.lower().replace("the ", "").split())
    local_now = now.astimezone(zone)
    today = local_now.date()
    due_time = at or DEFAULT_DUE_TIME

    def on(day: date) -> datetime:
        return datetime.combine(day, due_time, tzinfo=zone)

    if text in {"today", "tonight", "اليوم", "eod", "end of day"}:
        return on(today) if text != "tonight" or at else datetime.combine(today, time(21, 0), tzinfo=zone)
    if text in {"tomorrow", "غدا", "غداً", "بكرة", "بكره"}:
        return on(today + timedelta(days=1))
    if text in {"end of week", "this week", "نهاية الأسبوع", "نهاية الاسبوع"}:
        ahead = (3 - today.weekday()) % 7
        return on(today + timedelta(days=ahead))
    if text in {"next week", "الأسبوع القادم", "الاسبوع القادم"}:
        ahead = (6 - today.weekday()) % 7 or 7
        return on(today + timedelta(days=ahead))
    match = re.fullmatch(r"in (\d{1,3}) (day|days|week|weeks)", text)
    if match:
        count = int(match.group(1)) * (7 if match.group(2).startswith("week") else 1)
        return on(today + timedelta(days=count))
    match = re.fullmatch(r"(?:(this|next|coming) )?(\S+)", text)
    if match and match.group(2) in _WEEKDAYS:
        target = _WEEKDAYS[match.group(2)]
        ahead = (target - today.weekday()) % 7
        candidate = on(today + timedelta(days=ahead))
        if candidate <= local_now:
            candidate += timedelta(days=7)
        if match.group(1) == "next":
            candidate += timedelta(days=7)
        return candidate
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return on(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            raise CommandError(f"{original.strip()} is not a calendar date.") from None
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            candidate = on(date(year, month, day))
        except ValueError:
            raise CommandError(f"{original.strip()} is not a calendar date (day first: 15/10).") from None
        if not match.group(3) and candidate < local_now:
            candidate = candidate.replace(year=year + 1)
        return candidate
    raise CommandError(
        f'ANUM does not understand the date "{original.strip()}". Use today, tomorrow, a weekday, '
        '"in 3 days", "next week", 2026-10-15 or 15/10.'
    )


def parse_command(text: str, now: datetime, zone: ZoneInfo) -> ParsedCommand:
    sentence = _LEAD.sub("", _normalise(text)).strip().rstrip(".!؟?")
    parts = _split_parts(sentence)
    if parts is None:
        raise CommandError(
            "Start with who and what, for example: ask Ahmed to call the supplier by Thursday, "
            "or assign the stock count to Sara by tomorrow."
        )
    person = parts.person.strip().strip(",")
    work = parts.work.strip()
    due_text: str | None = None
    # "assign X to Ahmed by Thursday": the due date trails the person.
    tail_source = "person" if parts.kind == "assign" else "work"
    tail = person if tail_source == "person" else work
    found = _split_due(tail, parts.kind == "arabic")
    if found:
        due_text = found[1].strip()
        tail = found[0].strip()
        if tail_source == "person":
            person = tail
        else:
            work = tail
    if person.lower().startswith("the "):
        person = person[4:]
    if not person or len(person) > 120:
        raise CommandError("Name the person, for example: ask Ahmed to ...")
    due_at = resolve_when(due_text, now, zone) if due_text else None
    return ParsedCommand(person=person, work=_title(work), due_text=due_text, due_at=due_at)


def looks_like_assignment(text: str) -> bool:
    """True when ``text`` has the shape of an assignment (no lookup, no date check)."""
    sentence = _LEAD.sub("", _normalise(text)).strip().rstrip(".!؟?")
    return _split_parts(sentence) is not None


def match_people(name: str, people: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Candidates for ``name`` among (type, id, display name), best match first.

    An exact name or id wins; otherwise every person whose first name (or any word of the
    name) equals it, then everyone whose name starts with it.
    """
    needle = " ".join(name.casefold().split())
    if not needle:
        return []
    if needle in {"anum", "yourself", "you", "نفسك", "أنعم", "انعم"}:
        return [entry for entry in people if entry[0] == "anum"]
    exact = [p for p in people if p[2].casefold() == needle or p[1].casefold() == needle]
    if exact:
        return exact
    word = [p for p in people if needle in p[2].casefold().split()]
    if word:
        return word
    return [p for p in people if p[2].casefold().startswith(needle)]
