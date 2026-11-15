"""Matching gaps closed (docs/finance-matching.md): a card repayment linked by hand from Debts is compared with the card's
statement like an engine link, accepting a match in review works the comparison out again, and card repayments and combined
payments across currencies follow the bank-printed original amount rule (else they wait with the reason). Memory backend;
every record is SYNTHETIC and low entropy.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from test_finance_pf3_matching import (  # noqa: F401  (``clean`` is the autouse reset)
    API,
    OWNER,
    approve,
    card_setup,
    clean,
    client,
    coverage_of,
    ok,
    records,
    repay,
    send,
)

from anum_api.finance import matching as E
from anum_api.finance.links_store import LinkStatus, links_memory_store
from anum_api.finance.match_policy import MatchingPolicy

D = Decimal


def _card_records(debt_id: str) -> list[dict]:
    return sorted((r for r in records() if r["target_id"] == debt_id), key=lambda r: (r["posted_on"] or "", r["amount"]))


def _held_card_repayments() -> tuple[dict, list[dict]]:
    """Two repayments after the statement, held for review (card repayments are not linked on their own here)."""
    bank, token, debt = card_setup()
    approve(bank["id"], link_card_repayments=False)
    send(token, "SYNTHETIC. Purchase of SAR 3000.00 at EXAMPLE SHOP with card **1234")  # the card owes, so a hand link fits
    repay(token, "1000.00", days_ago=2)
    repay(token, "500.00")
    held = _card_records(debt["id"])
    assert [r["outcome"] for r in held] == ["review", "review"]
    return debt, held


# -- 4. a card repayment linked by hand, and a match accepted later --------------------------------------------------------


def test_accepting_a_card_match_later_works_the_statement_comparison_out_again() -> None:
    debt, held = _held_card_repayments()
    first, second = held
    # Made while nothing was linked: each alone is a part of the statement.
    assert coverage_of(first) == coverage_of(second) == ["card_statement_partial"]
    ok(client.post(f"{API}/matching/records/{first['id']}/accept", headers=OWNER, json={"version": first["version"]}))
    accepted = ok(client.post(f"{API}/matching/records/{second['id']}/accept", headers=OWNER, json={"version": second["version"]}))
    # 1,000 then 500 of a 1,500 statement: the second one completes it now.
    assert accepted["outcome"] == "accepted" and coverage_of(accepted) == ["card_statement_full"]
    assert "card_overpaid" not in accepted["flags"]


def test_a_card_repayment_linked_by_hand_from_debts_is_compared_with_the_statement() -> None:
    debt, held = _held_card_repayments()
    first, second = held
    for record, code in ((first, "card_statement_partial"), (second, "card_statement_full")):
        made = ok(client.post(f"{API}/debts/{debt['id']}/payments", headers=OWNER, json={"canonical_id": record["canonical_id"]}), 201)
        mine = next(r for r in records() if r["id"] == record["id"])
        assert mine["outcome"] == "accepted" and mine["link_id"] == made["link"]["id"] and coverage_of(mine) == [code]
    # Unmatched from Debts: the record says undone, so the engine never links that pair again on its own.
    link_id = next(r for r in records() if r["id"] == second["id"])["link_id"]
    assert client.delete(f"{API}/debts/{debt['id']}/payments/{link_id}", headers=OWNER).status_code == 204
    assert next(r for r in records() if r["id"] == second["id"])["outcome"] == "undone"


def test_a_hand_link_with_no_engine_record_gets_its_own_record() -> None:
    debt, held = _held_card_repayments()
    first = held[0]
    for r in held:
        links_memory_store.matches.pop(r["id"], None)  # as if the engine never looked at these payments
    ok(client.post(f"{API}/debts/{debt['id']}/payments", headers=OWNER, json={"canonical_id": first["canonical_id"]}), 201)
    (mine,) = _card_records(debt["id"])
    assert (mine["outcome"], mine["reason_code"], coverage_of(mine)) == ("accepted", "hand_link", ["card_statement_partial"])
    assert [e["code"] for e in mine["evidence"]][0] == "hand_link"


# -- 5. across currencies ---------------------------------------------------------------------------------------------------


def _card(currency: str = "USD") -> E.Obligation:
    return E.Obligation(kind="card_repayment", dimension="debt", target_type="finance_debt", target_id="fin_debt_usd", label="Synthetic Card",
                        due_on=date(9999, 12, 31), expected=D("1e12"), currency=currency, liability_account_id="fin_acct_card",
                        statement_on=date(2026, 11, 1), statement_balance=D("400"), statement_minimum=D("40"))


def _fx(original: str | None, currency: str = "USD", billed: str = "1475.00", purchase: str = "1475.00") -> E.FxEvidence:
    return E.FxEvidence(original_currency=currency, original_amount=D(original) if original else None, billed_amount=D(billed),
                        purchase_amount=D(purchase), rate=D("3.6875") if original else None, rate_kind="printed" if original else "not_stated",
                        rate_source="sms")


def _repayment(fx: E.FxEvidence | None, amount: str = "1475.00") -> E.Payment:
    return E.Payment(canonical_id="c_fx_card", posted_on=date(2026, 11, 5), account_id="fin_acct_bank", amount=D(amount), currency="AED",
                     direction="out", text="SYNTHETIC CARD PAYMENT", sources=frozenset({"sms"}), kind="card_repayment",
                     counter_account_id="fin_acct_card", own_transfer=True, left={"bill": D(amount), "debt": D(amount)}, fx=fx)


POLICY = MatchingPolicy(match_transactions=True, reconcile_installments=True, link_card_repayments=True, source_account_ids=["fin_acct_bank"],
                        approved_at=datetime(2026, 11, 1, tzinfo=UTC))


def test_a_card_repaid_from_an_account_in_another_currency_links_on_the_banks_original_amount() -> None:
    card = _card()
    c = E.score(_repayment(_fx("400.00")), card)
    assert c is not None and not c.fx_missing and c.amount == D("400.00") and c.bank_amount == D("1475.00")
    assert {"fx_original", "liability_account"} <= {e.code for e in c.evidence}
    assert E.card_coverage(card, date(2026, 11, 5), "c_fx_card", c.amount)[0] == "full"  # in the card's own currency
    result = E.decide([_repayment(_fx("400.00"))], [card], POLICY)
    (d,) = result.decisions
    assert (d.outcome, d.reason) == ("auto", "fx_original_amount")


def test_a_card_repaid_in_another_currency_without_the_original_amount_waits_with_the_reason() -> None:
    c = E.score(_repayment(_fx(None)), _card())
    assert c is not None and c.fx_missing and c.amount == D("1475.00")  # the bank side: what it paid of the card is not known
    assert any(e.code == "fx_evidence_missing" and "USD card" in (e.value or "") for e in c.evidence)
    (d,) = E.decide([_repayment(_fx(None))], [_card()], POLICY).decisions
    assert (d.outcome, d.reason) == ("review", "fx_evidence_missing")
    # A repayment that names another card's currency is never this card's.
    assert E.score(_repayment(_fx("400.00", currency="EUR")), _card()) is None


def _installment(no: int, amount: str = "200", currency: str = "USD") -> E.Obligation:
    return E.Obligation(kind="installment", dimension="bill", target_type="finance_bill", target_id=f"fin_bill_{no}:2026-11-05",
                        label=f"Example Store {no}/3", due_on=date(2026, 11, 5), expected=D(amount), currency=currency, provider="tamara",
                        order_ref="order-example", installment_no=no, group="tamara:order-example")


def _combined_payment(fx: E.FxEvidence | None, amount: str = "1475.00") -> E.Payment:
    return E.Payment(canonical_id="c_fx_combined", posted_on=date(2026, 11, 5), account_id="fin_acct_bank", amount=D(amount), currency="AED",
                     direction="out", text="TAMARA order-example", sources=frozenset({"sms"}), left={"bill": D(amount), "debt": D(amount)}, fx=fx)


def test_a_combined_payment_across_currencies_splits_on_the_banks_original_amount() -> None:
    parts = E._combined(_combined_payment(_fx("400.00", purchase="1475.00")), [_installment(1), _installment(2)], POLICY)
    assert parts is not None and [c.amount for c in parts] == [D("200"), D("200")]
    # The bank side is split by the original amounts and adds up to the purchase exactly; the FX evidence goes with each part.
    assert [c.bank_amount for c in parts] == [D("737.50"), D("737.50")] and all(c.fx is not None for c in parts)
    uneven = E._combined(_combined_payment(_fx("300.00", purchase="1000.00")), [_installment(1, "100"), _installment(2, "200")], POLICY)
    assert uneven is not None and sum((c.bank_amount for c in uneven), D(0)) == D("1000.00")
    assert [c.bank_amount for c in uneven] == [D("333.33"), D("666.67")]


def test_a_combined_payment_across_currencies_without_the_original_amount_is_never_combined() -> None:
    assert E._combined(_combined_payment(_fx(None)), [_installment(1), _installment(2)], POLICY) is None
    # Partly linked already: a foreign purchase is never split further.
    partly = _combined_payment(_fx("400.00"))
    partly = E.Payment(**{**partly.__dict__, "left": {"bill": D("500.00"), "debt": D("1475.00")}})
    assert E._combined(partly, [_installment(1), _installment(2)], POLICY) is None
    # The two installments still wait for the owner, each with the reason.
    (d,) = E.decide([_combined_payment(_fx(None))], [_installment(1), _installment(2)], POLICY).decisions
    assert (d.outcome, d.reason) == ("review", "fx_evidence_missing")


def test_accepting_a_card_repayment_without_the_cards_amount_is_refused_with_the_reason() -> None:
    from anum_api.finance import links as K
    from anum_api.finance import matching_service as S
    from anum_api.finance.match_store import EvidenceItem, MatchRecord
    from anum_api.schemas import TenantContext, utc_now

    me = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id=OWNER["x-user-id"], roles=["owner"])
    now = utc_now()
    record = MatchRecord(id="fin_match_fx_card", tenant_id=me.tenant_id, workspace_id=me.workspace_id, owner_user_id=me.user_id,
                         canonical_id="c_fx_card", kind="card_repayment", dimension="debt", target_type="finance_debt", target_id="fin_debt_usd",
                         label="Synthetic Card", amount=D("1475.00"), currency="AED", outcome="review", reason_code="fx_evidence_missing",
                         confidence=D("0.5"), evidence=[EvidenceItem(code="fx_evidence_missing", value="AED 1475.00 for a USD card")],
                         policy_version="v1", fx={"basis": "missing", "bank_amount": "1475.00", "bank_currency": "AED", "original_currency": "USD"},
                         created_at=now, updated_at=now)
    assert links_memory_store.add_match(record)
    try:
        S.accept(me, record.id, version=record.version)
    except K.LinkError as exc:
        assert exc.code == "fx_evidence_missing" and "card's currency" in exc.message
    else:
        raise AssertionError("a card repayment without the card's amount must not be accepted")
    assert links_memory_store.matches[record.id].outcome == "review"


def test_a_cross_currency_card_link_counts_on_the_card_by_its_settled_amount() -> None:
    """``load`` reads a card's earlier repayments in the card's currency: the settled amount of a link across currencies."""
    from anum_api.finance import links as K
    from anum_api.finance.links_store import FinanceLink
    from anum_api.schemas import utc_now

    now = utc_now()
    link = FinanceLink(id="fin_link_x", tenant_id="t", workspace_id="w", owner_user_id="u", canonical_id="c1", dimension="debt", role="payment",
                       target_type="finance_debt", target_id="fin_debt_usd", amount=D("1475.00"), currency="AED", settled_amount=D("400.00"),
                       settled_currency="USD", idem_key="k", created_by="u", created_at=now, updated_at=now, status=LinkStatus.ACTIVE)
    assert (K.settled(link, "USD"), K.settled_currency(link)) == (D("400.00"), "USD")
