"""De-duplication never drops a real purchase (docs/finance-sms.md, rule 7 and "Matching"): messages that state
different dates are different payments, and the same kind of source twice is one payment only within 10 minutes when
both give the time. In-memory stores; the bank, the merchant and the card are SYNTHETIC.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from test_finance_sms import (  # noqa: F401  (``clean`` resets every store)
    clean,
    send,
    setup,
    sms_memory_store,
)

from anum_api.finance import wallet as W

CAFE = "SYNTHETIC. Purchase of SAR 15.00 at CORNER CAFE with card **1234 on {on}"
RIYADH = ZoneInfo("Asia/Riyadh")


def spent() -> Decimal:
    """What the card owes: the sum of the purchases recorded on it."""
    return sum((p.amount for e in sms_memory_store.entries.values() for p in e.postings if p.amount > 0), Decimal(0))


def test_the_same_coffee_on_three_stated_days_is_three_purchases() -> None:
    s = setup()
    answers = [send(s["token"], CAFE.format(on=day)).json() for day in ("03/11/2026", "04/11/2026", "05/11/2026")]
    assert [a["status"] for a in answers] == ["posted", "posted", "posted"]
    entries = list(sms_memory_store.entries.values())
    assert sorted(e.posted_on for e in entries) == [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]
    assert spent() == Decimal("45.00")  # 3 x 15.00
    # The bank sending one of them again (the same stated date) is still a duplicate.
    again = send(s["token"], CAFE.format(on="04/11/2026").replace("SYNTHETIC.", "SYNTHETIC copy.")).json()
    assert (again["status"], again["reason"]) == ("duplicate", "already_recorded") and len(sms_memory_store.entries) == 3


def test_two_messages_with_a_time_are_one_payment_only_within_ten_minutes() -> None:
    s = setup()
    first = send(s["token"], CAFE.format(on="03/11/2026 09:05")).json()
    copy = send(s["token"], CAFE.format(on="03/11/2026 09:12").replace("SYNTHETIC.", "SYNTHETIC copy.")).json()  # 7 minutes later
    second = send(s["token"], CAFE.format(on="03/11/2026 09:25")).json()  # 20 minutes after the first, 13 after the copy
    assert [first["status"], copy["status"], second["status"]] == ["posted", "duplicate", "posted"]
    assert len(sms_memory_store.entries) == 2 and spent() == Decimal("30.00")


def _candidate(**changes: object) -> W.Candidate:
    at = datetime(2026, 11, 3, 9, 5, tzinfo=RIYADH)
    base = {"entry_id": "e1", "when": at, "day": at.replace(hour=12, minute=0), "merchant": "CORNER CAFE", "reference": None,
            "sources": frozenset({"sms"})}
    return W.Candidate(**{**base, **changes})  # type: ignore[arg-type]


def test_two_sources_of_one_payment_still_match_within_thirty_minutes() -> None:
    at = datetime(2026, 11, 3, 9, 25, tzinfo=RIYADH)  # 20 minutes after the candidate
    assert W.same_payment(W.Report("wallet", at, True, "Corner Cafe", None), _candidate()) == "match"  # Apple Pay, then the bank
    assert W.same_payment(W.Report("sms", at, True, "CORNER CAFE", None, dated=True), _candidate()) == "no"  # the bank twice
    assert W.same_payment(W.Report("wallet", at + timedelta(minutes=11), True, "Corner Cafe", None), _candidate()) == "no"  # 31 minutes


def test_the_two_day_window_still_serves_undated_messages_and_statement_lines() -> None:
    dated_only = _candidate(when=None, stated=frozenset({date(2026, 11, 3)}))
    noon = datetime(2026, 11, 5, 12, 0, tzinfo=RIYADH)  # two days after the candidate's stated day
    # A statement line posts two days later: the same payment.
    assert W.same_payment(W.Report("statement", noon, False, "CORNER CAFE", None), dated_only) == "match"
    # A message with its own, different date: another payment. With the same date: the bank sent it twice.
    assert W.same_payment(W.Report("sms", noon, False, "CORNER CAFE", None, dated=True), dated_only) == "no"
    assert W.same_payment(W.Report("sms", noon.replace(day=3), False, "CORNER CAFE", None, dated=True), dated_only) == "match"
    # A candidate dated by when its message arrived (it states no date) keeps the loose window for a dated message.
    arrived = _candidate(when=None, stated=frozenset())
    assert W.same_payment(W.Report("sms", noon, False, "CORNER CAFE", None, dated=True), arrived) == "match"
    assert W.same_payment(W.Report("sms", noon.replace(day=6), False, "CORNER CAFE", None, dated=True), arrived) == "no"  # three days
