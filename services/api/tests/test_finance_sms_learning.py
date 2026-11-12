"""Learning from the person's reviews of bank messages (docs/finance-sms.md#learning), in-memory stores.

Every message is SYNTHETIC: written for these tests and not the wording of any real bank.
``test_postgres_finance_sms.py`` repeats the main path under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import sms_learning as SL
from anum_api.finance import sms_service as svc
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

TENANT, WORKSPACE = "tenant_sms_learn", "workspace_sms_learn"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_learn", "x-user-roles": "owner"}
VIEWER = {**OWNER, "x-user-id": "viewer_learn", "x-user-roles": "viewer"}
OTHER_PERSON = {**OWNER, "x-user-id": "member_learn", "x-user-roles": "member"}
API = "/api/v1/finance"
SENDER = "TestBank"

client = TestClient(main.app)


def purchase(amount: str, day: int, merchant: str = "CARREFOUR RIYADH", card: str = "1234") -> str:
    return f"SYNTHETIC. Purchase of SAR {amount} at {merchant} with card **{card} on {day:02d}/11/2026 14:22"


def credit(amount: str, day: int, party: str = "ACME TRADING", word: str = "Deposit") -> str:
    return f"SYNTHETIC. {word} of SAR {amount} from {party} to account ***5678 on {day:02d}/11/2026"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    rules_memory_store.clear()
    svc._last_notified.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()
    rules_memory_store.clear()


def account(**body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def settings() -> dict:
    return client.get(f"{API}/sms/settings", headers=OWNER).json()


def put_settings(**body: Any) -> dict:
    response = client.put(f"{API}/sms/settings", headers=OWNER, json={"version": settings()["version"], **body})
    assert response.status_code == 200, response.text
    return response.json()


def setup(*, verified: bool = False) -> dict[str, Any]:
    bank = account(name="Current", last4="5678")
    card = account(name="Card", kind="card", last4="1234")
    savings = account(name="Savings", last4="9999")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    put_settings(enabled=True, extra_senders=[{"sender": SENDER, "bank": "test_bank"}], verified_senders=[SENDER] if verified else [],
                 account_map=[{"last4": "1234", "account_id": card["id"]}])
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    assert device.status_code == 201, device.text
    return {"bank": bank, "card": card, "savings": savings, "token": device.json()["token"]}


def send(token: str, message: str, sender: str = SENDER) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": sender})
    assert response.status_code == 200, response.text
    return response.json()


def record(message_id: str, **body: Any) -> dict:
    response = client.post(f"{API}/sms/messages/{message_id}/post", headers=OWNER, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def learning() -> dict:
    response = client.get(f"{API}/sms/learning", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def message(message_id: str) -> dict:
    return client.get(f"{API}/sms/messages/{message_id}", headers=OWNER).json()


def entry_of(message_id: str) -> Any:
    stored = sms_memory_store.messages[message_id]
    return sms_memory_store.entries[stored.entry_id]


# -- 1. a sender's format is confirmed by readings recorded unchanged ---------------------------------------


def test_two_unchanged_readings_trust_the_sender_with_a_notice_and_one_tap_undo() -> None:
    s = setup()
    first = send(s["token"], purchase("10.00", 1))
    assert first["reason"] == "format_unverified"
    assert message(first["message_id"])["fix"]["action"] == "trust_sender"
    assert record(first["message_id"])["learned"] == []
    assert learning()["senders"][0]["needed"] == 1
    second = send(s["token"], purchase("11.00", 2))
    learned = record(second["message_id"])["learned"]
    assert [n["kind"] for n in learned] == ["sender_trusted"] and learned[0]["sender"] == "testbank"
    assert "testbank" in settings()["verified_senders"]
    assert send(s["token"], purchase("12.00", 3))["status"] == "posted"
    view = learning()
    assert view["notices"][0]["kind"] == "sender_trusted" and view["senders"][0]["trusted"]
    # One-tap undo: the sender waits for review again and the count starts over.
    undone = client.post(f"{API}/sms/learning/senders/untrust", headers=OWNER, json={"sender": SENDER})
    assert undone.status_code == 200, undone.text
    assert undone.json()["notices"] == [] and undone.json()["senders"][0]["clean"] == 0
    assert send(s["token"], purchase("13.00", 4))["reason"] == "format_unverified"


def test_the_count_is_a_setting_and_learning_can_be_switched_off() -> None:
    s = setup()
    put_settings(learning_confirm_after=1)
    first = send(s["token"], purchase("10.00", 1))
    assert [n["kind"] for n in record(first["message_id"])["learned"]] == ["sender_trusted"]
    untrusted = client.post(f"{API}/sms/learning/senders/untrust", headers=OWNER, json={"sender": SENDER})
    assert untrusted.status_code == 200 and untrusted.json()["confirm_after"] == 1
    put_settings(learning_enabled=False)
    for amount, day in (("11.00", 2), ("12.00", 3), ("13.00", 4)):
        waiting = send(s["token"], purchase(amount, day))
        assert waiting["reason"] == "format_unverified"
        assert record(waiting["message_id"])["learned"] == []
    assert "testbank" not in settings()["verified_senders"]
    bad = client.put(f"{API}/sms/settings", headers=OWNER, json={"version": settings()["version"], "learning_confirm_after": 6})
    assert bad.status_code == 422


def test_a_changed_reading_starts_the_count_again() -> None:
    s = setup()
    first = send(s["token"], purchase("10.00", 1))
    record(first["message_id"], merchant="CARREFOUR HYPER")  # ANUM read the merchant wrong
    second = send(s["token"], purchase("11.00", 2))
    assert record(second["message_id"])["learned"] == []  # 1 clean since the correction
    third = send(s["token"], purchase("12.00", 3))
    assert [n["kind"] for n in record(third["message_id"])["learned"]] == ["sender_trusted"]
    # Changing only the account or the category is the person's books, not the bank's format.
    track = SL.track(svc.settings_for(sms_memory_store, _context()), "testbank")
    assert track is not None and track.clean == 2


def _context() -> Any:
    from anum_api.schemas import TenantContext

    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_learn", roles=["owner"])


# -- 5. a sender corrected lately keeps waiting -------------------------------------------------------------


def test_a_corrected_sender_waits_until_clean_again_or_the_person_trusts_it_again() -> None:
    s = setup(verified=True)
    posted = send(s["token"], purchase("10.00", 1))
    assert posted["status"] == "posted"
    record(posted["message_id"], amount="100.00")  # the amount was read wrong
    held = send(s["token"], purchase("11.00", 2))
    assert held["status"] == "review" and held["reason"] == "sender_corrected_recently"
    assert message(held["message_id"])["fix"]["action"] == "clear_hold"
    cleared = client.post(f"{API}/sms/learning/senders/clear-hold", headers=OWNER, json={"sender": SENDER})
    assert cleared.status_code == 200, cleared.text
    assert send(s["token"], purchase("12.00", 3))["status"] == "posted"
    # Clean readings lift the hold too, and it ends on its own after the hold days.
    settings_now = svc.settings_for(sms_memory_store, _context())
    settings_now.memory.senders[0].corrected_at = svc.utc_now() - timedelta(days=SL.HOLD_DAYS + 1)
    assert SL.hold_reason(settings_now, "testbank", Decimal(1), "SAR", svc.utc_now()) is None


def test_amounts_above_the_persons_limit_and_currency_doubts_never_post_on_their_own() -> None:
    s = setup(verified=True)
    put_settings(auto_post_limits=[{"currency": "SAR", "amount": "40"}])
    big = send(s["token"], purchase("45.50", 1))
    assert big["reason"] == "above_auto_limit"
    assert message(big["message_id"])["fix"]["action"] == "open_settings"
    assert send(s["token"], purchase("39.00", 2))["status"] == "posted"
    foreign = send(s["token"], "SYNTHETIC. Purchase USD 20.00 at NETFLIX.COM card **1234 on 08/11/2026")
    assert foreign["reason"] == "needs_converted_amount" and message(foreign["message_id"])["fix"]["action"] == "decide"
    # Apple Pay obeys the same limit.
    put_settings(wallet_cards=[{"card": "My Visa", "account_id": s["card"]["id"]}])
    paid = client.post(f"{API}/wallet/ingest", headers={"Authorization": f"Bearer {s['token']}"},
                       json={"source": "apple_wallet", "amount": "SAR 80.00", "merchant": "Panda", "card": "My Visa", "name": "Purchase"}).json()
    assert paid["status"] == "review" and paid["reason"] == "above_auto_limit"
    twice = client.put(f"{API}/sms/settings", headers=OWNER, json={"version": settings()["version"], "auto_post_limits": [
        {"currency": "SAR", "amount": "1"}, {"currency": "SAR", "amount": "2"}]})
    assert twice.status_code == 422


def test_a_possible_duplicate_still_waits_for_a_trusted_sender() -> None:
    s = setup(verified=True)
    text = purchase("10.00", 1)
    assert send(s["token"], text)["status"] == "posted"
    again = client.post(f"{API}/sms/paste", headers=OWNER, json={"message": text, "sender": SENDER}).json()
    assert again["status"] == "duplicate"
    assert message(again["message_id"])["fix"]["action"] == "decide"


# -- 2. what money from a party is, per sender --------------------------------------------------------------


def test_the_same_kind_twice_for_a_party_is_applied_next_time_and_can_be_undone() -> None:
    s = setup(verified=True)
    one = send(s["token"], credit("2000.00", 1))
    assert one["status"] == "posted" and entry_of(one["message_id"]).kind == "deposit"
    assert message(one["message_id"])["fix"]["action"] == "record"
    assert record(one["message_id"], kind="salary")["learned"] == []
    # After one choice the posted deposit offers "Treat money from ACME TRADING as Salary".
    two = send(s["token"], credit("2100.00", 2))
    fix = message(two["message_id"])["fix"]
    assert fix["action"] == "remember_kind" and fix["kind"] == "salary" and fix["party"] == "ACME TRADING"
    learned = record(two["message_id"], kind="salary")["learned"]
    assert [n["kind"] for n in learned] == ["kind_remembered"] and learned[0]["value"] == "salary"
    three = send(s["token"], credit("2200.00", 3))
    assert three["status"] == "posted" and entry_of(three["message_id"]).kind == "salary"
    shown = message(three["message_id"])
    assert shown["parsed"]["learned"]["what"] == "kind" and shown["fix"]["action"] == "undo_learning"
    # Another party, or money out, is untouched.
    assert entry_of(send(s["token"], credit("50.00", 4, party="ZETA HOLDINGS"))["message_id"]).kind == "deposit"
    undo = client.post(f"{API}/sms/messages/{three['message_id']}/undo-learning", headers=OWNER)
    assert undo.status_code == 200, undo.text
    assert undo.json()["status"] == "review" and undo.json()["reason"] == "learning_undone" and undo.json()["learning"]["kinds"] == []
    assert entry_of(send(s["token"], credit("2300.00", 5))["message_id"]).kind == "deposit"


def test_remember_now_and_one_tap_activate_and_forget() -> None:
    s = setup(verified=True)
    one = send(s["token"], credit("500.00", 1))
    learned = record(one["message_id"], kind="salary", remember=True)["learned"]
    assert [n["kind"] for n in learned] == ["kind_remembered"]
    memory_id = learning()["kinds"][0]["id"]
    assert entry_of(send(s["token"], credit("501.00", 2))["message_id"]).kind == "salary"
    forgot = client.delete(f"{API}/sms/learning/kinds/{memory_id}", headers=OWNER)
    assert forgot.status_code == 200 and forgot.json()["kinds"] == []
    other = send(s["token"], credit("502.00", 3))
    record(other["message_id"], kind="salary")
    memory_id = learning()["kinds"][0]["id"]
    assert client.post(f"{API}/sms/learning/kinds/{memory_id}/activate", headers=OWNER).json()["kinds"][0]["active"]
    assert entry_of(send(s["token"], credit("503.00", 4))["message_id"]).kind == "salary"
    assert client.post(f"{API}/sms/learning/kinds/nope/activate", headers=OWNER).status_code == 404


def test_money_from_the_persons_own_account_is_remembered_as_an_own_transfer() -> None:
    s = setup(verified=True)
    for amount, day in (("300.00", 1), ("310.00", 2)):
        got = send(s["token"], credit(amount, day, party="MY SAVINGS", word="Incoming transfer"))
        record(got["message_id"], kind="own_transfer", account_id=s["savings"]["id"], counter_account_id=s["bank"]["id"])
    view = learning()
    assert view["kinds"][0]["kind"] == "own_transfer" and view["kinds"][0]["counter_account_id"] == s["savings"]["id"]
    third = send(s["token"], credit("320.00", 3, party="MY SAVINGS", word="Incoming transfer"))
    entry = entry_of(third["message_id"])
    assert third["status"] == "posted" and entry.kind == "own_transfer"
    amounts = {p.account_id: p.amount for p in entry.postings if p.account_id}
    assert amounts == {s["bank"]["id"]: Decimal("320.00"), s["savings"]["id"]: Decimal("-320.00")}


def test_choosing_the_rough_kind_again_forgets_the_choice() -> None:
    s = setup(verified=True)
    for amount, day in (("10.00", 1), ("11.00", 2)):
        record(send(s["token"], credit(amount, day))["message_id"], kind="salary")
    assert learning()["kinds"][0]["active"]
    record(send(s["token"], credit("12.00", 3))["message_id"], kind="deposit")
    assert learning()["kinds"] == []


# -- 2. a merchant's category, through the rules engine ------------------------------------------------------


def test_the_same_category_twice_makes_an_approved_learned_rule_that_files_the_next_one() -> None:
    s = setup(verified=True)
    for amount, day in (("20.00", 1), ("21.00", 2)):
        got = send(s["token"], purchase(amount, day, merchant="ZEBRA MART"))
        result = record(got["message_id"], category="health")
    assert [n["kind"] for n in result["learned"]] == ["category_remembered"]
    rule_id = result["learned"][0]["ref"]
    rules = client.get(f"{API}/rules", headers=OWNER).json()["items"]
    made = next(r for r in rules if r["rule_id"] == rule_id)
    assert made["current"]["provenance"] == "learned" and made["current"]["status"] == "approved"
    third = send(s["token"], purchase("22.00", 3, merchant="ZEBRA MART"))
    assert entry_of(third["message_id"]).category == "health"
    assert message(third["message_id"])["parsed"]["learned"] == {"what": "category", "value": "health", "ref": rule_id, "party": "ZEBRA MART"}
    # Forgetting it switches the rule off with a new version; the next one is filed by the built-in rules again.
    gone = client.delete(f"{API}/sms/learning/categories/{rule_id}", headers=OWNER)
    assert gone.status_code == 200, gone.text
    assert gone.json()["categories"] == []
    assert entry_of(send(s["token"], purchase("23.00", 4, merchant="ZEBRA MART"))["message_id"]).category != "health"


# -- 2. an unmapped card is proposed, never mapped silently ------------------------------------------------------


def test_the_same_account_twice_for_an_unmapped_card_is_proposed_and_mapped_in_one_tap() -> None:
    s = setup(verified=True)
    learned: list[dict] = []
    for amount, day in (("30.00", 1), ("31.00", 2)):
        got = send(s["token"], purchase(amount, day, card="4321"))
        assert got["reason"] == "account_unknown"
        assert message(got["message_id"])["fix"] == {**message(got["message_id"])["fix"], "action": "map_card", "last4": "4321"}
        learned = record(got["message_id"], account_id=s["card"]["id"])["learned"]
    assert [n["kind"] for n in learned] == ["card_proposed"] and learned[0]["last4"] == "4321"
    third = send(s["token"], purchase("32.00", 3, card="4321"))
    assert third["reason"] == "account_unknown"  # proposed, not mapped
    fix = message(third["message_id"])["fix"]
    assert fix["action"] == "map_card" and fix["account_id"] == s["card"]["id"] and fix["account_name"] == "Card"
    assert learning()["card_proposals"][0]["last4"] == "4321"
    mapped = client.post(f"{API}/sms/learning/cards/map", headers=OWNER, json={"last4": "4321", "account_id": s["card"]["id"]})
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["card_proposals"] == []
    assert any(m["last4"] == "4321" for m in settings()["account_map"])
    assert send(s["token"], purchase("33.00", 4, card="4321"))["status"] == "posted"
    again = client.post(f"{API}/sms/learning/cards/map", headers=OWNER, json={"last4": "4321", "account_id": s["card"]["id"]})
    assert again.status_code == 409


def test_a_declined_card_proposal_is_not_offered_again() -> None:
    s = setup(verified=True)
    for amount, day in (("30.00", 1), ("31.00", 2)):
        record(send(s["token"], purchase(amount, day, card="4321"))["message_id"], account_id=s["card"]["id"])
    declined = client.post(f"{API}/sms/learning/cards/decline", headers=OWNER, json={"last4": "4321"})
    assert declined.status_code == 200 and declined.json()["card_proposals"] == []
    assert message(send(s["token"], purchase("32.00", 3, card="4321"))["message_id"])["fix"]["account_id"] is None


# -- 3 and 4. why things wait ----------------------------------------------------------------------------------


def test_the_review_summary_counts_reasons_with_the_fix_for_each() -> None:
    s = setup()
    send(s["token"], purchase("10.00", 1))
    send(s["token"], purchase("11.00", 2))
    send(s["token"], purchase("12.00", 3, card="4321"), sender="OtherBank")
    put_settings(verified_senders=[SENDER], auto_post=False)
    send(s["token"], purchase("13.00", 4))
    summary = client.get(f"{API}/sms/review/summary", headers=OWNER)
    assert summary.status_code == 200, summary.text
    groups = {g["reason"]: g for g in summary.json()["groups"]}
    assert summary.json()["total"] == 4 and summary.json()["complete"]
    assert groups["format_unverified"]["count"] == 2
    assert groups["format_unverified"]["items"][0] == {"key": "testbank", "count": 2, "fix": {**groups["format_unverified"]["items"][0]["fix"],
                                                                                                 "action": "trust_sender", "sender": "testbank"}}
    assert groups["unknown_sender"]["items"][0]["fix"]["action"] == "trust_sender"
    assert groups["auto_post_off"]["items"][0]["fix"]["action"] == "auto_post_on"
    on = client.post(f"{API}/sms/learning/auto-post", headers=OWNER, json={"on": True})
    assert on.status_code == 200 and on.json()["auto_post"] is True
    trusted = client.post(f"{API}/sms/learning/senders/trust", headers=OWNER, json={"sender": "OtherBank"})
    assert trusted.status_code == 200 and "otherbank" in settings()["verified_senders"]


def test_notices_are_dismissed_and_everything_is_the_owners_own() -> None:
    s = setup()
    put_settings(learning_confirm_after=1)
    record(send(s["token"], purchase("10.00", 1))["message_id"])
    notice = learning()["notices"][0]
    assert client.post(f"{API}/sms/learning/notices/{notice['id']}/dismiss", headers=OWNER).json()["notices"] == []
    assert client.post(f"{API}/sms/learning/notices/{notice['id']}/dismiss", headers=OWNER).status_code == 404
    assert client.post(f"{API}/sms/learning/senders/trust", headers=VIEWER, json={"sender": "X"}).status_code == 403
    assert client.post(f"{API}/sms/learning/auto-post", headers=VIEWER, json={"on": False}).status_code == 403
    assert client.get(f"{API}/sms/learning", headers=VIEWER).status_code == 403  # like every SMS read: a viewer has no finance access
    assert client.get(f"{API}/sms/learning", headers=OTHER_PERSON).json()["senders"] == []
    assert client.post(f"{API}/sms/learning/cards/map", headers=OWNER, json={"last4": "4321", "account_id": "fin_acct_nope"}).status_code == 422
    assert client.post(f"{API}/sms/learning/senders/trust", headers=OWNER, json={"sender": "a", "user_id": "x"}).status_code == 422


def test_pure_reading_comparison() -> None:
    from anum_api.finance.sms_store import MessageStatus, SmsMessage

    now = svc.utc_now()
    m = SmsMessage(id="m", tenant_id="t", workspace_id="w", owner_user_id="u", source="device", idem_key="k", received_at=now, sender="b",
                   status=MessageStatus.REVIEW, reason="format_unverified",
                   parsed={"kind": "card_purchase", "amount": "10.00", "merchant": "POS ZEBRA MART", "occurred_on": "2026-11-01"}, created_at=now, updated_at=now)
    assert not SL.reading_changed(m, {})
    assert not SL.reading_changed(m, {"amount": "10", "merchant": "zebra mart", "account_id": "x", "category": "health"})
    assert SL.reading_changed(m, {"amount": "10.01"})
    assert SL.reading_changed(m, {"occurred_on": "2026-11-02"})
    assert SL.reading_changed(m.model_copy(update={"reason": "missing_amount"}), {})
