"""Tamara refunds on the installment statement (docs/finance.md#tamara-refunds).

In-memory stores. Fixtures are plainly fake: lowercase "example" order numbers, round amounts.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import advisor
from anum_api.finance.installment_refunds import plan_refunds, proportional_estimate
from anum_api.finance.models import Bill, CategorySource, InstallmentRefundNotice, InstallmentStatementEntry, Transaction
from anum_api.finance.store import finance_store, open_finance_store
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_refund", "workspace_refund"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_refund", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_refund", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_refund", "x-user-roles": "viewer"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_refund", roles=["owner"])
API = "/api/v1/finance"
client = TestClient(main.app)
TODAY = date.today()
ORDER = "example-order-a"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    yield
    finance_store.clear()


def due(n: int) -> date:
    # Installment 1 was paid at purchase; the others fall due from tomorrow, a few days apart.
    return TODAY - timedelta(days=30) if n == 1 else TODAY + timedelta(days=n - 1)


def entry(n: int, *, amount: str = "100.00", status: str | None = None, order: str = ORDER, total: int = 4, **extra: object) -> dict:
    return {"provider": "Tamara", "order_id": order, "merchant": "Example Shop", "purchase_on": due(1).isoformat(), "due_on": due(n).isoformat(),
            "installment": n, "total_installments": total, "amount": amount, "currency": "SAR",
            "source_status": status or ("paid" if n == 1 else "scheduled"), "source_email": None, "review_notes": None, **extra}


def notice(ref: str, *, amount: str | None, full: bool = False, order: str = ORDER, days_ago: int = 2) -> dict:
    return {"provider": "Tamara", "order_id": order, "amount": amount, "currency": "SAR", "full": full,
            "received_on": (TODAY - timedelta(days=days_ago)).isoformat(), "message_ref": ref,
            "source_email": "https://mail.google.com/mail/u/0/#all/" + ref}


def sync(entries: list[dict], notices: list[dict] | None = None, headers: dict = OWNER) -> dict:
    response = client.post(f"{API}/installment-statement/sync", headers=headers, json={"entries": entries, "refund_notices": notices or []})
    assert response.status_code == 200, response.text
    return response.json()


def bills_for_statement(order: str = ORDER, parts: tuple[int, ...] = (2, 3, 4), amount: str = "100.00") -> dict[int, str]:
    found = {}
    for n in parts:
        made = client.post(f"{API}/bills", headers=OWNER, json={
            "name": f"Tamara | Example Shop | {n}", "amount": amount, "currency": "SAR", "cadence": "once", "next_due_on": due(n).isoformat(),
            "category": "shopping", "active": True, "source": "manual",
            "notes": f"Order: {order} | Installment: {n} | Provider-reported scheduled; no bank match confirmed."})
        assert made.status_code == 201, made.text
        found[n] = made.json()["id"]
    return found


def preview() -> dict:
    response = client.get(f"{API}/installment-refunds", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def order_plan(body: dict, order: str = ORDER) -> dict:
    return next(o for o in body["orders"] if o["order_id"] == order)


def apply(plan: dict, headers: dict = OWNER):
    return client.post(f"{API}/installment-refunds/apply", headers=headers,
                       json={"orders": [{"provider": plan["provider"], "order_id": plan["order_id"], "plan_hash": plan["plan_hash"]}]})


def by_installment(plan: dict) -> dict[int, dict]:
    return {i["installment"]: i for i in plan["installments"]}


def bill(bill_id: str) -> dict:
    return client.get(f"{API}/bills/{bill_id}", headers=OWNER).json()


def statement_rows() -> dict[int, dict]:
    return {r["installment"]: r for r in client.get(f"{API}/installment-statement", headers=OWNER).json()["items"] if r["order_id"] == ORDER}


# -- case 2: full refund, or refunds that cover everything unpaid ---------------------------------------------------------


def test_full_refund_email_cancels_every_unpaid_installment_and_paid_money_comes_back() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-full", amount="400.00", full=True)])
    ids = bills_for_statement()
    plan = order_plan(preview())
    assert plan["mode"] == "cancel_all" and plan["state"] == "refunded" and plan["applied_state"] == "active"
    assert plan["applicable"] and plan["pending"]
    assert plan["refund_total"] == "400.00" and plan["credit_back"] == "100.00" and plan["credit_received"] == "0.00"
    assert plan["outstanding_before"] == "300.00" and plan["outstanding_after"] == "0.00" and plan["next_due_after"] is None
    rows = by_installment(plan)
    assert rows[1]["state_after"] == "paid" and rows[1]["refunded_after"] == "0.00"
    assert [rows[n]["state_after"] for n in (2, 3, 4)] == ["refunded"] * 3
    assert {e["code"] for e in plan["evidence"]} == {"email_full"}

    applied = apply(plan)
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["applied"] == 1 and result["installments"] == 3 and result["bills_paused"] == 3 and result["bills_lowered"] == 0
    assert all(not bill(ids[n])["active"] for n in (2, 3, 4))
    saved = statement_rows()
    assert saved[1]["source_status"] == "paid" and saved[1]["refunded_amount"] == "0"
    assert [saved[n]["refunded_amount"] for n in (2, 3, 4)] == ["100.00"] * 3
    again = order_plan(result["preview"])
    assert not again["pending"] and again["state"] == "refunded" and again["applied_state"] == "refunded"
    # Applying the same plan twice is refused, never applied twice.
    assert apply(again).status_code == 422


def test_refund_larger_than_what_is_unpaid_cancels_all_and_the_rest_goes_back_to_the_card() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-big", amount="350.00")])
    plan = order_plan(preview())
    assert plan["mode"] == "cancel_all" and plan["applicable"]
    assert plan["refund_total"] == "350.00" and plan["credit_back"] == "50.00"
    assert [by_installment(plan)[n]["due_after"] for n in (2, 3, 4)] == ["0.00"] * 3


def test_two_refunds_on_one_order_add_up_and_a_repeated_email_counts_once() -> None:
    notices = [notice("m-one", amount="100.00", days_ago=10), notice("m-one-again", amount="100.00", days_ago=9), notice("m-two", amount="200.00", days_ago=2)]
    sync([entry(n) for n in (1, 2, 3, 4)], notices)
    plan = order_plan(preview())
    codes = sorted(e["code"] for e in plan["evidence"])
    assert codes == ["email_amount", "email_amount", "email_duplicate"]
    assert plan["refund_total"] == "300.00" and plan["mode"] == "cancel_all" and plan["credit_back"] == "0.00"


def test_ledger_credit_that_matches_the_refund_email_shows_money_back_received() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-full", amount="400.00", full=True, days_ago=5)])
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR"}).json()
    made = client.post(f"{API}/transactions", headers=OWNER, json={
        "account_id": acct["id"], "posted_on": (TODAY - timedelta(days=3)).isoformat(), "amount": "100.00",
        "description": "TAMARA REFUND EXAMPLE", "category": "refunds"})
    assert made.status_code == 201, made.text
    plan = order_plan(preview())
    # The 400 refund counts once; the 100 credit back is not a second refund.
    assert plan["refund_total"] == "400.00" and plan["credit_back"] == "100.00"
    money_back = [e for e in plan["evidence"] if e["kind"] == "ledger"]
    assert [(e["role"], e["code"], e["strength"]) for e in money_back] == [("money_back", "ledger_matches_email", "strong")]
    assert plan["credit_received"] == "100.00"


# -- case 1: Tamara's own new schedule wins -------------------------------------------------------------------------------


def test_statement_lower_amount_on_reimport_is_applied_exactly() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)])
    ids = bills_for_statement()
    result = sync([entry(4, amount="40.00")])
    assert result["refund_signals"] and not result["conflicts"]
    assert statement_rows()[4]["amount"] == "100.00" and statement_rows()[4]["reported_amount"] == "40.00"
    plan = order_plan(preview())
    assert plan["mode"] == "schedule" and plan["applicable"] and plan["state"] == "partly_refunded"
    rows = by_installment(plan)
    assert rows[4]["due_after"] == "40.00" and rows[4]["state_after"] == "partly_refunded"
    assert rows[2]["due_after"] == "100.00" and rows[3]["due_after"] == "100.00"
    applied = apply(plan).json()
    assert applied["bills_lowered"] == 1 and applied["bills_paused"] == 0
    assert bill(ids[4])["amount"] == "40.00" and bill(ids[4])["active"]
    assert bill(ids[2])["amount"] == "100.00"


def test_statement_marking_installments_refunded_cancels_exactly_those() -> None:
    sync([entry(1), entry(2), entry(3, status="refunded"), entry(4, status="refunded")], [notice("m-part", amount="200.00")])
    plan = order_plan(preview())
    assert plan["mode"] == "schedule" and plan["applicable"]
    rows = by_installment(plan)
    assert [rows[n]["state_after"] for n in (2, 3, 4)] == ["due", "refunded", "refunded"]
    assert plan["refund_total"] == "200.00" and plan["outstanding_after"] == "100.00"


# -- case 3: partial refund without a schedule: estimate only ----------------------------------------------------------------


def test_partial_refund_without_a_new_schedule_is_an_estimate_that_needs_review() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-part", amount="150.00")])
    ids = bills_for_statement()
    plan = order_plan(preview())
    assert plan["mode"] == "estimate" and plan["state"] == "needs_review" and "estimate_only" in plan["reasons"]
    assert not plan["applicable"] and not plan["pending"]
    rows = by_installment(plan)
    # Tamara's Key Fact Statement: spread over the upcoming installments in proportion; never one cancelled.
    assert [rows[n]["estimate_due"] for n in (2, 3, 4)] == ["50.00", "50.00", "50.00"]
    assert [rows[n]["due_after"] for n in (2, 3, 4)] == ["100.00"] * 3
    assert all(rows[n]["state_after"] == "due" for n in (2, 3, 4))
    refused = apply(plan)
    assert refused.status_code == 422 and "estimate" in refused.json()["error"]["message"]
    assert all(bill(ids[n])["active"] and bill(ids[n])["amount"] == "100.00" for n in (2, 3, 4))


def test_proportional_estimate_adds_up_to_the_refund_in_minor_units() -> None:
    rows = [InstallmentStatementEntry(**entry(n, amount=a)) for n, a in ((2, "100.00"), (3, "100.00"), (4, "100.01"))]
    shares = proportional_estimate(rows, Decimal("100.00"), "SAR")
    assert sum(shares.values()) == Decimal("100.00")
    assert all(v.as_tuple().exponent == -2 for v in shares.values())


# -- weak evidence ---------------------------------------------------------------------------------------------------------------


def test_amount_only_ledger_credit_without_order_number_stays_in_review() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)])
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR"}).json()
    client.post(f"{API}/transactions", headers=OWNER, json={"account_id": acct["id"], "posted_on": TODAY.isoformat(), "amount": "100.00",
                                                           "description": "TAMARA CREDIT", "category": "refunds"})
    plan = order_plan(preview())
    assert plan["state"] == "needs_review" and not plan["applicable"]
    assert [e["code"] for e in plan["evidence"]] == ["ledger_amount_only"]
    assert all(e["strength"] == "weak" for e in plan["evidence"])


def test_ledger_credit_naming_the_order_is_strong_evidence() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)])
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR"}).json()
    client.post(f"{API}/transactions", headers=OWNER, json={"account_id": acct["id"], "posted_on": TODAY.isoformat(), "amount": "300.00",
                                                           "description": f"TAMARA REFUND {ORDER.upper()}", "category": "refunds"})
    plan = order_plan(preview())
    assert plan["evidence"][0]["code"] == "ledger_order" and plan["evidence"][0]["strength"] == "strong"
    assert plan["mode"] == "cancel_all" and plan["applicable"]


def test_refund_email_without_an_amount_and_no_evidence_at_all() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)] + [entry(1, order="example-order-b", total=1, status="scheduled")], [notice("m-vague", amount=None)])
    body = preview()
    vague = order_plan(body)
    assert vague["state"] == "needs_review" and "email_without_amount" in vague["reasons"] and not vague["applicable"]
    quiet = order_plan(body, "example-order-b")
    assert quiet["state"] == "active" and quiet["evidence"] == [] and not quiet["pending"]


def test_more_refund_than_the_order_total_is_never_applied() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-a", amount="300.00", days_ago=10), notice("m-b", amount="300.00", days_ago=1)])
    plan = order_plan(preview())
    assert "more_than_order" in plan["reasons"] and not plan["applicable"] and plan["state"] == "needs_review"


# -- saving: reimport, stale plans, undo, permissions ---------------------------------------------------------------------------


def test_reimport_keeps_the_applied_refund_and_ignores_uploaded_refund_fields() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-full", amount="400.00", full=True)])
    assert apply(order_plan(preview())).status_code == 200
    again = sync([entry(n) for n in (1, 2, 3, 4)])
    assert again["conflicts"] == []
    rows = statement_rows()
    assert [rows[n]["refunded_amount"] for n in (2, 3, 4)] == ["100.00"] * 3
    noted = sync([{**entry(2), "review_notes": "a new note", "refunded_amount": "0"}])
    assert noted["updated"] == 1
    assert statement_rows()[2]["refunded_amount"] == "100.00" and statement_rows()[2]["review_notes"] == "a new note"
    # A client cannot apply a refund through the import.
    sync([entry(1, order="example-order-c", total=1, status="scheduled", refunded_amount="50.00")])
    other = client.get(f"{API}/installment-statement", headers=OWNER).json()["items"]
    assert next(r for r in other if r["order_id"] == "example-order-c")["refunded_amount"] == "0"
    assert not order_plan(preview())["pending"]


def test_stale_plan_hash_is_refused_and_undo_puts_everything_back() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-full", amount="400.00", full=True)])
    ids = bills_for_statement()
    plan = order_plan(preview())
    stale = client.post(f"{API}/installment-refunds/apply", headers=OWNER,
                        json={"orders": [{"provider": "Tamara", "order_id": ORDER, "plan_hash": "0" * 64}]})
    assert stale.status_code == 409 and "changed" in stale.json()["error"]["message"]
    record_id = apply(plan).json()["record_ids"][0]
    body = preview()
    assert body["records"][0]["id"] == record_id and body["records"][0]["can_undo"]
    undone = client.post(f"{API}/installment-refunds/{record_id}/undo", headers=OWNER)
    assert undone.status_code == 200, undone.text
    assert all(bill(ids[n])["active"] for n in (2, 3, 4))
    assert [statement_rows()[n]["refunded_amount"] for n in (2, 3, 4)] == ["0.00"] * 3
    assert order_plan(undone.json())["pending"]
    assert client.post(f"{API}/installment-refunds/{record_id}/undo", headers=OWNER).status_code == 409
    assert client.post(f"{API}/installment-refunds/fin_refund_missing/undo", headers=OWNER).status_code == 404


def test_only_the_owner_sees_and_applies_their_refunds() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-full", amount="400.00", full=True)])
    plan = order_plan(preview())
    assert client.get(f"{API}/installment-refunds", headers=MEMBER).json()["orders"] == []
    assert client.get(f"{API}/installment-refunds", headers=VIEWER).status_code in (401, 403)
    assert apply(plan, headers=MEMBER).status_code == 404
    assert apply(plan, headers=VIEWER).status_code in (401, 403)


# -- downstream readers: refunded installments are not due anywhere ------------------------------------------------------------


def test_refunded_installments_drop_out_of_bills_due_safe_to_spend_and_the_advisor() -> None:
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR"}).json()
    client.post(f"{API}/transactions", headers=OWNER, json={"account_id": acct["id"], "posted_on": TODAY.isoformat(), "amount": "5000.00",
                                                           "description": "SALARY EXAMPLE", "category": "income"})
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-full", amount="400.00", full=True)])
    ids = set(bills_for_statement().values())

    def visible() -> dict[str, set[str]]:
        schedule = client.get(f"{API}/bill-schedule", headers=OWNER,
                              params={"from": TODAY.isoformat(), "to": (TODAY + timedelta(days=10)).isoformat()}).json()
        summary = client.get(f"{API}/summary", headers=OWNER).json()
        safe = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
        with open_finance_store(CONTEXT) as store:
            bills = [b for b in store.list_items(CONTEXT, "bill") if isinstance(b, Bill)]
        coming = advisor._coming(TODAY, bills, [], set(), [], "SAR")
        return {
            "schedule": {i["bill_id"] for i in schedule["items"]} & ids,
            "summary": {i["id"] for i in summary["upcoming_bills"]} & ids,
            "safe": {line["ref_id"] for c in safe["currencies"] for line in c["lines"] if line["kind"] == "bill"} & ids,
            "advisor": {i.ref_id for i in coming.items} & ids,
        }

    before = visible()
    assert before["schedule"] == ids and before["summary"] == ids and before["advisor"] == ids
    if due(2).month == TODAY.month:
        assert before["safe"]  # safe-to-spend counts the bills due before the month ends
    assert apply(order_plan(preview())).status_code == 200
    after = visible()
    assert after == {"schedule": set(), "summary": set(), "safe": set(), "advisor": set()}


def test_pure_plan_never_marks_a_refund_without_evidence() -> None:
    rows = [InstallmentStatementEntry(**entry(n)) for n in (1, 2, 3, 4)]
    plans = plan_refunds(rows, [], [])
    assert plans[0].state == "active" and not plans[0].pending and plans[0].refund_total == 0
    other_currency = InstallmentRefundNotice(order_id=ORDER, amount=Decimal("400"), currency="AED", full=True,
                                             received_on=TODAY, message_ref="m-aed")
    assert plan_refunds(rows, [other_currency], [])[0].state == "active"
    credit = Transaction(id="fin_txn_x", tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id="owner_refund", account_id="a", posted_on=TODAY,
                         amount=Decimal("-100.00"), currency="SAR", description="TAMARA PAYMENT", category="shopping",
                         category_source=CategorySource.RULE, dedupe_hash="x", created_at=datetime.now(timezone.utc),
                         updated_at=datetime.now(timezone.utc))
    assert plan_refunds(rows, [], [credit])[0].evidence == []


# -- review fixes (PR 412) -------------------------------------------------------------------------------------------------------


def test_card_credit_naming_the_order_that_is_the_paid_share_of_an_emailed_refund_is_money_back_not_a_second_refund() -> None:
    # 100 paid, 300 unpaid; a 200 refund email and the 100 paid share coming back to the card, naming the order.
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-part", amount="200.00", days_ago=5)])
    acct = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR"}).json()
    made = client.post(f"{API}/transactions", headers=OWNER, json={
        "account_id": acct["id"], "posted_on": (TODAY - timedelta(days=3)).isoformat(), "amount": "100.00",
        "description": f"TAMARA REFUND {ORDER.upper()}", "category": "refunds"})
    assert made.status_code == 201, made.text
    plan = order_plan(preview())
    ledger = [e for e in plan["evidence"] if e["kind"] == "ledger"]
    assert [(e["role"], e["code"]) for e in ledger] == [("money_back", "ledger_matches_email")]
    # Only the 200 email counts: a partial refund, so an estimate that stays in review, never a full cancel.
    assert plan["refund_total"] == "200.00" and plan["mode"] == "estimate" and not plan["applicable"]


def test_tamaras_revised_schedule_wins_over_the_amount_based_full_cancel() -> None:
    # 100 paid, 300 unpaid; a 300 refund email, but Tamara's revised statement leaves 25 due on each unpaid installment.
    sync([entry(n) for n in (1, 2, 3, 4)], [notice("m-300", amount="300.00")])
    sync([entry(n, amount="25.00") for n in (2, 3, 4)])
    plan = order_plan(preview())
    assert plan["mode"] == "schedule" and plan["applicable"]
    assert [by_installment(plan)[n]["due_after"] for n in (2, 3, 4)] == ["25.00"] * 3
    assert plan["outstanding_after"] == "75.00" and "statement_differs" in plan["reasons"]


def test_a_notice_only_sync_saves_refund_evidence_for_an_existing_order() -> None:
    sync([entry(n) for n in (1, 2, 3, 4)])
    response = client.post(f"{API}/installment-statement/sync", headers=OWNER,
                           json={"entries": [], "refund_notices": [notice("m-full", amount="400.00", full=True)]})
    assert response.status_code == 200, response.text
    assert response.json()["notices_saved"] == 1 and response.json()["created"] == 0
    assert order_plan(preview())["mode"] == "cancel_all"
    empty = client.post(f"{API}/installment-statement/sync", headers=OWNER, json={"entries": [], "refund_notices": []})
    assert empty.status_code == 422
