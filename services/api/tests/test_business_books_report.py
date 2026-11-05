"""The pure figures of the business books (``business/books_report.py``, docs/business-books.md#how-the-figures-are-made).

Every record here is SYNTHETIC and built in memory: no storage, no clock.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from anum_api.business import books_report as R
from anum_api.business.books_models import Adjustment, Bill, BillPayment, BookSettings, Rate, Section
from anum_api.business.invoice_models import Invoice, InvoiceLine, InvoicePayment
from anum_api.business.models import LineKind

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
SCOPE = {"tenant_id": "t", "workspace_id": "w", "created_at": NOW}


def settings(**body: object) -> BookSettings:
    return BookSettings(id="s", base_currency="SAR", updated_by="u", updated_at=NOW, **SCOPE, **body)  # type: ignore[arg-type]


def invoice(currency: str = "USD", issued: date = date(2026, 3, 10), net: str = "1000", tax: str = "0") -> Invoice:
    total = Decimal(net) + Decimal(tax)
    return Invoice(id="inv1", updated_at=NOW, account_id="acc", status="sent", currency=currency, subtotal=Decimal(net), tax=Decimal(tax), total=total,
                   lines=[InvoiceLine(description="Synthetic", kind=LineKind.SERVICE, net=Decimal(net), tax=Decimal(tax), total=total)],
                   issue_date=issued, due_date=issued, number="INV-1", created_by="u", **SCOPE)


def rate(value: str, on: date, source: str = "Synthetic reference") -> Rate:
    return Rate(id=f"r{on}", base="USD", quote="SAR", rate=Decimal(value), as_of=on, source=source, created_by="u", **SCOPE)


def test_dates_fiscal_years_and_exact_spreads() -> None:
    assert R.fiscal_year(date(2026, 3, 31), 4) == (date(2025, 4, 1), date(2026, 3, 31))
    assert R.fiscal_year(date(2026, 4, 1), 4) == (date(2026, 4, 1), date(2027, 3, 31))
    assert R.add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    parts = R._spread(Decimal("100.00"), "SAR", 3)
    assert parts == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")] and sum(parts) == Decimal("100.00")
    assert sum(R._spread(Decimal("1.000"), "KWD", 7)) == Decimal("1.000")


def test_a_realised_fx_difference_is_the_base_value_paid_less_the_base_value_booked() -> None:
    inputs = R.Inputs(settings=settings(), invoices=[invoice()], rates=[rate("3.70", date(2026, 3, 10)), rate("3.75", date(2026, 4, 20))],
                      payments=[InvoicePayment(id="p1", updated_at=NOW, invoice_id="inv1", amount=Decimal("400"), paid_on=date(2026, 4, 20),
                                               recorded_by="u", **SCOPE)])
    data = R.report(inputs, date(2026, 1, 1), date(2026, 12, 31), R.Basis.ACCRUAL)
    assert data["pnl"]["revenue"]["total"] == "3700.00"
    assert data["pnl"]["fx_differences"] == "20.00"  # 400 x (3.75 - 3.70)
    assert data["pnl"]["net_profit"] == "3720.00"
    assert {c["rate"] for c in data["conversions"]} == {"3.7", "3.75"}
    # Without a rate on or before the issue date the revenue is not converted and no difference is guessed.
    missing = R.report(R.Inputs(settings=settings(), invoices=[invoice()], payments=inputs.payments, rates=[rate("3.75", date(2026, 4, 20))]),
                       date(2026, 1, 1), date(2026, 12, 31), R.Basis.ACCRUAL)
    assert missing["pnl"]["not_converted"] == [{"currency": "USD", "groups": {"revenue": "1000.00"}}]
    assert "fx_difference_incomplete" in missing["notes"] and missing["pnl"]["fx_differences"] == "0.00"


def test_a_rate_older_than_the_settings_allow_is_never_used() -> None:
    data = R.report(R.Inputs(settings=settings(rate_max_age_days=5), invoices=[invoice()], rates=[rate("3.70", date(2026, 3, 1))]),
                    date(2026, 1, 1), date(2026, 12, 31), R.Basis.ACCRUAL)
    assert not data["pnl"]["complete"] and data["conversions"] == []


def test_cash_basis_splits_a_part_payment_between_net_and_tax_and_counts_capital_as_spend_not_cost() -> None:
    sar = invoice(currency="SAR", net="1000", tax="150")
    paid = InvoicePayment(id="p1", updated_at=NOW, invoice_id="inv1", amount=Decimal("575"), paid_on=date(2026, 5, 1), recorded_by="u", **SCOPE)
    laptop = Bill(id="b1", vendor_id="v", description="Synthetic laptop", bill_date=date(2026, 5, 1), due_date=date(2026, 5, 1), currency="SAR",
                  subtotal=Decimal("1200"), tax=Decimal("180"), total=Decimal("1380"), category="equipment", section=Section.CAPITAL,
                  useful_life_months=12, created_by="u", updated_at=NOW, **SCOPE)
    cash_out = BillPayment(id="bp1", bill_id="b1", kind="recorded", amount=Decimal("1380"), currency="SAR", paid_on=date(2026, 5, 2),
                           method="bank_transfer", recorded_by="u", **SCOPE)
    inputs = R.Inputs(settings=settings(), invoices=[sar], payments=[paid], bills=[laptop], bill_payments=[cash_out])
    cash = R.report(inputs, date(2026, 5, 1), date(2026, 5, 31), R.Basis.CASH)
    assert cash["pnl"]["revenue"]["total"] == "500.00" and cash["tax"]["by_currency"][0]["output"] == "75.00"
    assert cash["tax"]["by_currency"][0]["input"] == "180.00"
    assert cash["pnl"]["operating_expenses"]["lines"] == [{"key": "depreciation", "amount": "100.00"}]
    assert cash["cash_flow"]["lines"]["capital_spend"] == [{"currency": "SAR", "amount": "1380.00"}]
    assert cash["cash_flow"]["net"] == [{"currency": "SAR", "amount": "-805.00"}]


def test_a_prepayment_moves_its_cost_into_the_months_it_covers() -> None:
    prepaid = Adjustment(id="a1", kind="prepayment", entry_date=date(2026, 1, 15), currency="SAR", amount=Decimal("1200"), description="Synthetic insurance",
                         start_month=date(2026, 1, 1), months=12, created_by="u", **SCOPE)
    inputs = R.Inputs(settings=settings(), adjustments=[prepaid])
    january = R.report(inputs, date(2026, 1, 1), date(2026, 1, 31), R.Basis.ACCRUAL)["pnl"]
    assert january["operating_expenses"]["total"] == "-1100.00"  # the paid cost (recorded elsewhere) less the eleven months still ahead
    assert R.report(inputs, date(2026, 1, 1), date(2026, 12, 31), R.Basis.ACCRUAL)["pnl"]["operating_expenses"]["total"] == "0.00"
    assert R.report(inputs, date(2026, 1, 1), date(2026, 12, 31), R.Basis.CASH)["pnl"]["operating_expenses"]["total"] == "0.00"
