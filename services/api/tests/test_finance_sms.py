"""iPhone SMS banking and the double-entry journal (docs/finance-sms.md), in-memory stores.

Every message here is SYNTHETIC: written for these tests, labelled, and not the wording of any
real bank. ``test_postgres_finance_sms.py`` repeats the isolation and journal checks under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import ledger as L
from anum_api.finance import sms_parser as P
from anum_api.finance import sms_service as svc
from anum_api.finance.models import Account, AccountKind
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import utc_now

TENANT, WORKSPACE = "tenant_sms", "workspace_sms"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_sms", "x-user-roles": "owner"}
OTHER_PERSON = {**OWNER, "x-user-id": "member_sms", "x-user-roles": "member"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_sms_other"}
VIEWER = {**OWNER, "x-user-id": "viewer_sms", "x-user-roles": "viewer"}
API = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender, allowed by the person in Settings

# SYNTHETIC messages (not real bank wording).
SALARY_EN = "SYNTHETIC. Salary of SAR 12,000.00 credited to your account ***5678 on 01/11/2026. Available balance SAR 14,000.00"
PURCHASE_EN = "SYNTHETIC. Purchase of SAR 45.50 at CARREFOUR RIYADH with card **1234 on 03/11/2026 14:22. Ref 88AB12"
PURCHASE_AR = "SYNTHETIC. شراء بمبلغ 120.00 ر.س لدى مطعم البيك بطاقة **1234 في 2026-11-03 20:15"
SALARY_AR = "SYNTHETIC. تم إيداع راتب 9,500.00 ريال في حسابك ***5678 بتاريخ 02/11/2026"
REPAYMENT_BANK = "SYNTHETIC. Credit card payment of SAR 500.00 from account ***5678 to card ***1234 on 05/11/2026"
REPAYMENT_CARD = "SYNTHETIC. Card payment received SAR 500.00 on card ending 1234 on 05/11/2026"
OWN_TRANSFER = "SYNTHETIC. Transfer between your accounts SAR 300.00 from account ***5678 to account ***9999 on 06/11/2026"
REFUND = "SYNTHETIC. Refund of SAR 45.50 from CARREFOUR RIYADH to card **1234 on 07/11/2026"
FX = "SYNTHETIC. Purchase USD 20.00 (SAR 75.40) at NETFLIX.COM card **1234 on 08/11/2026"
OTP = "SYNTHETIC. Your OTP for purchase of SAR 50 at SHOP is 482913. Do not share it."
NO_AMOUNT = "SYNTHETIC. Your account ***5678 was debited."

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    svc._last_notified.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()


def account(headers: dict = OWNER, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def settings(headers: dict = OWNER) -> dict:
    return client.get(f"{API}/sms/settings", headers=headers).json()


def put_settings(headers: dict = OWNER, **body: Any) -> dict:
    response = client.put(f"{API}/sms/settings", headers=headers, json={"version": settings(headers)["version"], **body})
    assert response.status_code == 200, response.text
    return response.json()


def setup(headers: dict = OWNER, *, verified: bool = True) -> dict[str, Any]:
    """Accounts, the safety check, the switch and one device: what the owner does in the web."""
    bank = account(headers, name="Current", last4="5678")
    card = account(headers, name="Card", kind="card", last4="1234")
    savings = account(headers, name="Savings", last4="9999")
    assert client.post(f"{API}/sms/self-test", headers=headers).json()["passed"]
    put_settings(headers, enabled=True, extra_senders=[{"sender": SENDER, "bank": "test_bank"}],
                 verified_senders=[SENDER] if verified else [], account_map=[{"last4": "1234", "account_id": card["id"], "credit_limit": "5000"}])
    device = client.post(f"{API}/sms/devices", headers=headers, json={"name": "My iPhone"})
    assert device.status_code == 201, device.text
    return {"bank": bank, "card": card, "savings": savings, "token": device.json()["token"], "device": device.json()["device"]}


def send(token: str, message: str, sender: str = SENDER, **extra: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": sender, **extra})


def dashboard(headers: dict = OWNER, **params: str) -> dict:
    response = client.get(f"{API}/ledger/dashboard", headers=headers, params={"month": "2026-11", **params})
    assert response.status_code == 200, response.text
    return response.json()


def money(rows: list[dict], currency: str = "SAR") -> Decimal:
    return next((Decimal(r["amount"]) for r in rows if r["currency"] == currency), Decimal(0))


def balanced_journal() -> None:
    for entry in sms_memory_store.entries.values():
        assert L.balanced(entry.postings), entry


# -- acceptance --------------------------------------------------------------------------------


def test_acceptance_a_synthetic_sms_is_authenticated_parsed_deduplicated_posted_and_on_the_dashboard() -> None:
    s = setup(verified=False)
    first = send(s["token"], SALARY_EN)
    assert first.status_code == 200 and first.json()["status"] == "review" and first.json()["reason"] == "format_unverified"
    # The owner checks the reading and confirms the format: it is recorded, and the sender is trusted from now on.
    approved = client.post(f"{API}/sms/messages/{first.json()['message_id']}/post", headers=OWNER, json={"confirm_format": True})
    assert approved.status_code == 200, approved.text
    second = send(s["token"], PURCHASE_EN)
    assert second.json()["status"] == "posted"
    assert send(s["token"], PURCHASE_EN).json()["replayed"] is True  # the Shortcut retried
    again = send(s["token"], PURCHASE_EN.replace("SYNTHETIC.", "SYNTHETIC copy."))  # the bank sent it twice
    assert again.json()["status"] == "duplicate"
    entries = [e for e in sms_memory_store.entries.values()]
    assert len(entries) == 2
    balanced_journal()
    purchase = next(e for e in entries if e.kind == "card_purchase")
    accounts = {p.ledger_account: p.amount for p in purchase.postings}
    assert accounts == {"expense:groceries": Decimal("45.50"), f"liability:{s['card']['id']}": Decimal("-45.50")}
    board = dashboard()
    assert money(board["income"]) == Decimal("12000.00") and money(board["expenses"]) == Decimal("45.50")
    assert money(board["surplus"]) == Decimal("11954.50")
    card = next(c for c in board["cards"] if c["id"] == s["card"]["id"])
    assert Decimal(card["owed"]) == Decimal("45.50") and card["utilization_percent"] == 1
    assert money(board["net_worth"]) == Decimal("11954.50")
    assert board["sync"]["enabled"] is True and board["sync"]["devices_active"] == 1


# -- the 20 cases ------------------------------------------------------------------------------


def test_salary_sms_is_income_and_cash_up() -> None:
    s = setup()
    assert send(s["token"], SALARY_EN).json()["status"] == "posted"
    entry = next(iter(sms_memory_store.entries.values()))
    assert {p.ledger_account: p.amount for p in entry.postings} == {f"asset:{s['bank']['id']}": Decimal("12000.00"), "income:salary": Decimal("-12000.00")}
    txns = client.get(f"{API}/transactions", headers=OWNER).json()["items"]
    assert txns[0]["amount"] == "12000.00" and txns[0]["category"] == "income"
    # The stated balance is an observation, not a posting.
    board = dashboard()
    line = next(a for a in board["accounts"] if a["id"] == s["bank"]["id"])
    assert Decimal(line["observed_balance"]) == Decimal("14000.00") and Decimal(line["balance"]) == Decimal("12000.00")


def test_purchase_sms_is_expense_up_and_card_debt_up() -> None:
    s = setup()
    assert send(s["token"], PURCHASE_EN).json()["status"] == "posted"
    assert Decimal(next(a for a in client.get(f"{API}/accounts", headers=OWNER).json() if a["id"] == s["card"]["id"])["balance"]) == Decimal("-45.50")


def test_arabic_sms_is_read() -> None:
    s = setup()
    assert send(s["token"], PURCHASE_AR).json()["status"] == "posted"
    assert send(s["token"], SALARY_AR).json()["status"] == "posted"
    kinds = sorted(e.kind for e in sms_memory_store.entries.values())
    assert kinds == ["card_purchase", "salary"]
    purchase = next(e for e in sms_memory_store.entries.values() if e.kind == "card_purchase")
    assert purchase.merchant == "مطعم البيك" and any(p.amount == Decimal("120.00") for p in purchase.postings)


def test_english_sms_fields() -> None:
    parsed = P.parse(SENDER, PURCHASE_EN, extra_senders={"testbank": "x"}, verified_senders={"testbank"})
    assert parsed.status == "ok" and parsed.kind == P.Kind.CARD_PURCHASE
    assert (parsed.amount, parsed.currency, parsed.merchant, parsed.last4, parsed.reference) == (Decimal("45.50"), "SAR", "CARREFOUR RIYADH", "1234", "88AB12")
    assert str(parsed.occurred_on) == "2026-11-03" and str(parsed.occurred_at_time) == "14:22:00"


def test_duplicate_delivery_is_recorded_once() -> None:
    s = setup()
    first = send(s["token"], PURCHASE_EN).json()
    retry = send(s["token"], PURCHASE_EN).json()
    assert retry["replayed"] and retry["message_id"] == first["message_id"]
    assert len(sms_memory_store.entries) == 1 and len(sms_memory_store.messages) == 1


def test_missing_amount_waits_for_review() -> None:
    s = setup()
    result = send(s["token"], NO_AMOUNT).json()
    assert result["status"] == "review" and result["reason"] == "missing_amount" and not sms_memory_store.entries


def test_unknown_sender_waits_for_review() -> None:
    s = setup()
    result = send(s["token"], PURCHASE_EN, sender="SomeoneElse").json()
    assert result["status"] == "review" and result["reason"] == "unknown_sender" and not sms_memory_store.entries


def test_known_bank_sender_still_needs_the_owner_to_confirm_its_format() -> None:
    s = setup()
    assert send(s["token"], PURCHASE_EN, sender="AlRajhiBank").json()["reason"] == "format_unverified"
    assert all(not bank.verified for bank in P.BANKS)


def test_otp_and_promotions_are_refused_and_their_text_is_not_kept() -> None:
    s = setup()
    for text in (OTP, "SYNTHETIC. رمز التحقق 4821 لعملية شراء", "SYNTHETIC. Your password was changed", "SYNTHETIC. Big offer! Win a discount today"):
        result = send(s["token"], text).json()
        assert result["status"] == "rejected" and result["message_id"] is None
    assert all(m.body_masked == "" and m.parsed == {} for m in sms_memory_store.messages.values())
    assert not sms_memory_store.entries


def test_unauthorized_device_is_refused() -> None:
    s = setup()
    assert client.post(f"{API}/sms/ingest", json={"message": PURCHASE_EN, "sender": SENDER}).status_code == 401
    assert send("anum_sms.bad.token", PURCHASE_EN).status_code == 401
    forged = s["token"][:-6] + "abcdef"  # right ids, wrong secret
    response = send(forged, PURCHASE_EN)
    assert response.status_code == 401 and response.json()["code"] == "invalid_token"
    # A token minted for this owner's ids but never stored is refused the same way.
    from anum_api.schemas import TenantContext

    token, _, _ = svc.mint(TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_sms"), "fin_smsdev_unknown")
    assert send(token, PURCHASE_EN).json()["code"] == "invalid_token"
    assert not sms_memory_store.entries


def test_revoked_and_rotated_tokens_stop_working_and_pause_holds_messages() -> None:
    s = setup()
    device_id = s["device"]["id"]
    assert client.post(f"{API}/sms/devices/{device_id}/pause", headers=OWNER).json()["status"] == "paused"
    assert send(s["token"], PURCHASE_EN).status_code == 409
    client.post(f"{API}/sms/devices/{device_id}/resume", headers=OWNER)
    rotated = client.post(f"{API}/sms/devices/{device_id}/rotate", headers=OWNER).json()
    assert send(s["token"], PURCHASE_EN).status_code == 401  # the old token
    assert send(rotated["token"], PURCHASE_EN).json()["status"] == "posted"
    assert client.post(f"{API}/sms/devices/{device_id}/revoke", headers=OWNER).json()["status"] == "revoked"
    response = send(rotated["token"], SALARY_EN)
    assert response.status_code == 401
    # The token is never returned again and its hash is never shown.
    listed = client.get(f"{API}/sms/devices", headers=OWNER).text
    assert rotated["token"] not in listed and "token_hash" not in listed


def test_cross_workspace_and_cross_person_attempts_find_nothing() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)
    message_id = next(iter(sms_memory_store.messages))
    for headers in (OTHER_PERSON, OTHER_WORKSPACE):
        assert client.get(f"{API}/sms/messages/{message_id}", headers=headers).status_code == 404
        assert client.get(f"{API}/sms/messages", headers=headers).json()["total"] == 0
        assert client.post(f"{API}/sms/devices/{s['device']['id']}/revoke", headers=headers).status_code == 404
        assert dashboard(headers)["entry_count"] == 0
    # The body cannot name another owner or workspace.
    assert send(s["token"], PURCHASE_EN, user_id="member_sms").status_code == 422
    assert send(s["token"], PURCHASE_EN, workspace_id="workspace_sms_other").status_code == 422
    assert client.get(f"{API}/sms/messages", headers=VIEWER).status_code == 403


def test_card_purchase_then_repayment_never_counts_the_repayment_as_an_expense() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)
    assert send(s["token"], REPAYMENT_BANK).json()["status"] == "posted"
    # The card's own message about the same payment is a duplicate, not a second payment.
    assert send(s["token"], REPAYMENT_CARD).json()["status"] == "duplicate"
    board = dashboard()
    assert money(board["expenses"]) == Decimal("45.50")
    card = next(c for c in board["cards"] if c["id"] == s["card"]["id"])
    assert Decimal(card["balance"]) == Decimal("454.50") and Decimal(card["owed"]) == Decimal("0")
    bank = next(a for a in board["accounts"] if a["id"] == s["bank"]["id"])
    assert Decimal(bank["balance"]) == Decimal("-500.00")
    balanced_journal()


def test_card_side_first_then_bank_side_clears_the_suspense() -> None:
    s = setup()
    send(s["token"], REPAYMENT_CARD)
    assert money(dashboard()["in_transit"]) == Decimal("-500.00")
    assert send(s["token"], REPAYMENT_BANK).json()["status"] == "duplicate"
    board = dashboard()
    assert board["in_transit"] == [] and money(board["expenses"]) == Decimal("0")
    assert Decimal(next(a for a in board["accounts"] if a["id"] == s["bank"]["id"])["balance"]) == Decimal("-500.00")
    balanced_journal()


def test_own_account_transfer_is_neither_income_nor_expense() -> None:
    s = setup()
    assert send(s["token"], OWN_TRANSFER).json()["status"] == "posted"
    board = dashboard()
    assert board["income"] == [] and board["expenses"] == []
    balances = {a["id"]: Decimal(a["balance"]) for a in board["accounts"]}
    assert balances[s["bank"]["id"]] == Decimal("-300.00") and balances[s["savings"]["id"]] == Decimal("300.00")


def test_refund_reverses_the_original_category_and_undo_is_a_reversal_not_a_delete() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)
    refund = send(s["token"], REFUND).json()
    assert refund["status"] == "posted"
    entry = next(e for e in sms_memory_store.entries.values() if e.kind == "refund")
    assert entry.category == "groceries" and entry.reverses_id is not None
    assert money(dashboard()["expenses"]) == Decimal("0.00")
    undone = client.post(f"{API}/sms/messages/{refund['message_id']}/reverse", headers=OWNER)
    assert undone.status_code == 200 and undone.json()["status"] == "dismissed"
    assert len(sms_memory_store.entries) == 3  # purchase, refund, and the refund's reversal
    assert money(dashboard()["expenses"]) == Decimal("45.50")
    balanced_journal()


def test_multi_currency_keeps_the_original_amount_and_rate() -> None:
    s = setup()
    assert send(s["token"], FX).json()["status"] == "posted"
    entry = next(iter(sms_memory_store.entries.values()))
    assert (entry.original_amount, entry.original_currency) == (Decimal("20.00"), "USD")
    assert entry.fx_rate == Decimal("3.7700000000")
    assert all(p.currency == "SAR" for p in entry.postings)
    # Without the billed amount nothing is converted: the message waits.
    result = send(s["token"], "SYNTHETIC. Purchase USD 9.99 at STEAM card **1234 on 08/11/2026").json()
    assert result["status"] == "review" and result["reason"] == "needs_converted_amount"
    # Different currencies are never added together.
    account(OWNER, name="Dollar", currency="USD", last4="4444")
    send(s["token"], "SYNTHETIC. Salary of USD 100.00 credited to your account ***4444 on 09/11/2026")
    board = dashboard()
    assert {r["currency"] for r in board["income"]} == {"USD"} and board["currencies"] == ["SAR", "USD"]


def _import(account_id: str, content: str) -> dict:
    response = client.post(f"{API}/imports", headers=OWNER, json={"account_id": account_id, "content": content, "filename": "nov.csv"})
    assert response.status_code == 201, response.text
    return response.json()


def test_statement_after_sms_reconciles_instead_of_creating() -> None:
    s = setup()
    send(s["token"], SALARY_EN)
    imported = _import(s["bank"]["id"], "Date,Description,Amount\n2026-11-01,SALARY NOV,12000.00\n2026-11-02,PHARMACY NOT ON SMS,-10.00\n")
    assert imported["reconciled"] == 1 and imported["imported_count"] == 1 and imported["held_for_review"] == 0
    board = dashboard()
    assert money(board["income"]) == Decimal("12000.00") and money(board["expenses"]) == Decimal("10.00")
    salary = next(e for e in sms_memory_store.entries.values() if e.kind == "salary")
    assert salary.reconciled_at is not None
    canonical = client.get(f"{API}/ledger/transactions/{salary.canonical_id}", headers=OWNER).json()
    assert sorted(src["kind"] for src in canonical["current"]["sources"]) == ["sms", "statement"]
    # Nothing is left for the manual comparison, and importing the same file again adds nothing.
    assert client.get(f"{API}/sms/reconcile/{imported['id']}", headers=OWNER).json()["matched"] == []
    assert _import(s["bank"]["id"], "Date,Description,Amount\n2026-11-02,PHARMACY NOT ON SMS,-10.00\n")["imported_count"] == 0


def test_sms_after_statement_attaches_to_the_statement_transaction() -> None:
    s = setup()
    _import(s["card"]["id"], "Date,Description,Amount\n2026-11-03,POS CARREFOUR RIYADH,-45.50\n")
    result = send(s["token"], PURCHASE_EN).json()
    assert result["status"] == "attached"
    assert money(dashboard()["expenses"]) == Decimal("45.50")
    assert len(client.get(f"{API}/transactions", headers=OWNER).json()["items"]) == 1


def test_an_ambiguous_statement_row_waits_for_review_and_counts_once() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)  # SAR 45.50 at CARREFOUR on the card
    imported = _import(s["card"]["id"], "Date,Description,Amount\n2026-11-03,CARD TXN 9912,-45.50\n")
    assert imported["held_for_review"] == 1 and imported["imported_count"] == 0 and imported["reconciled"] == 0
    waiting = client.get(f"{API}/sms/messages", headers=OWNER, params={"status": "review"}).json()["items"]
    assert waiting[0]["source"] == "statement" and waiting[0]["reason"] == "ambiguous_match"
    assert money(dashboard()["expenses"]) == Decimal("45.50")
    # The person decides it was a separate purchase: it is recorded on the statement's account.
    posted = client.post(f"{API}/sms/messages/{waiting[0]['id']}/post", headers=OWNER, json={"merchant": "OTHER SHOP", "category": "shopping"})
    assert posted.status_code == 200, posted.text
    assert money(dashboard()["expenses"]) == Decimal("91.00")


def test_canonical_id_survives_corrections_and_hand_entries_are_journalled() -> None:
    s = setup()
    posted = send(s["token"], PURCHASE_EN).json()
    first = sms_memory_store.entries[next(m.entry_id for m in sms_memory_store.messages.values() if m.id == posted["message_id"])]
    corrected = client.post(f"{API}/sms/messages/{posted['message_id']}/post", headers=OWNER, json={"amount": "40.00"}).json()
    assert corrected["canonical_id"] == first.canonical_id == first.id
    view = client.get(f"{API}/ledger/transactions/{first.canonical_id}", headers=OWNER).json()
    assert len(view["chain"]) == 3 and view["current"]["id"] == corrected["entry_id"]  # original, reversal, correction
    # A hand-entered transaction is journalled; editing it reposts in the same chain and keeps the row id.
    txn = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": s["bank"]["id"], "posted_on": "2026-11-04",
                                                                  "amount": "-20.00", "description": "CASH TAXI"}).json()
    manual = next(e for e in sms_memory_store.entries.values() if e.kind == "manual")
    client.patch(f"{API}/transactions/{txn['id']}", headers=OWNER, json={"amount": "-25.00"})
    chain = client.get(f"{API}/ledger/transactions/{manual.canonical_id}", headers=OWNER).json()
    assert len(chain["chain"]) == 3 and chain["transaction_ids"] == [txn["id"]]
    assert money(dashboard()["expenses"]) == Decimal("65.00")
    balanced_journal()


def test_device_scopes_limit_what_a_token_may_send() -> None:
    s = setup()
    only_sms = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "SMS only", "scopes": ["finance.sms"]}).json()
    assert only_sms["device"]["scopes"] == ["finance.sms"]
    put_settings(wallet_cards=[{"card": "My Visa", "account_id": s["card"]["id"]}])
    refused = client.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {only_sms['token']}"},
                          json={"source": "apple_wallet", "amount": "SAR 1.00", "merchant": "X", "card": "My Visa", "name": "n"})
    assert refused.status_code == 403 and refused.json()["code"] == "scope_missing"
    assert send(only_sms["token"], PURCHASE_EN).status_code == 200
    assert client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "x", "scopes": ["vehicle.trips"]}).status_code == 422


def test_network_failure_and_retry_records_once(monkeypatch: pytest.MonkeyPatch) -> None:
    s = setup()
    real = svc.process
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "process", flaky)
    first = send(s["token"], PURCHASE_EN)
    assert first.status_code == 503 and first.headers["Retry-After"] == "60"
    failed = next(iter(sms_memory_store.messages.values()))
    assert failed.status == "failed" and "***" not in failed.body_masked or failed.body_masked
    assert next(iter(sms_memory_store.devices.values())).last_error == "processing_error"
    retry = send(s["token"], PURCHASE_EN).json()
    assert retry["status"] == "posted" and len(sms_memory_store.messages) == 1 and len(sms_memory_store.entries) == 1
    assert next(iter(sms_memory_store.messages.values())).attempts == 2


def test_invalid_json_and_malformed_input_are_refused() -> None:
    s = setup()
    auth = {"Authorization": f"Bearer {s['token']}"}
    assert client.post(f"{API}/sms/ingest", headers={**auth, "content-type": "application/json"}, content=b"{not json").status_code == 422
    assert client.post(f"{API}/sms/ingest", headers=auth, json={"message": PURCHASE_EN}).status_code == 422
    assert client.post(f"{API}/sms/ingest", headers=auth, json={"message": 5, "sender": SENDER}).status_code == 422
    assert client.post(f"{API}/sms/ingest", headers=auth, json={"message": "x" * 2_001, "sender": SENDER}).status_code == 422
    assert send(s["token"], "   ").status_code == 422
    assert send(s["token"], PURCHASE_EN, sender="").status_code == 422
    assert not sms_memory_store.entries


def test_incorrect_classification_is_corrected_with_a_reversal_and_a_taught_rule() -> None:
    s = setup()
    weird = "SYNTHETIC. Txn: 1,250.00 SAR spent @ JARIR BOOKSTORE using crd 1234 dt 03Nov26. Bal: 5,000.10 SAR"
    posted = send(s["token"], weird).json()
    assert posted["status"] == "review" and posted["reason"] == "account_unknown"  # "crd 1234" is not a wording ANUM reads
    first = client.post(f"{API}/sms/messages/{posted['message_id']}/post", headers=OWNER,
                        json={"merchant": "JARIR BOOKSTORE", "category": "shopping", "account_id": s["card"]["id"], "last4": "1234", "teach": True})
    assert first.status_code == 200, first.text
    # Recorded in the wrong category: the correction reverses the entry and records a new one.
    corrected = client.post(f"{API}/sms/messages/{posted['message_id']}/post", headers=OWNER, json={"category": "education"})
    assert corrected.status_code == 200, corrected.text
    body = {**corrected.json(), "proposed_rule": first.json()["proposed_rule"]}
    assert body["reversed_entry_id"] and body["proposed_rule"]["status"] == "proposed"
    board = dashboard()
    assert [(c["category"], c["amount"]) for c in board["categories"]] == [("education", "1250.00")]
    assert money(board["expenses"]) == Decimal("1250.00")
    rule_id = body["proposed_rule"]["id"]
    assert client.post(f"{API}/sms/rules/{rule_id}/activate", headers=OWNER).json()["status"] == "active"
    nxt = send(s["token"], "SYNTHETIC. Txn: 99.00 SAR spent @ VIRGIN MEGASTORE using crd 1234 dt 05Nov26. Bal: 4,901.10 SAR").json()
    assert nxt["status"] == "posted"
    learned = next(m for m in sms_memory_store.messages.values() if m.id == nxt["message_id"])
    assert learned.rule == f"learned:{rule_id}" and learned.parsed["merchant"] == "VIRGIN MEGASTORE"
    balanced_journal()


def test_dashboard_totals_after_imports_and_corrections() -> None:
    s = setup()
    send(s["token"], SALARY_EN)
    purchase = send(s["token"], PURCHASE_EN).json()
    client.post(f"{API}/imports", headers=OWNER, json={"account_id": s["bank"]["id"], "content": "Date,Description,Amount\n2026-11-04,DEWA,-200.00\n", "filename": "a.csv"})
    assert money(dashboard()["expenses"]) == Decimal("245.50")
    client.post(f"{API}/sms/messages/{purchase['message_id']}/post", headers=OWNER, json={"amount": "40.00"})
    board = dashboard()
    assert money(board["expenses"]) == Decimal("240.00") and money(board["income"]) == Decimal("12000.00")
    assert {c["category"] for c in board["categories"]} == {"groceries", "utilities"}
    assert dashboard(category="utilities")["expenses"][0]["amount"] == "200.00"
    assert money(dashboard(account_id=s["card"]["id"])["expenses"]) == Decimal("40.00")
    assert dashboard(currency="USD")["expenses"] == []


# -- switches, settings, privacy -----------------------------------------------------------------


def test_off_by_default_and_turning_on_needs_the_safety_check() -> None:
    assert settings()["enabled"] is False
    response = client.put(f"{API}/sms/settings", headers=OWNER, json={"version": 0, "enabled": True})
    assert response.status_code == 409
    assert client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "x"}).status_code == 409
    status = client.get(f"{API}/sms/status", headers=OWNER).json()
    assert status["ready"] is False and status["ingest_path"] is None
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"] is True
    assert client.get(f"{API}/sms/status", headers=OWNER).json()["ingest_path"] == "/api/v1/finance/sms/ingest"


def test_switched_off_refuses_and_records_the_error() -> None:
    s = setup()
    put_settings(enabled=False)
    response = send(s["token"], PURCHASE_EN)
    assert response.status_code == 409 and response.json()["code"] == "disabled"
    assert client.get(f"{API}/sms/devices", headers=OWNER).json()[0]["last_error"] == "disabled"


def test_safe_test_message_records_nothing() -> None:
    s = setup()
    result = send(s["token"], "ANUM TEST", sender="ANUM TEST").json()
    assert result["status"] == "test_ok" and not sms_memory_store.messages and not sms_memory_store.entries
    assert client.get(f"{API}/sms/devices", headers=OWNER).json()[0]["last_ok_at"]


def test_rate_limit_per_device(monkeypatch: pytest.MonkeyPatch) -> None:
    s = setup()
    monkeypatch.setattr(svc, "PER_MINUTE", 2)
    send(s["token"], SALARY_EN)
    send(s["token"], PURCHASE_EN)
    limited = send(s["token"], PURCHASE_AR)
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"


def test_masking_and_retention() -> None:
    assert P.mask("Account 1234567890123 IBAN SA0380000000608010167519 SAR 123456.00") == "Account ***0123 IBAN ***7519 SAR 123456.00"
    s = setup()
    put_settings(body_retention_days=0)
    send(s["token"], "SYNTHETIC. Purchase of SAR 10.00 at SHOP with card 4111111111111234 on 03/11/2026")
    assert all(m.body_masked == "" for m in sms_memory_store.messages.values())
    put_settings(body_retention_days=1)
    send(s["token"], "SYNTHETIC. Purchase of SAR 11.00 at SHOP with card 4111111111111234 on 03/11/2026")
    kept = [m for m in sms_memory_store.messages.values() if m.body_masked]
    assert kept and "4111111111111234" not in kept[0].body_masked and "***1234" in kept[0].body_masked
    assert sms_memory_store.purge_bodies(svc.TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_sms"), utc_now() + timedelta(days=2)) == 1


def test_ai_help_needs_explicit_consent_and_a_model() -> None:
    s = setup()
    review = send(s["token"], NO_AMOUNT).json()
    assert client.post(f"{API}/sms/messages/{review['message_id']}/suggest", headers=OWNER).status_code == 409
    assert client.put(f"{API}/sms/settings", headers=OWNER, json={"version": settings()["version"], "ai_enabled": True}).status_code == 422
    on = put_settings(ai_enabled=True, ai_consent=True)
    assert on["ai"]["enabled"] and on["ai"]["opted_in_at"]
    # The test server has only the mock model: ANUM says so instead of guessing.
    assert client.post(f"{API}/sms/messages/{review['message_id']}/suggest", headers=OWNER).status_code == 409


def test_privacy_delete_removes_the_journal_and_messages() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)
    ctx = svc.TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_sms")
    assert sms_memory_store.count_everything(ctx) > 0
    finance_store.delete_everything(ctx)
    assert sms_memory_store.count_everything(ctx) == 0


def test_viewer_cannot_use_the_integration_and_a_viewer_owner_device_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get(f"{API}/sms/status", headers=VIEWER).status_code == 403
    s = setup()
    monkeypatch.setattr(svc, "_membership_roles", lambda context: ["viewer"])
    response = send(s["token"], PURCHASE_EN)
    assert response.status_code == 403 and response.json()["code"] == "not_allowed"


# -- pure parts --------------------------------------------------------------------------------


def test_every_plan_balances_and_card_repayment_has_no_expense() -> None:
    now = utc_now()

    def acct(kind: AccountKind, ident: str) -> Account:
        return Account(id=ident, tenant_id="t", workspace_id="w", owner_user_id="u", name=ident, kind=kind, currency="SAR", created_at=now, updated_at=now)

    bank, card, loan, cash = acct(AccountKind.BANK, "b"), acct(AccountKind.CARD, "c"), acct(AccountKind.LOAN, "l"), acct(AccountKind.CASH, "k")
    amount = Decimal("100.00")
    for kind in P.Kind:
        if kind == P.Kind.UNKNOWN:
            with pytest.raises(L.PlanError):
                L.plan(kind, amount, account=bank)
            continue
        counter = loan if kind in (P.Kind.LOAN_DISBURSEMENT, P.Kind.LOAN_REPAYMENT) else card if kind == P.Kind.CARD_REPAYMENT else None
        legs = L.plan(kind, amount, account=bank, counter=counter, category="dining", cash_account=cash, interest=Decimal("10.00") if kind == P.Kind.LOAN_REPAYMENT else None)
        assert L.balanced(legs), kind
    repayment = L.plan(P.Kind.CARD_REPAYMENT, amount, account=bank, counter=card)
    assert not any(leg.ledger_account.startswith("expense:") for leg in repayment)
    loan_payment = L.plan(P.Kind.LOAN_REPAYMENT, amount, account=bank, counter=loan, interest=Decimal("10.00"))
    # The whole payment moves bank to loan (a transfer); the interest is charged on the loan as its own pair.
    assert sorted((leg.ledger_account, leg.amount) for leg in loan_payment) == sorted([
        ("liability:l", Decimal("100.00")), ("asset:b", Decimal("-100.00")), ("expense:interest", Decimal("10.00")), ("liability:l", Decimal("-10.00"))])
    proceeds = L.plan(P.Kind.LOAN_DISBURSEMENT, amount, account=bank, counter=loan)
    assert {leg.ledger_account: leg.amount for leg in proceeds} == {"asset:b": amount, "liability:l": -amount}


def test_taught_rule_pattern_is_built_from_fixed_pieces_only() -> None:
    body = P.normalize("SYNTHETIC (.*)+ Paid 50.00 SAR at A[B] ref 1234")
    pattern = P.build_pattern(body, {"amount": "50.00", "merchant": "A[B]"})
    assert r"\(\.\*\)\+" in pattern and P.apply_learned(P.LearnedRule("r", "x", pattern, P.Kind.CARD_PURCHASE, "SAR"), body) is not None
    with pytest.raises(P.TeachError):
        P.build_pattern(body, {"amount": "77.00"})


def test_transactions_tab_cannot_silently_edit_the_journal() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)
    txn = next(t for t in client.get(f"{API}/transactions", headers=OWNER).json()["items"])
    assert client.patch(f"{API}/transactions/{txn['id']}", headers=OWNER, json={"amount": "1.00"}).status_code == 409
    assert client.patch(f"{API}/transactions/{txn['id']}", headers=OWNER, json={"notes": "fine"}).status_code == 200
    assert client.delete(f"{API}/transactions/{txn['id']}", headers=OWNER).status_code == 204
    assert len(sms_memory_store.entries) == 2 and money(dashboard()["expenses"]) == Decimal("0")
    balanced_journal()


def test_the_assistant_capability_says_whether_sms_forwarding_is_on() -> None:
    from anum_api.capabilities import _finance
    from anum_api.schemas import TenantContext

    ctx = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_sms", roles=["owner"])
    off = _finance(ctx, {"finance.summary"})
    assert off.available and "off" in (off.note or "")
    s = setup()
    send(s["token"], NO_AMOUNT)
    on = _finance(ctx, {"finance.summary"})
    assert on.available and "1 waiting for review" in (on.note or "")


# -- Apple Wallet and SMS: one payment, two sources ------------------------------------------------

WALLET_SMS = "SYNTHETIC. Purchase of SAR 125.00 at PANDA HYPERMARKET with card **1234"


def wallet(token: str, amount: object = "SAR 125.00", merchant: str = "Panda Hypermarket", card: str = "My Visa", **extra: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {token}"},
                       json={"source": "apple_wallet", "amount": amount, "merchant": merchant, "card": card, "name": "Purchase", **extra})


def wallet_setup() -> dict[str, Any]:
    s = setup()
    put_settings(wallet_cards=[{"card": "My Visa", "last4": "8888", "account_id": s["card"]["id"]}])
    return s


def expenses_sar() -> Decimal:
    return money(dashboard()["expenses"])


def test_apple_pay_first_then_the_bank_sms_make_one_expense() -> None:
    s = wallet_setup()
    first = wallet(s["token"]).json()
    assert first["status"] == "posted"
    second = send(s["token"], WALLET_SMS).json()
    assert second["status"] == "attached" and second["reason"] == "attached_to_existing"
    assert len(sms_memory_store.entries) == 1 and expenses_sar() == Decimal("125.00")
    entry = client.get(f"{API}/ledger/entries", headers=OWNER).json()["items"][0]
    assert sorted(src["kind"] for src in entry["sources"]) == ["sms", "wallet"]


def test_the_bank_sms_first_then_apple_pay_make_one_expense() -> None:
    s = wallet_setup()
    assert send(s["token"], WALLET_SMS).json()["status"] == "posted"
    assert wallet(s["token"]).json()["status"] == "attached"
    assert len(sms_memory_store.entries) == 1 and expenses_sar() == Decimal("125.00")


def test_concurrent_apple_pay_and_sms_make_one_expense() -> None:
    from concurrent.futures import ThreadPoolExecutor

    s = wallet_setup()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda f: f(), [lambda: wallet(s["token"]).json(), lambda: send(s["token"], WALLET_SMS).json()]))
    assert sorted(r["status"] for r in results) == ["attached", "posted"]
    assert len(sms_memory_store.entries) == 1 and expenses_sar() == Decimal("125.00")


def test_two_genuine_purchases_of_the_same_amount_stay_two() -> None:
    s = wallet_setup()
    assert wallet(s["token"], merchant="Panda Hypermarket").json()["status"] == "posted"
    assert wallet(s["token"], merchant="Jarir Bookstore").json()["status"] == "posted"
    # The same merchant twice through Wallet is two purchases too: a second Wallet event never attaches.
    assert wallet(s["token"], merchant="Panda Hypermarket", name="Second").json()["status"] == "posted"
    assert len(sms_memory_store.entries) == 3 and expenses_sar() == Decimal("375.00")
    # An SMS from another merchant at the same amount does not attach to any of them.
    other = send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at NAHDI PHARMACY with card **1234").json()
    assert other["status"] == "posted" and len(sms_memory_store.entries) == 4


def test_amount_alone_never_matches_another_account() -> None:
    s = wallet_setup()
    wallet(s["token"])
    # The same amount and merchant on the bank account (debit card 5678) is not the Visa payment.
    result = send(s["token"], "SYNTHETIC. Purchase of SAR 125.00 at PANDA HYPERMARKET with card **5678").json()
    assert result["status"] == "posted" and len(sms_memory_store.entries) == 2


def test_wallet_amounts_in_any_shape_and_currency_from_the_mapped_card() -> None:
    from anum_api.finance.wallet import parse_amount

    assert parse_amount("SAR 1,234.50").amount == Decimal("1234.50") and parse_amount("SAR 1,234.50").currency == "SAR"
    assert parse_amount("1.234,50 €").amount == Decimal("1234.50") and parse_amount("1.234,50 €").currency == "EUR"
    assert parse_amount("١٢٥٫٠٠ ر.س").amount == Decimal("125.00") and parse_amount("١٢٥٫٠٠ ر.س").currency == "SAR"
    assert parse_amount(125).amount == Decimal("125") and parse_amount("-20.00").refund
    s = wallet_setup()
    # No currency from the Shortcut: shown with the card's currency, but never posted on that guess (it may be USD 125).
    result = wallet(s["token"], amount="125").json()
    assert result["status"] == "review" and result["reason"] == "wallet_currency_missing" and not sms_memory_store.entries
    message = sms_memory_store.messages[result["message_id"]]
    assert message.parsed["currency"] == "SAR" and message.parsed["currency_inferred"] is True
    assert wallet(s["token"], amount="SAR 125", merchant="OTHER SHOP").json()["status"] == "posted"


def test_unmapped_wallet_card_and_bad_input_wait_or_are_refused() -> None:
    s = wallet_setup()
    unknown = wallet(s["token"], card="Some Other Card").json()
    assert unknown["status"] == "review" and unknown["reason"] == "wallet_card_unknown" and not sms_memory_store.entries
    assert wallet(s["token"], amount="abc").json()["reason"] == "missing_amount"
    assert wallet(s["token"], user_id="someone").status_code == 422
    assert client.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {s['token']}"},
                       json={"source": "sms", "amount": "1", "card": "My Visa"}).status_code == 422
    assert client.post(f"{API}/wallet/ingest", json={"source": "apple_wallet", "amount": "1", "card": "My Visa"}).status_code == 401
    # The virtual card's last four digits find the card too.
    assert wallet(s["token"], card="Visa •••• 8888").json()["status"] == "posted"


def test_wallet_retry_is_recorded_once() -> None:
    s = wallet_setup()
    first = wallet(s["token"]).json()
    again = wallet(s["token"]).json()
    assert again["replayed"] and again["message_id"] == first["message_id"] and len(sms_memory_store.entries) == 1


def test_privacy_export_includes_messages_and_the_journal_but_never_token_hashes() -> None:
    s = setup()
    send(s["token"], PURCHASE_EN)
    finance = client.get("/api/v1/privacy/export", headers=OWNER).json()["finance"]
    assert len(finance["sms_messages"]) == 1 and len(finance["journal_entries"]) == 1 and len(finance["sms_devices"]) == 1
    assert "token_hash" not in finance["sms_devices"][0] and "idem_key" not in finance["sms_messages"][0]


# -- review fixes (PR #364 review, 4 November 2026) -------------------------------------------------


def _bank_balance(s: dict) -> Decimal:
    return Decimal(next(a for a in client.get(f"{API}/accounts", headers=OWNER).json() if a["id"] == s["bank"]["id"])["balance"])


def test_a_repeated_bank_side_repayment_clears_the_suspense_only_once() -> None:
    s = setup()
    send(s["token"], REPAYMENT_CARD)
    assert send(s["token"], REPAYMENT_BANK).json()["status"] == "duplicate"
    # The same bank message again, outside the one-minute idempotency bucket, and pasted by hand.
    later = svc.ingest(svc.TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_sms", roles=["owner"]),
                       s["device"]["id"], s["token"].rsplit(".", 1)[1], SENDER, REPAYMENT_BANK, now=utc_now() + timedelta(minutes=5))
    assert later.message.status == "duplicate"
    assert client.post(f"{API}/sms/paste", headers=OWNER, json={"sender": SENDER, "message": REPAYMENT_BANK}).json()["status"] == "duplicate"
    clearings = [e for e in sms_memory_store.entries.values() if e.source == "clearing"]
    assert len(clearings) == 1 and _bank_balance(s) == Decimal("-500.00")
    assert dashboard()["in_transit"] == []
    balanced_journal()


def test_undoing_a_repayment_also_reverses_its_clearing_entry() -> None:
    s = setup()
    card_side = send(s["token"], REPAYMENT_CARD).json()
    send(s["token"], REPAYMENT_BANK)
    assert _bank_balance(s) == Decimal("-500.00")
    assert client.post(f"{API}/sms/messages/{card_side['message_id']}/reverse", headers=OWNER).status_code == 200
    assert _bank_balance(s) == Decimal("0.00")
    board = dashboard()
    assert board["in_transit"] == [] and Decimal(next(c for c in board["cards"] if c["id"] == s["card"]["id"])["balance"]) == Decimal("0.00")
    assert not [t for t in client.get(f"{API}/transactions", headers=OWNER).json()["items"]]
    balanced_journal()


def test_correcting_a_repayment_replaces_its_clearing_entry() -> None:
    s = setup()
    card_side = send(s["token"], REPAYMENT_CARD).json()
    send(s["token"], REPAYMENT_BANK)
    corrected = client.post(f"{API}/sms/messages/{card_side['message_id']}/post", headers=OWNER,
                            json={"amount": "400.00", "account_id": s["bank"]["id"], "counter_account_id": s["card"]["id"]})
    assert corrected.status_code == 200, corrected.text
    assert _bank_balance(s) == Decimal("-400.00")
    board = dashboard()
    assert board["in_transit"] == [] and Decimal(next(c for c in board["cards"] if c["id"] == s["card"]["id"])["balance"]) == Decimal("400.00")
    balanced_journal()


def test_a_hand_entered_transaction_edited_twice_then_deleted_keeps_balances_right() -> None:
    s = setup()
    txn = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": s["bank"]["id"], "posted_on": "2026-11-04",
                                                                  "amount": "-20.00", "description": "CASH TAXI"}).json()
    client.patch(f"{API}/transactions/{txn['id']}", headers=OWNER, json={"amount": "-25.00"})
    client.patch(f"{API}/transactions/{txn['id']}", headers=OWNER, json={"amount": "-30.00"})
    assert money(dashboard()["expenses"]) == Decimal("30.00")
    assert client.delete(f"{API}/transactions/{txn['id']}", headers=OWNER).status_code == 204
    assert money(dashboard()["expenses"]) == Decimal("0") and _bank_balance(s) == Decimal("0.00")
    balanced_journal()


@pytest.mark.parametrize("text", [
    "SYNTHETIC. Service fee SAR 25.00 on account ***5678",
    "SYNTHETIC. رسوم خدمة 25.00 ر.س على حسابك ***5678",
    "SYNTHETIC. Annual fee: SAR 25.00 charged to account ***5678",
])
def test_a_standalone_bank_fee_is_read_and_posted(text: str) -> None:
    parsed = P.parse(SENDER, text, extra_senders={"testbank": "x"}, verified_senders={"testbank"})
    assert parsed.status == "ok" and parsed.kind == P.Kind.BANK_FEE and parsed.amount == Decimal("25.00") and parsed.currency == "SAR"
    s = setup()
    assert send(s["token"], text).json()["status"] == "posted"
    assert [(c["category"], c["amount"]) for c in dashboard()["categories"]] == [("fees", "25.00")]


def test_a_wrong_secret_gets_one_answer_whatever_the_named_person_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security audit, November 2026: a device token names its tenant, workspace and user in the
    clear, and the membership, role and archive checks ran before the secret was compared, so a
    forged token asked "is this person a member here, and with what role?". The stored device and
    its secret come first now: every failure before that is the one ``invalid_token`` answer."""
    from anum_api import dependencies
    from anum_api.schemas import TenantContext

    s = setup()
    forged = s["token"][:-6] + "abcdef"  # the real ids and device, a wrong secret
    stranger, _, _ = svc.mint(TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="never_a_member"), "fin_smsdev_guess")
    asked: list[str] = []

    def not_member(context: Any) -> list[str]:
        asked.append(context.user_id)
        raise svc.IngestError(401, "The person this device belongs to is no longer a member of the workspace.", "not_member")

    def answers() -> set[tuple[int, str, str]]:
        found = set()
        for token in (forged, stranger):
            ingest = send(token, PURCHASE_EN)
            scoped = client.post("/api/v1/attendance/device/events", headers={"Authorization": f"Bearer {token}"},
                                 json={"event": "arrive", "site": "Head office", "idempotency_key": "guess-0001"})
            for response in (ingest, scoped):
                body = response.json()
                error = body.get("error", body)  # the endpoint's own shape, or the API's error envelope
                found.add((response.status_code, str(error.get("code")), str(error.get("message") or error.get("detail"))))
        return found

    from anum_api.finance import devices as D

    D.plug_module_authorities()
    as_member = answers()
    monkeypatch.setattr(svc, "_membership_roles", not_member)
    as_stranger = answers()
    monkeypatch.setattr(svc, "_membership_roles", lambda context: ["viewer"])
    as_viewer = answers()
    monkeypatch.setattr(svc, "_membership_roles", lambda context: ["owner"])
    monkeypatch.setattr(dependencies, "workspace_archived", lambda context: True)
    as_archived = answers()
    assert as_member == as_stranger == as_viewer == as_archived
    assert {status for status, _, _ in as_member} == {401}, as_member
    assert {code for _, code, _ in as_member} <= {"invalid_token", "unauthorized"}, as_member
    assert asked == []  # the membership was never looked up for a token whose secret is wrong
    assert not sms_memory_store.entries
