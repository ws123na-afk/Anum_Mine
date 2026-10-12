"""The team command parser (docs/team-work.md#assigning-in-words): behaviour and ReDoS safety."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from anum_api import team_commands
from anum_api.team_commands import CommandError, looks_like_assignment, parse_command, resolve_when

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)  # a Monday, 12:00 in Dubai
DUBAI = ZoneInfo("Asia/Dubai")


@pytest.mark.parametrize(
    ("sentence", "person", "work", "due_text"),
    [
        ("ANUM, ask Ahmed to call the supplier by Thursday", "Ahmed", "Call the supplier", "Thursday"),
        ("hey anum  ask\tSara to count stock tomorrow at 5pm", "Sara", "Count stock", "tomorrow at 5pm"),
        ("please tell Omar to check tyres in 3 days", "Omar", "Check tyres", "in 3 days"),
        ("anum,ask Ahmed to go to the bank to pay by Monday at 9 am", "Ahmed", "Go to the bank to pay", "Monday at 9 am"),
        ("assign the stock count to Sara by tomorrow", "Sara", "The stock count", "tomorrow"),
        ("Assign report to Omar", "Omar", "Report", None),
        ("اطلب من أحمد أن يتصل بالمورد قبل الخميس", "أحمد", "يتصل بالمورد", "الخميس"),
        ("ذكر سارة بإرسال الفاتورة غدا", "سارة", "إرسال الفاتورة", "غدا"),
    ],
)
def test_parses_who_what_and_when(sentence: str, person: str, work: str, due_text: str | None) -> None:
    parsed = parse_command(sentence, NOW, DUBAI)
    assert (parsed.person, parsed.work, parsed.due_text) == (person, work, due_text)
    assert looks_like_assignment(sentence)


def test_dates_and_times_resolve_in_the_workspace_zone() -> None:
    assert resolve_when("tomorrow  at  5pm", NOW, DUBAI) == datetime(2026, 10, 13, 17, 0, tzinfo=DUBAI)
    assert resolve_when("Thursday at 9 am", NOW, DUBAI) == datetime(2026, 10, 15, 9, 0, tzinfo=DUBAI)
    assert resolve_when("الخميس الساعة 5 م", NOW, DUBAI) == datetime(2026, 10, 15, 17, 0, tzinfo=DUBAI)
    with pytest.raises(CommandError):
        resolve_when("Thursday at 25pm", NOW, DUBAI)
    assert not looks_like_assignment("what is the weather")


# CodeQL's "polynomial regular expression" inputs: a command opening followed by a very long
# whitespace run, or a due-date keyword followed by one. Each must finish well within a second.
HOSTILE = [
    "ask " + " " * 100_000,
    "ask a to " + " " * 100_000,
    "assign " + " " * 100_000,
    "assign a to " + " " * 100_000,
    "ذكر" + " " * 100_000,
    "ask a to b by " + " " * 100_000 + "x",
    "ask a to b في " + " " * 100_000 + "x",
    "ask a to b at" + " " * 100_000 + "5",
    "ask " + "a " * 50_000,
    "assign " + "a to " * 20_000,
    "ask a to " + "b by " * 20_000 + "!",
    "\t\n " * 33_000 + "anum",
]


@pytest.mark.parametrize("text", HOSTILE, ids=range(len(HOSTILE)))
def test_hostile_input_finishes_quickly(text: str) -> None:
    started = time.perf_counter()
    looks_like_assignment(text)
    for call in (lambda: parse_command(text, NOW, DUBAI), lambda: resolve_when(text, NOW, DUBAI)):
        try:
            call()
        except CommandError:
            pass
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("text", HOSTILE, ids=range(len(HOSTILE)))
def test_patterns_are_linear_without_the_length_cap(text: str) -> None:
    # The cap is defence in depth: each pattern is linear on 100 KB of normalised text too.
    normalised = " ".join(text.split()) + " " + "a " * 50_000 + "by"
    started = time.perf_counter()
    for pattern in (team_commands._LEAD, team_commands._ASK, team_commands._ASSIGN, team_commands._ARABIC):
        pattern.match(normalised)
    for pattern in (team_commands._DUE_SPLIT, team_commands._ARABIC_DUE_SPLIT, team_commands._TIME_TAIL):
        pattern.search(normalised)
    assert time.perf_counter() - started < 1.0
