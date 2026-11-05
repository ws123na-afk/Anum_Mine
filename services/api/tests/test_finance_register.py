"""The transaction register and account overview (docs/finance.md#register-and-account-overview), in-memory stores.

Every message here is SYNTHETIC: written for these tests, labelled, and not the wording of any real bank.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import links as K
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import utc_now

OWNER = {"x-tenant-id": "tenant_register", "x-workspace-id": "ws_register", "x-user-id": "owner_register", "x-user-roles": "owner"}
STRANGER = {**OWNER, "x-user-id": "someone_else"}
VIEWER = {**OWNER, "x-user-id": "viewer_register", "x-user-roles": "viewer"}
API = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    # Tests here stand in their own document target; the vault's real one (registered at start-up) comes back after.
    saved = (dict(K._targets), dict(K._observers), dict(K._vets), set(K._single_payment))
    yield
    for registry, before in zip((K._targets, K._observers, K._vets, K._single_payment), saved, strict=True):
        registry.clear()
        registry.update(before)  # type: ignore[arg-type]
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def account(**body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def txn(account_id: str, amount: str, description: str, days_ago: int = 0, **body: Any) -> dict:
    posted = (utc_now().date() - timedelta(days=days_ago)).isoformat()
    response = client.post(f"{API}/transactions", headers=OWNER,
                           json={"account_id": account_id, "posted_on": posted, "amount": amount, "description": description, **body})
    assert response.status_code == 201, response.text
    return response.json()


def register(**params: Any) -> dict:
    response = client.get(f"{API}/register", headers=OWNER, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def sms_card() -> tuple[dict, str]:
    card = account(name="Card", kind="card", last4="1234")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    saved = client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"], "credit_limit": "20000"}]})
    assert saved.status_code == 200, saved.text
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    assert device.status_code == 201, device.text
    return card, device.json()["token"]


def send(token: str, message: str = FUEL_SMS) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text
    return response.json()


def test_rows_carry_origin_status_and_a_running_balance() -> None:
    bank = account(opening_balance="1000")
    txn(bank["id"], "-40", "Carrefour groceries", days_ago=3)
    txn(bank["id"], "500", "Salary October", days_ago=2)
    txn(bank["id"], "-12.5", "Mystery shop 991", days_ago=1)
    page = register(account_id=bank["id"])
    assert page["total"] == 3 and page["truncated"] is False
    newest, middle, oldest = page["items"]
    assert [r["origin"] for r in page["items"]] == ["manual", "manual", "manual"]
    assert oldest["running_balance"] == "960.00" and middle["running_balance"] == "1460.00" and newest["running_balance"] == "1447.50"
    assert newest["status"] == "uncategorized" and newest["evidence"] == "missing"
    assert oldest["status"] == "recorded" and oldest["category"] == "groceries"
    assert middle["evidence"] == "not_needed"
    totals = page["totals"]
    assert totals["count"] == 3 and totals["categorized"] == 2 and totals["receipts_missing"] == 2
    assert totals["outflow"] == [{"currency": "SAR", "amount": "52.50"}] and totals["inflow"] == [{"currency": "SAR", "amount": "500.00"}]
    assert page["balances"] == [{"currency": "SAR", "beginning": "1000.00", "ending": "1447.50"}]


def test_period_balances_and_server_side_filters_and_paging() -> None:
    bank = account(opening_balance="100")
    txn(bank["id"], "-10", "Old coffee", days_ago=40)
    for i in range(30):
        txn(bank["id"], "-1", f"Purchase{i}", days_ago=5)
    start = (utc_now().date() - timedelta(days=10)).isoformat()
    page = register(account_id=bank["id"], **{"from": start}, limit=25, offset=25)
    assert page["total"] == 30 and len(page["items"]) == 5
    assert page["balances"] == [{"currency": "SAR", "beginning": "90.00", "ending": "60.00"}]
    assert register(status="uncategorized", limit=5)["total"] == 30
    assert register(evidence="linked")["total"] == 0
    assert register(origin="sms")["total"] == 0


def test_a_possible_duplicate_is_flagged_and_the_detail_names_its_match() -> None:
    bank = account()
    first = txn(bank["id"], "-620", "Transfer to Fahad", days_ago=2)
    second = txn(bank["id"], "-620", "Mobile transfer Fahad", days_ago=1)
    txn(bank["id"], "-620", "Rent office", days_ago=1)  # same amount, no shared word: not a duplicate
    page = register()
    flagged = [r for r in page["items"] if r["status"] == "duplicate_candidate"]
    assert [r["id"] for r in flagged] == [second["id"]] and flagged[0]["duplicate_of"] == first["id"]
    detail = client.get(f"{API}/register/{first['id']}", headers=OWNER).json()
    assert detail["duplicate"]["id"] == second["id"]
    detail = client.get(f"{API}/register/{second['id']}", headers=OWNER).json()
    assert detail["duplicate"]["id"] == first["id"] and detail["account_name"] == "Current"


def test_an_sms_payment_shows_its_masked_text_only_while_it_is_kept() -> None:
    card, token = sms_card()
    assert send(token)["status"] == "posted"
    row = register(origin="sms")["items"][0]
    assert row["origin"] == "sms" and row["canonical_id"] and row["sources"] == ["sms"]
    detail = client.get(f"{API}/register/{row['id']}", headers=OWNER).json()
    [source] = detail["sources"]
    assert source["kind"] == "sms" and "PETROMIN" in source["text"] and "1234" in source["text"] and source["kept_until"]
    assert detail["suggested_category"] == "fuel"
    for message in sms_memory_store.messages.values():
        message.body_purge_at = utc_now() - timedelta(minutes=1)
    detail = client.get(f"{API}/register/{row['id']}", headers=OWNER).json()
    assert detail["sources"][0]["text"] is None and detail["sources"][0]["kept_until"] is None
    overview = client.get(f"{API}/accounts/overview", headers=OWNER).json()
    item = next(a for a in overview["accounts"] if a["account_id"] == card["id"])
    assert "sms" in item["sources"] and item["credit_limit"] == "20000.00" or item["credit_limit"] == "20000"


def test_a_linked_receipt_marks_the_row_linked() -> None:
    _card, token = sms_card()
    send(token)
    row = register()["items"][0]
    K.register_target("document", lambda context, kind, target: kind == "vault_document" and target == "vdoc_receipt")
    made = client.post(f"{API}/links", headers=OWNER, json={"canonical_id": row["canonical_id"], "dimension": "document", "role": "evidence",
                                                            "target_type": "vault_document", "target_id": "vdoc_receipt"})
    assert made.status_code == 201, made.text
    row = register()["items"][0]
    assert row["receipts"] == 1 and row["evidence"] == "linked" and row["status"] == "linked"
    assert register(evidence="linked")["total"] == 1
    assert client.get(f"{API}/register/{row['id']}", headers=OWNER).json()["receipt_document_ids"] == ["vdoc_receipt"]


def test_accounts_overview_sources_attention_and_payment_due() -> None:
    bank = account(name="Everyday")
    card = account(name="Visa", kind="card", last4="6814")
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "Visa payment", "amount": "500", "currency": "SAR", "cadence": "monthly",
                                                            "next_due_on": (utc_now().date() + timedelta(days=5)).isoformat(), "account_id": card["id"]})
    assert bill.status_code == 201, bill.text
    txn(bank["id"], "-20", "Coffee Ritual", days_ago=1)
    txn(bank["id"], "-20", "Coffee Ritual again", days_ago=0)
    overview = client.get(f"{API}/accounts/overview", headers=OWNER).json()
    by_id = {a["account_id"]: a for a in overview["accounts"]}
    assert by_id[bank["id"]]["sources"] == ["manual"] and by_id[bank["id"]]["needs_attention"] == ["possible_duplicates"]
    assert by_id[card["id"]]["payment_due"]["name"] == "Visa payment"
    assert by_id[bank["id"]]["payment_due"] is None
    activity = client.get(f"{API}/accounts/{bank['id']}/activity", headers=OWNER, params={"days": 7}).json()
    assert len(activity["days"]) == 7 and activity["days"][-1]["outflow"] == "20.00" and activity["currency"] == "SAR"
    assert client.get(f"{API}/accounts/{bank['id']}/activity", headers=OWNER, params={"days": 5}).status_code == 422


def test_rows_are_the_callers_own_and_viewers_are_refused() -> None:
    bank = account()
    made = txn(bank["id"], "-5", "Water")
    assert client.get(f"{API}/register", headers=STRANGER).json()["total"] == 0
    assert client.get(f"{API}/register/{made['id']}", headers=STRANGER).status_code == 404
    assert client.get(f"{API}/accounts/{bank['id']}/activity", headers=STRANGER).status_code == 404
    assert client.get(f"{API}/accounts/overview", headers=STRANGER).json()["accounts"] == []
    assert client.get(f"{API}/register", headers=VIEWER).status_code == 403
    assert client.get(f"{API}/accounts/overview", headers=VIEWER).status_code == 403


def test_statement_lines_reconciled_onto_an_sms_payment_count_as_imported_and_matched() -> None:
    card, token = sms_card()
    assert send(token)["status"] == "posted"
    today = utc_now().date().isoformat()
    made = client.post(f"{API}/imports", headers=OWNER, json={
        "account_id": card["id"], "filename": "card.csv",
        "content": f"Date,Description,Amount\n{today},PETROMIN FUEL,-150.00\n{today},BOOKSHOP NOT ON SMS,-20.00\n"})
    assert made.status_code == 201, made.text
    assert made.json()["reconciled"] == 1
    item = next(a for a in client.get(f"{API}/accounts/overview", headers=OWNER).json()["accounts"] if a["account_id"] == card["id"])
    assert item["imported"] == 2 and item["matched"] == 1
    assert item["last_reconciled_at"] is not None
