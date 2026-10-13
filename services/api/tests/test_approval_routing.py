"""Approval routing by AED amount and to non-owner approvers (docs/approvals-and-risk.md#approval-routing)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.approval_routing import (
    ApprovalConditions,
    ApproverStep,
    RelatedAction,
    amount_of,
    build_route,
    eligible_for,
    self_approval_refused,
    summary_of,
)
from anum_api.governance import governance_store
from anum_api.schemas import Approval, ApprovalStatus, RiskLevel, Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.store import store
from anum_api.tool_governance import GovernanceApprovalRule

TENANT = "tenant_routing"
WORKSPACE = "workspace_routing"
OTHER_TENANT = "tenant_routing_other"
REFUND = "commerce.own_store.refund_order"
HASH = "a" * 64

client = TestClient(main.app)


def headers(user: str, role: str = "owner", tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


OWNER = headers("owner_a")
OWNER_B = headers("owner_b")
CARE_LEAD = headers("care_lead", "member")
OTHER_MEMBER = headers("member_x", "member")
FINANCE = headers("finance_reviewer", "member")
VIEWER = headers("viewer_v", "viewer")
ORG_ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    governance_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records, store.approval_approvers):
        collection.clear()
    yield
    governance_store.clear()


def _rule(**body: object) -> dict:
    response = client.post("/api/v1/organization/approval-rules", headers=ORG_ADMIN, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _refund_rules(care_lead: str = "user:care_lead") -> None:
    _rule(name="Refunds up to AED 250", action_pattern="*refund*",
          conditions={"amount_at_most": 250, "disputed": "no"}, approver_steps=[{"approvers": ["owner", care_lead]}])
    _rule(name="Refunds above AED 250", action_pattern="*refund*",
          conditions={"amount_above": 250}, approver_steps=[{"approvers": ["owner"]}])


def _seed(arguments: dict, *, action: str = REFUND, created_by: str = "owner_a", requested_by: str | None = None,
          task_id: str | None = None, tenant: str = TENANT, expires_in: timedelta = timedelta(hours=24),
          status: ApprovalStatus = ApprovalStatus.PENDING) -> Approval:
    now = utc_now()
    if task_id is None or task_id not in store.tasks:
        task = Task(id=task_id or new_id("task"), title="Refund", prompt="Refund the order", status=TaskStatus.WAITING_APPROVAL,
                    tenant_id=tenant, workspace_id=WORKSPACE, created_at=now, updated_at=now, created_by=created_by)
        store.tasks[task.id] = task
        task_id = task.id
    approval = Approval(id=new_id("approval"), task_id=task_id, action=action, risk_level=RiskLevel.HIGH, status=status,
                        reason="Payment", created_at=now, arguments=arguments, payload_hash=HASH,
                        expires_at=now + expires_in, requested_by=requested_by or created_by)
    store.approvals[approval.id] = approval
    return approval


def _decide(approval: Approval, who: dict[str, str], decision: str = "approve", payload_hash: str = HASH) -> httpx.Response:
    return client.post(f"/api/v1/approvals/{approval.id}/{decision}", headers=who, json={"payload_hash": payload_hash})


def _listed(who: dict[str, str], approval: Approval) -> dict:
    response = client.get("/api/v1/approvals", headers=who)
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == approval.id)


def _audits(action: str) -> list:
    return [record for record in store.audit_records if record.action == action]


# Pure routing ----------------------------------------------------------------------------


def _g(name: str, pattern: str = "*refund*", steps: list[list[str]] | None = None, **conditions: object) -> GovernanceApprovalRule:
    return GovernanceApprovalRule(id=name, name=name, action_pattern=pattern, conditions=dict(conditions),
                                  approver_steps=steps if steps is not None else [["owner"]])


SMALL = _g("small", steps=[["owner", "user:care_lead"]], amount_at_most=250, disputed="no")
LARGE = _g("large", amount_above=250)
DISPUTED = _g("disputed", disputed="yes")


def _ctx(user: str, *roles: str, org: tuple[str, ...] = ()) -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=list(roles), organization_roles=list(org))


def test_a_small_refund_routes_to_the_care_lead_and_a_large_one_to_owners_only() -> None:
    small = build_route([SMALL, LARGE, DISPUTED], {"amount": 200, "currency": "AED"})
    assert small.routed and small.rules == ["small"] and small.steps == [[["owner", "user:care_lead"]]]
    assert eligible_for(_ctx("care_lead", "member"), small.steps[0])
    large = build_route([SMALL, LARGE, DISPUTED], {"amount": 300, "currency": "AED"})
    assert large.rules == ["large"] and large.steps == [[["owner"]]]
    assert not eligible_for(_ctx("care_lead", "member"), large.steps[0])
    assert "AED 300 > AED 250" in summary_of(large, 0) and "owners" in summary_of(large, 0)
    # Exactly 250 is inside the delegated band (at most).
    assert build_route([SMALL, LARGE], {"amount": "250.00"}).rules == ["small"]


def test_a_disputed_refund_always_goes_to_owners() -> None:
    route = build_route([SMALL, LARGE, DISPUTED], {"amount": 50, "disputed": True})
    assert route.rules == ["disputed"] and route.steps == [[["owner"]]]
    # Without a disputed rule the delegation still does not apply: owners decide (fallback).
    fallback = build_route([SMALL, LARGE], {"amount": 50, "disputed": "yes"})
    assert fallback.fallback and fallback.steps == [[["owner"]]]


@pytest.mark.parametrize("arguments", [{}, {"amount": "a lot"}, {"amount": 100, "currency": "USD"}, {"amount": -5}, {"amount": True}])
def test_an_unknown_amount_never_delegates(arguments: dict) -> None:
    route = build_route([SMALL, LARGE], arguments)
    assert route.rules == ["large"], route
    assert any(reason.code == "amount_unknown" for reason in route.reasons)


def test_amounts_of_the_same_task_are_added_so_an_action_cannot_be_split() -> None:
    first = RelatedAction(action=REFUND, risk_level=RiskLevel.HIGH, arguments={"amount": 150, "currency": "AED"})
    route = build_route([SMALL, LARGE], {"amount": 150, "currency": "AED"}, [first])
    assert route.rules == ["large"]
    reason = next(r for r in route.reasons if r.code == "amount_above")
    assert reason.amount == "150" and reason.total == "300" and reason.related == 1
    # Another tool's amount in the same task is not added.
    unrelated = RelatedAction(action="marketing.schedule_post", risk_level=RiskLevel.HIGH, arguments={"amount": 900})
    assert build_route([SMALL, LARGE], {"amount": 150}, [unrelated]).rules == ["small"]


def test_goodwill_discount_needs_both_the_amount_and_the_share_of_the_order() -> None:
    staff = _g("staff", pattern="*discount*", steps=[["owner", "role:member"]], amount_at_most=100, percent_of_order_at_most=5)
    assert build_route([staff], {"amount": 80, "order_total": 2000}).rules == ["staff"]
    too_big_share = build_route([staff], {"amount": 80, "order_total": 1000})
    assert too_big_share.fallback and any(r.code == "percent_at_most" and not r.applies for r in too_big_share.reasons)
    assert build_route([staff], {"amount": 80}).fallback  # order value unknown
    assert build_route([staff], {"amount": 120, "order_total": 10000}).fallback
    assert eligible_for(_ctx("member_x", "member"), build_route([staff], {"amount": 80, "order_total": 2000}).steps[0])


def test_a_commitment_above_aed_5000_needs_finance_then_owner() -> None:
    commitment = _g("commit", pattern="*purchase*", steps=[["user:finance_reviewer"], ["owner"]], amount_above=5000)
    route = build_route([commitment], {"amount": 6000})
    assert route.required == 2 and route.steps == [[["user:finance_reviewer"]], [["owner"]]]
    assert eligible_for(_ctx("finance_reviewer", "member"), route.steps[0])
    assert not eligible_for(_ctx("owner_a", "owner"), route.steps[0]) and eligible_for(_ctx("owner_a", "owner"), route.steps[1])
    assert "step 1 of 2" in summary_of(route, 0)


def test_rules_without_conditions_or_steps_keep_the_original_behaviour() -> None:
    legacy = GovernanceApprovalRule(id="l", name="l", action_pattern="*", minimum_approvers=3)
    assert not build_route([legacy], {"amount": 1}).routed
    # Mixed with a routed rule, a legacy rule still needs owners holding its roles.
    mixed = build_route([legacy, SMALL], {"amount": 10})
    assert mixed.required == 3 and ["owner"] in mixed.steps[0] and ["owner", "user:care_lead"] in mixed.steps[0]
    assert not eligible_for(_ctx("care_lead", "member"), mixed.steps[0])


def test_unreadable_stored_conditions_and_steps_fail_closed() -> None:
    broken = GovernanceApprovalRule(id="b", name="b", action_pattern="*refund*",
                                    conditions={"amount_at_most": "lots"}, approver_steps=[["user:"]])
    route = build_route([broken], {"amount": 1})
    assert route.steps == [[["owner"]]]


def test_eligibility_viewers_and_self_approval() -> None:
    step = [["user:viewer_v", "role:member", "org_admin"]]
    assert not eligible_for(_ctx("viewer_v", "viewer"), step)
    assert eligible_for(_ctx("someone", "owner", org=("org_admin",)), step)
    assert not eligible_for(_ctx("someone", "owner"), [["role:member"]])
    route = build_route([SMALL], {"amount": 10})
    # A delegated approver never approves their own request; an owner follows the two-person rules.
    assert self_approval_refused(_ctx("care_lead", "member"), route, own_request=True)
    assert not self_approval_refused(_ctx("owner_a", "owner"), route, own_request=True)
    assert not self_approval_refused(_ctx("care_lead", "member"), route, own_request=False)


def test_rule_validation() -> None:
    with pytest.raises(ValueError):
        ApprovalConditions(amount_above=500, amount_at_most=250)
    with pytest.raises(ValueError):
        ApproverStep(approvers=["role:viewer"])
    with pytest.raises(ValueError):
        ApproverStep(approvers=[])
    assert ApproverStep(approvers=["OWNER", " user:u1 ", "owner"]).approvers == ["owner", "user:u1"]
    assert amount_of({"refund_amount": "1,250.50 AED"}) is not None


# API ---------------------------------------------------------------------------------------


def test_rules_with_aed_bands_are_created_updated_and_deleted_by_an_org_admin_only() -> None:
    body = {"name": "Refunds up to AED 250", "action_pattern": "*refund*",
            "conditions": {"amount_at_most": 250, "disputed": "no"}, "approver_steps": [{"approvers": ["owner", "user:care_lead"]}]}
    assert client.post("/api/v1/organization/approval-rules", headers=OWNER, json=body).status_code == 403
    created = _rule(**body)
    assert created["conditions"]["amount_at_most"] == 250 and created["approver_steps"][0]["approvers"] == ["owner", "user:care_lead"]
    bad = client.post("/api/v1/organization/approval-rules", headers=ORG_ADMIN,
                      json={**body, "approver_steps": [{"approvers": ["owner"]}] * 3})
    assert bad.status_code == 422
    updated = client.put(f"/api/v1/organization/approval-rules/{created['id']}", headers=ORG_ADMIN,
                         json={**body, "conditions": {"amount_at_most": 300}, "enabled": False})
    assert updated.status_code == 200 and updated.json()["conditions"]["amount_at_most"] == 300
    assert updated.json()["enabled"] is False and updated.json()["updated_by"] == "owner_a"
    assert client.put(f"/api/v1/organization/approval-rules/{created['id']}", headers=OWNER, json=body).status_code == 403
    assert client.put("/api/v1/organization/approval-rules/missing", headers=ORG_ADMIN, json=body).status_code == 404
    other = {**ORG_ADMIN, "x-tenant-id": OTHER_TENANT}
    assert client.delete(f"/api/v1/organization/approval-rules/{created['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/organization/approval-rules/{created['id']}", headers=ORG_ADMIN).status_code == 204
    assert client.get("/api/v1/organization/approval-rules", headers=OWNER).json() == []
    assert [len(_audits(a)) for a in ("approval_rule.created", "approval_rule.updated", "approval_rule.deleted")] == [1, 1, 1]
    assert _audits("approval_rule.updated")[0].metadata["before"]["conditions"]["amount_at_most"] == 250


def test_the_care_lead_approves_a_small_refund_and_others_see_why_they_cannot() -> None:
    _refund_rules()
    approval = _seed({"order": "TEST-ORDER-001", "amount": 200, "currency": "AED"})
    shown = _listed(CARE_LEAD, approval)["routing"]
    assert shown["routed"] and shown["can_approve"] and shown["can_reject"] and shown["rules"] == ["Refunds up to AED 250"]
    assert shown["steps"][0]["approvers"] == [["owner", "user:care_lead"]]
    other = _listed(OTHER_MEMBER, approval)["routing"]
    assert not other["can_approve"] and not other["can_reject"] and other["refusal"] == "not_listed"
    refused = _decide(approval, OTHER_MEMBER)
    assert refused.status_code == 403 and "routed" in refused.json()["error"]["message"]
    assert _audits("approval.route_denied") and store.approvals[approval.id].status == ApprovalStatus.PENDING
    assert _decide(approval, VIEWER).status_code == 403
    approved = _decide(approval, CARE_LEAD)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["status"] == "approved" and approved.json()["approval"]["decided_by"] == "care_lead"


def test_a_refund_above_aed_250_is_for_owners_only() -> None:
    _refund_rules()
    approval = _seed({"order": "TEST-ORDER-003", "amount": 300, "currency": "AED"})
    shown = _listed(CARE_LEAD, approval)["routing"]
    assert shown["refusal"] == "not_listed" and "AED 300 > AED 250" in shown["summary"]
    reason = next(r for r in shown["reasons"] if r["code"] == "amount_above")
    assert reason == {**reason, "total": "300", "threshold": "250", "applies": True, "currency": "AED"}
    refused = _decide(approval, CARE_LEAD)
    assert refused.status_code == 403 and "AED 300 > AED 250" in refused.json()["error"]["message"]
    assert _decide(approval, OWNER).status_code == 200


def test_splitting_a_refund_in_one_task_routes_the_second_part_to_owners() -> None:
    _refund_rules()
    first = _seed({"amount": 150, "currency": "AED"})
    assert _decide(first, CARE_LEAD).status_code == 200
    second = _seed({"amount": 150, "currency": "AED"}, task_id=first.task_id)
    shown = _listed(CARE_LEAD, second)["routing"]
    assert shown["rules"] == ["Refunds above AED 250"] and not shown["can_approve"]
    assert _decide(second, CARE_LEAD).status_code == 403


def test_a_delegated_approver_never_approves_their_own_request() -> None:
    _refund_rules()
    approval = _seed({"amount": 100}, created_by="care_lead")
    assert _listed(CARE_LEAD, approval)["routing"]["refusal"] == "own_request"
    refused = _decide(approval, CARE_LEAD)
    assert refused.status_code == 403 and "own request" in refused.json()["error"]["message"]
    assert _audits("approval.self_approval_denied")
    # They may still reject it, and an owner may approve it.
    assert _decide(approval, OWNER).status_code == 200


def test_a_rule_that_does_not_list_owners_binds_owners_too() -> None:
    _rule(name="Care lead only", action_pattern="*refund*", conditions={"amount_at_most": 250},
          approver_steps=[{"approvers": ["user:care_lead"]}])
    approval = _seed({"amount": 100})
    assert _decide(approval, OWNER).status_code == 403
    # Rejecting stays open to owners.
    assert _decide(approval, OWNER, "reject").status_code == 200


def test_two_step_commitment_finance_review_then_owner() -> None:
    _rule(name="Commitments above AED 5,000", action_pattern="*purchase*", conditions={"amount_above": 5000},
          approver_steps=[{"approvers": ["user:finance_reviewer"]}, {"approvers": ["owner"]}])
    approval = _seed({"supplier": "Test Supplier", "amount": 6000}, action="commerce.own_store.purchase_stock")
    assert _listed(OWNER, approval)["required_approvals"] == 2
    assert _decide(approval, OWNER).status_code == 403  # step 1 is the finance review
    step_one = _decide(approval, FINANCE)
    assert step_one.status_code == 200, step_one.text
    assert step_one.json()["approval"]["status"] == "pending"
    assert [a["user_id"] for a in step_one.json()["approval"]["approvers"]] == ["finance_reviewer"]
    assert _decide(approval, FINANCE).status_code == 409  # one approval per person
    assert _decide(approval, OTHER_MEMBER).status_code == 403  # step 2 is the owner's
    shown = _listed(OWNER, approval)["routing"]
    assert shown["current_step"] == 1 and shown["steps"][0]["approved_by"] == "finance_reviewer" and shown["can_approve"]
    done = _decide(approval, OWNER)
    assert done.status_code == 200 and done.json()["approval"]["status"] == "approved"


def test_hash_binding_and_expiry_hold_for_delegated_approvers() -> None:
    _refund_rules()
    approval = _seed({"amount": 100})
    assert _decide(approval, CARE_LEAD, payload_hash="b" * 64).status_code == 409
    lapsed = _seed({"amount": 100}, expires_in=timedelta(seconds=-1))
    assert _decide(lapsed, CARE_LEAD).status_code == 410
    assert store.approvals[lapsed.id].status == ApprovalStatus.EXPIRED


def test_rules_of_another_tenant_never_route_here() -> None:
    other_admin = {**ORG_ADMIN, "x-tenant-id": OTHER_TENANT}
    response = client.post("/api/v1/organization/approval-rules", headers=other_admin,
                           json={"name": "x", "action_pattern": "*refund*", "conditions": {"amount_at_most": 250},
                                 "approver_steps": [{"approvers": ["user:care_lead"]}]})
    assert response.status_code == 201
    approval = _seed({"amount": 100})
    assert _decide(approval, CARE_LEAD).status_code == 403
    assert not _listed(OWNER, approval)["routing"]["routed"]
