"""A document in another currency is reversed, released and settled at the rate it was posted with
(docs/business-books.md#what-is-posted-from-what), never at a rate read again for the same day: a closer
rate recorded after posting must not leave a receivable or payable that never clears. In-memory stores;
customers, vendors and rates are SYNTHETIC. The figures are worked by hand in each test.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

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

from anum_api.business import books_ledger
from anum_api.business.books_models import Rate
from anum_api.business.books_posting import CarryingRate, Chart, Leg, Proposal, realise
from anum_api.business.books_report import Converter
from anum_api.business.books_store import books_memory_store
from anum_api.schemas import utc_now


def post() -> dict:
    response = client.post(f"{API}/ledger/post", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def rate(value: str, on: date) -> None:
    made = client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": value, "as_of": on.isoformat(),
                                                           "source": "Synthetic reference"})
    assert made.status_code == 201, made.text


def balances() -> dict[str, tuple[Decimal, Decimal]]:
    """(base balance, original-currency balance) per system account, debit positive, straight from the journal lines."""
    keys = {row.id: row.system_key or row.code for row in books_memory_store.rows["ledger_accounts"].values()}
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for line in books_memory_store.rows["journal_lines"].values():
        base, original = out.get(keys[line.account_id], (Decimal(0), Decimal(0)))
        out[keys[line.account_id]] = (base + line.debit - line.credit, original + (line.amount if line.currency == "USD" else Decimal(0)))
    return out


def lines(account: str) -> list:
    keys = {row.id: row.system_key or row.code for row in books_memory_store.rows["ledger_accounts"].values()}
    return [line for line in books_memory_store.rows["journal_lines"].values() if keys[line.account_id] == account]


def test_an_invoice_is_settled_at_the_rate_it_was_posted_with_not_one_recorded_later() -> None:
    setup_books(rate_max_age_days=10)
    issued = today() - timedelta(days=3)
    rate("3.75", today() - timedelta(days=5))
    invoice = seed_invoice(subtotal="1000", tax="0", currency="USD", issued=issued)
    post()
    assert balances()["ar"] == (Decimal("3750.00"), Decimal(1000))  # 1,000 x 3.75
    rate("3.80", issued)  # the issue day's own rate arrives after the invoice was posted
    rate("3.80", today())
    seed_payment(invoice, "1000", today())
    post()
    after = balances()
    # Bank 1,000 x 3.80 = 3,800.00; the receivable leaves at the 3,750.00 it was booked at; the 50.00 is a realised gain.
    assert after["bank"] == (Decimal("3800.00"), Decimal(1000))
    assert after["ar"] == (Decimal("0.00"), Decimal(0))
    assert after["fx"][0] == Decimal("-50.00")
    settled = next(line for line in lines("ar") if line.credit > 0)
    assert (settled.credit, settled.amount, settled.rate, settled.rate_date) == (Decimal("3750.00"), Decimal(-1000), Decimal("3.75"),
                                                                                 today() - timedelta(days=5))
    assert post()["posted"] == 0 and post()["reversed"] == 0  # idempotent: nothing is posted again


def test_a_bill_is_settled_at_the_rate_it_was_posted_with_not_one_recorded_later() -> None:
    setup_books(rate_max_age_days=10)
    billed = today() - timedelta(days=3)
    rate("3.75", today() - timedelta(days=5))
    made = bill(vendor()["id"], currency="USD", subtotal="1000", tax="0", bill_date=billed.isoformat())
    assert made.status_code == 201, made.text
    post()
    assert balances()["ap"] == (Decimal("-3750.00"), Decimal("-1000.00"))
    rate("3.80", billed)
    rate("3.80", today())
    paid = client.post(f"{API}/bills/{made.json()['id']}/payments", headers=OWNER, json={"amount": "1000", "paid_on": today().isoformat()})
    assert paid.status_code == 201, paid.text
    post()
    after = balances()
    # Bank pays 1,000 x 3.80 = 3,800.00 for a payable booked at 3,750.00: a realised loss of 50.00, and the payable is clear.
    assert after["bank"] == (Decimal("-3800.00"), Decimal("-1000.00"))
    assert after["ap"] == (Decimal("0.00"), Decimal("0.00"))
    assert after["fx"][0] == Decimal("50.00")


def test_part_payments_clear_the_receivable_to_zero_on_the_last_payment() -> None:
    setup_books()
    rate("3.755", today())
    invoice = seed_invoice(subtotal="10", tax="0", currency="USD")
    post()
    assert balances()["ar"] == (Decimal("37.55"), Decimal(10))  # 10 x 3.755
    for amount in ("3.33", "3.33", "3.34"):
        seed_payment(invoice, amount)
        post()
    credits = sorted(line.credit for line in lines("ar") if line.credit > 0)
    # 3.33 of 10.00 booked at 37.55 = 12.50415 -> 12.50, leaving 6.67 at 25.05; 3.33 of that = 12.50622 -> 12.51, leaving
    # 3.34 at 12.54, which the last payment takes whole. Each payment at the day's rate alone gave 12.50 + 12.50 + 12.54 =
    # 37.54 and left 0.01 on the receivable for good.
    assert credits == [Decimal("12.50"), Decimal("12.51"), Decimal("12.54")]
    after = balances()
    assert after["ar"] == (Decimal("0.00"), Decimal("0.00"))
    assert after["bank"] == (Decimal("37.54"), Decimal("10.00"))  # 12.50 + 12.50 + 12.54 at 3.755
    assert after["fx"][0] == Decimal("0.01")  # the cent the bank did not receive is a loss, not a receivable


def test_a_credit_note_and_the_payment_after_it_use_the_rate_the_invoice_was_posted_with() -> None:
    setup_books(rate_max_age_days=10)
    issued = today() - timedelta(days=3)
    rate("3.75", today() - timedelta(days=5))
    invoice = seed_invoice(subtotal="1000", tax="0", currency="USD", issued=issued)
    post()
    rate("3.80", issued)
    rate("3.80", today())
    seed_credit(invoice, "200", "0")
    post()
    # The credit note reverses 200 of the sale at the 3.75 it was booked at: 750.00, not 760.00.
    assert balances()["credit_notes"] == (Decimal("750.00"), Decimal(200))
    assert balances()["ar"] == (Decimal("3000.00"), Decimal(800))
    seed_payment(invoice, "800", today())
    post()
    after = balances()
    assert after["ar"] == (Decimal("0.00"), Decimal(0))
    assert after["bank"] == (Decimal("3040.00"), Decimal(800))  # 800 x 3.80
    assert after["fx"][0] == Decimal("-40.00")  # 3,040.00 received for 3,000.00 booked


def test_depreciation_and_prepayment_releases_keep_the_rate_their_document_was_posted_with() -> None:
    setup_books(rate_max_age_days=10)
    rate("3.75", today() - timedelta(days=2))
    made = bill(vendor("Synthetic Tools", default_category="equipment")["id"], currency="USD", subtotal="1200", tax="0", category="equipment",
                section="capital", useful_life_months=12, bill_date=today().isoformat())
    assert made.status_code == 201, made.text
    start = today().replace(day=1)
    prepaid = client.post(f"{API}/adjustments", headers=OWNER, json={
        "entry_date": today().isoformat(), "currency": "USD", "description": "Synthetic insurance", "kind": "prepayment", "amount": "1200",
        "category": "insurance", "start_month": start.isoformat(), "months": 12})
    assert prepaid.status_code == 201, prepaid.text
    post()
    assert balances()["fixed_assets"] == (Decimal("4500.00"), Decimal("1200.00"))  # 1,200 x 3.75
    rate("3.80", today())  # a closer rate for the purchase day, recorded after the purchase was posted
    result = books_ledger.post(ctx(), today=today() + timedelta(days=35))  # the next month's depreciation and release fall due
    assert result.issues == 0 and result.reversed == 0 and result.posted >= 2
    depreciation = [line.debit for line in lines("depreciation")]
    releases = [line.credit for line in lines("prepayments") if line.credit > 0]
    # 100 a month at the purchase's 3.75 = 375.00, every month; a rate read again would give 380.00 for the later months.
    assert len(depreciation) >= 2 and set(depreciation) == {Decimal("375.00")}
    assert len(releases) >= 2 and set(releases) == {Decimal("375.00")}


# -- the rule itself (books_posting.realise), without a store -------------------------------------------------------------


class _Journal:
    """What the journal holds of one posted invoice: USD 10.00 booked at 3.755 = SAR 37.55, with ``left`` still open."""

    def __init__(self, left: tuple[Decimal, Decimal] | None) -> None:
        self.left = left

    def rate(self, event_key: str, currency: str) -> CarryingRate | None:
        return CarryingRate(Decimal("3.755"), date(2026, 11, 1), "Synthetic reference") if event_key == "invoice:inv_1" else None

    def open(self, event_key: str, account_id: str, currency: str) -> tuple[Decimal, Decimal] | None:
        return self.left


def _payment(amount: str) -> Proposal:
    paid = date(2026, 11, 9)
    return Proposal("invoice_payment:pay_1", "invoice_payment", "pay_1", paid, "Payment", [
        Leg("key:bank", True, "USD", Decimal(amount), paid, flow="customers_paid"),
        Leg("key:ar", False, "USD", Decimal(amount), date(2026, 11, 1), absorb=True, booked_by="invoice:inv_1", settles=True)], fx_balance=True)


def _realised(amount: str, journal: _Journal | None) -> dict[str, Decimal]:
    now = utc_now()
    rates = [Rate(id=f"rate_{index}", tenant_id="t", workspace_id="w", created_at=now, updated_at=now, base="USD", quote="SAR", rate=Decimal(value),
                  as_of=day, source="Synthetic reference", created_by="owner_books")
             for index, (day, value) in enumerate(((date(2026, 11, 1), "3.80"), (date(2026, 11, 9), "3.80")))]
    chart = Chart(by_key={"bank": "bank", "ar": "ar", "fx": "fx"})
    lines = realise(_payment(amount), Converter(rates, "SAR", 31), chart, None, journal)
    assert isinstance(lines, list)
    assert sum((line.debit - line.credit for line in lines), Decimal(0)) == 0  # every entry balances
    return {line.account_id: line.debit - line.credit for line in lines}


def test_the_settling_leg_takes_its_share_then_all_that_is_left_and_never_more() -> None:
    # The whole invoice: bank 10.00 x 3.80 = 38.00, the receivable leaves at the 37.55 booked, gain 0.45.
    assert _realised("10.00", _Journal((Decimal("10.00"), Decimal("37.55")))) == {"bank": Decimal("38.00"), "ar": Decimal("-37.55"), "fx": Decimal("-0.45")}
    # A part: 4.00 of 10.00 booked at 37.55 = 15.02; bank 4.00 x 3.80 = 15.20; gain 0.18.
    assert _realised("4.00", _Journal((Decimal("10.00"), Decimal("37.55")))) == {"bank": Decimal("15.20"), "ar": Decimal("-15.02"), "fx": Decimal("-0.18")}
    # The last 3.34 takes the 12.54 that is left, not 3.34 x 3.755 = 12.5417 rounded on its own; bank 3.34 x 3.80 = 12.69.
    assert _realised("3.34", _Journal((Decimal("3.34"), Decimal("12.54")))) == {"bank": Decimal("12.69"), "ar": Decimal("-12.54"), "fx": Decimal("-0.15")}
    # More than is left (or nothing left, or a balance on the wrong side): the rate the invoice was posted with, 5.00 x 3.755 =
    # 18.775 -> 18.78 (half to even), never the whole of someone else's balance. Bank 5.00 x 3.80 = 19.00.
    for left in ((Decimal("3.34"), Decimal("12.54")), (Decimal(0), Decimal(0)), (Decimal("-10.00"), Decimal("-37.55")), None):
        assert _realised("5.00", _Journal(left)) == {"bank": Decimal("19.00"), "ar": Decimal("-18.78"), "fx": Decimal("-0.22")}
    # Nothing reads the journal (a dry run): the rate table, as before: both sides at 3.80, no difference.
    assert _realised("10.00", None) == {"bank": Decimal("38.00"), "ar": Decimal("-38.00")}


def test_the_new_leg_fields_are_not_part_of_the_fingerprint_so_nothing_posted_is_posted_again() -> None:
    paid = date(2026, 11, 9)
    before = Proposal("invoice_payment:pay_1", "invoice_payment", "pay_1", paid, "Payment", [
        Leg("key:bank", True, "USD", Decimal("10.00"), paid, flow="customers_paid"),
        Leg("key:ar", False, "USD", Decimal("10.00"), date(2026, 11, 1), absorb=True)], fx_balance=True)
    assert before.fingerprint == _payment("10.00").fingerprint
