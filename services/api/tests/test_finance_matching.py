"""The matching engine and its policy (docs/finance-matching.md), pure: every payment, order and amount here is SYNTHETIC.

``test_finance_matching_flow.py`` drives the same engine through the API (SMS in, link out, undo, history backfill);
``test_postgres_finance_matching.py`` checks the tables of ``0184_pf_matching`` under RLS.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from anum_api.finance import matching as E
from anum_api.finance.match_policy import MatchingPolicy
from anum_api.finance.rules import NEVER_AUTOMATED, AutomationRefused, require_automatable

TODAY = date(2026, 11, 15)
BANK = "fin_acct_bank"
CARD = "fin_acct_card"


def policy(**over: object) -> MatchingPolicy:
    values: dict = {"match_transactions": True, "reconcile_installments": True, "link_recurring_bills": True, "link_card_repayments": True,
                    "link_bank_loan_repayments": True, "source_account_ids": [BANK], "approved_at": datetime(2026, 11, 1, tzinfo=timezone.utc)}
    values.update(over)
    return MatchingPolicy(**values)


def pay(canonical: str = "fin_je_1", amount: str = "125", text: str = "POS PURCHASE TAMARA RIYADH", *, on: date = TODAY, account: str = BANK,
        sources: tuple[str, ...] = ("sms",), currency: str = "SAR", direction: str = "out", **over: object) -> E.Payment:
    value = Decimal(amount)
    return E.Payment(canonical_id=canonical, posted_on=on, account_id=account, amount=value, currency=currency, direction=direction, text=text,
                     sources=frozenset(sources), left={"bill": value, "debt": value}, **over)  # type: ignore[arg-type]


def installment(order: str = "ORD10001", number: int = 2, amount: str = "125", *, due: date = TODAY, bill: str = "fin_bill_a",
                currency: str = "SAR") -> E.Obligation:
    return E.Obligation(kind="installment", dimension="bill", target_type="finance_bill_due", target_id=f"{bill}:{due.isoformat()}",
                        label=f"Shop {number}/4", due_on=due, expected=Decimal(amount), currency=currency, provider="tamara", order_ref=order,
                        installment_no=number, group=f"tamara:{order.casefold()}")


def only(result: E.Result) -> E.Decision:
    assert len(result.decisions) == 1, result.decisions
    return result.decisions[0]


# -- installments ----------------------------------------------------------------------------------------------------


def test_tamara_paid_today_is_linked_automatically_and_verified_by_the_bank_sms() -> None:
    decision = only(E.decide([pay()], [installment()], policy()))
    assert decision.outcome == "auto" and decision.state == "verified"
    assert decision.reason == "provider_amount_date" and decision.confidence >= Decimal("0.9")
    assert decision.parts[0].obligation.installment_no == 2 and decision.parts[0].amount == Decimal("125")
    codes = {e.code for e in decision.parts[0].evidence}
    assert {"provider", "amount_exact", "date_close"} <= codes


def test_the_same_amount_on_two_orders_waits_for_review_and_the_order_number_decides() -> None:
    two = [installment("ORD10001", bill="fin_bill_a"), installment("ORD20002", bill="fin_bill_b")]
    decision = only(E.decide([pay()], two, policy()))
    assert decision.outcome == "review" and decision.reason == "ambiguous_obligation" and decision.competitors == 1
    named = only(E.decide([pay(text="TAMARA ORDER ORD20002")], two, policy()))
    assert named.outcome == "auto" and named.reason == "order_reference_match"
    assert named.parts[0].obligation.order_ref == "ORD20002"


def test_two_identical_debits_for_one_installment_are_never_split_by_guessing() -> None:
    result = E.decide([pay("fin_je_1", account=BANK), pay("fin_je_2", account=CARD, text="TAMARA PAYMENT")], [installment()], policy())
    assert {d.outcome for d in result.decisions} == {"review"}


def test_within_one_order_the_oldest_unpaid_installment_is_the_one_paid() -> None:
    due = [installment(number=2, due=date(2026, 11, 12), bill="fin_bill_a"), installment(number=3, due=date(2026, 11, 18), bill="fin_bill_b")]
    decision = only(E.decide([pay()], due, policy()))
    assert decision.outcome == "auto" and decision.parts[0].obligation.installment_no == 2


def test_pending_evidence_links_provisionally_and_settled_evidence_verifies() -> None:
    wallet_only = only(E.decide([pay(sources=("wallet",))], [installment()], policy()))
    assert wallet_only.outcome == "auto" and wallet_only.state == "provisional"
    sms_only_strict = only(E.decide([pay(sources=("sms",))], [installment()], policy(settled_sources=["statement"])))
    assert sms_only_strict.state == "provisional"
    statement = only(E.decide([pay(sources=("sms", "statement"))], [installment()], policy(settled_sources=["statement"])))
    assert statement.state == "verified"


def test_apple_pay_and_the_bank_sms_of_one_purchase_are_one_payment_and_one_link() -> None:
    decision = only(E.decide([pay(sources=("wallet", "sms"))], [installment()], policy()))
    assert decision.outcome == "auto" and decision.state == "verified" and not decision.flags


def test_a_duplicate_sms_and_statement_row_are_never_both_linked() -> None:
    sms = pay("fin_je_1", sources=("sms",))
    statement = pay("fin_je_2", sources=("statement",), on=date(2026, 11, 16))
    result = E.decide([sms, statement], [installment()], policy())
    assert result.duplicates == {"fin_je_2": "fin_je_1"}
    assert all(d.outcome == "review" for d in result.decisions)
    assert any(d.reason == "duplicate_event" for d in result.decisions)
    # The original already linked: its repeat waits, it never pays the next installment on its own.
    later = E.decide([statement], [installment(number=3, due=date(2026, 11, 16), bill="fin_bill_c")], policy(),
                     linked=[pay("fin_je_1", sources=("sms",))])
    assert only(later).outcome == "review" and "duplicate_event" in only(later).flags


def test_a_partial_installment_waits_with_its_reason() -> None:
    decision = only(E.decide([pay(amount="60")], [installment()], policy()))
    assert decision.outcome == "review" and decision.reason == "partial_amount" and decision.parts[0].amount == Decimal("60")


def test_one_debit_for_two_installments_of_one_order_is_a_combined_payment() -> None:
    due = [installment(number=2, due=date(2026, 11, 14), bill="fin_bill_a"), installment(number=3, due=date(2026, 11, 15), bill="fin_bill_b")]
    decision = only(E.decide([pay(amount="250")], due, policy()))
    assert decision.reason == "combined_payment" and decision.outcome == "auto"
    assert sorted(c.obligation.installment_no for c in decision.parts) == [2, 3]
    assert sum(c.amount for c in decision.parts) == Decimal("250")


def test_a_refund_after_the_purchase_holds_the_match() -> None:
    refund = pay("fin_je_9", direction="in", text="TAMARA REFUND", on=date(2026, 11, 18))
    decision = only(E.decide([pay()], [installment()], policy(), credits=[refund]))
    assert decision.outcome == "review" and "refund_signal" in decision.flags


def test_own_account_transfer_is_never_a_payment() -> None:
    moved = pay(text="TRANSFER TO OWN ACCOUNT TAMARA", own_transfer=True, kind="own_transfer")
    result = E.decide([moved], [installment()], policy())
    assert result.decisions == [] and result.unmatched == ["fin_je_1"]


def test_another_currency_is_never_linked_without_the_banks_conversion_figures() -> None:
    # Naming the provider is enough to show the match to the owner, never to link it: no rate is guessed
    # (test_finance_matching_fx.py covers the bank's printed original amount).
    usd = only(E.decide([pay(currency="USD")], [installment()], policy()))
    assert usd.outcome == "review" and usd.reason == "fx_evidence_missing"
    stated = only(E.decide([pay(currency="USD", original_currency="SAR", original_amount=Decimal("125"))], [installment()], policy()))
    assert stated.outcome == "review" and stated.reason == "fx_evidence_missing"  # no billed figures: not conversion evidence
    assert E.decide([pay(currency="USD", text="POS PURCHASE RIYADH")], [installment()], policy()).decisions == []  # nothing names it


def test_another_providers_debit_never_pays_a_tamara_installment() -> None:
    assert E.decide([pay(text="TABBY INSTALMENT")], [installment()], policy()).decisions == []


def test_a_date_far_from_the_due_date_needs_the_order_number() -> None:
    early = pay(on=date(2026, 10, 20))
    assert E.decide([early], [installment()], policy()).decisions == []
    named = only(E.decide([pay(on=date(2026, 10, 20), text="TAMARA ORD10001 EARLY")], [installment()], policy()))
    assert named.outcome == "auto" and named.reason == "order_reference_match"


# -- policy -------------------------------------------------------------------------------------------------------


def test_nothing_is_automatic_until_the_policy_is_on_approved_and_the_account_named() -> None:
    off = only(E.decide([pay()], [installment()], MatchingPolicy()))
    assert off.outcome == "review" and off.reason == "policy_off"
    unapproved = only(E.decide([pay()], [installment()], policy(approved_at=None)))
    assert unapproved.outcome == "review"
    elsewhere = only(E.decide([pay(account=CARD)], [installment()], policy()))
    assert elsewhere.outcome == "review" and elsewhere.reason == "account_not_source"
    kind_off = only(E.decide([pay()], [installment()], policy(reconcile_installments=False)))
    assert kind_off.reason == "policy_off"


def test_record_linking_is_automatable_but_money_and_people_loans_never_are() -> None:
    for action in ("match_transactions", "reconcile_installments", "link_recurring_bills", "link_card_repayments", "link_bank_loan_repayment",
                   "reprocess_history"):
        require_automatable(action)
    for action in ("approve_payment", "post_adjustment", "link_loan_payments", "link_person_loan_payments", "change_policy"):
        assert action in NEVER_AUTOMATED
        with pytest.raises(AutomationRefused):
            require_automatable(action)


# -- bills, cards and loans ---------------------------------------------------------------------------------------


def bill(amount: str = "290", label: str = "STC broadband", *, due: date = TODAY, account: str | None = BANK) -> E.Obligation:
    return E.Obligation(kind="bill", dimension="bill", target_type="finance_bill_due", target_id=f"fin_bill_stc:{due.isoformat()}", label=label,
                        due_on=due, expected=Decimal(amount), currency="SAR", account_id=account)


def test_a_bill_needs_agreeing_words_amount_date_and_its_account_to_link_alone() -> None:
    linked = only(E.decide([pay(amount="290", text="STC BROADBAND BILL")], [bill()], policy()))
    assert linked.outcome == "auto" and linked.reason == "merchant_amount_date"
    no_account = only(E.decide([pay(amount="290", text="STC BROADBAND BILL")], [bill(account=None)], policy()))
    assert no_account.outcome == "review" and no_account.reason == "low_confidence"
    other_shop = E.decide([pay(amount="290", text="NOON SHOPPING")], [bill()], policy())
    assert all(d.outcome == "review" for d in other_shop.decisions)


def test_a_card_repayment_links_to_its_card_and_a_purchase_never_does() -> None:
    card = E.Obligation(kind="card_repayment", dimension="debt", target_type="finance_debt", target_id="fin_debt_card", label="Visa",
                        due_on=date(9999, 12, 31), expected=Decimal("1e12"), currency="SAR", liability_account_id=CARD)
    repayment = pay(amount="3000", text="CARD PAYMENT", kind="card_repayment", counter_account_id=CARD, own_transfer=True)
    decision = only(E.decide([repayment], [card], policy()))
    assert decision.outcome == "auto" and decision.reason == "repayment_to_account"
    purchase = pay(amount="3000", text="VISA PURCHASE")
    assert E.decide([purchase], [card], policy()).decisions == []
    # A card repayment is not a second expense: it never pays a bill.
    assert E.decide([repayment], [bill(amount="3000")], policy()).decisions == []
    # Two repayments in one month are two separate links, never rivals.
    second = pay("fin_je_2", amount="500", text="CARD PAYMENT", kind="card_repayment", counter_account_id=CARD, own_transfer=True)
    assert {d.outcome for d in E.decide([repayment, second], [card], policy()).decisions} == {"auto"}


def test_a_bank_loan_month_links_the_instalment_debited_to_the_loan() -> None:
    loan = E.Obligation(kind="bank_loan", dimension="debt", target_type="finance_debt", target_id="fin_debt_loan", label="Bank auto loan",
                        due_on=date(2026, 11, 14), expected=Decimal("1850"), currency="SAR", liability_account_id="fin_acct_loan")
    debit = pay(amount="1850", text="LOAN REPAYMENT", kind="loan_repayment", counter_account_id="fin_acct_loan")
    decision = only(E.decide([debit], [loan], policy()))
    assert decision.outcome == "auto" and decision.kind == "bank_loan"
    assert only(E.decide([debit], [loan], policy(link_bank_loan_repayments=False))).reason == "policy_off"


def test_scores_are_deterministic() -> None:
    first = E.decide([pay()], [installment()], policy())
    again = E.decide([pay()], [installment()], policy())
    assert [(d.confidence, d.reason) for d in first.decisions] == [(d.confidence, d.reason) for d in again.decisions]
    assert E.provider_in("شراء تمارا") == "tamara" and E.provider_in("TABBY") == "tabby" and E.provider_in("STC") is None
    assert E.names_order("ref: ord-10001", "ORD10001") and not E.names_order("ord1", "ORD1")
