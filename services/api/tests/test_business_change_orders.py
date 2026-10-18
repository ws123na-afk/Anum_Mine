"""Business change orders, the usage and cost ledger and project profitability
(docs/business.md#change-orders), against the in-memory backend.

Covers owner-only approval bound to the content hash, immutability of accepted and amended
versions, totals and rounding reconciliation, milestone adjustments, stale change orders,
the ledger (labour, costs, AI usage from the model budget), the profitability view with
revenue and cash collected kept apart, permissions and cross-workspace and cross-tenant
isolation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.changes import ChangeError, apply_change, change_hash
from anum_api.business.models import ChangeOrder, ChangeOrderLine, ProposalVersion
from anum_api.business.service import content_hash, proposal_totals
from anum_api.business.store import business_store
from anum_api.model_budget import UsageDelta, memory_model_budget_store
from anum_api.schemas import TenantContext, utc_now
from anum_api.suppression import suppression_store

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, TENANT, VIEWER, WORKSPACE, approve, lead, ok, proposal_body, qualify

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    yield
    business_store.clear()
    suppression_store.clear()


def project(total: float = 2500, tax: float = 0, start_on: str | None = "2026-11-01") -> dict[str, Any]:
    """A project from an accepted proposal (the owner approved the acceptance)."""
    created = lead()
    qualify(created["lead"]["id"])
    opportunity = ok(client.post(f"{BASE}/opportunities", headers=MEMBER, json={"lead_id": created["lead"]["id"]}), 201)
    body = proposal_body(opportunity["id"], total)
    body["content"]["tax_rate_pct"] = tax
    if tax:
        gross = float(Decimal(str(total)) * (1 + Decimal(str(tax)) / 100))
        body["content"]["milestones"] = [{"title": "Deposit", "amount": round(gross / 2, 2), "due_in_days": 0},
                                         {"title": "Handover", "amount": round(gross - round(gross / 2, 2), 2), "due_in_days": 14}]
    proposal = ok(client.post(f"{BASE}/proposals", headers=MEMBER, json=body), 201)
    pending = ok(client.post(f"{BASE}/proposals/{proposal['id']}/request-acceptance", headers=MEMBER))
    assert approve(pending["approval"]).status_code == 200
    payload: dict[str, Any] = {"proposal_id": proposal["id"]}
    if start_on:
        payload["start_on"] = start_on
    return ok(client.post(f"{BASE}/projects", headers=MEMBER, json=payload), 201)


def draft(project_id: str, headers: dict[str, str] = MEMBER, **body: Any) -> dict[str, Any]:
    payload = {"title": "Second dashboard", "request": "Client asked for a fleet fuel dashboard.", "requested_by": "Sara Khan",
               "lines": [{"description": "Fuel dashboard", "kind": "service", "quantity": 1, "unit_price": 800}], "timeline_days": 10,
               "internal_cost_delta": 300, **body}
    return ok(client.post(f"{BASE}/projects/{project_id}/change-orders", headers=headers, json=payload), 201)


def submit(change_id: str, headers: dict[str, str] = MEMBER) -> dict[str, Any]:
    return ok(client.post(f"{BASE}/change-orders/{change_id}/submit", headers=headers))


def decide(change: dict[str, Any], headers: dict[str, str] = OWNER) -> Any:
    return client.post(f"{BASE}/change-orders/{change['id']}/approve", headers=headers, json={"content_hash": change["content_hash"]})


# -- pure rules ------------------------------------------------------------------------------------


def _version(**content: Any) -> ProposalVersion:
    now = utc_now()
    base = {"currency": "SAR", "deliverables": ["Dashboard"], "acceptance_criteria": ["Walkthrough"], "tax_rate_pct": "15",
            "line_items": [{"description": "Dashboard", "quantity": "3", "unit_price": "333.33"}],
            "milestones": [{"title": "All", "amount": "1149.99"}], "internal_cost": "100", **content}
    version = ProposalVersion.model_validate({**base, "id": "v1", "tenant_id": TENANT, "workspace_id": WORKSPACE, "proposal_id": "p1", "version": 1,
                                              "content_hash": "x", "locked": True, "created_by": "o", "created_at": now})
    return version.model_copy(update={"content_hash": content_hash(version)})


def _order(**fields: Any) -> ChangeOrder:
    now = utc_now()
    return ChangeOrder.model_validate({"id": "c1", "tenant_id": TENANT, "workspace_id": WORKSPACE, "project_id": "pr1", "proposal_id": "p1", "account_id": "a1",
                                       "number": 1, "title": "Extra", "created_by": "o", "created_at": now, "updated_at": now, **fields})


def test_apply_change_reconciles_totals_and_rounding_in_decimal() -> None:
    base = _version()
    assert proposal_totals(base)["total"] == Decimal("1149.99")  # 999.99 + tax 149.9985 rounded half up to 150.00
    applied = apply_change(base, _order(lines=[ChangeOrderLine(description="Extra", quantity=Decimal("1"), unit_price=Decimal("0.05"))]), set())
    after = proposal_totals(applied.content)
    assert after["subtotal"] == Decimal("1000.04") and after["tax"] == Decimal("150.01") and after["total"] == Decimal("1150.05")
    assert applied.subtotal_delta == Decimal("0.05") and applied.tax_delta == Decimal("0.01") and applied.total_delta == Decimal("0.06")
    # The added milestone carries the delta so milestones still add up to the new total.
    assert applied.added is not None and applied.added.amount == Decimal("0.06")
    assert sum((item.amount for item in applied.content.milestones), Decimal(0)) == after["total"]
    # The base version object is untouched.
    assert len(base.line_items) == 1 and base.milestones[0].amount == Decimal("1149.99")


def test_apply_change_removals_must_match_and_reductions_skip_settled_milestones() -> None:
    base = _version(tax_rate_pct="0", line_items=[{"description": "A", "quantity": "1", "unit_price": "600"}, {"description": "B", "quantity": "1", "unit_price": "400"}],
                    milestones=[{"title": "One", "amount": "500"}, {"title": "Two", "amount": "500"}])
    with pytest.raises(ChangeError, match="must match a line exactly"):
        apply_change(base, _order(lines=[ChangeOrderLine(action="remove", description="B", quantity=Decimal("1"), unit_price=Decimal("401"))]), set())
    applied = apply_change(base, _order(lines=[ChangeOrderLine(action="remove", description="b", quantity=Decimal("1"), unit_price=Decimal("400"))]), set())
    assert applied.total_delta == Decimal("-400.00") and applied.reduced == {1: Decimal("100.00")} and applied.added is None
    # The second milestone is paid: the reduction comes out of the first one instead.
    applied = apply_change(base, _order(lines=[ChangeOrderLine(action="remove", description="B", quantity=Decimal("1"), unit_price=Decimal("400"))]), {1})
    assert applied.reduced == {0: Decimal("100.00")}
    with pytest.raises(ChangeError, match="more than the milestones not yet accepted"):
        apply_change(base, _order(lines=[ChangeOrderLine(action="remove", description="A", quantity=Decimal("1"), unit_price=Decimal("600"))]), {1})
    with pytest.raises(ChangeError, match="changes the scope"):
        apply_change(base, _order(), set())
    with pytest.raises(ChangeError, match="no line items left"):
        apply_change(base, _order(lines=[ChangeOrderLine(action="remove", description="A", quantity=Decimal("1"), unit_price=Decimal("600")),
                                         ChangeOrderLine(action="remove", description="B", quantity=Decimal("1"), unit_price=Decimal("400"))]), set())


def test_change_hash_binds_the_change_and_the_version_it_amends() -> None:
    order = _order(lines=[ChangeOrderLine(description="Extra", unit_price=Decimal("10"))])
    first = change_hash(order, 1, "a" * 64)
    assert first == change_hash(order, 1, "a" * 64) and len(first) == 64
    assert first != change_hash(order, 2, "a" * 64) and first != change_hash(order, 1, "b" * 64)
    assert first != change_hash(order.model_copy(update={"timeline_days": 3}), 1, "a" * 64)


# -- change orders over the API --------------------------------------------------------------------


def test_main_path_draft_submit_owner_approves_new_immutable_version() -> None:
    started = project()
    proposal_id = started["proposal_id"]
    accepted_before = ok(client.get(f"{BASE}/proposals/{proposal_id}/versions/1", headers=MEMBER))
    change = draft(started["id"])
    assert change["status"] == "draft" and change["number"] == 1 and change["total_delta"] == 800.0 and change["issues"] == []
    assert change["content_hash"] is None and change["current_version"] == 1
    edited = ok(client.patch(f"{BASE}/change-orders/{change['id']}", headers=MEMBER, json={"timeline_days": 12}))
    assert edited["timeline_days"] == 12
    sent = submit(change["id"])
    assert sent["status"] == "pending_approval" and sent["base_version"] == 1 and len(sent["content_hash"]) == 64
    assert sent["base_hash"] == accepted_before["content_hash"] and sent["submitted_by"] == "member_biz"
    # A sent change order does not change; approval needs an owner and the hash they were shown.
    assert client.patch(f"{BASE}/change-orders/{change['id']}", headers=MEMBER, json={"title": "Other"}).status_code == 409
    denied = decide(sent, MEMBER)
    assert denied.status_code == 403 and "Only a workspace owner" in denied.json()["error"]["message"]
    wrong = client.post(f"{BASE}/change-orders/{change['id']}/approve", headers=OWNER, json={"content_hash": "0" * 64})
    assert wrong.status_code == 409 and "not the one you were shown" in wrong.json()["error"]["message"]
    approved = ok(decide(sent))
    assert approved["status"] == "approved" and approved["result_version"] == 2 and approved["decided_by"] == "owner_biz" and approved["decided_at"]
    assert decide(sent).status_code == 409  # decided once
    # The accepted version is byte for byte what it was; version 2 is new, locked and names the change order.
    assert ok(client.get(f"{BASE}/proposals/{proposal_id}/versions/1", headers=MEMBER)) == accepted_before
    amended = ok(client.get(f"{BASE}/proposals/{proposal_id}/versions/2", headers=MEMBER))
    assert amended["locked"] and amended["change_order_id"] == change["id"] and amended["total"] == 3300.0 and amended["internal_cost"] == 1500.0
    assert [m["title"] for m in amended["milestones"]] == ["Deposit", "Handover", "Change order 1: Second dashboard"]
    proposal = ok(client.get(f"{BASE}/proposals/{proposal_id}", headers=MEMBER))
    assert proposal["status"] == "accepted" and proposal["accepted_version"] == 1 and proposal["current_version"] == 2
    assert proposal["accepted_hash"] == accepted_before["content_hash"]
    assert [item["change_order_id"] for item in proposal["versions"]] == [change["id"], None]
    # The accepted proposal still refuses direct edits.
    assert client.put(f"{BASE}/proposals/{proposal_id}/content", headers=OWNER, json=proposal_body("x", 1)["content"]).status_code == 409
    after = ok(client.get(f"{BASE}/projects/{started['id']}", headers=MEMBER))
    assert after["contract_total"] == 3300.0 and after["proposal_version"] == 2
    assert after["milestones"][2]["amount"] == 800.0 and after["milestones"][2]["due_on"] == "2026-11-27"  # 14 + 12 days after start
    exported = client.get(f"{BASE}/proposals/{proposal_id}/export", headers=MEMBER)
    assert "Total: SAR 3300.00" in exported.text and "Amends accepted version 1" in exported.text
    trail = {item.kind for item in business_store._rows["activities"].values() if item.project_id == started["id"]}
    assert {"change_order_created", "change_order_submitted", "change_order_approved"} <= trail
    listed = ok(client.get(f"{BASE}/projects/{started['id']}/change-orders", headers=MEMBER))
    assert listed["total"] == 1 and listed["items"][0]["status"] == "approved"


def test_reduction_lowers_unsettled_milestones_and_totals_reconcile_with_tax() -> None:
    started = project(total=1000, tax=15)
    assert started["contract_total"] == 1150.0
    deposit = started["milestones"][0]["id"]
    ok(client.patch(f"{BASE}/milestones/{deposit}", headers=MEMBER, json={"paid_amount": 575}))
    change = draft(started["id"], lines=[{"action": "remove", "description": "Reporting Quick Win", "quantity": 1, "unit_price": 1000},
                                          {"action": "add", "description": "Reporting Quick Win, reduced", "quantity": 1, "unit_price": 700.01}],
                   timeline_days=0, internal_cost_delta=-200)
    assert change["subtotal_delta"] == -299.99 and change["tax_delta"] == -45.0 and change["total_delta"] == -344.99
    approved = ok(decide(submit(change["id"])))
    assert approved["status"] == "approved"
    after = ok(client.get(f"{BASE}/projects/{started['id']}", headers=MEMBER))
    # 700.01 + 15% = 805.0115 -> 805.01; the paid deposit is untouched, the handover absorbs the reduction.
    assert after["contract_total"] == 805.01 and [m["amount"] for m in after["milestones"]] == [575.0, 230.01]
    assert sum(Decimal(str(m["amount"])) for m in after["milestones"]) == Decimal("805.01")
    money = ok(client.get(f"{BASE}/projects/{started['id']}/profitability", headers=MEMBER))
    assert money["accepted_revenue"] == 1000.0 and money["change_orders_revenue"] == -299.99 and money["revenue"] == 700.01 and money["reconciled"]
    assert money["tax"] == 105.0 and money["contract_total"] == 805.01


def test_a_stale_change_order_cannot_be_approved_until_sent_again() -> None:
    started = project()
    first, second = draft(started["id"]), draft(started["id"], title="Training day", lines=[{"description": "Training", "unit_price": 400}])
    assert second["number"] == 2
    sent_first, sent_second = submit(first["id"]), submit(second["id"])
    ok(decide(sent_first))
    stale = ok(client.get(f"{BASE}/change-orders/{second['id']}", headers=MEMBER))
    assert stale["stale"] and "version 2 is now current" in stale["issues"][0]
    refused = decide(sent_second)
    assert refused.status_code == 409 and "Send it for approval again" in refused.json()["error"]["message"]
    again = submit(second["id"])
    assert again["base_version"] == 2 and again["content_hash"] != sent_second["content_hash"]
    ok(decide(again))
    assert ok(client.get(f"{BASE}/projects/{started['id']}", headers=MEMBER))["contract_total"] == 3700.0


def test_reject_withdraw_and_drafts_that_cannot_apply() -> None:
    started = project()
    change = draft(started["id"])
    assert client.post(f"{BASE}/change-orders/{change['id']}/reject", headers=OWNER, json={"reason": "Not sent yet"}).status_code == 409
    sent = submit(change["id"])
    assert client.post(f"{BASE}/change-orders/{change['id']}/reject", headers=MEMBER, json={"reason": "Not now"}).status_code == 403
    rejected = ok(client.post(f"{BASE}/change-orders/{change['id']}/reject", headers=OWNER, json={"reason": "Client declined the price"}))
    assert rejected["status"] == "rejected" and rejected["decision_note"] == "Client declined the price"
    assert decide(sent).status_code == 409
    other = draft(started["id"])
    withdrawn = ok(client.post(f"{BASE}/change-orders/{other['id']}/withdraw", headers=MEMBER, json={"reason": "Merged into the next phase"}))
    assert withdrawn["status"] == "withdrawn"
    assert client.post(f"{BASE}/change-orders/{other['id']}/submit", headers=MEMBER).status_code == 409
    broken = draft(started["id"], lines=[{"action": "remove", "description": "Not on the contract", "unit_price": 5}])
    assert "must match a line exactly" in broken["issues"][0]
    refused = client.post(f"{BASE}/change-orders/{broken['id']}/submit", headers=MEMBER)
    assert refused.status_code == 409 and "Fix the change order first" in refused.json()["error"]["message"]
    ok(client.patch(f"{BASE}/projects/{started['id']}", headers=OWNER, json={"status": "closed"}))
    closed = client.post(f"{BASE}/projects/{started['id']}/change-orders", headers=MEMBER, json={"title": "Late", "timeline_days": 3})
    assert closed.status_code == 409 and "closed" in closed.json()["error"]["message"]


def test_permissions_and_cross_workspace_and_tenant_isolation() -> None:
    started = project()
    change = draft(started["id"])
    assert client.get(f"{BASE}/projects/{started['id']}/change-orders", headers=VIEWER).status_code == 403
    assert client.post(f"{BASE}/projects/{started['id']}/change-orders", headers=VIEWER, json={"title": "x" * 3}).status_code == 403
    for headers in (OTHER_WORKSPACE, OTHER_TENANT):
        assert client.get(f"{BASE}/change-orders/{change['id']}", headers=headers).status_code == 404
        assert client.get(f"{BASE}/projects/{started['id']}/change-orders", headers=headers).status_code == 404
        assert client.post(f"{BASE}/projects/{started['id']}/change-orders", headers=headers, json={"title": "Guess", "timeline_days": 1}).status_code == 404
        assert client.post(f"{BASE}/change-orders/{change['id']}/submit", headers=headers).status_code == 404
        assert client.post(f"{BASE}/change-orders/{change['id']}/approve", headers=headers, json={"content_hash": "0" * 64}).status_code == 404
        assert client.get(f"{BASE}/projects/{started['id']}/profitability", headers=headers).status_code == 404
        assert client.post(f"{BASE}/usage", headers=headers, json={"project_id": started["id"], "kind": "hosting", "description": "Guess",
                                                                    "occurred_on": "2026-10-01", "unit_cost": 1}).status_code == 404
        assert ok(client.get(f"{BASE}/usage", headers=headers))["total"] == 0


# -- ledger and profitability ----------------------------------------------------------------------


def test_ledger_and_profitability_keep_revenue_and_cash_apart() -> None:
    started = project()
    empty = ok(client.get(f"{BASE}/projects/{started['id']}/profitability", headers=MEMBER))
    assert empty["revenue"] == 2500.0 and empty["cash_collected"] is None and empty["outstanding"] is None
    assert empty["labour_cost"] is None and empty["direct_costs"] is None and empty["gross_margin"] is None and empty["gross_margin_pct"] is None
    labour = ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"project_id": started["id"], "kind": "labour", "description": "Build the dashboard",
                                                                     "occurred_on": "2026-11-03", "quantity": 7.5, "unit_cost": 80.33}), 201)
    assert labour["amount"] == 602.48 and labour["account_id"] == started["account_id"] and labour["source"] == "manual"  # 602.475 half up
    ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"project_id": started["id"], "kind": "hosting", "description": "Server share, November",
                                                           "occurred_on": "2026-11-30", "unit_cost": 120, "billable": True}), 201)
    ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"project_id": started["id"], "kind": "messaging", "description": "WhatsApp fees",
                                                           "occurred_on": "2026-11-30", "unit_cost": 15, "currency": "USD"}), 201)
    account_only = ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"account_id": started["account_id"], "kind": "other", "description": "Site visit",
                                                                           "occurred_on": "2026-11-02", "unit_cost": 50}), 201)
    assert account_only["project_id"] is None
    assert client.post(f"{BASE}/usage", headers=MEMBER, json={"kind": "other", "description": "Nowhere", "occurred_on": "2026-11-02"}).status_code == 422
    ok(client.patch(f"{BASE}/milestones/{started['milestones'][0]['id']}", headers=MEMBER, json={"paid_amount": 1250}))
    money = ok(client.get(f"{BASE}/projects/{started['id']}/profitability", headers=MEMBER))
    assert money["revenue"] == 2500.0 and money["cash_collected"] == 1250.0 and money["outstanding"] == 1250.0
    assert money["labour_hours"] == 7.5 and money["labour_cost"] == 602.48 and money["direct_costs"] == 120.0
    assert money["gross_margin"] == 1777.52 and money["gross_margin_pct"] == 71.1
    assert money["billable_costs"] == 120.0 and money["non_billable_costs"] == 602.48
    assert money["other_currency"] == [{"currency": "USD", "amount": 15.0, "entries": 1}] and money["reconciled"]
    listed = ok(client.get(f"{BASE}/usage?project_id={started['id']}", headers=MEMBER))
    assert listed["total"] == 3 and listed["items"][0]["project_name"]
    assert ok(client.get(f"{BASE}/usage?account_id={started['account_id']}&kind=other", headers=MEMBER))["total"] == 1
    edited = ok(client.patch(f"{BASE}/usage/{labour['id']}", headers=MEMBER, json={"quantity": 8}))
    assert edited["amount"] == 642.64
    assert client.delete(f"{BASE}/usage/{labour['id']}", headers=MEMBER).status_code == 403  # deletions are for owners
    assert client.delete(f"{BASE}/usage/{labour['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{BASE}/usage", headers=VIEWER).status_code == 403


def test_ai_usage_is_imported_from_the_model_budget_once_per_month() -> None:
    started = project()
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_biz", roles=["owner"])
    memory_model_budget_store.record_usage(context, date(2026, 9, 1), UsageDelta(1000, 500, 12.345), utc_now())
    memory_model_budget_store.record_usage(context, date(2026, 9, 1), UsageDelta(10, 5, None), utc_now())
    try:
        assert client.post(f"{BASE}/usage/import-ai", headers=MEMBER, json={"project_id": started["id"], "month": "2026-08"}).status_code == 409
        current = utc_now().strftime("%Y-%m")
        refused = client.post(f"{BASE}/usage/import-ai", headers=MEMBER, json={"project_id": started["id"], "month": current})
        assert refused.status_code == 409 and "finished month" in refused.json()["error"]["message"]
        imported = ok(client.post(f"{BASE}/usage/import-ai", headers=MEMBER, json={"project_id": started["id"], "month": "2026-09", "share_pct": 50}), 201)
        assert imported["kind"] == "ai_usage" and imported["source"] == "model_budget" and imported["currency"] == "USD"
        assert imported["amount"] == 6.17 and "2 calls" in imported["description"] and "1 without a price estimate" in imported["description"]  # 6.1725 half up
        again = client.post(f"{BASE}/usage/import-ai", headers=MEMBER, json={"project_id": started["id"], "month": "2026-09"})
        assert again.status_code == 409 and "already" in again.json()["error"]["message"]
        locked = client.patch(f"{BASE}/usage/{imported['id']}", headers=MEMBER, json={"unit_cost": 1})
        assert locked.status_code == 409
        assert ok(client.patch(f"{BASE}/usage/{imported['id']}", headers=MEMBER, json={"billable": True}))["billable"] is True
        other = client.post(f"{BASE}/usage/import-ai", headers=OTHER_TENANT, json={"project_id": started["id"], "month": "2026-09"})
        assert other.status_code == 409  # another tenant has no usage that month (and could not see the project)
    finally:
        memory_model_budget_store._usage.clear()
