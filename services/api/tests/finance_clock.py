"""A fixed "now" for Personal Finance tests, and the owner's local day computed the way the code does.

Personal Finance keeps one rule for "today": the owner's Finance time zone (``summary.today_in``, Asia/Dubai by default), and
one clock (``anum_api.schemas.utc_now``). ``freeze`` replaces that clock in every loaded ``anum_api`` module that imported it,
so a test can run at a UTC moment on either side of the owner's local midnight, whatever time CI runs.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime

import pytest

from anum_api import schemas
from anum_api.finance.models import FinanceSettings
from anum_api.finance.summary import today_in

ORIGINAL = schemas.utc_now

# UTC moments around the owner's local midnight (Asia/Dubai is UTC+4): the UTC day and the local day differ on the
# evening side, at a month end and at a year end too.
LATE_EVENING = datetime(2026, 11, 15, 21, 30, tzinfo=UTC)  # 16 Nov 01:30 in Dubai
EARLY_MORNING = datetime(2026, 11, 15, 19, 30, tzinfo=UTC)  # 15 Nov 23:30 in Dubai
MONTH_END = datetime(2026, 11, 30, 20, 30, tzinfo=UTC)  # 1 Dec 00:30 in Dubai
YEAR_END = datetime(2026, 12, 31, 22, 15, tzinfo=UTC)  # 1 Jan 2027 02:15 in Dubai
MOMENTS = [LATE_EVENING, EARLY_MORNING, MONTH_END, YEAR_END]


_FROZEN: dict[str, datetime | None] = {"now": None}


def _clock() -> datetime:
    """The frozen moment while a test holds one, else the real clock. A module first imported during a frozen test keeps
    this function, so it never carries a stale moment into the next test."""
    return _FROZEN["now"] or ORIGINAL()


def freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Every ``utc_now()`` in the API answers ``moment`` until the test ends."""
    monkeypatch.setitem(_FROZEN, "now", moment)
    for name, module in list(sys.modules.items()):
        if (name == "anum_api" or name.startswith("anum_api.")) and getattr(module, "utc_now", None) is ORIGINAL:
            monkeypatch.setattr(module, "utc_now", _clock)


def owner_today(settings: FinanceSettings | None = None) -> date:
    """Today in the owner's Finance time zone (the default settings unless given), from the current, possibly frozen, clock."""
    return today_in(settings or FinanceSettings(), schemas.utc_now())
