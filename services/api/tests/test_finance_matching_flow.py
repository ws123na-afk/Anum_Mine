"""Automatic matching through the API, in-memory stores (docs/finance-matching.md). Every message, order and amount is SYNTHETIC.

A Tamara installment due today, the owner's approved policy, a bank SMS of the debit: the installment is linked as
verified, the bill counts as paid, Undo removes the link and keeps the transaction, and no rerun links it again.
History backfill: preview, apply, a second apply that creates nothing, undo. The records stay the owner's.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import LinkStatus, links_memory_store
from anum_api.finance.models import FinanceSettings
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.summary import today_in

API = "/api/v1/finance"
OWNER = {"x-tenant-id": "tenant_match", "x-workspace-id": "ws_match", "x-user-id": "owner_match", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "someone_else"}
VIEWER = {**OWNER, "x-user-roles": "viewer"}
SENDER = "TestBank"  # SYNTHETIC
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    rules_memory_store.clear()
    svc._last_notified.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    rules_memory_store.clear()


def today() -> date:
    return today_in(FinanceSettings())


def account(**body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def setup() -> dict[str, Any]:
    bank = account(name="Current", last4="5678")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    put = client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "5678", "account_id": bank["id"]}]})
    assert put.status_code == 200, put.text
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    assert device.status_code == 201
    return {"bank": bank, "token": device.json()["token"]}


def send(token: str, message: str) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text
    return response.json()


def tamara_order(order: str = "ORD10001", amount: str = "125", due: date | None = None, number: int = 2) -> dict:
    """The approved import: the statement row and the one-time bill for the installment."""
    due = due or today()
    entry = {"provider": "Tamara", "order_id": order, "merchant": "Synthetic Shop", "purchase_on": (due - timedelta(days=30)).isoformat(),
             "due_on": due.isoformat(), "installment": number, "total_installments": 4, "amount": amount, "currency": "SAR",
             "source_status": "scheduled"}
    synced = client.post(f"{API}/installment-statement/sync", headers=OWNER, json={"entries": [entry]})
    assert synced.status_code == 200, synced.text
    bill = client.post(f"{API}/bills", headers=OWNER, json={
        "name": f"Tamara: Synthetic Shop {number}/4", "amount": amount, "currency": "SAR", "cadence": "once", "next_due_on": due.isoformat(),
        "notes": f"Order: {order} | Installment: {number} | Provider-reported scheduled; no bank match confirmed."})
    assert bill.status_code == 201, bill.text
    return bill.json()


def approve(bank_id: str, **over: Any) -> dict:
    body = {"match_transactions": True, "reconcile_installments": True, "link_recurring_bills": True, "reprocess_history": True,
            "source_account_ids": [bank_id], "approve": True, **over}
    response = client.put(f"{API}/automation/matching", headers=OWNER, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def schedule_status(bill_id: str) -> str:
    day = today()
    rows = client.get(f"{API}/bill-schedule?from={(day - timedelta(days=40)).isoformat()}&to={(day + timedelta(days=40)).isoformat()}",
                      headers=OWNER).json()["items"]
    return next(r["status"] for r in rows if r["bill_id"] == bill_id)


def records(**params: str) -> list[dict]:
    response = client.get(f"{API}/matching/records", headers=OWNER, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_policy_is_off_until_approved_and_the_old_automation_page_never_resets_it() -> None:
    s = setup()
    view = client.get(f"{API}/automation/matching", headers=OWNER).json()
    assert view["approved"] is False and view["policy"]["match_transactions"] is False
    assert "link_person_loan_payments" in view["never_automated"] and view["spending_policy"] == "installments_when_paid"
    assert any(p["key"] == "tamara" and p["connected"] for p in view["providers"])
    assert any(p["key"] == "tabby" and not p["connected"] for p in view["providers"])
    approved = approve(s["bank"]["id"])
    assert approved["approved"] is True and approved["version"].endswith(".r1")
    # Saving classification switches on the older page keeps the matching policy as it was.
    assert client.put(f"{API}/automation", headers=OWNER, json={"auto_classify": False}).status_code == 200
    assert client.get(f"{API}/automation/matching", headers=OWNER).json()["approved"] is True
    # Any change withdraws the approval unless the same request approves it.
    changed = client.put(f"{API}/automation/matching", headers=OWNER, json={**approved["policy"], "approved_at": None, "approved_by": None,
                                                                            "revision": None} | {"min_confidence": "0.95"})
    assert changed.status_code == 422  # the stored fields are not inputs
    body = {k: v for k, v in approved["policy"].items() if k not in ("revision", "approved_at", "approved_by")}
    changed = client.put(f"{API}/automation/matching", headers=OWNER, json={**body, "min_confidence": "0.95"})
    assert changed.status_code == 200 and changed.json()["approved"] is False
    assert client.put(f"{API}/automation/matching", headers=VIEWER, json=body).status_code == 403
    foreign = client.put(f"{API}/automation/matching", headers=OWNER, json={**body, "source_account_ids": ["fin_acct_not_mine"]})
    assert foreign.status_code == 422


def test_tamara_paid_today_is_matched_verified_and_can_be_undone_without_losing_the_payment() -> None:
    s = setup()
    bill = tamara_order()
    approve(s["bank"]["id"])
    posted = send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at TAMARA with card **5678")
    assert posted["status"] == "posted"
    assert schedule_status(bill["id"]) == "paid"
    made = records(outcome="auto_linked")
    assert len(made) == 1 and made[0]["kind"] == "installment" and made[0]["evidence_state"] == "verified"
    assert made[0]["reason_code"] == "provider_amount_date" and made[0]["policy_version"].startswith("pfm-1")
    assert {e["code"] for e in made[0]["evidence"]} >= {"provider", "amount_exact"}
    status = client.get(f"{API}/matching/installments", headers=OWNER).json()
    row = status["orders"][0]["installments"][0]
    assert row["status"] == "verified_paid" and status["orders"][0]["still_due"] == "0.00" or Decimal(status["orders"][0]["still_due"]) == 0
    # Undo: the link is voided, the transaction stays, and a rerun does not link it again.
    entries_before = len(sms_memory_store.entries)
    undone = client.post(f"{API}/matching/records/{made[0]['id']}/undo", headers=OWNER, json={"version": made[0]["version"]})
    assert undone.status_code == 200, undone.text and undone.json()["outcome"] == "undone"
    assert len(sms_memory_store.entries) == entries_before
    assert all(l.status == LinkStatus.VOID for l in links_memory_store.links.values())
    assert schedule_status(bill["id"]) in ("due_soon", "overdue")
    rerun = client.post(f"{API}/matching/run", headers=OWNER)
    assert rerun.status_code == 200 and rerun.json()["counts"]["auto_linked"] == 0
    assert rerun.json()["counts"]["kept_owner_choice"] == 1
    assert all(l.status == LinkStatus.VOID for l in links_memory_store.links.values())


def test_two_orders_with_the_same_amount_wait_for_review_and_the_owner_accepts_one() -> None:
    s = setup()
    first = tamara_order("ORD10001")
    tamara_order("ORD20002")
    approve(s["bank"]["id"])
    send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at TAMARA with card **5678")
    waiting = records(outcome="review")
    assert len(waiting) == 2 and {w["reason_code"] for w in waiting} == {"ambiguous_obligation"}
    assert not [l for l in links_memory_store.links.values() if l.status != LinkStatus.VOID]
    status = client.get(f"{API}/matching/installments", headers=OWNER).json()
    assert {r["status"] for o in status["orders"] for r in o["installments"]} == {"review"}
    chosen = next(w for w in waiting if w["target_id"].startswith(first["id"]))
    accepted = client.post(f"{API}/matching/records/{chosen['id']}/accept", headers=OWNER, json={"version": chosen["version"]})
    assert accepted.status_code == 200, accepted.text
    assert schedule_status(first["id"]) == "paid"
    other = next(w for w in waiting if w["id"] != chosen["id"])
    # The payment is used up: accepting the other one too is refused by the ledger's own rule.
    refused = client.post(f"{API}/matching/records/{other['id']}/accept", headers=OWNER, json={"version": other["version"]})
    assert refused.status_code == 409 and refused.json()["code"] == "over_allocated"
    assert client.post(f"{API}/matching/records/{other['id']}/reject", headers=OWNER, json={}).json()["outcome"] == "rejected"
    # Someone else never sees or decides these records.
    assert client.get(f"{API}/matching/records", headers=OTHER).json()["total"] == 0
    assert client.post(f"{API}/matching/records/{chosen['id']}/undo", headers=OTHER, json={}).status_code == 404


def test_a_partial_installment_waits_and_shows_partly_paid_once_accepted() -> None:
    s = setup()
    bill = tamara_order(amount="125")
    approve(s["bank"]["id"])
    send(s["token"], "SYNTHETIC. Purchase of SAR 60.00 at TAMARA with card **5678")
    waiting = records(outcome="review")
    assert len(waiting) == 1 and waiting[0]["reason_code"] == "partial_amount" and Decimal(waiting[0]["amount"]) == Decimal("60")
    client.post(f"{API}/matching/records/{waiting[0]['id']}/accept", headers=OWNER, json={})
    row = client.get(f"{API}/matching/installments", headers=OWNER).json()["orders"][0]["installments"][0]
    assert row["status"] == "partly_paid" and Decimal(row["still_due"]) == Decimal("65")
    assert bill["id"] == row["bill_id"]


def test_history_backfill_previews_applies_once_and_undoes() -> None:
    s = setup()
    bill = tamara_order()
    # The debit arrived before matching was on: nothing linked it.
    send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at TAMARA with card **5678")
    assert schedule_status(bill["id"]) != "paid"
    preview = client.post(f"{API}/matching/backfill", headers=OWNER, json={"mode": "preview"})
    assert preview.status_code == 201, preview.text
    run = preview.json()
    while run["status"] == "running":
        run = client.post(f"{API}/matching/backfill/{run['id']}/continue", headers=OWNER).json()
    assert run["status"] == "done" and run["counts"]["matched"] == 1 and run["counts"]["review"] == 1  # the policy is off: review only
    assert not links_memory_store.links and not records()
    refused = client.post(f"{API}/matching/backfill", headers=OWNER, json={"mode": "apply"})
    assert refused.status_code == 409 and refused.json()["code"] == "policy_off"
    approve(s["bank"]["id"])
    applied = client.post(f"{API}/matching/backfill", headers=OWNER, json={"mode": "apply"}).json()
    while applied["status"] == "running":
        applied = client.post(f"{API}/matching/backfill/{applied['id']}/continue", headers=OWNER).json()
    assert applied["status"] == "done" and applied["counts"]["auto_linked"] == 1 and applied["cursor_on"] is not None
    assert schedule_status(bill["id"]) == "paid"
    again = client.post(f"{API}/matching/backfill", headers=OWNER, json={"mode": "apply"}).json()
    while again["status"] == "running":
        again = client.post(f"{API}/matching/backfill/{again['id']}/continue", headers=OWNER).json()
    assert again["counts"]["auto_linked"] == 0
    assert len([l for l in links_memory_store.links.values() if l.status != LinkStatus.VOID]) == 1
    assert len(records()) == 1
    undone = client.post(f"{API}/matching/backfill/{applied['id']}/undo", headers=OWNER)
    assert undone.status_code == 200 and undone.json()["links_removed"] == 1
    assert schedule_status(bill["id"]) != "paid"
    runs = client.get(f"{API}/matching/backfill", headers=OWNER).json()
    assert runs["total"] == 3 and client.get(f"{API}/matching/backfill", headers=OTHER).json()["total"] == 0
    assert client.get(f"{API}/matching/backfill/{applied['id']}", headers=OTHER).status_code == 404


def test_a_link_removed_elsewhere_makes_the_record_stale_and_it_is_not_relinked() -> None:
    s = setup()
    bill = tamara_order()
    approve(s["bank"]["id"])
    send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at TAMARA with card **5678")
    made = records(outcome="auto_linked")[0]
    removed = client.delete(f"{API}/bills/{bill['id']}/matches/{made['link_id']}", headers=OWNER)
    assert removed.status_code == 204
    client.post(f"{API}/matching/run", headers=OWNER)
    stale = records(outcome="stale")
    assert len(stale) == 1 and "link_removed" in stale[0]["flags"]
    assert all(l.status == LinkStatus.VOID for l in links_memory_store.links.values())


def test_capture_health_reports_the_capture_without_tokens_or_bodies() -> None:
    s = setup()
    send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at TAMARA with card **5678")
    response = client.get(f"{API}/capture-health", headers=OWNER)
    assert response.status_code == 200, response.text
    health = response.json()
    assert health["enabled"] is True and health["self_test_passed"] is True
    sms = next(c for c in health["channels"] if c["channel"] == "sms")
    assert sms["last_captured_at"] is not None
    assert health["mappings"] == [{"kind": "card_number", "label": "5678", "account_id": s["bank"]["id"], "account_name": "Current"}]
    text = response.text
    assert s["token"] not in text and "TAMARA" not in text
    assert client.get(f"{API}/capture-health", headers=OTHER).json()["devices"] == []
