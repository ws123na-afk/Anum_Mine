"""Invoices, credit notes and payments are dated on the business's own day, not the UTC day
(docs/business.md#invoices). The clock is pinned to 00:30 on 1 January 2027 in Riyadh, which is 21:30 UTC on
31 December 2026. In-memory stores; the customer is SYNTHETIC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from test_business_books import (  # noqa: F401  (``clean`` resets every store)
    API,
    OWNER,
    clean,
    client,
    ctx,
    seed_invoice,
    setup_books,
)

from anum_api.business import api_invoice_money, books_ledger
from anum_api.business import invoice_service as invoices
from anum_api.business.invoice_models import PaymentMethod
from anum_api.business.store import business_store
from anum_api.settings import settings

NOW = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo("Asia/Riyadh")).astimezone(UTC)  # 2026-12-31 21:30 UTC


def pay(invoice, paid_on: date):  # type: ignore[no-untyped-def]
    return invoices.apply_payment(business_store.scoped(ctx()), ctx(), invoice, amount=Decimal(100), paid_on=paid_on,
                                  method=PaymentMethod.BANK_TRANSFER, reference="", note="", now=NOW)


def test_an_invoice_sent_just_after_midnight_in_riyadh_is_dated_and_numbered_in_the_new_year() -> None:
    assert NOW.date() == date(2026, 12, 31)  # the UTC day is still the old year
    store = business_store.scoped(ctx())
    draft = seed_invoice(status="draft", issued=date(2026, 12, 1)).model_copy(update={"issue_date": None, "number": None})
    sent = invoices.issue(draft, store, ctx(), NOW)
    assert (sent.issue_date, sent.number) == (date(2027, 1, 1), "INV-2027-0001")
    assert sent.due_date == date(2027, 1, 15)  # 14 days after the issue date
    # The business's own today is not the future; tomorrow is. A payment cannot be dated before the invoice either.
    store.save("invoices", sent)
    paid, payment = pay(sent, date(2027, 1, 1))
    assert payment.paid_on == date(2027, 1, 1) and paid.amount_paid == Decimal("100.00")
    with pytest.raises(HTTPException) as refused:
        pay(paid, date(2027, 1, 2))
    assert refused.value.status_code == 422 and "future" in str(refused.value.detail)


def test_the_day_follows_the_time_zone_the_business_set() -> None:
    store = business_store.scoped(ctx())
    current = store.get_settings()
    store.save_settings(current.model_copy(update={"support_policy": current.support_policy.model_copy(update={"timezone": "America/New_York"})}))
    # 21:30 UTC is 16:30 on 31 December in New York: still the old year there.
    assert invoices.business_day(store, NOW) == date(2026, 12, 31)
    draft = seed_invoice(status="draft", issued=date(2026, 12, 1)).model_copy(update={"issue_date": None, "number": None})
    sent = invoices.issue(draft, store, ctx(), NOW)
    assert (sent.issue_date, sent.number) == (date(2026, 12, 31), "INV-2026-0001")
    with pytest.raises(HTTPException):
        pay(sent, date(2027, 1, 1))  # tomorrow in New York
    # A time zone name that no longer resolves falls back to Asia/Riyadh instead of failing the send.
    store.save_settings(current.model_copy(update={"support_policy": current.support_policy.model_copy(update={"timezone": "Not/A_Zone"})}))
    assert invoices.business_day(store, NOW) == date(2027, 1, 1)


def test_a_credit_note_is_dated_on_the_business_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "business_credit_notes_enabled", True)
    monkeypatch.setattr(api_invoice_money, "utc_now", lambda: NOW)
    invoice = seed_invoice(issued=date(2026, 12, 20))  # 1,000 + 150 VAT
    made = client.post(f"/api/v1/business/invoices/{invoice.id}/credit-notes", headers=OWNER, json={"amount": 115, "reason": "Synthetic goodwill"})
    assert made.status_code == 201, made.text
    note = next(iter(business_store._rows["credit_notes"].values()))
    # 115.00 of an invoice taxed at 15 %: 100.00 net and 15.00 VAT, dated 1 January 2027 in Riyadh.
    assert (note.issue_date, note.subtotal, note.tax) == (date(2027, 1, 1), Decimal("100.00"), Decimal("15.00"))


def test_an_invoice_dated_on_the_business_day_is_posted_at_once_not_scheduled() -> None:
    setup_books()
    seed_invoice(issued=date(2027, 1, 1))
    result = books_ledger.post(ctx(), now=NOW)  # the UTC day is still 31 December
    assert (result.posted, result.scheduled, result.issues) == (1, 0, 0)
