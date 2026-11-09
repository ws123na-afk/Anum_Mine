"""Bank and card reconciliation sessions (docs/finance.md#reconciliation-sessions).

Scenario tests on the in-memory backend: a statement that ties can be completed; a row the ledger does not hold keeps
the session unresolved and is listed; a duplicate SMS and its statement row are one record with no exception; reversal
pairs, wrong directions, unrecognised fees and missing months are found; a card's amount due is checked against its
liability; a later edit inside a completed period marks it changed; no balancing entry is ever posted; sessions are the
owner's alone; two requests resolving the same row at once cannot both win. Every row, merchant and amount is
SYNTHETIC: invented names ("EXAMPLE CAFE", "SAMPLE STORE") and round fake amounts.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import reconciliation as R
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.reconciliation_api import list_open_exceptions
from anum_api.finance.reconciliation_store import recon_memory_store
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_recon", "workspace_recon"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_recon", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "someone_else"}
API = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender
PURCHASE = "SYNTHETIC. Purchase of SAR 50.00 at EXAMPLE CAFE with card **1234 on 03/11/2026 14:22."

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, recon_memory_store, rules_memory_store):
        store.clear()
    svc._last_notified.clear()
    yield
    for store in (finance_store, sms_memory_store, links_memory_store, recon_memory_store, rules_memory_store):
        store.clear()


def account(**body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def csv_import(account_id: str, content: str, **extra: Any) -> dict:
    response = client.post(f"{API}/imports", headers=OWNER, json={"account_id": account_id, "content": content, "filename": "oct.csv", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def manual(account_id: str, amount: str, on: str, description: str, **extra: Any) -> dict:
    made = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": account_id, "posted_on": on, "amount": amount,
                                                                   "description": description, **extra})
    assert made.status_code == 201, made.text
    return made.json()


def start(headers: dict[str, str] = OWNER, **body: Any) -> dict:
    made = client.post(f"{API}/reconciliations", headers=headers, json=body)
    assert made.status_code == 201, made.text
    return made.json()


def kinds(detail: dict) -> list[str]:
    return [i["kind"] for i in detail["items"]]


def transactions(account_id: str) -> list[dict]:
    return client.get(f"{API}/transactions", headers=OWNER, params={"account_id": account_id, "limit": 200}).json()["items"]


STATEMENT = "Date,Description,Amount,Balance\n2026-10-03,EXAMPLE CAFE,-50.00,950.00\n2026-10-04,SAMPLE BOOKS,-20.00,930.00\n"


def test_a_statement_that_ties_can_be_completed_and_becomes_the_reconciled_source() -> None:
    bank = account(opening_balance="1000")
    imported = csv_import(bank["id"], STATEMENT, opening_balance="1000.00", closing_balance="930.00")
    detail = start(account_id=bank["id"], import_id=imported["id"])
    assert (detail["period_start"], detail["period_end"]) == ("2026-10-03", "2026-10-04")
    figures = detail["figures"]
    assert (figures["book_opening"], figures["book_closing"], figures["statement_closing"], figures["unexplained"]) == ("1000.00", "930.00", "930.00", "0.00")
    assert detail["state"] == "balanced" and detail["can_complete"] and detail["blocking"] == 0
    done = client.post(f"{API}/reconciliations/{detail['id']}/complete", headers=OWNER, json={"version": detail["version"]})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "completed" and body["completed_by"] == "owner_recon" and body["snapshot"]["figures"]["book_closing"] == "930.00"
    assert len(body["snapshot"]["confirmed"]) == 2 and body["history"][0]["action"] == "completed"
    card = next(a for a in client.get(f"{API}/accounts/overview", headers=OWNER).json()["accounts"] if a["account_id"] == bank["id"])
    assert card["reconciliation"]["status"] == "completed" and card["last_reconciled_at"] == body["completed_at"]
    rows = client.get(f"{API}/register", headers=OWNER, params={"account_id": bank["id"]}).json()["items"]
    assert all(r["reconciled"] and r["reconciliation_id"] == detail["id"] for r in rows)
    # Completing twice, or with a stale version, is refused.
    assert client.post(f"{API}/reconciliations/{detail['id']}/complete", headers=OWNER, json={"version": body["version"]}).status_code == 409


def test_a_row_missing_from_the_ledger_keeps_it_unresolved_and_a_correction_needs_the_approved_hash() -> None:
    bank = account(opening_balance="1000")
    imported = csv_import(bank["id"], STATEMENT, opening_balance="1000.00", closing_balance="930.00", decisions=[{"line": 3, "action": "skip"}])
    detail = start(account_id=bank["id"], import_id=imported["id"])
    assert detail["state"] == "unresolved" and set(detail["blockers"]) == {"difference", "statement_rows_not_in_ledger"}
    assert detail["figures"]["unexplained"] == "-20.00"
    missing = next(i for i in detail["items"] if i["kind"] == "not_in_ledger")
    assert (missing["severity"], missing["reason"], missing["amount"], missing["description"]) == ("blocking", "unticked", "-20.00", "SAMPLE BOOKS")
    before = len(transactions(bank["id"]))
    # Never a balancing entry: completing is refused and nothing was posted.
    refused = client.post(f"{API}/reconciliations/{detail['id']}/complete", headers=OWNER, json={"version": detail["version"]})
    assert refused.status_code == 409 and len(transactions(bank["id"])) == before
    # A correcting entry exists only for a statement row the ledger does not hold, for that row's own amount.
    assert client.post(f"{API}/reconciliations/{detail['id']}/corrections/preview", headers=OWNER,
                       json={"statement_ref": "fin_imp_none#9"}).status_code == 422
    preview = client.post(f"{API}/reconciliations/{detail['id']}/corrections/preview", headers=OWNER, json={"statement_ref": missing["statement_ref"]})
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["amount"] == "-20.00" and sum(Decimal(leg["amount"]) for leg in shown["legs"]) == 0 and len(shown["legs"]) == 2
    assert client.post(f"{API}/reconciliations/{detail['id']}/corrections", headers=OWNER,
                       json={"statement_ref": missing["statement_ref"], "payload_hash": "0" * 64}).status_code == 409
    assert len(transactions(bank["id"])) == before
    posted = client.post(f"{API}/reconciliations/{detail['id']}/corrections", headers=OWNER,
                         json={"statement_ref": missing["statement_ref"], "payload_hash": shown["payload_hash"]})
    assert posted.status_code == 201, posted.text
    # A retried approval posts nothing twice.
    again = client.post(f"{API}/reconciliations/{detail['id']}/corrections", headers=OWNER,
                        json={"statement_ref": missing["statement_ref"], "payload_hash": shown["payload_hash"]})
    assert again.status_code in (409, 422) and len(transactions(bank["id"])) == before + 1
    after = client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).json()
    assert after["state"] == "balanced" and after["figures"]["unexplained"] == "0.00"
    entries = client.get(f"{API}/ledger/entries", headers=OWNER).json()["items"]
    assert any(e["source_ref"] == posted.json()["transaction_id"] for e in entries)


def sms_setup() -> dict[str, Any]:
    card = account(name="Card", kind="card", last4="1234")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    put = client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"], "credit_limit": "5000"}]})
    assert put.status_code == 200, put.text
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    return {"card": card, "token": device.json()["token"]}


def send_sms(token: str, message: str) -> None:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text


def test_a_duplicate_sms_and_its_statement_row_are_one_record_with_no_exception() -> None:
    s = sms_setup()
    send_sms(s["token"], PURCHASE)
    send_sms(s["token"], PURCHASE)  # the bank sent the same message twice
    done = csv_import(s["card"]["id"], "Date,Description,Amount\n2026-11-04,POS EXAMPLE CAFE RIYADH,-50.00\n")
    assert done["reconciled"] == 1
    detail = start(account_id=s["card"]["id"], period_start="2026-11-01", period_end="2026-11-08", statement_closing="-50.00")
    assert detail["state"] == "balanced"
    assert not {"pending", "unmatched_payment", "not_in_ledger", "possible_duplicate"} & set(kinds(detail))


def test_an_unconfirmed_sms_is_pending_and_marking_it_not_on_the_statement_explains_the_difference() -> None:
    s = sms_setup()
    send_sms(s["token"], PURCHASE)
    detail = start(account_id=s["card"]["id"], period_start="2026-11-01", period_end="2026-11-08", statement_closing="0")
    pending = next(i for i in detail["items"] if i["kind"] == "pending")
    assert pending["reason"] == "sms" and detail["figures"]["unexplained"] == "50.00"
    marked = client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=OWNER,
                         json={"kind": "pending", "transaction_id": pending["transaction_id"]})
    assert marked.status_code == 201, marked.text
    after = client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).json()
    assert after["figures"]["pending_total"] == "-50.00" and after["figures"]["unexplained"] == "0.00" and after["state"] == "balanced"
    assert "pending" not in kinds(after)
    # Undoing it brings the row back.
    assert client.delete(f"{API}/reconciliations/{detail['id']}/resolutions/{marked.json()['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).json()["state"] == "unresolved"


def test_reversal_pairs_wrong_directions_and_unrecognised_fees_are_found() -> None:
    bank = account(opening_balance="1000")
    debit = manual(bank["id"], "-80.00", "2026-10-05", "CARD PURCHASE SAMPLE STORE")
    credit = manual(bank["id"], "80.00", "2026-10-06", "REVERSAL SAMPLE STORE")
    rent = manual(bank["id"], "1500.00", "2026-10-07", "MONTHLY ROOM RENT")
    fee = manual(bank["id"], "-15.00", "2026-10-08", "SERVICE CHARGES", category="shopping")
    detail = start(account_id=bank["id"], period_start="2026-10-01", period_end="2026-10-31", statement_closing="2485.00")
    by_kind = {(i["kind"], i.get("transaction_id")): i for i in detail["items"]}
    pair = by_kind[("reversal_pair", debit["id"])]
    assert pair["other_transaction_id"] == credit["id"] and pair["severity"] == "info"
    assert by_kind[("wrong_direction", rent["id"])]["reason"] == "rent_shown_as_incoming"
    assert ("unrecognized_fee", fee["id"]) in by_kind
    accepted = client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=OWNER,
                           json={"kind": "reversal", "transaction_id": credit["id"], "other_transaction_id": debit["id"]})
    assert accepted.status_code == 201 and accepted.json()["transaction_id"] == debit["id"]
    after = client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).json()
    assert "reversal_pair" not in kinds(after) and after["state"] == "balanced"
    # A pair that is not equal and opposite is refused.
    assert client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=OWNER,
                       json={"kind": "reversal", "transaction_id": rent["id"], "other_transaction_id": fee["id"]}).status_code == 422


def test_a_month_with_activity_and_no_statement_is_missing() -> None:
    bank = account(opening_balance="1000")
    csv_import(bank["id"], "Date,Description,Amount\n2026-08-01,EXAMPLE CAFE,-10.00\n2026-08-31,SAMPLE BOOKS,-10.00\n")
    manual(bank["id"], "-25.00", "2026-09-12", "SAMPLE MARKET")
    csv_import(bank["id"], "Date,Description,Amount\n2026-10-01,EXAMPLE CAFE,-10.00\n2026-10-31,SAMPLE BOOKS,-10.00\n")
    health = client.get(f"{API}/reconciliations/health", headers=OWNER).json()
    mine = next(a for a in health["accounts"] if a["account_id"] == bank["id"])
    assert mine["missing_months"] == ["2026-09"] and mine["gaps"] == 1 and mine["last_statement_end"] == "2026-10-31"
    assert (mine["next_step"], mine["next_month"], mine["bank_feed"]) == ("import_statement", "2026-09", "not_connected")
    detail = start(account_id=bank["id"], period_start="2026-09-01", period_end="2026-09-30", statement_closing="955.00")
    assert any(i["kind"] == "missing_period" and i["month"] == "2026-09" for i in detail["items"])
    queue = client.get(f"{API}/reconciliations/exceptions", headers=OWNER).json()
    assert any(q["exception"]["kind"] == "missing_period" for q in queue["items"])
    # Pure: a whole month inside a gap between two statement periods is missing even without activity.
    gap = R.missing_months([], [R.Coverage("a", date(2026, 6, 1), date(2026, 6, 30)), R.Coverage("b", date(2026, 9, 1), date(2026, 9, 30))],
                           before=date(2026, 11, 1))
    assert gap == ["2026-07", "2026-08"]


def test_a_card_statement_checks_the_amount_due_against_the_card_liability() -> None:
    card = account(name="Card", kind="card", last4="5678")
    manual(card["id"], "-300.00", "2026-10-10", "SAMPLE STORE")
    manual(card["id"], "-200.00", "2026-10-20", "EXAMPLE CAFE")
    manual(card["id"], "100.00", "2026-11-02", "PAYMENT RECEIVED THANK YOU")
    detail = start(account_id=card["id"], period_start="2026-10-01", period_end="2026-10-31", statement_closing="-500.00",
                   amount_due="500.00", minimum_due="50.00", due_on="2026-11-05")
    assert detail["state"] == "balanced"
    assert detail["card"] == {"amount_due": "500.00", "minimum_due": "50.00", "due_on": "2026-11-05", "owed_in_ledger": "500.00",
                              "due_difference": "0.00", "paid_since": "100.00", "minimum_covered": True}
    changed = client.patch(f"{API}/reconciliations/{detail['id']}", headers=OWNER, json={"version": detail["version"], "amount_due": "520.00"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["card"]["due_difference"] == "20.00" and "amount_due_differs" in kinds(changed.json())
    bank = account(name="Savings")
    assert client.post(f"{API}/reconciliations", headers=OWNER, json={"account_id": bank["id"], "period_start": "2026-10-01",
                                                                      "period_end": "2026-10-31", "statement_closing": "0",
                                                                      "amount_due": "10"}).status_code == 422


def test_an_edit_inside_a_completed_period_marks_it_changed_and_reopening_needs_a_reason() -> None:
    bank = account(opening_balance="1000")
    row = manual(bank["id"], "-70.00", "2026-10-03", "EXAMPLE CAFE")
    detail = start(account_id=bank["id"], period_start="2026-10-01", period_end="2026-10-31", statement_closing="930.00")
    done = client.post(f"{API}/reconciliations/{detail['id']}/complete", headers=OWNER, json={"version": detail["version"]}).json()
    assert done["status"] == "completed" and not done["changed"]
    edited = client.patch(f"{API}/transactions/{row['id']}", headers=OWNER, json={"amount": "-75.00"})
    assert edited.status_code == 200, edited.text
    after = client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).json()
    assert after["changed"] and after["status"] == "completed"
    card = next(a for a in client.get(f"{API}/accounts/overview", headers=OWNER).json()["accounts"] if a["account_id"] == bank["id"])
    assert card["reconciliation"]["status"] == "changed" and "reconciliation_changed" in card["needs_attention"]
    assert client.get(f"{API}/reconciliations", headers=OWNER, params={"state": "changed"}).json()["total"] == 1
    # Resolving inside a completed session needs a reopen first; a reopen needs a reason.
    assert client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=OWNER,
                       json={"kind": "attach", "transaction_id": row["id"]}).status_code == 409
    assert client.post(f"{API}/reconciliations/{detail['id']}/reopen", headers=OWNER, json={"version": after["version"], "reason": ""}).status_code == 422
    reopened = client.post(f"{API}/reconciliations/{detail['id']}/reopen", headers=OWNER,
                           json={"version": after["version"], "reason": "Corrected the cafe amount"})
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["status"] == "open" and body["state"] == "unresolved" and [h["action"] for h in body["history"]] == ["completed", "reopened"]
    assert body["history"][1]["reason"] == "Corrected the cafe amount"
    # A session with history cannot be deleted.
    assert client.delete(f"{API}/reconciliations/{detail['id']}", headers=OWNER).status_code == 409


def test_sessions_are_the_owners_alone() -> None:
    bank = account(opening_balance="1000")
    row = manual(bank["id"], "-70.00", "2026-10-03", "EXAMPLE CAFE")
    detail = start(account_id=bank["id"], period_start="2026-10-01", period_end="2026-10-31", statement_closing="930.00")
    assert client.get(f"{API}/reconciliations/{detail['id']}", headers=OTHER).status_code == 404
    assert client.get(f"{API}/reconciliations", headers=OTHER).json()["total"] == 0
    assert client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=OTHER,
                       json={"kind": "attach", "transaction_id": row["id"]}).status_code == 404
    assert client.post(f"{API}/reconciliations", headers=OTHER, json={"account_id": bank["id"], "period_start": "2026-10-01",
                                                                      "period_end": "2026-10-31", "statement_closing": "0"}).status_code == 404
    assert client.get(f"{API}/reconciliations/exceptions", headers=OTHER).json()["total"] == 0
    other_tenant = {**OWNER, "x-tenant-id": "tenant_recon_b"}
    assert client.get(f"{API}/reconciliations/{detail['id']}", headers=other_tenant).status_code in (403, 404)
    # The same period twice is refused; deleting the account removes its sessions.
    assert client.post(f"{API}/reconciliations", headers=OWNER, json={"account_id": bank["id"], "period_start": "2026-10-01",
                                                                      "period_end": "2026-10-31", "statement_closing": "0"}).status_code == 409
    assert client.delete(f"{API}/accounts/{bank['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).status_code == 404


def test_two_requests_matching_the_same_row_at_once_cannot_both_win() -> None:
    bank = account(opening_balance="1000")
    row = manual(bank["id"], "-70.00", "2026-10-03", "EXAMPLE CAFE")
    detail = start(account_id=bank["id"], period_start="2026-10-01", period_end="2026-10-31", statement_closing="930.00")
    barrier = threading.Barrier(2)
    results: list[int] = []

    def attach() -> None:
        api = TestClient(main.app)
        barrier.wait()
        results.append(api.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=OWNER,
                                json={"kind": "attach", "transaction_id": row["id"]}).status_code)

    threads = [threading.Thread(target=attach) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [201, 409]
    ctx = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_recon")
    assert len([i for i in recon_memory_store.items(ctx, detail["id"]) if i.status == "active"]) == 1


def test_the_queue_lists_open_exceptions_only() -> None:
    bank = account(opening_balance="1000")
    imported = csv_import(bank["id"], STATEMENT, opening_balance="1000.00", closing_balance="930.00", decisions=[{"line": 3, "action": "skip"}])
    detail = start(account_id=bank["id"], import_id=imported["id"])
    ctx = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_recon")
    page = list_open_exceptions(ctx)
    assert page.total >= 1 and all(q.reconciliation_id == detail["id"] and q.exception.severity != "info" for q in page.items)
    assert any(q.exception.kind == "not_in_ledger" for q in page.items)
    # Paging keeps the total.
    first = client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER, params={"limit": 1}).json()
    assert len(first["items"]) == 1 and first["total"] >= 1


def test_an_open_exception_is_in_the_suggestion_queue_with_a_review_link_and_a_dismissed_one_stays_dismissed() -> None:
    bank = account(opening_balance="1000")
    imported = csv_import(bank["id"], STATEMENT, opening_balance="1000.00", closing_balance="930.00", decisions=[{"line": 3, "action": "skip"}])
    detail = start(account_id=bank["id"], import_id=imported["id"])

    def recon_items() -> list[dict]:
        found = client.get(f"{API}/suggestions", headers=OWNER, params={"limit": 100})
        assert found.status_code == 200, found.text
        assert "reconciliation" not in found.json()["unavailable"]
        return [s for s in found.json()["items"] if s["producer"] == "reconciliation"]

    items = recon_items()
    missing = next(s for s in items if s["params"]["exception"] == "not_in_ledger")
    # What was found, why, the evidence and the money, and Review opens the session.
    assert missing["kind"] == "reconciliation" and missing["params"]["severity"] == "blocking" and missing["params"]["account"] == "Current"
    assert missing["detected"] and missing["why"] and any(e["label"] == "SAMPLE BOOKS" for e in missing["evidence"])
    assert missing["effect"] == [{"amount": "20.00", "currency": "SAR", "direction": "out", "note": None}]
    assert missing["href"] == f"#finance-reconciliation?session={detail['id']}"
    # The queue never changes the ledger: Accept is refused, the session is where it is resolved.
    before = len(transactions(bank["id"]))
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": missing["key"], "payload_hash": missing["payload_hash"]})
    assert accepted.status_code == 409 and len(transactions(bank["id"])) == before
    # Dismissed, it stays dismissed while the exception is still open on the session.
    assert client.post(f"{API}/suggestions/reject", headers=OWNER, json={"key": missing["key"]}).status_code == 200
    assert missing["key"] not in {s["key"] for s in recon_items()}
    assert any(i["kind"] == "not_in_ledger" for i in client.get(f"{API}/reconciliations/{detail['id']}", headers=OWNER).json()["items"])
    assert missing["key"] not in {s["key"] for s in recon_items()}


def test_a_pdf_import_keeps_its_printed_period_for_coverage_and_a_new_session() -> None:
    bank = account(opening_balance="1000")
    body = {"account_id": bank["id"], "filename": "oct.pdf", "file_sha256": "0" * 64, "source": "text", "period_start": "2026-10-01",
            "period_end": "2026-10-31", "rows": [{"posted_on": "2026-10-05", "description": "EXAMPLE CAFE", "amount": "-50.00"}]}
    made = client.post(f"{API}/imports/pdf", headers=OWNER, json=body)
    assert made.status_code == 201, made.text
    assert (made.json()["reconciliation"]["period_start"], made.json()["reconciliation"]["period_end"]) == ("2026-10-01", "2026-10-31")
    mine = next(a for a in client.get(f"{API}/reconciliations/health", headers=OWNER).json()["accounts"] if a["account_id"] == bank["id"])
    assert mine["last_statement_end"] == "2026-10-31" and mine["next_step"] == "reconcile"
    # The import printed no closing balance: the owner types it.
    assert client.post(f"{API}/reconciliations", headers=OWNER, json={"account_id": bank["id"], "import_id": made.json()["id"]}).status_code == 422
    detail = start(account_id=bank["id"], import_id=made.json()["id"], statement_closing="950.00")
    assert (detail["period_start"], detail["period_end"], detail["state"]) == ("2026-10-01", "2026-10-31", "balanced")
