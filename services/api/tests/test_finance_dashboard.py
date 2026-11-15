"""The Personal Finance dashboard contract (docs/finance.md#dashboard) and transaction detail
(docs/finance.md#transaction-detail), in-memory stores.

Every amount, merchant and message here is SYNTHETIC: written for these tests, never real bank data.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import links as K
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import DEFAULT_TIMEZONE, InstallmentStatementEntry
from anum_api.finance.register_api import RegisterRow, link_evidence
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.summary import FinanceSummary, UpcomingBill, upcoming_payments

OWNER = {"x-tenant-id": "tenant_dash", "x-workspace-id": "ws_dash", "x-user-id": "owner_dash", "x-user-roles": "owner"}
STRANGER = {**OWNER, "x-user-id": "someone_else"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "ws_elsewhere"}
API = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender
FIXTURE = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures" / "finance-summary.json"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    saved = (dict(K._targets), dict(K._observers), dict(K._vets), set(K._single_payment))
    yield
    for registry, before in zip((K._targets, K._observers, K._vets, K._single_payment), saved, strict=True):
        registry.clear()
        registry.update(before)  # type: ignore[arg-type]
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def today() -> date:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()


def account(headers: dict = OWNER, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def txn(account_id: str, amount: str, description: str, headers: dict = OWNER, **body: Any) -> dict:
    response = client.post(f"{API}/transactions", headers=headers,
                           json={"account_id": account_id, "posted_on": today().isoformat(), "amount": amount, "description": description, **body})
    assert response.status_code == 201, response.text
    return response.json()


def summary(headers: dict = OWNER, **params: Any) -> dict:
    response = client.get(f"{API}/summary", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by(rows: list[dict], *keys: str) -> dict:
    return {tuple(row[k] for k in keys) if len(keys) > 1 else row[keys[0]]: row for row in rows}


# -- what the month counts and what it leaves out ---------------------------------------------------------------


def test_own_transfers_card_repayments_loan_principal_and_refunds_are_not_income_or_spend() -> None:
    bank = account(opening_balance="10000")
    savings = account(name="Savings")
    card = account(name="Card", kind="card", last4="1111")
    txn(bank["id"], "15000", "SYNTHETIC EXAMPLE EMPLOYER PAYROLL", category="income")
    txn(bank["id"], "-400", "SYNTHETIC GROCER ALPHA", category="groceries")
    txn(bank["id"], "100", "SYNTHETIC GROCER ALPHA REFUND", category="groceries")  # gives back spending in its category
    txn(bank["id"], "-2000", "SYNTHETIC MOVE TO SAVINGS", category="transfer")
    txn(savings["id"], "2000", "SYNTHETIC MOVE FROM CURRENT", category="transfer")
    txn(bank["id"], "-1500", "SYNTHETIC CARD PAYMENT", transaction_type="card_payment")
    txn(card["id"], "1500", "SYNTHETIC PAYMENT RECEIVED", category="transfer")  # a credit on a card: a card payment
    txn(bank["id"], "-3000", "SYNTHETIC LOAN INSTALMENT PRINCIPAL", transaction_type="loan_principal")
    txn(bank["id"], "20000", "SYNTHETIC LOAN DISBURSED", transaction_type="loan_received")
    txn(card["id"], "-700", "SYNTHETIC SHOP BETA", category="shopping")

    data = summary()
    assert data["detail"] == "full"
    assert data["income"] == [{"currency": "SAR", "amount": "15000.00"}]
    # 400 groceries less the 100 refund, plus 700 shopping: transfers, the card payment and principal are not spend.
    assert data["spend"] == [{"currency": "SAR", "amount": "1000.00"}]
    excluded = by(data["excluded"], "kind")
    assert excluded["own_transfer"]["money_in"] == "2000.00" and excluded["own_transfer"]["money_out"] == "2000.00"
    assert excluded["own_transfer"]["count"] == 2
    assert excluded["card_payment"]["money_in"] == "1500.00" and excluded["card_payment"]["money_out"] == "1500.00"
    assert excluded["loan_principal"]["money_out"] == "3000.00" and excluded["loan_principal"]["money_in"] == "20000.00"
    assert [r["kind"] for r in data["excluded"]] == ["own_transfer", "card_payment", "loan_principal"]
    # Income by kind: no salary profile, so the payroll credit is other income.
    assert by(data["income_breakdown"], "kind")["other_income"]["amount"] == "15000.00"
    # Cash against card credit, per currency: nothing converted.
    position = data["positions"][0]
    assert position["currency"] == "SAR"
    assert position["cash"] == str(Decimal("10000") + 15000 - 400 + 100 - 2000 + 2000 - 1500 - 3000 + 20000) + ".00"
    assert position["card_owed"] == "0.00" and position["card_in_credit"] == "800.00" and position["card_limit"] is None


def test_figures_detail_skips_the_dashboard_extras_but_keeps_the_same_figures() -> None:
    bank = account()
    txn(bank["id"], "-50", "SYNTHETIC CAFE", category="dining")
    full, lite = summary(), summary(detail="figures")
    assert lite["detail"] == "figures" and lite["freshness"] == [] and lite["income_breakdown"] == [] and lite["salary"] is None
    for key in ("spend", "income", "net", "excluded", "positions", "balances", "upcoming_payments"):
        assert lite[key] == full[key], key
    assert client.get(f"{API}/summary", headers=OWNER, params={"detail": "other"}).status_code == 422


def test_review_counts_and_freshness_follow_the_sources() -> None:
    bank = account()
    txn(bank["id"], "-12", "zz synthetic unknown merchant 9")  # no rule knows it: uncategorized
    statement = "Date,Description,Amount\n" + f"{today().isoformat()},SYNTHETIC SPINNEYS,-80.00\n"
    imported = client.post(f"{API}/imports", headers=OWNER, json={"account_id": bank["id"], "content": statement, "filename": "s.csv"})
    assert imported.status_code == 201, imported.text
    cash = account(name="Wallet", kind="cash")
    data = summary()
    assert data["review"]["uncategorized"] == 1 and data["review"]["total"] >= 1
    fresh = by(data["freshness"], "account_id")
    assert fresh[bank["id"]]["sources"] == ["manual", "statement"] and fresh[bank["id"]]["state"] == "current"
    assert fresh[bank["id"]]["last_import_at"] is not None and fresh[bank["id"]]["last_activity_on"] == today().isoformat()
    assert fresh[cash["id"]]["sources"] == ["manual"] and fresh[cash["id"]]["state"] == "manual"
    assert fresh[cash["id"]]["balance_kind"] == "estimated"


def test_an_sms_card_shows_its_last_capture_limit_and_available_credit() -> None:
    card = account(name="Card", kind="card", last4="1234")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    saved = client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"], "credit_limit": "20000"}]})
    assert saved.status_code == 200, saved.text
    token = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"}).json()["token"]
    sent = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"},
                       json={"message": "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234", "sender": SENDER})
    assert sent.status_code == 200, sent.text
    data = summary()
    fresh = by(data["freshness"], "account_id")[card["id"]]
    assert "sms" in fresh["sources"] and fresh["last_sms_at"] is not None and fresh["state"] == "current"
    assert fresh["credit_limit"] == "20000"
    position = data["positions"][0]
    assert position["card_owed"] == "150.00" and position["card_limit"] == "20000.00" and position["card_available"] == "19850.00"
    # Accounts reads the same freshness (one calculation, two pages).
    overview = client.get(f"{API}/accounts/overview", headers=OWNER).json()
    assert by(overview["accounts"], "account_id")[card["id"]]["freshness"] == fresh


def test_upcoming_payments_join_bills_and_installments_still_due() -> None:
    day = date(2026, 11, 15)
    bill = UpcomingBill(id="b1", name="Rent", amount=Decimal("2500"), currency="SAR", cadence="monthly", due_on=date(2026, 11, 20), days_until=5,
                        category="housing")
    plan = [
        InstallmentStatementEntry(provider="tamara", order_id="o1", merchant="Shop Alpha", due_on=date(2026, 11, 18), installment=2,
                                  total_installments=4, amount=Decimal("125"), currency="SAR", source_status="scheduled"),
        InstallmentStatementEntry(provider="tamara", order_id="o1", merchant="Shop Alpha", due_on=date(2026, 11, 3), installment=1,
                                  total_installments=4, amount=Decimal("125"), currency="SAR", source_status="paid"),
        InstallmentStatementEntry(provider="tamara", order_id="o2", merchant="Shop Beta", due_on=date(2026, 11, 10), installment=1,
                                  total_installments=3, amount=Decimal("90"), currency="SAR", source_status="review", refunded_amount=Decimal("30")),
        InstallmentStatementEntry(provider="tamara", order_id="o3", merchant="Shop Gamma", due_on=date(2027, 2, 1), installment=1,
                                  total_installments=3, amount=Decimal("90"), currency="SAR", source_status="scheduled"),
    ]
    rows = upcoming_payments([bill], plan, day)
    assert [(r.kind, r.name, str(r.amount), r.status, r.overdue) for r in rows] == [
        ("installment", "Shop Beta 1/3", "60.00", "needs_review", True),
        ("installment", "Shop Alpha 2/4", "125.00", "pending", False),
        ("bill", "Rent", "2500", "pending", False),
    ]


# -- privacy -------------------------------------------------------------------------------------------------------


def test_the_dashboard_is_the_owners_own() -> None:
    bank = account(opening_balance="500")
    txn(bank["id"], "-20", "SYNTHETIC CAFE", category="dining")
    mine = summary()
    assert mine["accounts"] and mine["freshness"]
    for other in (STRANGER, OTHER_WORKSPACE):
        theirs = summary(other)
        assert theirs["accounts"] == [] and theirs["spend"] == [] and theirs["freshness"] == [] and theirs["positions"] == []
        assert client.get(f"{API}/register/{mine['accounts'][0]['id']}", headers=other).status_code == 404


# -- transaction detail --------------------------------------------------------------------------------------------


def test_detail_shows_category_source_links_with_evidence_and_the_refund() -> None:
    bank = account()
    purchase = txn(bank["id"], "-300", "SYNTHETIC GADGET STORE", category="shopping")
    refund = txn(bank["id"], "300", "REFUND SYNTHETIC GADGET STORE")  # a rule files it under refunds; it finds its purchase
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "Internet", "amount": "300", "currency": "SAR",
                                                            "next_due_on": today().isoformat()}).json()
    detail = client.get(f"{API}/register/{purchase['id']}", headers=OWNER)
    assert detail.status_code == 200, detail.text
    canonical = detail.json()["row"]["canonical_id"]
    made = client.post(f"{API}/bills/{bill['id']}/matches", headers={**OWNER, "Idempotency-Key": "dash-link-1"},
                       json={"canonical_id": canonical, "due_on": today().isoformat(), "amount": "300"})
    assert made.status_code == 201, made.text
    body = client.get(f"{API}/register/{purchase['id']}", headers=OWNER).json()
    assert body["category_by"] == "owner"
    assert len(body["links"]) == 1
    link = body["links"][0]
    # A typed bank entry with no statement behind it: provisionally matched, never "verified paid".
    assert link["dimension"] == "bill" and link["target_name"] == "Internet" and link["evidence"] == "provisional" and link["status"] == "active"
    related = by(body["related"], "id")
    assert related[refund["id"]]["relation"] == "refunded_by"
    back = client.get(f"{API}/register/{refund['id']}", headers=OWNER).json()
    assert by(back["related"], "id")[purchase["id"]]["relation"] == "refund_of"


def test_link_evidence_states() -> None:
    def row(**over: Any) -> RegisterRow:
        values: dict[str, Any] = {
            "id": "t", "account_id": "a", "posted_on": date(2026, 11, 1), "amount": Decimal("-1"), "currency": "SAR", "description": "x",
            "category": "other", "category_source": "rule", "notes": None, "import_id": None, "created_at": None, "updated_at": None,
            "canonical_id": "c", "origin": "sms", "statement_format": None, "sources": ["sms"], "reconciled": False, "receipts": 0,
            "evidence": "missing", "links_need_review": 0, "duplicate_of": None, "status": "recorded", "running_balance": None}
        values.update(over)
        return RegisterRow(**values)

    assert link_evidence("needs_review", row(sources=["statement"]), None) == "pending"
    assert link_evidence("active", row(), None) == "provisional"  # an SMS alone is not settlement
    assert link_evidence("active", row(sources=["sms", "statement"]), None) == "verified"
    assert link_evidence("active", row(reconciled=True), None) == "verified"
    assert link_evidence("active", row(sources=[]), "cash") == "verified"


# -- the contract the web and the phone share ---------------------------------------------------------------------


def test_the_shared_fixture_is_a_valid_summary_answer() -> None:
    """``packages/contracts/fixtures/finance-summary.json`` is what the web (``apps/web/test``) and Flutter
    (``apps/mobile/test/finance``) parse to prove they show the same totals; it must stay a real answer shape."""
    parsed = FinanceSummary.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert parsed.detail == "full" and parsed.positions and parsed.freshness and parsed.excluded
    assert {p.kind for p in parsed.upcoming_payments} == {"bill", "installment"}
    assert sum((p.amount for p in parsed.upcoming_payments if p.currency == "SAR"), Decimal(0)) == next(
        a.amount for a in parsed.payments_due if a.currency == "SAR")
    assert parsed.review.total == parsed.review.uncategorized + parsed.review.sms_review + parsed.review.links_need_review + parsed.review.installments_review
