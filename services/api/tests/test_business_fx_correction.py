"""Entries posted before a document's own rate was used for what reverses, releases or settles it are corrected by the
posting run (docs/business-books.md#rate-corrections): reversed and posted again with a memo, only when their rate is
wrong, on the first open day when their own day is closed. "Before the fix" is made here by posting with a reader that
sees nothing of the journal, which is what the old release did. Figures are worked by hand; everything is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    OWNER,
    bill,
    clean,
    client,
    ctx,
    seed_credit,
    seed_invoice,
    seed_payment,
    setup_books,
    today,
    vendor,
)
from test_business_fx_settlement import balances, lines, post, rate

from anum_api.business import books_ledger
from anum_api.business.books_store import books_memory_store


class _Blind:
    """The old release: nothing reads what the journal holds of a document, so every leg asks the rate table again."""

    def __init__(self, *args: object) -> None:
        pass

    def rate(self, event_key: str, currency: str) -> None:
        return None

    def open(self, event_key: str, account_id: str, currency: str) -> None:
        return None


@contextmanager
def old_release(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with monkeypatch.context() as patch:
        patch.setattr(books_ledger, "_BookedReader", _Blind)
        patch.setattr(books_ledger, "_wrong_rates", lambda *args: [])
        patch.setattr(books_ledger, "_left_behind", lambda *args: [])
        yield


def entries_of(kind: str) -> list:
    rows = [entry for entry in books_memory_store.rows["journal_entries"].values() if entry.source_kind == kind]
    return sorted(rows, key=lambda entry: (entry.created_at, entry.id))


def parts(rows: list) -> tuple:
    """(the entry that was reversed, its reversal, the entry posted again) of an event corrected once."""
    assert len(rows) == 3
    return (next(row for row in rows if row.status == "reversed"), next(row for row in rows if row.reverses_id is not None),
            next(row for row in rows if row.status == "posted" and row.reverses_id is None))


def test_a_payment_posted_at_a_rate_recorded_later_is_reversed_and_posted_again_with_a_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_books(rate_max_age_days=10)
    issued = today() - timedelta(days=3)
    rate("3.75", today() - timedelta(days=5))
    invoice = seed_invoice(subtotal="1000", tax="0", currency="USD", issued=issued)
    with old_release(monkeypatch):
        post()
        rate("3.80", issued)
        rate("3.80", today())
        seed_payment(invoice, "1000", today())
        post()
    # Before: the receivable was credited 1,000 x 3.80 = 3,800.00 against 3,750.00 booked: SAR -50.00 left, no gain.
    assert balances()["ar"] == (Decimal("-50.00"), Decimal(0)) and "fx" not in balances()
    old = entries_of("invoice_payment")[0]
    result = post()
    assert (result["reversed"], result["posted"], result["issues"]) == (1, 1, 0)
    # After: bank still 3,800.00, the receivable is clear and the 50.00 is a realised gain.
    after = balances()
    assert after["bank"] == (Decimal("3800.00"), Decimal(1000)) and after["ar"] == (Decimal("0.00"), Decimal(0))
    assert after["fx"][0] == Decimal("-50.00")
    first, reversal, again = parts(entries_of("invoice_payment"))
    assert first.id == old.id and reversal.reverses_id == old.id
    assert reversal.description.startswith(f"Rate corrected, reversal of entry {old.id}")
    assert f"Rate corrected: replaces entry {old.id}" in again.description and again.status == "posted"
    assert again.fingerprint == old.fingerprint and again.event_key == old.event_key
    # The run is idempotent: a second run changes nothing.
    second = post()
    assert (second["reversed"], second["posted"]) == (0, 0) and len(entries_of("invoice_payment")) == 3


def test_an_entry_posted_at_the_right_rate_is_not_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_books(rate_max_age_days=10)
    issued = today() - timedelta(days=3)
    rate("3.75", today() - timedelta(days=5))
    rate("3.80", today())
    invoice = seed_invoice(subtotal="1000", tax="0", currency="USD", issued=issued)
    seed_credit(invoice, "200", "0")
    seed_payment(invoice, "800", today())
    with old_release(monkeypatch):
        post()
    # No rate was recorded for the issue day afterwards, so the old release used 3.75 too: 750.00 credited, 3,000.00 settled.
    before = {entry.id: (entry.fingerprint, entry.status) for entry in books_memory_store.rows["journal_entries"].values()}
    assert balances()["ar"] == (Decimal("0.00"), Decimal(0)) and balances()["fx"][0] == Decimal("-40.00")
    result = post()
    assert (result["reversed"], result["posted"]) == (0, 0)
    assert {entry.id: (entry.fingerprint, entry.status) for entry in books_memory_store.rows["journal_entries"].values()} == before
    assert all(status == "posted" for _, status in before.values())


def test_a_wrong_settlement_in_a_closed_month_is_corrected_on_the_first_open_day(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_books(rate_max_age_days=10)
    month_end = today().replace(day=1) - timedelta(days=1)
    issued = month_end - timedelta(days=3)
    rate("3.75", issued - timedelta(days=2))
    invoice = seed_invoice(subtotal="1000", tax="0", currency="USD", issued=issued)
    with old_release(monkeypatch):
        post()
        rate("3.80", issued)
        seed_payment(invoice, "1000", issued)
        post()
        closed = client.post(f"{API}/close", headers=OWNER, json={"through": month_end.isoformat()})
        assert closed.status_code == 200, closed.text
    assert balances()["ar"] == (Decimal("-50.00"), Decimal(0))
    frozen = {line.id: (line.entry_date, line.debit, line.credit) for line in books_memory_store.rows["journal_lines"].values()}
    result = post()
    assert (result["reversed"], result["posted"], result["late"]) == (1, 1, 1)
    first_open = month_end + timedelta(days=1)
    _, reversal, again = parts(entries_of("invoice_payment"))
    # Nothing in the closed month changed; the reversal and the corrected entry are dated on the first open day.
    assert all(frozen[line.id] == (line.entry_date, line.debit, line.credit) for line in books_memory_store.rows["journal_lines"].values()
               if line.id in frozen)
    assert (reversal.entry_date, again.entry_date, again.event_date, again.late) == (first_open, first_open, issued, True)
    assert balances()["ar"] == (Decimal("0.00"), Decimal(0)) and balances()["fx"][0] == Decimal("-50.00")
    assert post()["reversed"] == 0


def test_credit_notes_depreciation_and_prepayment_releases_at_a_later_rate_are_corrected(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_books(rate_max_age_days=10)
    rate("3.75", today() - timedelta(days=2))
    invoice = seed_invoice(subtotal="1000", tax="0", currency="USD")
    made = bill(vendor("Synthetic Tools", default_category="equipment")["id"], currency="USD", subtotal="1200", tax="0", category="equipment",
                section="capital", useful_life_months=12, bill_date=today().isoformat())
    assert made.status_code == 201, made.text
    prepaid = client.post(f"{API}/adjustments", headers=OWNER, json={
        "entry_date": today().isoformat(), "currency": "USD", "description": "Synthetic insurance", "kind": "prepayment", "amount": "1200",
        "category": "insurance", "start_month": today().replace(day=1).isoformat(), "months": 12})
    assert prepaid.status_code == 201, prepaid.text
    later = today() + timedelta(days=35)
    with old_release(monkeypatch):
        post()
        rate("3.80", today())
        seed_credit(invoice, "200", "0")
        books_ledger.post(ctx(), today=later)
    # Before: the credit note and the later months read 3.80: 200 x 3.80 = 760.00, 100 x 3.80 = 380.00 a month.
    assert balances()["credit_notes"] == (Decimal("760.00"), Decimal(200))
    assert Decimal("380.00") in {line.debit for line in lines("depreciation")}
    assert Decimal("380.00") in {line.credit for line in lines("prepayments") if line.credit > 0}
    result = books_ledger.post(ctx(), today=later)
    assert result.reversed >= 3 and result.reversed == result.posted and result.issues == 0
    # After: 200 x 3.75 = 750.00 and 375.00 for every month, net of the reversals.
    after = balances()
    months = len({entry.event_key for entry in entries_of("bill") if ":dep:" in entry.event_key})
    assert after["credit_notes"] == (Decimal("750.00"), Decimal(200))
    assert after["depreciation"][0] == months * Decimal("375.00") and months >= 2
    assert after["prepayments"][0] == Decimal("4500.00") - months * Decimal("375.00")  # 1,200 x 3.75 less the releases
    again = books_ledger.post(ctx(), today=later)
    assert (again.reversed, again.posted) == (0, 0)


def test_a_cent_left_by_payments_rounded_one_by_one_is_cleared_by_the_last_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_books()
    rate("3.755", today())
    invoice = seed_invoice(subtotal="10", tax="0", currency="USD")
    with old_release(monkeypatch):
        post()
        for amount in ("3.33", "3.33", "3.34"):
            seed_payment(invoice, amount)
            post()
    # Before: 12.50 + 12.50 + 12.54 = 37.54 against 37.55 booked: 0.01 stays although every dollar is paid.
    assert balances()["ar"] == (Decimal("0.01"), Decimal("0.00"))
    result = post()
    assert (result["reversed"], result["posted"]) == (1, 1)
    # After: the last payment takes the 12.55 that was left; the bank still received 12.54, so 0.01 is a loss.
    assert balances()["ar"] == (Decimal("0.00"), Decimal("0.00")) and balances()["fx"][0] == Decimal("0.01")
    assert max(line.credit for line in lines("ar") if line.credit > 0) == Decimal("12.55")
    assert post()["reversed"] == 0
