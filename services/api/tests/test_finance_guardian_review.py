"""Finance Guardian: paused rules, the review queue and the activity log (docs/finance.md#guardian).

In-memory stores; ``test_postgres_finance.py`` repeats the review table under RLS. Fixtures are
plainly fake (merchant words, round amounts).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance.guardian import (
    ActionKind,
    AmountLimit,
    CategoryCap,
    GuardianAction,
    GuardianRules,
    LedgerState,
    evaluate,
    gate,
    loosening,
    payload_hash,
)
from anum_api.finance.models import Category
from anum_api.finance.store import finance_store

TENANT, WORKSPACE = "tenant_grd", "workspace_grd"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_grd", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_grd", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_grd", "x-user-roles": "viewer"}
API = "/api/v1/finance"
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    from anum_api.dependencies import memory_repository

    finance_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()


def _apply(rules: dict[str, object], headers: dict[str, str] = OWNER) -> dict[str, object]:
    change = client.post(f"{API}/guardian/changes", headers=headers, json={"rules": rules}).json()
    done = client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=headers, json={"payload_hash": change["payload_hash"]})
    assert done.status_code == 200, done.text
    return change


# -- paused kinds ------------------------------------------------------------------------------------


def test_a_paused_kind_is_kept_but_not_applied_and_pausing_loosens() -> None:
    rules = GuardianRules(max_single=[AmountLimit(currency="AED", amount=Decimal("100"))], blocked_merchants=["casino"], paused=["max_single"])
    action = GuardianAction(kind=ActionKind.RECORD, amount=Decimal("500"), currency="AED", merchant="shop", at=NOW)
    assert evaluate(action, rules, LedgerState()).outcome == "allow"
    assert evaluate(action, rules.model_copy(update={"paused": []}), LedgerState()).outcome == "deny"
    # The kernel's own limit is never paused: a real payment is still refused.
    spend = action.model_copy(update={"kind": ActionKind.SPEND})
    assert [r.code for r in gate(spend, rules, LedgerState()).reasons] == ["execution_not_available"]
    assert rules.configured_kinds() == ["max_single", "blocked_merchants"] and rules.active_kinds() == ["blocked_merchants"]
    active = GuardianRules(max_single=[AmountLimit(currency="AED", amount=Decimal("100"))])
    assert loosening(active, active.model_copy(update={"paused": ["max_single"]})) == ["max_single"]
    assert loosening(rules, rules.model_copy(update={"paused": []})) == []  # resuming is stricter
    # Raising a paused limit loosens it too, for when it is resumed.
    raised = GuardianRules(max_single=[AmountLimit(currency="AED", amount=Decimal("900"))], blocked_merchants=["casino"], paused=["max_single"])
    assert loosening(rules, raised) == ["max_single"]


def test_a_pause_names_only_kinds_that_are_set_and_keeps_old_hashes() -> None:
    rules = GuardianRules(category_caps=[CategoryCap(category=Category.DINING, currency="AED", amount=Decimal("10"))],
                          paused=["quiet_hours", "category_caps", "category_caps"])
    assert rules.paused == ["category_caps"]
    with pytest.raises(ValueError):
        GuardianRules.model_validate({"paused": ["send_money"]})
    plain = GuardianRules(blocked_merchants=["casino"])
    assert "paused" not in plain.canonical()
    assert payload_hash(change_id="c", owner_user_id="o", base_version=1, rules=plain) == payload_hash(
        change_id="c", owner_user_id="o", base_version=1, rules=GuardianRules.model_validate({"blocked_merchants": ["casino"]}))
    assert GuardianRules().is_empty()


def test_only_the_person_pauses_and_an_assistant_cannot() -> None:
    _apply({"max_single": [{"currency": "AED", "amount": "100"}]})
    pause = client.post(f"{API}/guardian/changes", headers=OWNER, json={"rules": {"max_single": [{"currency": "AED", "amount": "100"}], "paused": ["max_single"]}})
    assert pause.status_code == 201 and pause.json()["loosens"] == ["max_single"]
    from anum_api.finance.plan_api import propose_change
    from anum_api.finance.store import open_finance_store
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_grd")
    with open_finance_store(context) as store, pytest.raises(PermissionError):
        propose_change(store, context, GuardianRules.model_validate(pause.json()["rules"]), proposed_by="agent", proposer="agent_x", reason=None, now=NOW)


# -- the review queue --------------------------------------------------------------------------------


def _ledger() -> tuple[str, str]:
    account = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank"}).json()
    today = client.get(f"{API}/summary", headers=OWNER).json()["today"]
    return account["id"], today


def _pay(account: str, day: str, amount: str, description: str, category: str | None = None) -> str:
    body = {"account_id": account, "posted_on": day, "amount": amount, "description": description, **({"category": category} if category else {})}
    created = client.post(f"{API}/transactions", headers=OWNER, json=body)
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def test_the_queue_lists_flagged_payments_with_what_is_typical_and_rule_stats() -> None:
    account, today = _ledger()
    month = today[:7]
    earlier = f"{int(month[:4]) - (1 if month[5:] == '01' else 0)}-{'12' if month[5:] == '01' else f'{int(month[5:]) - 1:02d}'}-10"
    _pay(account, earlier, "-400", "SUPPLIER ALPHA")
    _pay(account, earlier, "-600", "SUPPLIER ALPHA")
    empty = client.get(f"{API}/guardian/review", headers=OWNER).json()
    assert empty["counts"] == {"flagged": 0, "awaiting": 0, "resolved": 0, "snoozed": 0} and empty["items"] == []
    _apply({"max_single": [{"currency": "AED", "amount": "1000"}], "category_caps": [{"category": "dining", "currency": "AED", "amount": "100"}],
            "approval_above": [{"currency": "AED", "amount": "50"}]})
    big = _pay(account, today, "-1500", "Supplier Alpha")
    _pay(account, today, "-80", "CAFE", "dining")
    over = _pay(account, today, "-40", "CAFE", "dining")
    _pay(account, today, "-20", "GROCER")
    queue = client.get(f"{API}/guardian/review", headers=OWNER)
    assert queue.status_code == 200
    data = queue.json()
    assert data["month"] == month and data["counts"]["flagged"] == 2 and data["counts"]["awaiting"] == 2
    items = {i["transaction_id"]: i for i in data["items"]}
    assert set(items) == {big, over}
    assert items[big]["rules"] == ["max_single"] and items[big]["typical_amount"] == "500.00" and items[big]["prior_count"] == 2
    assert items[big]["account_name"] == "Current" and items[big]["amount"] == "1500.00" and items[big]["state"] == "awaiting"
    # The cafe payment of 80 is under the cap; the next one of 40 takes dining over 100.
    assert items[over]["rules"] == ["category_caps"] and items[over]["typical_amount"] is None and items[over]["prior_count"] == 0
    stats = {s["kind"]: s for s in data["rules"]}
    assert stats["max_single"]["count"] == 1 and stats["max_single"]["last"] == today
    # Approval applies to actions asked for, never to money already recorded.
    assert stats["approval_above"]["count"] == 0
    assert client.get(f"{API}/guardian/review", headers=VIEWER).status_code == 403
    assert client.get(f"{API}/guardian/review", headers=MEMBER).json()["counts"]["flagged"] == 0
    assert client.get(f"{API}/guardian/review?month=2026-13", headers=OWNER).status_code == 422


def test_decisions_resolve_snooze_reopen_and_follow_corrections() -> None:
    from anum_api.dependencies import memory_repository

    account, today = _ledger()
    _apply({"max_single": [{"currency": "AED", "amount": "1000"}], "blocked_merchants": ["casino"]})
    big = _pay(account, today, "-1500", "BIG SHOP")
    bet = _pay(account, today, "-20", "CASINO NIGHT")
    assert client.post(f"{API}/guardian/review/{big}", headers=VIEWER, json={"decision": "expected"}).status_code == 403
    assert client.post(f"{API}/guardian/review/{big}", headers=MEMBER, json={"decision": "expected"}).status_code == 404
    assert client.post(f"{API}/guardian/review/{big}", headers=OWNER, json={"decision": "snoozed"}).status_code == 422
    assert client.post(f"{API}/guardian/review/{big}", headers=OWNER, json={"decision": "expected", "snooze_days": 3}).status_code == 422
    assert client.post(f"{API}/guardian/review/{big}", headers=OWNER, json={"decision": "snoozed", "snooze_days": 2}).status_code == 422
    marked = client.post(f"{API}/guardian/review/{big}", headers=OWNER, json={"decision": "expected"})
    assert marked.status_code == 200 and marked.json()["state"] == "expected"
    snoozed = client.post(f"{API}/guardian/review/{bet}", headers=OWNER, json={"decision": "snoozed", "snooze_days": 7})
    assert snoozed.json()["state"] == "snoozed" and snoozed.json()["snoozed_until"]
    counts = client.get(f"{API}/guardian/review?state=all", headers=OWNER).json()["counts"]
    assert counts == {"flagged": 2, "awaiting": 0, "resolved": 1, "snoozed": 1}
    assert [i["transaction_id"] for i in client.get(f"{API}/guardian/review?state=resolved", headers=OWNER).json()["items"]] == [big]
    reopened = client.post(f"{API}/guardian/review/{bet}", headers=OWNER, json={"decision": "reopen"})
    assert reopened.json()["state"] == "awaiting"
    # A corrected amount waits for review again: the decision was about the payment as it was.
    client.patch(f"{API}/transactions/{big}", headers=OWNER, json={"amount": "-1700"})
    awaiting = client.get(f"{API}/guardian/review", headers=OWNER).json()
    assert {i["transaction_id"] for i in awaiting["items"]} == {big, bet}
    income = _pay(account, today, "500", "SALARY")
    assert client.post(f"{API}/guardian/review/{income}", headers=OWNER, json={"decision": "expected"}).status_code == 422
    audits = [r for r in memory_repository.store.audit_records if r.action == "finance.guardian.review"]
    assert len(audits) == 3 and {r.metadata["decision"] for r in audits} == {"expected", "snoozed", "reopen"}
    for record in audits:
        assert "1500" not in str(record.metadata) and "CASINO" not in str(record.metadata).upper()


def test_a_paused_kind_flags_nothing_and_pausing_everything_empties_the_queue() -> None:
    account, today = _ledger()
    rules = {"max_single": [{"currency": "AED", "amount": "1000"}]}
    _apply(rules)
    _pay(account, today, "-1500", "BIG SHOP")
    assert client.get(f"{API}/guardian/review", headers=OWNER).json()["counts"]["flagged"] == 1
    assert len(client.get(f"{API}/guardian/alerts", headers=OWNER).json()["alerts"]) == 1
    _apply({**rules, "paused": ["max_single"]})
    data = client.get(f"{API}/guardian/review", headers=OWNER).json()
    assert data["counts"]["flagged"] == 0 and data["rules"] == [{"kind": "max_single", "count": 0, "last": None}]
    assert client.get(f"{API}/guardian/alerts", headers=OWNER).json()["alerts"] == []
    assert client.get(f"{API}/guardian", headers=OWNER).json()["rules"]["paused"] == ["max_single"]


def test_the_activity_log_shows_only_your_own_guardian_records_and_pages() -> None:
    from anum_api.dependencies import memory_repository

    _apply({"blocked_merchants": ["casino"]})
    _apply({"blocked_merchants": ["casino", "lottery"]})
    _apply({"blocked_merchants": ["casino", "lottery"]}, MEMBER)
    first = client.get(f"{API}/guardian/activity?limit=3", headers=OWNER)
    assert first.status_code == 200
    page = first.json()
    assert len(page["items"]) == 3 and page["next_cursor"]
    assert all(i["action"].startswith("finance.guardian.") for i in page["items"])
    rest = client.get(f"{API}/guardian/activity?limit=3&cursor={page['next_cursor']}", headers=OWNER).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    owners = {r.id for r in memory_repository.store.audit_records if r.actor == "owner_grd" and r.action.startswith("finance.guardian.")}
    assert {i["id"] for i in page["items"] + rest["items"]} == owners
    assert client.get(f"{API}/guardian/activity", headers=VIEWER).status_code == 403
    assert client.get(f"{API}/guardian/activity?cursor=@@", headers=OWNER).status_code == 400


def test_privacy_exports_and_deletes_review_decisions() -> None:
    account, today = _ledger()
    _apply({"max_single": [{"currency": "AED", "amount": "10"}]})
    paid = _pay(account, today, "-50", "SHOP")
    client.post(f"{API}/guardian/review/{paid}", headers=OWNER, json={"decision": "reviewed"})
    export = client.get("/api/v1/privacy/export", headers=OWNER).json()["finance"]
    assert [r["transaction_id"] for r in export["guardian_reviews"]] == [paid] and "owner_user_id" not in export["guardian_reviews"][0]
    assert client.post("/api/v1/privacy/delete", headers=OWNER, json={"confirm": "DELETE MY DATA"}).status_code == 200
    assert finance_store.reviews == {}


def test_a_decision_keeps_the_cumulative_reasons_the_queue_showed() -> None:
    from anum_api.dependencies import memory_repository

    account, today = _ledger()
    _apply({"category_caps": [{"category": "dining", "currency": "AED", "amount": "100"}],
            "max_daily_total": [{"currency": "AED", "amount": "110"}]})
    _pay(account, today, "-80", "CAFE", "dining")
    over = _pay(account, today, "-40", "CAFE", "dining")
    shown = next(i for i in client.get(f"{API}/guardian/review", headers=OWNER).json()["items"] if i["transaction_id"] == over)
    assert {r["code"] for r in shown["reasons"]} == {"over_category_cap", "over_daily_total"}
    assert client.post(f"{API}/guardian/review/{over}", headers=OWNER, json={"decision": "expected"}).status_code == 200
    record = next(r for r in memory_repository.store.audit_records if r.action == "finance.guardian.review")
    assert sorted(record.metadata["codes"]) == ["over_category_cap", "over_daily_total"]
