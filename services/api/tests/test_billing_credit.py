"""The two kinds of account credit (docs/billing.md#account-credit). Net credit (a downgrade's prorated credit) lowers the
taxable subtotal of the next invoice; tax-inclusive credit (a credit note on a paid invoice, a card overpayment) pays the
issued invoice after tax. Every figure is worked by hand: the plan is SAR 100.00 a month (10,000 minor), KSA VAT 15 %.
In-memory rows; the customer is SYNTHETIC.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from test_billing import (  # noqa: F401  (``clean`` resets the stores)
    ISSUER,
    NO_USAGE,
    T0,
    _customer,
    clean,
)

from anum_api.billing import readers
from anum_api.billing import service as svc
from anum_api.billing.pdf import render_invoice, render_receipt
from anum_api.billing.rows import open_billing
from anum_api.billing.views import account_view, invoice_detail_view

FIGURES = ("subtotal_minor", "tax_minor", "total_minor", "amount_paid_minor", "amount_credited_minor", "amount_due_minor")


def figures(rows: Any, invoice_id: str) -> tuple[str, tuple[int, ...]]:
    invoice = svc.get_invoice(rows, invoice_id)
    return invoice["status"], tuple(int(invoice[key]) for key in FIGURES)


def balances(rows: Any) -> tuple[int, int]:
    """(net, tax inclusive)."""
    account = svc.account_of(rows)
    return int(account["credit_balance_minor"]), int(account["credit_gross_minor"] or 0)


def subscribed(rows: Any) -> tuple[dict[str, Any], str]:
    """A customer on the 100.00 plan whose first invoice (115.00) is paid by bank transfer."""
    account, basic = _customer(rows)
    sub, issued = svc.start_subscription(rows, account, basic, now=T0, actor="t", trial_days=0)
    svc.record_payment(rows, issued.invoice["id"], amount_minor=11_500, method="bank_transfer", reference="x", received_at=T0, now=T0, actor="t")
    return sub, issued.invoice["id"]


def renew(rows: Any, sub: dict[str, Any]) -> tuple[dict[str, Any], str]:
    result = svc.run_tenant(rows, now=sub["current_period_end"] + timedelta(minutes=1))
    assert len(result.invoices) == 1
    return svc.live_subscription(rows), result.invoices[0]


def set_net(rows: Any, amount: int) -> None:
    rows.update("billing_accounts", {"tenant_id": rows.tenant_id}, {"credit_balance_minor": amount})


def credit_payments(rows: Any, invoice_id: str) -> list[int]:
    return [int(p["amount_minor"]) for p in rows.select("billing_payments", {"invoice_id": invoice_id}) if p["method"] == "account_credit"]


def test_a_credit_note_on_a_paid_invoice_pays_the_renewal_after_tax() -> None:
    with open_billing("cust-a") as rows:
        sub, first = subscribed(rows)
        note = svc.issue_credit_note(rows, first, total_minor=11_500, reason="Goodwill", now=T0, actor="t")
        assert (note["subtotal_minor"], note["tax_minor"]) == (10_000, 1_500)
        assert balances(rows) == (0, 11_500)  # paid with tax, so credited with tax
        sub, renewal = renew(rows, sub)
        # 100.00 + 15.00 VAT = 115.00, paid in full from the 115.00 credit. Before: subtotal 0, tax 0 and 15.00 left over.
        assert figures(rows, renewal) == ("paid", (10_000, 1_500, 11_500, 11_500, 0, 0))
        assert credit_payments(rows, renewal) == [11_500] and balances(rows) == (0, 0)
        lines = rows.select("billing_invoice_lines", {"invoice_id": renewal})
        assert [line["kind"] for line in lines] == ["plan"]  # no line lowers the taxable amount
        # The customer's invoice lists the credit as a payment, and the invoice and its receipt still print.
        detail = svc.invoice_detail(rows, svc.get_invoice(rows, renewal))
        shown = invoice_detail_view(detail, T0)["payments"]
        assert [(p["method"], p["amount_minor"], p["reference"]) for p in shown] == [("account_credit", 11_500, "Credit from your account balance")]
        assert render_invoice(detail).startswith(b"%PDF") and render_receipt(detail["invoice"], detail["payments"][0], []).startswith(b"%PDF")
        # No new money arrived in the renewal's month: the console's "collected" leaves the credit payment out.
        assert readers.summary(ISSUER, sub["current_period_start"] + timedelta(days=1))["collected_this_month_minor"] == 0
        assert readers.summary(ISSUER, T0 + timedelta(days=1))["collected_this_month_minor"] == 11_500
        # A credit note on the invoice the credit paid puts the tax-inclusive amount back.
        svc.issue_credit_note(rows, renewal, total_minor=11_500, reason="Cancelled", now=T0, actor="t")
        assert balances(rows) == (0, 11_500)


def test_a_downgrade_credit_still_lowers_the_taxable_subtotal() -> None:
    with open_billing("cust-a") as rows:
        account, basic = _customer(rows)
        _, pro = _customer(rows, 30_000, id="plan_pro", code="pro", name="Pro", limits={"seats": 20})
        sub, _ = svc.start_subscription(rows, account, pro, now=T0, actor="t", trial_days=0)
        halfway = T0 + (sub["current_period_end"] - T0) / 2
        sub, _ = svc.change_plan(rows, basic, seats=None, when="now", usage=NO_USAGE, now=halfway, actor="t")
        # Half a month of the 200.00 difference, before tax: 100.00 of net credit.
        assert balances(rows) == (10_000, 0)
        sub, renewal = renew(rows, sub)
        # 100.00 less 100.00 of net credit = 0.00 taxable, no VAT, nothing to pay and no payment recorded.
        assert figures(rows, renewal) == ("paid", (0, 0, 0, 0, 0, 0))
        assert credit_payments(rows, renewal) == [] and balances(rows) == (0, 0)
        lines = {line["kind"]: int(line["amount_minor"]) for line in rows.select("billing_invoice_lines", {"invoice_id": renewal})}
        assert lines == {"plan": 10_000, "credit": -10_000}


def test_net_credit_comes_off_before_tax_and_tax_inclusive_credit_after() -> None:
    with open_billing("cust-a") as rows:
        sub, _ = subscribed(rows)
        set_net(rows, 4_000)
        svc.add_gross_credit(rows, 5_000, T0)
        sub, renewal = renew(rows, sub)
        # 100.00 - 40.00 net credit = 60.00; VAT 15 % = 9.00; total 69.00; 50.00 paid from credit; 19.00 still due.
        assert figures(rows, renewal) == ("open", (6_000, 900, 6_900, 5_000, 0, 1_900))
        assert credit_payments(rows, renewal) == [5_000] and balances(rows) == (0, 0)


def test_net_credit_that_covers_the_invoice_leaves_the_tax_inclusive_credit_alone() -> None:
    with open_billing("cust-a") as rows:
        sub, _ = subscribed(rows)
        set_net(rows, 12_000)
        svc.add_gross_credit(rows, 3_000, T0)
        sub, renewal = renew(rows, sub)
        # 100.00 of the 120.00 net credit makes the invoice 0.00; 20.00 net and the 30.00 tax-inclusive credit remain.
        assert figures(rows, renewal) == ("paid", (0, 0, 0, 0, 0, 0))
        assert credit_payments(rows, renewal) == [] and balances(rows) == (2_000, 3_000)


def test_credit_larger_than_the_invoice_is_kept_for_the_next_one_and_never_goes_negative() -> None:
    with open_billing("cust-a") as rows:
        sub, _ = subscribed(rows)
        svc.add_gross_credit(rows, 20_000, T0)
        sub, second = renew(rows, sub)
        # 115.00 of the 200.00 pays the renewal; 85.00 stays.
        assert figures(rows, second) == ("paid", (10_000, 1_500, 11_500, 11_500, 0, 0)) and balances(rows) == (0, 8_500)
        sub, third = renew(rows, sub)
        # The next renewal takes the remaining 85.00 and leaves 30.00 to pay.
        assert figures(rows, third) == ("open", (10_000, 1_500, 11_500, 8_500, 0, 3_000)) and balances(rows) == (0, 0)
        assert credit_payments(rows, second) == [11_500] and credit_payments(rows, third) == [8_500]
        sub, fourth = renew(rows, sub)
        assert figures(rows, fourth) == ("open", (10_000, 1_500, 11_500, 0, 0, 11_500)) and balances(rows) == (0, 0)


def test_running_the_same_renewal_again_uses_no_more_credit() -> None:
    with open_billing("cust-a") as rows:
        sub, _ = subscribed(rows)
        svc.add_gross_credit(rows, 20_000, T0)
        moment = sub["current_period_end"] + timedelta(minutes=1)
        first = svc.run_tenant(rows, now=moment)
        again = svc.run_tenant(rows, now=moment)
        assert len(first.invoices) == 1 and again.invoices == []
        assert credit_payments(rows, first.invoices[0]) == [11_500] and balances(rows) == (0, 8_500)
        assert len(rows.select("billing_invoices")) == 2  # the first invoice and one renewal


def test_the_account_shows_the_whole_credit_and_its_two_parts() -> None:
    with open_billing("cust-a") as rows:
        subscribed(rows)
        set_net(rows, 4_000)
        svc.add_gross_credit(rows, 5_000, T0)
        svc.add_gross_credit(rows, 0, T0)  # nothing to add changes nothing
        view = account_view(svc.account_of(rows))
        assert (view["credit_balance_minor"], view["credit_net_minor"], view["credit_gross_minor"]) == (9_000, 4_000, 5_000)
    # An account written before the column existed (no value) reads as 0 tax-inclusive credit.
    legacy = account_view({"display_name": "Old", "credit_balance_minor": 700, "state": "active"})
    assert (legacy["credit_balance_minor"], legacy["credit_net_minor"], legacy["credit_gross_minor"]) == (700, 700, 0)
