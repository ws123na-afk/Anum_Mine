"""Rules, learning from corrections, the suggestion queue and automation policies (docs/finance.md#rules-and-learning,
#suggestions, #automation-policies), in-memory stores.

Every merchant, description and amount here is SYNTHETIC and low-entropy.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_finance_sms import API, OTHER_PERSON, OTHER_WORKSPACE, OWNER, VIEWER, account, clean, client  # noqa: F401

from anum_api.dependencies import memory_repository
from anum_api.finance import rules as R
from anum_api.finance import suggestion_jobs as J
from anum_api.finance.models import Category, CategorySource, Transaction, TransactionType
from anum_api.finance.rules_store import SuggestionDecision, rules_memory_store
from anum_api.schemas import TenantContext, utc_now

DAY = date.today() - timedelta(days=10)
CTX = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id=OWNER["x-user-id"])


@pytest.fixture(autouse=True)
def _rules() -> Iterator[None]:
    from anum_api.schemas import WorkspaceMembership

    rules_memory_store.clear()
    memory_repository.store.audit_records.clear()
    memberships = dict(memory_repository.store.memberships)
    stamp = utc_now()
    memory_repository.store.memberships[(CTX.tenant_id, CTX.workspace_id, CTX.user_id)] = WorkspaceMembership(
        tenant_id=CTX.tenant_id, workspace_id=CTX.workspace_id, user_id=CTX.user_id, role="owner", active=True, created_at=stamp, updated_at=stamp)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    rules_memory_store.clear()


def txn(account_id: str, amount: str, description: str, *, days: int = 10, headers: dict = OWNER, **extra: object) -> dict:
    on = (date.today() - timedelta(days=days)).isoformat()
    made = client.post(f"{API}/transactions", headers=headers, json={"account_id": account_id, "posted_on": on, "amount": amount,
                                                                     "description": description, **extra})
    assert made.status_code == 201, made.text
    return made.json()


def get(transaction_id: str) -> dict:
    found = client.get(f"{API}/transactions/{transaction_id}", headers=OWNER)
    assert found.status_code == 200, found.text
    return found.json()


def save_rule(content: dict, headers: dict = OWNER, **extra: object) -> dict:
    made = client.post(f"{API}/rules", headers=headers, json={"content": content, **extra})
    assert made.status_code == 201, made.text
    approved = client.post(f"{API}/rules/{made.json()['rule_id']}/approve", headers=headers,
                           json={"version": made.json()["version"], "payload_hash": made.json()["payload_hash"]})
    assert approved.status_code == 200, approved.text
    return approved.json()


def apply(rule_id: str) -> dict:
    preview = client.post(f"{API}/rules/{rule_id}/preview", headers=OWNER)
    assert preview.status_code == 200, preview.text
    done = client.post(f"{API}/rules/{rule_id}/apply", headers=OWNER, json={"preview_hash": preview.json()["preview_hash"]})
    assert done.status_code == 201, done.text
    return done.json()


def queue(**params: object) -> dict:
    found = client.get(f"{API}/suggestions", headers=OWNER, params={"limit": 100, **params})
    assert found.status_code == 200, found.text
    return found.json()


# -- learning from a correction ------------------------------------------------------------------------


def test_a_correction_offers_a_rule_with_a_preview_and_the_corrected_row_stays_manual() -> None:
    bank = account()
    rows = [txn(bank["id"], "-40.00", f"POS 44{i} ZEBRAMART TESTCITY", days=10 + i) for i in range(3)]
    assert all(r["transaction_type"] == "uncategorized" for r in rows)
    corrected = client.patch(f"{API}/transactions/{rows[0]['id']}", headers=OWNER, json={"category": "groceries"})
    assert corrected.status_code == 200 and corrected.json()["category_source"] == "manual"
    similar = client.post(f"{API}/transactions/{rows[0]['id']}/similar", headers=OWNER, json={"scope": "all"})
    assert similar.status_code == 200, similar.text
    body = similar.json()
    assert body["content"]["match"]["words"] == ["zebramart testcity"] and body["content"]["match"]["direction"] == "out"
    assert body["content"]["category"] == "groceries"
    assert body["preview"]["changes"] == 2 and body["preview"]["manual_kept"] == 1
    assert {r["transaction_id"] for r in body["preview"]["rows"]} == {rows[1]["id"], rows[2]["id"]}
    # Nothing was saved by the preview.
    assert get(rows[1]["id"])["transaction_type"] == "uncategorized"
    assert client.get(f"{API}/rules", headers=OWNER).json()["total"] == 0


def test_retroactive_apply_is_one_batch_that_undo_puts_back_and_manual_rows_are_never_overwritten() -> None:
    bank = account()
    rows = [txn(bank["id"], "-40.00", f"ZEBRAMART TESTCITY {i}", days=10 + i) for i in range(3)]
    client.patch(f"{API}/transactions/{rows[0]['id']}", headers=OWNER, json={"category": "dining"})
    rule = save_rule({"match": {"words": ["zebramart"]}, "category": "groceries"}, source_transaction_id=rows[0]["id"])
    assert rule["provenance"] == "learned" and rule["status"] == "approved"
    batch = apply(rule["rule_id"])
    assert batch["changed"] == 2 and batch["origin"] == "owner"
    assert get(rows[0]["id"])["category"] == "dining"  # the owner's own choice is kept
    for row in rows[1:]:
        after = get(row["id"])
        assert after["category"] == "groceries" and after["category_source"] == "owner_rule" and after["transaction_type"] == "expense"
        assert after["amount"] == "-40.00" and after["posted_on"] == row["posted_on"] and after["currency"] == row["currency"]
    undone = client.post(f"{API}/rule-batches/{batch['id']}/undo", headers=OWNER)
    assert undone.status_code == 200 and undone.json()["restored"] == 2
    assert all(get(r["id"])["transaction_type"] == "uncategorized" for r in rows[1:])
    assert client.post(f"{API}/rule-batches/{batch['id']}/undo", headers=OWNER).status_code == 409
    actions = {r.action for r in memory_repository.store.audit_records}
    assert {"finance.rule.propose", "finance.rule.approve", "finance.rule.apply", "finance.rule_batch.undo"} <= actions


def test_apply_refuses_a_stale_preview() -> None:
    bank = account()
    one = txn(bank["id"], "-40.00", "ZEBRAMART ONE")
    txn(bank["id"], "-41.00", "ZEBRAMART TWO")
    rule = save_rule({"match": {"words": ["zebramart"]}, "category": "groceries"})
    stale = client.post(f"{API}/rules/{rule['rule_id']}/preview", headers=OWNER).json()["preview_hash"]
    client.patch(f"{API}/transactions/{one['id']}", headers=OWNER, json={"category": "dining"})  # the owner changed a row meanwhile
    assert client.post(f"{API}/rules/{rule['rule_id']}/apply", headers=OWNER, json={"preview_hash": stale}).status_code == 409


def test_new_rows_follow_an_approved_rule_but_not_a_proposed_one() -> None:
    bank = account()
    made = client.post(f"{API}/rules", headers=OWNER, json={"content": {"match": {"words": ["zebramart"]}, "category": "groceries"}}).json()
    assert txn(bank["id"], "-10.00", "ZEBRAMART A")["transaction_type"] == "uncategorized"
    client.post(f"{API}/rules/{made['rule_id']}/approve", headers=OWNER, json={"version": 1, "payload_hash": made["payload_hash"]})
    row = txn(bank["id"], "-10.00", "ZEBRAMART B")
    assert row["category"] == "groceries" and row["category_source"] == "owner_rule"


def test_approval_needs_the_payload_hash_the_owner_was_shown() -> None:
    made = client.post(f"{API}/rules", headers=OWNER, json={"content": {"match": {"words": ["zebramart"]}, "category": "groceries"}}).json()
    wrong = client.post(f"{API}/rules/{made['rule_id']}/approve", headers=OWNER, json={"version": 1, "payload_hash": "0" * 64})
    assert wrong.status_code == 409
    assert client.post(f"{API}/rules/{made['rule_id']}/approve", headers=OWNER, json={"version": 1}).status_code == 409


# -- versions --------------------------------------------------------------------------------------------


def test_every_change_is_a_version_and_revert_restores_an_older_one() -> None:
    first = save_rule({"match": {"words": ["zebramart"]}, "category": "groceries"})
    rule_id = first["rule_id"]
    edited = client.post(f"{API}/rules/{rule_id}/versions", headers=OWNER, json={"content": {"match": {"words": ["zebramart"]}, "category": "shopping"}}).json()
    assert edited["version"] == 2 and edited["status"] == "proposed"
    listed = client.get(f"{API}/rules", headers=OWNER).json()["items"][0]
    assert listed["current"]["version"] == 1 and listed["pending"]["version"] == 2  # v1 runs until v2 is approved
    client.post(f"{API}/rules/{rule_id}/approve", headers=OWNER, json={"version": 2, "payload_hash": edited["payload_hash"]})
    reverted = client.post(f"{API}/rules/{rule_id}/revert", headers=OWNER, json={"to_version": 1}).json()
    assert reverted["version"] == 3 and reverted["reverted_from"] == 1 and reverted["content"]["category"] == "groceries"
    client.post(f"{API}/rules/{rule_id}/approve", headers=OWNER, json={"version": 3, "payload_hash": reverted["payload_hash"]})
    versions = client.get(f"{API}/rules/{rule_id}", headers=OWNER).json()["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1] and all(v["status"] == "approved" for v in versions)
    bank = account()
    assert txn(bank["id"], "-10.00", "ZEBRAMART C")["category"] == "groceries"


def test_the_older_phrase_list_is_read_until_the_first_save_and_then_copied_in() -> None:
    client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "SAR", "timezone": "Asia/Dubai",
                                                      "category_rules": [{"match": "quokkashop", "category": "shopping"}]})
    bank = account()
    assert txn(bank["id"], "-5.00", "QUOKKASHOP ONE")["category"] == "shopping"
    assert client.get(f"{API}/rules", headers=OWNER).json()["legacy_rules"] == 1
    save_rule({"match": {"words": ["zebramart"]}, "category": "groceries"})
    rules = client.get(f"{API}/rules", headers=OWNER).json()
    assert rules["legacy_rules"] == 0 and {r["current"]["provenance"] for r in rules["items"]} == {"imported", "owner"}
    assert txn(bank["id"], "-5.00", "QUOKKASHOP TWO")["category"] == "shopping"


# -- scope, amounts, aliases ------------------------------------------------------------------------------


def test_an_account_scoped_rule_never_touches_other_accounts() -> None:
    first, second = account(name="First"), account(name="Second")
    a = txn(first["id"], "-20.00", "ZEBRAMART X")
    b = txn(second["id"], "-20.00", "ZEBRAMART X")
    rule = save_rule({"match": {"words": ["zebramart"], "account_ids": [first["id"]]}, "category": "groceries"})
    batch = apply(rule["rule_id"])
    assert batch["changed"] == 1
    assert get(a["id"])["category"] == "groceries" and get(b["id"])["transaction_type"] == "uncategorized"
    assert txn(second["id"], "-20.00", "ZEBRAMART Y")["transaction_type"] == "uncategorized"
    assert txn(first["id"], "-20.00", "ZEBRAMART Y")["category"] == "groceries"
    other = client.post(f"{API}/rules", headers=OWNER, json={"content": {"match": {"words": ["zebramart"], "account_ids": ["fin_acc_not_mine"]},
                                                                         "category": "groceries"}})
    assert other.status_code == 422


def test_an_amount_range_rule_matches_only_inside_its_range_and_currency() -> None:
    bank = account()
    save_rule({"match": {"words": ["zebramart"], "min_amount": "100", "max_amount": "200", "currency": "SAR"}, "category": "shopping"})
    assert txn(bank["id"], "-50.00", "ZEBRAMART")["transaction_type"] == "uncategorized"
    assert txn(bank["id"], "-150.00", "ZEBRAMART")["category"] == "shopping"
    assert txn(bank["id"], "-250.00", "ZEBRAMART")["transaction_type"] == "uncategorized"
    assert client.post(f"{API}/rules", headers=OWNER, json={"content": {"match": {"words": ["zebramart"], "min_amount": "1"},
                                                                        "category": "shopping"}}).status_code == 422


def test_an_arabic_merchant_alias_files_every_spelling_under_one_merchant() -> None:
    bank = account()
    for alias in ("زيبرا", "ZEBRA MRT"):
        assert client.post(f"{API}/merchants/aliases", headers=OWNER, json={"alias": alias, "merchant": "Zebra Mart"}).status_code == 201
    assert client.post(f"{API}/merchants/aliases", headers=OWNER, json={"alias": "زيبرا", "merchant": "Other"}).status_code == 409
    save_rule({"match": {"merchant": "Zebra Mart"}, "category": "shopping"})
    assert txn(bank["id"], "-30.00", "شراء زيبرا دبي")["category"] == "shopping"
    assert txn(bank["id"], "-30.00", "POS ZEBRA MRT 5521")["category"] == "shopping"
    assert txn(bank["id"], "-30.00", "ZEBRAS UNRELATED")["transaction_type"] == "uncategorized"
    # Folding: a hamza or taa marbuta written differently is the same word.
    assert R.has_phrase(R.fold("إيجار الشقة"), "ايجار")


def test_too_broad_rules_are_refused() -> None:
    made = client.post(f"{API}/rules", headers=OWNER, json={"content": {"match": {"words": ["payment"]}, "category": "shopping"}})
    assert made.status_code == 422


# -- AI ------------------------------------------------------------------------------------------------------


def test_the_ai_suggestion_is_off_without_a_model_never_applied_and_never_learned(monkeypatch: pytest.MonkeyPatch) -> None:
    bank = account()
    row = txn(bank["id"], "-12.00", "ZEBRAMART AI")
    assert client.post(f"{API}/transactions/{row['id']}/ai-suggestion", headers=OWNER).status_code == 409  # AI help is off
    from anum_api.finance import sms_ai
    from anum_api.finance import sms_service

    real = sms_service.settings_for

    def opted_in(store, context):  # type: ignore[no-untyped-def]
        found = real(store, context).model_copy(deep=True)
        found.ai.enabled, found.ai.opted_in_at = True, utc_now()
        return found

    monkeypatch.setattr(sms_service, "settings_for", opted_in)
    assert client.post(f"{API}/transactions/{row['id']}/ai-suggestion", headers=OWNER).status_code == 409  # the mock model does not count
    monkeypatch.setattr(sms_ai, "has_model", lambda context: True)
    monkeypatch.setattr(sms_ai, "_ask", lambda context, prompt: '{"category": "groceries", "transaction_type": "expense"}')
    answer = client.post(f"{API}/transactions/{row['id']}/ai-suggestion", headers=OWNER)
    assert answer.status_code == 200 and answer.json()["category"] == "groceries"
    assert get(row["id"])["transaction_type"] == "uncategorized"  # shown, never applied
    assert client.get(f"{API}/rules", headers=OWNER).json()["total"] == 0  # never a rule
    # The owner takes it on two rows: kept, but history never learns from an AI answer.
    second = txn(bank["id"], "-13.00", "ZEBRAMART AI", days=11)
    for r in (row, second):
        assert client.patch(f"{API}/transactions/{r['id']}", headers=OWNER, json={"category": "groceries", "from_ai_suggestion": True}).status_code == 200
    third = txn(bank["id"], "-14.00", "ZEBRAMART AI", days=3)
    assert not [s for s in queue()["items"] if s["key"].startswith(f"history:{third['id']}")]


# -- history and the queue ------------------------------------------------------------------------------------


def _taught(bank: dict, times: int = 2) -> list[dict]:
    rows = [txn(bank["id"], "-25.00", "POS 3381 ZEBRAMART TESTCITY", days=30 + i) for i in range(times)]
    for r in rows:
        client.patch(f"{API}/transactions/{r['id']}", headers=OWNER, json={"category": "groceries"})
    return rows


def test_history_suggests_the_owners_earlier_category_with_a_confidence_and_accept_is_undoable() -> None:
    bank = account()
    _taught(bank)
    fresh = txn(bank["id"], "-27.00", "POS 9902 ZEBRAMART TESTCITY", days=2)
    items = [s for s in queue()["items"] if s["kind"] == "classification"]
    assert len(items) == 1 and items[0]["action"]["transaction_ids"] == [fresh["id"]] and items[0]["action"]["category"] == "groceries"
    assert Decimal(items[0]["confidence"]) == Decimal("0.85") and items[0]["evidence"]
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": items[0]["key"], "payload_hash": items[0]["payload_hash"]})
    assert accepted.status_code == 200, accepted.text
    assert get(fresh["id"])["category"] == "groceries" and get(fresh["id"])["category_source"] == "manual"
    assert client.post(f"{API}/rule-batches/{accepted.json()['batch']['id']}/undo", headers=OWNER).json()["restored"] == 1
    assert get(fresh["id"])["transaction_type"] == "uncategorized"


def test_a_rejected_suggestion_stays_dismissed_until_restored() -> None:
    bank = account()
    row = txn(bank["id"], "-9.00", "QUOKKA UNKNOWN")
    key = f"uncategorized:{row['id']}"
    item = next(s for s in queue()["items"] if s["key"] == key)
    assert item["action"]["kind"] == "open" and item["href"] == "#finance-transactions"
    assert client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": key, "payload_hash": item["payload_hash"]}).status_code == 409
    assert client.post(f"{API}/suggestions/reject", headers=OWNER, json={"key": key}).status_code == 200
    assert key not in {s["key"] for s in queue()["items"]}
    assert client.post(f"{API}/suggestions/reject", headers=OWNER, json={"key": key}).status_code == 409
    assert client.post(f"{API}/suggestions/restore", headers=OWNER, json={"key": key}).status_code == 200
    assert key in {s["key"] for s in queue()["items"]}


def test_two_accepts_of_the_same_suggestion_one_wins() -> None:
    bank = account()
    _taught(bank)
    txn(bank["id"], "-27.00", "POS 9902 ZEBRAMART TESTCITY", days=2)
    item = next(s for s in queue()["items"] if s["kind"] == "classification")
    results: list[int] = []
    barrier = threading.Barrier(2)

    def accept() -> None:
        barrier.wait()
        results.append(client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": item["key"], "payload_hash": item["payload_hash"]}).status_code)

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409]
    decision = SuggestionDecision(id="d2", tenant_id=CTX.tenant_id, workspace_id=CTX.workspace_id, owner_user_id=CTX.user_id, key=item["key"],
                                  producer="classification", status="accepted", payload_hash="x", decided_by="u", decided_at=utc_now())
    assert rules_memory_store.claim_decision(decision) is False
    assert len(client.get(f"{API}/rule-batches", headers=OWNER).json()["items"]) == 1


def test_the_queue_lists_every_kind_with_evidence_and_links_into_the_existing_pages() -> None:
    bank = account()
    # A bill due in three days, and a possible duplicate.
    due = (date.today() + timedelta(days=3)).isoformat()
    made = client.post(f"{API}/bills", headers=OWNER, json={"name": "Synthetic gym", "amount": "99.00", "currency": "SAR", "cadence": "monthly",
                                                            "next_due_on": due, "category": "subscriptions"})
    assert made.status_code == 201, made.text
    txn(bank["id"], "-61.00", "CARREFOUR TESTCITY", days=4)
    txn(bank["id"], "-61.00", "CARREFOUR TESTCITY BRANCH", days=3)
    body = queue()
    kinds = {s["kind"] for s in body["items"]}
    assert {"bill_due", "duplicate"} <= kinds and body["unavailable"] == []
    bill = next(s for s in body["items"] if s["kind"] == "bill_due")
    assert bill["href"] == "#finance-bills" and bill["effect"][0]["amount"] == "99.00" and bill["action"]["kind"] == "acknowledge"
    assert client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": bill["key"], "payload_hash": bill["payload_hash"]}).status_code == 200
    assert bill["key"] not in {s["key"] for s in queue()["items"]}
    assert {c["kind"] for c in body["counts"]} == kinds


def test_an_external_producer_plugs_into_the_queue_and_a_failing_one_hides_nothing_else() -> None:
    from anum_api.finance import suggestions as SG

    def demo(snap: SG.Snapshot) -> list[SG.Suggestion]:
        return [SG.Suggestion(key="demo:external", producer="demo_external", kind="external", detected="A statement line has no ledger row.",
                              why="Reconciliation exception.", action=SG.ProposedAction(kind="acknowledge"), href="#finance-reconciliation")]

    def broken(snap: SG.Snapshot) -> list[SG.Suggestion]:
        raise RuntimeError("down")

    SG.register_producer("demo_external", demo)
    SG.register_producer("broken", broken)
    try:
        body = queue()
        assert "demo:external" in {s["key"] for s in body["items"]} and body["unavailable"] == ["broken"]
    finally:
        SG.unregister_producer("demo_external")
        SG.unregister_producer("broken")


# -- automation policies ----------------------------------------------------------------------------------------


def test_a_policy_auto_applies_only_when_turned_on_and_is_audited_and_undoable() -> None:
    bank = account()
    _taught(bank)
    fresh = txn(bank["id"], "-27.00", "POS 9902 ZEBRAMART TESTCITY", days=2)
    assert J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id).applied == 0  # off by default
    assert get(fresh["id"])["transaction_type"] == "uncategorized"
    put = client.put(f"{API}/automation", headers=OWNER, json={"auto_classify": True, "min_confidence": "0.8", "sources": ["history"]})
    assert put.status_code == 200, put.text
    result = J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id)
    assert result.applied == 1 and result.batches == 1
    after = get(fresh["id"])
    assert after["category"] == "groceries" and after["category_source"] == "system" and after["type_reason"] == "auto_history"
    audits = [r for r in memory_repository.store.audit_records if r.action == "finance.suggestion.auto_apply"]
    assert len(audits) == 1 and audits[0].actor == "anum.automation"
    batch = client.get(f"{API}/rule-batches", headers=OWNER).json()["items"][0]
    assert batch["origin"] == "policy"
    assert client.post(f"{API}/rule-batches/{batch['id']}/undo", headers=OWNER).json()["restored"] == 1
    assert get(fresh["id"])["transaction_type"] == "uncategorized"
    # Above the owner's threshold only.
    client.put(f"{API}/automation", headers=OWNER, json={"auto_classify": True, "min_confidence": "0.95", "sources": ["history"]})
    rules_memory_store.decisions.clear()
    assert J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id).applied == 0


def test_a_rule_marked_auto_apply_reaches_earlier_rows_only_with_the_policy_on() -> None:
    bank = account()
    row = txn(bank["id"], "-15.00", "ZEBRAMART AUTO")
    save_rule({"match": {"words": ["zebramart"]}, "category": "groceries", "auto_apply": True})
    assert J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id).applied == 0
    client.put(f"{API}/automation", headers=OWNER, json={"auto_classify": True, "sources": ["rule"]})
    assert J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id).applied == 1
    assert get(row["id"])["category"] == "groceries"


def test_the_unresolved_notice_is_sent_once_a_day_and_in_app_only() -> None:
    from anum_api.push import IN_APP_ONLY_KINDS

    bank = account()
    txn(bank["id"], "-9.00", "QUOKKA UNKNOWN")
    client.put(f"{API}/automation", headers=OWNER, json={"notify_unresolved": True})
    assert J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id).notices == 1
    assert J.run_person(CTX.tenant_id, CTX.workspace_id, CTX.user_id).notices == 0
    assert "finance_suggestions" in IN_APP_ONLY_KINDS


@pytest.mark.parametrize("action", sorted(R.NEVER_AUTOMATED) + ["anything_else"])
def test_forbidden_automations_are_refused_in_code(action: str) -> None:
    with pytest.raises(R.AutomationRefused):
        J.automate(CTX, action)


def test_the_policy_cannot_be_given_a_forbidden_switch_or_a_low_threshold() -> None:
    assert client.put(f"{API}/automation", headers=OWNER, json={"auto_classify": True, "approve_payments": True}).status_code == 422
    assert client.put(f"{API}/automation", headers=OWNER, json={"auto_classify": True, "min_confidence": "0.5"}).status_code == 422
    view = client.get(f"{API}/automation", headers=OWNER).json()
    assert view["policy"]["auto_classify"] is False and "approve_payment" in view["never_automated"]


# -- isolation and permissions ----------------------------------------------------------------------------------


def test_rules_aliases_and_suggestions_are_the_owners_alone() -> None:
    rule = save_rule({"match": {"words": ["zebramart"]}, "category": "groceries"})
    client.post(f"{API}/merchants/aliases", headers=OWNER, json={"alias": "ZEBRA MRT", "merchant": "Zebra Mart"})
    for other in (OTHER_PERSON, OTHER_WORKSPACE):
        assert client.get(f"{API}/rules", headers=other).json()["total"] == 0
        assert client.get(f"{API}/rules/{rule['rule_id']}", headers=other).status_code == 404
        assert client.post(f"{API}/rules/{rule['rule_id']}/versions", headers=other,
                           json={"content": {"match": {"words": ["zebramart"]}, "category": "dining"}}).status_code == 404
        assert client.get(f"{API}/merchants/aliases", headers=other).json() == []
    for path in ("/rules", "/suggestions", "/merchants/aliases", "/automation", "/subcategories"):
        assert client.get(f"{API}{path}", headers=VIEWER).status_code == 403
    assert client.post(f"{API}/rules", headers=VIEWER, json={"content": {"match": {"words": ["zebramart"]}, "category": "dining"}}).status_code == 403


# -- subcategories --------------------------------------------------------------------------------------------


def test_subcategories_split_a_category_without_changing_its_total() -> None:
    bank = account()
    sub = client.post(f"{API}/subcategories", headers=OWNER, json={"parent": "groceries", "name": "Fruit"})
    assert sub.status_code == 201
    assert client.post(f"{API}/subcategories", headers=OWNER, json={"parent": "groceries", "name": "fruit"}).status_code == 409
    a = txn(bank["id"], "-10.00", "CARREFOUR ONE", days=1)
    txn(bank["id"], "-5.00", "CARREFOUR TWO", days=1)
    assert client.patch(f"{API}/transactions/{a['id']}", headers=OWNER, json={"subcategory_id": sub.json()["id"]}).json()["subcategory_id"] == sub.json()["id"]
    wrong = client.patch(f"{API}/transactions/{a['id']}", headers=OWNER, json={"category": "dining", "subcategory_id": sub.json()["id"]})
    assert wrong.status_code == 422
    month = date.today() - timedelta(days=1)
    spend = client.get(f"{API}/subcategories/spend", headers=OWNER, params={"month": month.strftime("%Y-%m")}).json()["items"]
    groceries = [i for i in spend if i["category"] == "groceries"]
    assert {(i["subcategory_id"], i["amount"]) for i in groceries} == {(sub.json()["id"], "10.00"), (None, "5.00")}
    rule = client.post(f"{API}/rules", headers=OWNER, json={"content": {"match": {"words": ["zebramart"]}, "category": "dining",
                                                                        "subcategory_id": sub.json()["id"]}})
    assert rule.status_code == 422  # a subcategory stays under its own category


# -- the engine itself ------------------------------------------------------------------------------------------


def test_history_ignores_rule_guesses_and_needs_two_owner_choices() -> None:
    now = utc_now()
    base = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "account_id": "a", "currency": "SAR", "dedupe_hash": "h",
            "created_at": now, "updated_at": now}

    def row(i: int, source: CategorySource, category: Category = Category.GROCERIES, reason: str | None = None) -> Transaction:
        return Transaction(id=f"r{i}", posted_on=DAY, amount=Decimal("-5"), description="ZEBRAMART", category=category, category_source=source,
                           type_reason=reason, **base)

    target = row(0, CategorySource.NONE, Category.OTHER)
    assert R.history_suggestion(target, [row(1, CategorySource.RULE), row(2, CategorySource.OWNER_RULE)]) is None
    assert R.history_suggestion(target, [row(1, CategorySource.MANUAL)]) is None
    assert R.history_suggestion(target, [row(1, CategorySource.MANUAL, reason="owner_ai"), row(2, CategorySource.MANUAL, reason="owner_ai")]) is None
    hint = R.history_suggestion(target, [row(1, CategorySource.MANUAL), row(2, CategorySource.MANUAL), row(3, CategorySource.MANUAL, Category.DINING)])
    assert hint is not None and hint.category == Category.GROCERIES and hint.confidence < Decimal("0.85")
    assert R.history_suggestion(target, [row(i, CategorySource.MANUAL) for i in range(1, 6)]).confidence == Decimal("0.95")  # type: ignore[union-attr]


def test_merchant_keys_drop_card_numbers_channels_and_cities() -> None:
    assert R.merchant_key("POS 1234 CARREFOUR CITY CENTRE DUBAI AE 12/11") == "carrefour city centre"
    assert R.merchant_key("شراء عبر نقاط البيع الدانوب") == "دانوب"
    assert R.merchant_key("TRANSFER 99881") == ""


def test_a_rule_type_is_kept_and_the_sign_guard_holds() -> None:
    from anum_api.finance.categories import classify_text

    content = R.RuleContent(match=R.RuleMatch(words=["zebrapay"]), category=Category.INCOME)
    rule = R.ActiveRule.from_content("r", 1, content)
    assert classify_text("ZEBRAPAY", Decimal("-5"), [rule]).type == TransactionType.UNCATEGORIZED  # an income rule never files money out
    assert classify_text("ZEBRAPAY", Decimal("5"), [rule]).category == Category.INCOME
    typed = R.ActiveRule.from_content("r", 1, R.RuleContent(match=R.RuleMatch(words=["zebrapay"]), category=Category.TRANSFER,
                                                            transaction_type=TransactionType.OWNER_DRAWING))
    assert classify_text("ZEBRAPAY", Decimal("-5"), [typed]).type == TransactionType.OWNER_DRAWING
    with pytest.raises(ValueError):
        R.RuleContent(match=R.RuleMatch(words=["zebrapay"], direction="out"), category=Category.REFUNDS)
