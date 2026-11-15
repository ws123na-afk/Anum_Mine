"""One place for each match, the engine's evidence on transaction detail, Contacts evidence for loan repayments and card
repayments against the card statement (docs/finance-matching.md, docs/finance.md#transaction-detail, #people-from-contacts).
In-memory stores; every message, person, number and amount is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.contacts.store import memory_data as contacts_data
from anum_api.dependencies import memory_repository
from anum_api.finance import matching as E
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import LinkStatus, links_memory_store
from anum_api.finance.models import FinanceSettings
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.summary import today_in
from anum_api.schemas import WorkspaceMembership

API = "/api/v1/finance"
TENANT, WORKSPACE = "tenant_pf3", "ws_pf3"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_pf3", "x-user-roles": "owner"}
SENDER = "TestBank"  # SYNTHETIC
STAMP = datetime(2026, 11, 1, tzinfo=UTC)
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    stores = (finance_store, sms_memory_store, links_memory_store, rules_memory_store, personal_memory_store, contacts_data)
    for store in stores:
        store.clear()
    svc._last_notified.clear()
    memberships = dict(memory_repository.store.memberships)
    memory_repository.store.memberships[(TENANT, WORKSPACE, "owner_pf3")] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_pf3", role="owner", active=True, created_at=STAMP, updated_at=STAMP)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    for store in stores:
        store.clear()


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json() if response.content else None


def today() -> date:
    return today_in(FinanceSettings())


def account(**body: object) -> dict:
    return ok(client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body}), 201)


def sms_setup(*mapped: tuple[str, str]) -> str:
    assert ok(client.post(f"{API}/sms/self-test", headers=OWNER))["passed"]
    version = ok(client.get(f"{API}/sms/settings", headers=OWNER))["version"]
    ok(client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": last4, "account_id": account_id} for last4, account_id in mapped]}))
    return ok(client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"}), 201)["token"]


def send(token: str, message: str) -> dict:
    return ok(client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER}))


def approve(bank_id: str, **over: Any) -> dict:
    body = {"match_transactions": True, "reconcile_installments": True, "link_recurring_bills": True, "reprocess_history": True,
            "source_account_ids": [bank_id], "approve": True, **over}
    return ok(client.put(f"{API}/automation/matching", headers=OWNER, json=body))


def records(**params: str) -> list[dict]:
    return ok(client.get(f"{API}/matching/records", headers=OWNER, params=params))["items"]


def queue(kind: str) -> list[dict]:
    return ok(client.get(f"{API}/suggestions", headers=OWNER, params={"kind": kind, "limit": 100}))["items"]


# -- 1. one place for each match ----------------------------------------------------------------------------------------


def electricity(bank_id: str, due: date) -> dict:
    return ok(client.post(f"{API}/bills", headers=OWNER, json={
        "name": "Synthetic Electricity", "amount": "300", "currency": "SAR", "cadence": "monthly", "next_due_on": due.isoformat(),
        "account_id": bank_id}), 201)


def test_a_bill_payment_the_engine_holds_for_review_is_not_also_a_suggestion() -> None:
    bank = account(last4="5678")
    token = sms_setup(("5678", bank["id"]))
    due = today() - timedelta(days=3)
    bill = electricity(bank["id"], due)
    send(token, "SYNTHETIC. Purchase of SAR 300.00 at SYNTHETIC ELECTRICITY with card **5678")
    # Before the engine has looked at it, Suggestions proposes the payment for the overdue bill.
    before = queue("payment_match")
    assert len(before) == 1 and before[0]["action"]["kind"] == "link" and before[0]["action"]["link"]["target_id"].startswith(bill["id"])
    # The engine holds the pair for review (bills are not linked on their own here): Matches is the one place that shows it.
    approve(bank["id"], link_recurring_bills=False)
    ok(client.post(f"{API}/matching/run", headers=OWNER))
    waiting = records(outcome="review")
    assert len(waiting) == 1 and waiting[0]["target_id"] == before[0]["action"]["link"]["target_id"]
    assert queue("payment_match") == []
    # The owner rejects it there: the bill comes back as an open suggestion, never with the rejected payment proposed again.
    ok(client.post(f"{API}/matching/records/{waiting[0]['id']}/reject", headers=OWNER, json={}))
    after = queue("payment_match")
    assert len(after) == 1 and after[0]["action"]["kind"] == "open" and after[0]["action"].get("link") is None


# -- 2. the evidence state on transaction detail ------------------------------------------------------------------------


def detail_links(canonical_id: str) -> list[dict]:
    rows = ok(client.get(f"{API}/register", headers=OWNER, params={"limit": 100}))["items"]
    row = next(r for r in rows if r["canonical_id"] == canonical_id)
    return ok(client.get(f"{API}/register/{row['id']}", headers=OWNER))["links"]


def test_transaction_detail_reads_the_engines_evidence_state_and_falls_back_for_hand_links() -> None:
    bank = account(last4="5678")
    token = sms_setup(("5678", bank["id"]))
    bill = electricity(bank["id"], today())
    approve(bank["id"])
    send(token, "SYNTHETIC. Purchase of SAR 300.00 at SYNTHETIC ELECTRICITY with card **5678")
    made = records(outcome="auto_linked")
    assert len(made) == 1 and made[0]["evidence_state"] == "verified"  # the policy counts the bank SMS as settlement
    links = detail_links(made[0]["canonical_id"])
    # The display rule alone would say provisional (no statement row); the engine's stored state wins.
    assert [(l["evidence"], l["evidence_source"], l["match_id"]) for l in links] == [("verified", "matching", made[0]["id"])]
    # Undone: the engine's link is gone; a link made by hand on the bill uses the display rule.
    ok(client.post(f"{API}/matching/records/{made[0]['id']}/undo", headers=OWNER, json={}))
    assert detail_links(made[0]["canonical_id"]) == []
    hand = client.post(f"{API}/bills/{bill['id']}/matches", headers=OWNER, json={
        "canonical_id": made[0]["canonical_id"], "due_on": today().isoformat(), "amount": "300"})
    assert hand.status_code in (200, 201), hand.text
    links = detail_links(made[0]["canonical_id"])
    assert [(l["evidence"], l["evidence_source"], l["match_id"]) for l in links] == [("provisional", "rule", None)]


def test_a_provisional_engine_link_shows_provisional_not_pending() -> None:
    bank = account(last4="5678")
    token = sms_setup(("5678", bank["id"]))
    electricity(bank["id"], today())
    approve(bank["id"], settled_sources=["statement"])  # only a statement settles: an SMS alone links provisionally
    send(token, "SYNTHETIC. Purchase of SAR 300.00 at SYNTHETIC ELECTRICITY with card **5678")
    made = records(outcome="auto_linked")
    assert len(made) == 1 and made[0]["evidence_state"] == "provisional"
    assert [(l["status"], l["evidence"], l["evidence_source"]) for l in detail_links(made[0]["canonical_id"])] == [
        ("needs_review", "provisional", "matching")]


# -- 3. Contacts evidence for repayments of loans with people -----------------------------------------------------------


def contact(name: str, phones: list[str] | None = None) -> str:
    return ok(client.post("/api/v1/contacts", headers=OWNER, json={"name": name, "phones": phones or []}), 201)["person"]["id"]


def person_loan(person: str, contact_id: str | None = None, amount: str = "500") -> dict:
    body: dict[str, Any] = {"direction": "lent", "person": person, "amount": amount, "currency": "AED",
                            "lent_on": (today() - timedelta(days=30)).isoformat()}
    if contact_id:
        body["contact_id"] = contact_id
    return ok(client.post(f"{API}/people-loans", headers=OWNER, json=body), 201)


def money_in(bank_id: str, amount: str, description: str) -> None:
    ok(client.post(f"{API}/transactions", headers=OWNER, json={"account_id": bank_id, "posted_on": today().isoformat(), "amount": amount,
                                                              "description": description, "category": "income"}), 201)


def test_a_phone_number_from_contacts_beats_a_typed_name_and_the_current_name_is_shown() -> None:
    bank = account(currency="AED", name="AED Current")
    sara = contact("Sara Example", ["+971 50 123 4567"])
    by_contact = person_loan("Sara", contact_id=sara)
    person_loan("Omar Sara")  # a typed name that shares a word with the transaction below
    money_in(bank["id"], "200", "Transfer from 0501234567 SARA")
    found = queue("loan_repayment")
    assert len(found) == 1
    item = found[0]
    assert item["action"]["link"]["target_id"] == by_contact["id"] and item["params"]["evidence"] == "phone"
    assert item["params"]["person"] == "Sara Example" and Decimal(item["confidence"]) == Decimal("0.9")


def test_the_contacts_name_counts_when_the_typed_name_does_not() -> None:
    bank = account(currency="AED", name="AED Current")
    nadia = contact("Nadia Example")
    made = person_loan("My cousin", contact_id=nadia)
    money_in(bank["id"], "150", "Transfer from NADIA EXAMPLE")
    found = queue("loan_repayment")
    assert [(f["action"]["link"]["target_id"], f["params"]["evidence"], f["params"]["person"]) for f in found] == [
        (made["id"], "contact", "Nadia Example")]


def test_a_tie_on_the_strongest_evidence_waits_for_review() -> None:
    bank = account(currency="AED", name="AED Current")
    first = contact("Khalid Example")
    second = contact("Khalid Sample")
    person_loan("Khalid", contact_id=first)
    person_loan("Khalid", contact_id=second)
    money_in(bank["id"], "120", "Transfer from KHALID")
    found = queue("loan_repayment")
    assert len(found) == 1 and found[0]["action"]["kind"] == "open" and found[0]["params"]["review"] == "1"
    assert found[0]["params"]["candidates"] == "2"


def test_without_contacts_access_the_typed_name_still_matches() -> None:
    bank = account(currency="AED", name="AED Current")
    loan = person_loan("Synthetic Friend")
    money_in(bank["id"], "100", "Transfer from SYNTHETIC FRIEND")
    found = queue("loan_repayment")
    assert [(f["action"]["link"]["target_id"], f["params"]["evidence"]) for f in found] == [(loan["id"], "name")]


# -- 4. card repayments against the card statement ----------------------------------------------------------------------


def card_obligation(balance: str | None = "1500", minimum: str | None = "100", linked: tuple = ()) -> E.Obligation:
    return E.Obligation(kind="card_repayment", dimension="debt", target_type="finance_debt", target_id="fin_debt_card", label="Card",
                        due_on=date(9999, 12, 31), expected=Decimal("1e12"), currency="SAR", statement_on=date(2026, 11, 1),
                        statement_balance=Decimal(balance) if balance else None, statement_minimum=Decimal(minimum) if minimum else None,
                        statement_payments=linked)


def test_card_coverage_full_minimum_partial_and_over() -> None:
    day = date(2026, 11, 5)
    assert E.card_coverage(card_obligation(), day, "c1", Decimal("1500"))[0] == "full"
    assert E.card_coverage(card_obligation(), day, "c1", Decimal("100"))[0] == "minimum"
    assert E.card_coverage(card_obligation(), day, "c1", Decimal("700"))[0] == "partial"
    over = E.card_coverage(card_obligation(), day, "c1", Decimal("1600"))
    assert over is not None and over[0] == "over" and "1600 of 1500" in over[1]
    # Earlier repayments after the statement count; ones before it (an earlier cycle) and later ones do not.
    linked = ((date(2026, 11, 3), "c0", Decimal("1000")), (date(2026, 10, 30), "old", Decimal("900")), (date(2026, 11, 9), "late", Decimal("50")))
    assert E.card_coverage(card_obligation(linked=linked), day, "c1", Decimal("500"))[0] == "full"
    assert E.card_coverage(card_obligation(linked=linked), day, "c1", Decimal("600"))[0] == "over"
    # No statement stored, or a repayment on or before the statement date: nothing is said.
    assert E.card_coverage(card_obligation(balance=None), day, "c1", Decimal("100")) is None
    assert E.card_coverage(card_obligation(), date(2026, 11, 1), "c1", Decimal("100")) is None


def card_setup(balance: str = "1500", minimum: str = "100") -> tuple[dict, str, dict]:
    bank = account(last4="5678")
    card = account(name="Synthetic Card", kind="card", last4="1234")
    token = sms_setup(("5678", bank["id"]), ("1234", card["id"]))
    debt = ok(client.post(f"{API}/debts", headers=OWNER, json={"name": "Synthetic Card", "kind": "card", "currency": "SAR", "balance": "0",
                                                                "account_id": card["id"], "due_day": 25, "minimum_payment": minimum}), 201)
    statement_on = today() - timedelta(days=10)
    ok(client.put(f"{API}/debts/{debt['id']}/profile", headers=OWNER, json={
        "debt_type": "credit_card", "card_statement_day": statement_on.day, "card_statement_balance": balance,
        "card_statement_minimum": minimum, "card_statement_on": statement_on.isoformat()}))
    approve(bank["id"], link_card_repayments=True)
    return bank, token, debt


def repay(token: str, amount: str, days_ago: int = 0) -> None:
    day = today() - timedelta(days=days_ago)
    send(token, f"SYNTHETIC. Credit card payment of SAR {amount} from account ***5678 to card ***1234 on {day.strftime('%d/%m/%Y')}")


def coverage_of(record: dict) -> list[str]:
    return [e["code"] for e in record["evidence"] if e["code"].startswith("card_statement_")]


def test_a_card_repayment_records_partial_then_full_against_the_statement() -> None:
    _bank, token, debt = card_setup()
    repay(token, "1000.00", days_ago=2)
    repay(token, "500.00")
    made = sorted((r for r in records() if r["target_id"] == debt["id"]), key=lambda r: r["posted_on"])
    assert [coverage_of(r) for r in made] == [["card_statement_partial"], ["card_statement_full"]]
    assert all("card_overpaid" not in r["flags"] for r in made)


def test_a_card_over_payment_is_flagged_and_still_linked() -> None:
    _bank, token, debt = card_setup(balance="400", minimum="40")
    repay(token, "500.00")
    made = [r for r in records() if r["target_id"] == debt["id"]]
    assert len(made) == 1 and coverage_of(made[0]) == ["card_statement_over"] and "card_overpaid" in made[0]["flags"]
    assert made[0]["outcome"] == "auto_linked"
    assert [l for l in links_memory_store.links.values() if l.status != LinkStatus.VOID and l.target_id == debt["id"]]
