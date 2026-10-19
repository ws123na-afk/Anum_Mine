"""Business support tickets (docs/business.md#support-tickets), against the in-memory backend.

Covers the working-hours SLA arithmetic, the policy checks, permissions (viewers refused,
owners only for the policy, reopening a closed ticket and deletions), the status flow and
its resolution audit, pausing while waiting on the customer, escalation, notes that record a
response, links to accounts, contacts, projects and support plans (all scoped), tickets from
an inbound email or WhatsApp conversation (linked, never copied), reply drafts handed to the
inbox's own approval path, the agent's draft tool, paging and search, cross-workspace and
cross-tenant isolation, and the audit walk of every state-changing route.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_tools import ToolCall, ToolRegistry
from anum_api.business import support, support_channels
from anum_api.business import tools as business_tools
from anum_api.business.models import Severity, SeverityTarget, SupportPolicy, SupportTicket, TicketStatus
from anum_api.business.store import business_store
from anum_api.schemas import TenantContext
from anum_api.store import store as memory_store
from anum_api.suppression import suppression_store

TENANT = "tenant_sup"
WORKSPACE = "workspace_sup"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_sup", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_sup", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_sup", "x-user-roles": "viewer"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_sup_other"}
OTHER_TENANT = {**OWNER, "x-tenant-id": "tenant_sup_other", "x-workspace-id": "workspace_sup_b"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_sup", roles=["member"])
BASE = "/api/v1/business"
SUP = f"{BASE}/support"
# Thursday 15 October 2026, 16:00 in Riyadh (UTC+3): one working hour left before the weekend.
THURSDAY_4PM = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    yield
    business_store.clear()
    suppression_store.clear()


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def ticket(headers: dict[str, str] = MEMBER, **fields: Any) -> dict[str, Any]:
    body = {"title": "Dashboard does not load", "severity": "high", "requester_name": "Sara Khan", "requester_email": "sara@client.example", **fields}
    return ok(client.post(f"{SUP}/tickets", headers=headers, json=body), 201)


class FakeInbox:
    """Stands in for the email inbox and WhatsApp behind the support_channels adapters."""

    def __init__(self, reported_at: datetime = THURSDAY_4PM) -> None:
        self.reported_at = reported_at
        self.requests: list[tuple[str, str, str]] = []
        self.state = "pending_approval"

    def email(self, context: TenantContext, message_id: str) -> support_channels.SourceInfo:
        if message_id == "missing":
            raise support_channels.http_error(404, "Message not found.")
        return support_channels.SourceInfo(ref=message_id, account_id="acct_mail", requester_name="Omar Saleh", requester_email="omar@client.example",
                                           requester_phone=None, title="Invoices export is empty", reported_at=self.reported_at)

    def whatsapp(self, context: TenantContext, conversation_id: str) -> support_channels.SourceInfo:
        return support_channels.SourceInfo(ref=conversation_id, account_id=None, requester_name="Huda", requester_email=None,
                                           requester_phone="+971501234567", title="WhatsApp from Huda", reported_at=self.reported_at)

    async def request_email(self, context: TenantContext, repository: Any, message_id: str, body: str) -> support_channels.DeliveryRequest:
        self.requests.append(("email", message_id, body))
        return support_channels.DeliveryRequest("pending_approval", "edr_1", "appr_1", "task_1", "a" * 64, "Waiting for approval.")

    async def request_whatsapp(self, context: TenantContext, repository: Any, conversation_id: str, body: str) -> support_channels.DeliveryRequest:
        self.requests.append(("whatsapp", conversation_id, body))
        return support_channels.DeliveryRequest("pending_approval", "wam_1", "appr_2", "task_2", "b" * 64, "Waiting for approval.")

    def status(self, context: TenantContext, ref: str) -> tuple[str, str]:
        return self.state, ""


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch) -> FakeInbox:
    fake = FakeInbox()
    monkeypatch.setattr(support_channels, "load_email_source", fake.email)
    monkeypatch.setattr(support_channels, "load_whatsapp_source", fake.whatsapp)
    monkeypatch.setattr(support_channels, "request_email_reply", fake.request_email)
    monkeypatch.setattr(support_channels, "request_whatsapp_reply", fake.request_whatsapp)
    monkeypatch.setattr(support_channels, "email_reply_status", fake.status)
    monkeypatch.setattr(support_channels, "whatsapp_reply_status", fake.status)
    return fake


# -- SLA arithmetic -------------------------------------------------------------------------------


def test_working_minutes_skip_the_weekend_and_the_night() -> None:
    policy = SupportPolicy()  # Sunday to Thursday, 09:00 to 17:00, Asia/Riyadh
    # One hour on Thursday, then Friday and Saturday off, then one hour on Sunday.
    assert support.add_working_minutes(THURSDAY_4PM, 120, policy) == datetime(2026, 10, 18, 7, 0, tzinfo=UTC)
    # Reported on Friday night: counting starts on Sunday at 09:00 Riyadh time.
    assert support.add_working_minutes(datetime(2026, 10, 16, 19, 0, tzinfo=UTC), 120, policy) == datetime(2026, 10, 18, 8, 0, tzinfo=UTC)
    # Reported before the day starts: counting starts at 09:00.
    assert support.add_working_minutes(datetime(2026, 10, 18, 3, 0, tzinfo=UTC), 30, policy) == datetime(2026, 10, 18, 6, 30, tzinfo=UTC)
    # Exactly the rest of the day lands on the close, not the next morning.
    assert support.add_working_minutes(THURSDAY_4PM, 60, policy) == datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    assert support.working_minutes_between(THURSDAY_4PM, datetime(2026, 10, 18, 7, 0, tzinfo=UTC), policy) == 120
    assert support.working_minutes_between(datetime(2026, 10, 18, 7, 0, tzinfo=UTC), THURSDAY_4PM, policy) == 0
    # Critical: 16 working hours from Thursday 16:00 is Monday 16:00 Riyadh.
    response, resolution = support.target_minutes(policy, Severity.CRITICAL)
    assert (response, resolution) == (120, 960)
    assert support.add_working_minutes(THURSDAY_4PM, resolution, policy) == datetime(2026, 10, 19, 13, 0, tzinfo=UTC)


def test_a_policy_with_hours_to_midnight_and_every_day() -> None:
    policy = SupportPolicy(timezone="UTC", work_days=list(range(7)), start_hour=0, end_hour=24)
    start = datetime(2026, 10, 16, 23, 30, tzinfo=UTC)
    assert support.add_working_minutes(start, 60, policy) == datetime(2026, 10, 17, 0, 30, tzinfo=UTC)
    assert support.working_minutes_between(start, datetime(2026, 10, 17, 0, 30, tzinfo=UTC), policy) == 60


def test_policy_problems_are_named() -> None:
    assert support.policy_problems(SupportPolicy()) == []
    bad = SupportPolicy(timezone="Mars/Olympus", work_days=[1, 1], start_hour=17, end_hour=9)
    bad.targets["low"] = SeverityTarget(response_hours=Decimal("10"), resolution_hours=Decimal("5"))
    problems = " ".join(support.policy_problems(bad))
    assert "Unknown time zone" in problems and "each once" in problems and "end after they start" in problems and "low resolution target" in problems
    missing = SupportPolicy()
    del missing.targets["critical"]
    assert "missing: critical" in " ".join(support.policy_problems(missing))


def test_waiting_on_the_customer_pauses_the_resolution_clock() -> None:
    policy = SupportPolicy()
    item = SupportTicket(id="btkt_1", tenant_id=TENANT, workspace_id=WORKSPACE, title="Export", severity=Severity.NORMAL, reported_at=THURSDAY_4PM,
                         created_by="x", created_at=THURSDAY_4PM, updated_at=THURSDAY_4PM)
    support.apply_due_times(item, policy)
    due = item.resolution_due_at
    sunday_10 = datetime(2026, 10, 18, 7, 0, tzinfo=UTC)
    support.move(item, TicketStatus.WAITING_ON_CUSTOMER, sunday_10, "x", policy)
    assert item.first_responded_at == sunday_10 and item.sla_paused_at == sunday_10
    assert support.sla_state(item, sunday_10)["resolution"] == "paused"
    support.move(item, TicketStatus.IN_PROGRESS, datetime(2026, 10, 18, 9, 0, tzinfo=UTC), "x", policy)
    assert item.sla_paused_minutes == 120 and item.sla_paused_at is None
    assert item.resolution_due_at == support.add_working_minutes(due, 120, policy)


# -- permissions ----------------------------------------------------------------------------------


def test_viewers_are_refused_with_who_can_use_it() -> None:
    for method, path in (("GET", "/setup"), ("GET", "/tickets"), ("GET", "/summary"), ("POST", "/tickets")):
        response = client.request(method, f"{SUP}{path}", headers=VIEWER, json={"title": "Nope", "severity": "low"})
        assert response.status_code == 403
        assert "owners and members" in response.json()["error"]["message"]


def test_members_work_tickets_but_only_owners_set_the_policy() -> None:
    setup = ok(client.get(f"{SUP}/setup", headers=MEMBER))
    assert setup["can_write"] and not setup["can_manage"] and setup["policy"]["work_days"] == [6, 0, 1, 2, 3]
    assert setup["transitions"]["resolved"] == ["in_progress", "closed"]
    policy = {**setup["policy"], "start_hour": 8}
    assert client.put(f"{SUP}/policy", headers=MEMBER, json=policy).status_code == 403
    saved = ok(client.put(f"{SUP}/policy", headers=OWNER, json={**policy, "note": "Weekdays only, no 24/7 cover"}))
    assert saved["start_hour"] == 8
    assert ok(client.get(f"{SUP}/setup", headers=MEMBER))["policy"]["start_hour"] == 8
    # The research caps route keeps the support policy.
    ok(client.put(f"{BASE}/settings", headers=OWNER, json={"research_runs_per_day": 5, "research_searches_per_day": 9}))
    assert ok(client.get(f"{SUP}/setup", headers=MEMBER))["policy"]["start_hour"] == 8
    bad = client.put(f"{SUP}/policy", headers=OWNER, json={**policy, "start_hour": 18, "end_hour": 9})
    assert bad.status_code == 422 and "end after they start" in bad.json()["error"]["message"]


# -- create, read, update ---------------------------------------------------------------------------


def test_create_read_update_and_assign() -> None:
    created = ticket(description="The KPI page spins forever.")
    assert created["status"] == "new" and created["reference"].startswith("T-") and created["source"] == "manual"
    assert created["response_due_at"] and created["resolution_due_at"] and created["sla"]["response"] in {"on_track", "due_soon", "breached"}
    assert created["allowed"] == ["triaged", "in_progress", "waiting_on_customer", "resolved"]
    assert created["can_reply"] is False and created["activities"][0]["kind"] == "ticket_opened"
    assert created["requester_email"] == "sara@client.example" and created["requester_verified"] is False
    detail = ok(client.get(f"{SUP}/tickets/{created['id']}", headers=OWNER))
    assert detail["title"] == "Dashboard does not load"
    updated = ok(client.patch(f"{SUP}/tickets/{created['id']}", headers=MEMBER, json={"assignee_id": "member_sup", "severity": "critical"}))
    assert updated["assignee_id"] == "member_sup" and updated["severity"] == "critical"
    assert updated["resolution_due_at"] != created["resolution_due_at"]
    kinds = [item["kind"] for item in updated["activities"]]
    assert "ticket_assigned" in kinds and "ticket_updated" in kinds
    stranger = client.patch(f"{SUP}/tickets/{created['id']}", headers=MEMBER, json={"assignee_id": "someone_else"})
    assert stranger.status_code == 422 and "active owner or member" in stranger.json()["error"]["message"]
    assert client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "x"}).status_code == 422
    bad_email = client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "Bad email", "requester_email": "not-an-email"})
    assert bad_email.status_code == 422


def test_links_must_agree_and_belong_to_the_workspace() -> None:
    account = ok(client.post(f"{BASE}/accounts", headers=MEMBER, json={"name": "Client Co", "website": "https://client.example"}), 201)
    with_contact = ok(client.post(f"{BASE}/accounts/{account['id']}/contacts", headers=MEMBER,
                                  json={"name": "Sara Khan", "email": "sara@client.example", "consent_basis": "existing_relationship"}), 201)
    contact_id = with_contact["contacts"][0]["id"]
    other = ok(client.post(f"{BASE}/accounts", headers=MEMBER, json={"name": "Other Co"}), 201)
    # The requester matches a Business contact by e-mail: verified, and the contact and account are linked.
    created = ticket()
    assert created["requester_verified"] is True and created["contact_id"] == contact_id and created["account_id"] == account["id"]
    assert created["account_name"] == "Client Co" and created["contact_name"] == "Sara Khan"
    clash = client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "Clash", "account_id": other["id"], "contact_id": contact_id})
    assert clash.status_code == 422 and "another account" in clash.json()["error"]["message"]
    project = client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "Covered", "entitlement": "project"})
    assert project.status_code == 422 and "project that covers" in project.json()["error"]["message"]
    plan = client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "Plan", "entitlement": "support_plan", "support_offer_id": "boff_missing"})
    assert plan.status_code == 422
    # An id from another workspace or tenant reads as missing.
    foreign = ok(client.post(f"{BASE}/accounts", headers=OTHER_TENANT, json={"name": "Foreign Co"}), 201)
    assert client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "Foreign", "account_id": foreign["id"]}).status_code == 404


def test_a_support_plan_from_the_catalog_covers_a_ticket() -> None:
    product = ok(client.post(f"{BASE}/products", headers=OWNER, json={"kind": "service_package", "name": "Support and Care"}), 201)
    detail = ok(client.post(f"{BASE}/products/{product['id']}/versions", headers=OWNER, json={"price_type": "fixed", "price_min": 500}), 201)
    offer_id = detail["offers"][0]["id"]
    draft = client.post(f"{SUP}/tickets", headers=MEMBER, json={"title": "Plan", "entitlement": "support_plan", "support_offer_id": offer_id})
    assert draft.status_code == 422 and "not active" in draft.json()["error"]["message"]
    ok(client.post(f"{BASE}/offers/{offer_id}/activate", headers=OWNER))
    created = ticket(entitlement="support_plan", support_offer_id=offer_id)
    assert created["entitlement"] == "support_plan" and created["support_plan_name"] == "Support and Care v1"
    blocked = client.delete(f"{BASE}/products/{product['id']}", headers=OWNER)
    assert blocked.status_code == 409 and "support plan" in blocked.json()["error"]["message"]


# -- status flow ------------------------------------------------------------------------------------


def test_status_flow_with_resolution_audit_close_and_reopen() -> None:
    created = ticket()
    path = f"{SUP}/tickets/{created['id']}"
    refused = client.post(f"{path}/status", headers=MEMBER, json={"to": "closed"})
    assert refused.status_code == 409 and "can move to" in refused.json()["error"]["message"]
    ok(client.post(f"{path}/status", headers=MEMBER, json={"to": "triaged"}))
    ok(client.post(f"{path}/status", headers=MEMBER, json={"to": "in_progress"}))
    no_summary = client.post(f"{path}/status", headers=MEMBER, json={"to": "resolved", "resolution_code": "fixed", "resolution_summary": "done"})
    assert no_summary.status_code == 422 and "10 characters" in no_summary.json()["error"]["message"]
    resolved = ok(client.post(f"{path}/status", headers=MEMBER, json={"to": "resolved", "resolution_code": "fixed",
                                                                         "resolution_summary": "Cleared the stale cache and confirmed with Sara."}))
    assert resolved["resolved_by"] == "member_sup" and resolved["resolution_code"] == "fixed" and resolved["sla"]["resolution"] in {"met", "missed"}
    closed = ok(client.post(f"{path}/status", headers=MEMBER, json={"to": "closed"}))
    assert closed["closed_by"] == "member_sup" and closed["allowed"] == ["in_progress"]
    assert client.patch(path, headers=MEMBER, json={"title": "Changed after closing"}).status_code == 409
    member_reopen = client.post(f"{path}/status", headers=MEMBER, json={"to": "in_progress"})
    assert member_reopen.status_code == 403 and "owners" in member_reopen.json()["error"]["message"]
    reopened = ok(client.post(f"{path}/status", headers=OWNER, json={"to": "in_progress", "note": "Sara says it is back"}))
    assert reopened["reopened_count"] == 1 and reopened["resolved_at"] is None and reopened["resolution_code"] is None
    kinds = [item["kind"] for item in reopened["activities"]]
    assert {"ticket_resolved", "ticket_closed", "ticket_reopened"} <= set(kinds)


def test_waiting_on_customer_counts_as_a_response_and_pauses() -> None:
    created = ticket()
    waiting = ok(client.post(f"{SUP}/tickets/{created['id']}/status", headers=MEMBER, json={"to": "waiting_on_customer"}))
    assert waiting["first_responded_at"] and waiting["sla_paused_at"] and waiting["sla"]["resolution"] == "paused"
    assert waiting["sla"]["response"] == "met"
    back = ok(client.post(f"{SUP}/tickets/{created['id']}/status", headers=MEMBER, json={"to": "in_progress"}))
    assert back["sla_paused_at"] is None


def test_escalation_and_notes() -> None:
    created = ticket()
    path = f"{SUP}/tickets/{created['id']}"
    assert client.post(f"{path}/escalation", headers=MEMBER, json={"escalated": True}).status_code == 422
    escalated = ok(client.post(f"{path}/escalation", headers=MEMBER, json={"escalated": True, "reason": "Payroll run tomorrow"}))
    assert escalated["escalated"] and escalated["escalated_by"] == "member_sup"
    assert client.post(f"{path}/escalation", headers=MEMBER, json={"escalated": True, "reason": "Again"}).status_code == 409
    calm = ok(client.post(f"{path}/escalation", headers=MEMBER, json={"escalated": False, "reason": "Workaround in place"}))
    assert not calm["escalated"] and calm["escalation_reason"] == ""
    noted = ok(client.post(f"{path}/notes", headers=MEMBER, json={"text": "Looked at the logs."}))
    assert noted["first_responded_at"] is None
    responded = ok(client.post(f"{path}/notes", headers=MEMBER, json={"text": "Called Sara and explained the workaround.", "responded": True}))
    assert responded["first_responded_at"] and responded["sla"]["response"] in {"met", "missed"}
    assert responded["activities"][0]["kind"] == "response_recorded"


# -- list, summary and isolation ---------------------------------------------------------------------


def test_list_search_filters_paging_and_summary(inbox: FakeInbox) -> None:
    for number in range(5):
        ticket(title=f"Report {number} is slow", severity="low" if number % 2 else "normal")
    escalated = ticket(title="Payroll export broken", severity="critical")
    ok(client.post(f"{SUP}/tickets/{escalated['id']}/escalation", headers=MEMBER, json={"escalated": True, "reason": "Payroll today"}))
    ok(client.patch(f"{SUP}/tickets/{escalated['id']}", headers=MEMBER, json={"assignee_id": "member_sup"}))
    inbox.reported_at = datetime(2026, 1, 4, 6, 0, tzinfo=UTC)  # long ago: both clocks are overdue
    old = ok(client.post(f"{SUP}/tickets/from-message", headers=MEMBER, json={"source": "email", "ref_id": "emsg_old"}), 201)
    assert old["sla"]["breached"] is True and old["sla"]["response"] == "breached"
    page = ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"limit": 3}))
    assert page["total"] == 7 and len(page["items"]) == 3
    second = ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"limit": 3, "offset": 3}))
    assert not {item["id"] for item in page["items"]} & {item["id"] for item in second["items"]}
    assert ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"q": "payroll"}))["total"] == 1
    assert ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"severity": ["low"]}))["total"] == 2
    assert ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"assignee": "me"}))["total"] == 1
    assert ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"unassigned": True}))["total"] == 6
    assert ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"escalated": True}))["total"] == 1
    assert ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"source": "email"}))["total"] == 1
    due = ok(client.get(f"{SUP}/tickets", headers=MEMBER, params={"sort": "due", "limit": 1}))
    assert due["items"][0]["id"] == old["id"]
    summary = ok(client.get(f"{SUP}/summary", headers=MEMBER))
    assert summary == {"open": 7, "new": 7, "unassigned": 6, "escalated": 1, "breached": summary["breached"], "waiting_on_customer": 0, "truncated": False}
    assert summary["breached"] >= 1


def test_another_workspace_or_tenant_cannot_see_or_touch_a_ticket() -> None:
    created = ticket()
    for headers in (OTHER_WORKSPACE, OTHER_TENANT):
        assert client.get(f"{SUP}/tickets/{created['id']}", headers=headers).status_code == 404
        assert ok(client.get(f"{SUP}/tickets", headers=headers))["total"] == 0
        assert ok(client.get(f"{SUP}/summary", headers=headers))["open"] == 0
        assert client.patch(f"{SUP}/tickets/{created['id']}", headers=headers, json={"title": "Taken over"}).status_code == 404
        assert client.post(f"{SUP}/tickets/{created['id']}/status", headers=headers, json={"to": "triaged"}).status_code == 404
        assert client.post(f"{SUP}/tickets/{created['id']}/notes", headers=headers, json={"text": "hi"}).status_code == 404
        assert client.delete(f"{SUP}/tickets/{created['id']}", headers=headers).status_code == 404
    assert ok(client.get(f"{SUP}/tickets/{created['id']}", headers=OWNER))["title"] == "Dashboard does not load"
    # The agent tool runs in the caller's scope too.
    foreign = TenantContext(tenant_id="tenant_sup_other", workspace_id="workspace_sup_b", user_id="x", roles=["owner"])
    result = asyncio.run(business_tools.draft_support_reply(ToolCall(name=business_tools.SUPPORT_REPLY_TOOL, arguments={"ticket_id": created["id"], "body": "Hi"}), foreign))
    assert result.status == "failed" and "does not exist" in result.summary


# -- from an inbox, replies and the agent tool ------------------------------------------------------


def test_a_ticket_from_an_email_links_the_message_and_its_reply_goes_to_approval(inbox: FakeInbox) -> None:
    created = ok(client.post(f"{SUP}/tickets/from-message", headers=MEMBER, json={"source": "email", "ref_id": "emsg_1", "severity": "critical"}), 201)
    assert created["source"] == "email" and created["source_ref"] == "emsg_1" and created["source_account_id"] == "acct_mail"
    assert created["title"] == "Invoices export is empty" and created["requester_email"] == "omar@client.example" and created["description"] == ""
    assert created["reported_at"].startswith("2026-10-15T13:00")
    # SLA from when the customer wrote, in working hours: Sunday 10:00 and Monday 16:00 Riyadh.
    assert created["response_due_at"].startswith("2026-10-18T07:00") and created["resolution_due_at"].startswith("2026-10-19T13:00")
    assert created["can_reply"] is True and created["reply_channel"] == "email"
    duplicate = client.post(f"{SUP}/tickets/from-message", headers=MEMBER, json={"source": "email", "ref_id": "emsg_1"})
    assert duplicate.status_code == 409 and created["id"] in duplicate.json()["error"]["message"]
    assert client.post(f"{SUP}/tickets/from-message", headers=MEMBER, json={"source": "email", "ref_id": "missing"}).status_code == 404
    path = f"{SUP}/tickets/{created['id']}"
    promise = ok(client.post(f"{path}/replies", headers=MEMBER, json={"body": "We guarantee a fix, we are here 24/7."}), 201)
    refused = client.post(f"{path}/replies/{promise['replies'][0]['id']}/request-approval", headers=MEMBER)
    assert refused.status_code == 422 and "24/7" in refused.json()["error"]["message"] and inbox.requests == []
    discarded = ok(client.delete(f"{path}/replies/{promise['replies'][0]['id']}", headers=MEMBER))
    assert discarded["replies"][0]["status"] == "discarded"
    drafted = ok(client.post(f"{path}/replies", headers=MEMBER, json={"body": "Thanks Omar, we are looking at the export now."}), 201)
    reply_id = drafted["replies"][-1]["id"]
    edited = ok(client.patch(f"{path}/replies/{reply_id}", headers=MEMBER, json={"body": "Thanks Omar, we found the cause and are fixing it."}))
    assert edited["replies"][-1]["status"] == "draft" and edited["first_responded_at"] is None
    requested = ok(client.post(f"{path}/replies/{reply_id}/request-approval", headers=MEMBER))
    assert requested["reply"]["status"] == "pending_approval" and requested["reply"]["approval_id"] == "appr_1"
    assert inbox.requests == [("email", "emsg_1", "Thanks Omar, we found the cause and are fixing it.")]
    assert client.patch(f"{path}/replies/{reply_id}", headers=MEMBER, json={"body": "Changed"}).status_code == 409
    assert client.delete(f"{path}", headers=OWNER).status_code == 409  # a waiting reply keeps the ticket
    # The owner approves in the inbox; the next read sees it sent and the response clock stops.
    inbox.state = "sent"
    after = ok(client.get(path, headers=MEMBER))
    assert after["replies"][-1]["status"] == "sent" and after["first_responded_at"]
    assert "reply_sent" in [item["kind"] for item in after["activities"]]


def test_a_whatsapp_ticket_and_the_agent_draft_tool(inbox: FakeInbox) -> None:
    account = ok(client.post(f"{BASE}/accounts", headers=MEMBER, json={"name": "Bakery Co"}), 201)
    ok(client.post(f"{BASE}/accounts/{account['id']}/contacts", headers=MEMBER, json={"name": "Huda Ali", "phone": "+971501234567", "consent_basis": "inbound_inquiry"}), 201)
    created = ok(client.post(f"{SUP}/tickets/from-message", headers=MEMBER, json={"source": "whatsapp", "ref_id": "wcon_1"}), 201)
    assert created["requester_phone"] == "+971501234567" and created["requester_verified"] and created["account_id"] == account["id"]
    registry = business_tools.register_business_tools(ToolRegistry())
    definition = registry.definition(business_tools.SUPPORT_REPLY_TOOL)
    assert definition is not None and definition.risk_level.value == "low" and definition.delivery_channel is None
    result = asyncio.run(business_tools.draft_support_reply(
        ToolCall(name=business_tools.SUPPORT_REPLY_TOOL, arguments={"ticket_id": created["id"], "body": "Hello Huda, we are checking the order screen."}), CONTEXT))
    assert result.status == "succeeded" and "not sent" in result.summary
    detail = ok(client.get(f"{SUP}/tickets/{created['id']}", headers=MEMBER))
    reply = detail["replies"][0]
    assert reply["generator"] == "agent" and reply["status"] == "draft" and reply["channel"] == "whatsapp" and inbox.requests == []
    requested = ok(client.post(f"{SUP}/tickets/{created['id']}/replies/{reply['id']}/request-approval", headers=MEMBER))
    assert requested["reply"]["status"] == "pending_approval" and inbox.requests[0][:2] == ("whatsapp", "wcon_1")
    manual = ticket()
    no_channel = asyncio.run(business_tools.draft_support_reply(ToolCall(name=business_tools.SUPPORT_REPLY_TOOL, arguments={"ticket_id": manual["id"], "body": "Hi"}), CONTEXT))
    assert no_channel.status == "failed" and "by hand" in no_channel.summary
    assert client.post(f"{SUP}/tickets/{manual['id']}/replies", headers=MEMBER, json={"body": "Hi"}).status_code == 409


def test_a_private_mailbox_message_cannot_become_a_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import email_inbox

    class Account:
        id = "acct_private"
        visibility = email_inbox.Visibility.PRIVATE

    monkeypatch.setattr(email_inbox, "load_message", lambda context, message_id: (Account(), object()))
    response = client.post(f"{SUP}/tickets/from-message", headers=MEMBER, json={"source": "email", "ref_id": "emsg_private"})
    assert response.status_code == 409 and "private mailbox" in response.json()["error"]["message"]


def test_deleting_is_for_owners_and_keeps_accounts_with_tickets() -> None:
    account = ok(client.post(f"{BASE}/accounts", headers=MEMBER, json={"name": "Keep Co"}), 201)
    created = ticket(account_id=account["id"])
    assert client.delete(f"{SUP}/tickets/{created['id']}", headers=MEMBER).status_code == 403
    blocked = client.delete(f"{BASE}/accounts/{account['id']}", headers=OWNER)
    assert blocked.status_code == 409 and "support tickets" in blocked.json()["error"]["message"]
    assert client.delete(f"{SUP}/tickets/{created['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{SUP}/tickets/{created['id']}", headers=OWNER).status_code == 404
    assert client.delete(f"{BASE}/accounts/{account['id']}", headers=OWNER).status_code == 204


def test_every_state_changing_support_route_is_audited_without_content(inbox: FakeInbox) -> None:
    from audit_probe import AuditProbe

    memory_store.audit_records.clear()
    probe = AuditProbe(client, "business_support")
    sent = ["sara@client.example", "Thanks Omar, the export works again now.", "Called Sara back about it", "Payroll export issue"]
    call = lambda method, path, **kwargs: probe.call(method, f"{SUP}{path}", OWNER, sent=sent, **kwargs)  # noqa: E731
    setup = ok(client.get(f"{SUP}/setup", headers=OWNER))
    call("PUT", "/policy", json=setup["policy"])
    manual = call("POST", "/tickets", json={"title": "Payroll export issue", "requester_email": "sara@client.example"}).json()
    call("PATCH", f"/tickets/{manual['id']}", json={"severity": "high"})
    call("POST", f"/tickets/{manual['id']}/status", json={"to": "triaged"})
    call("POST", f"/tickets/{manual['id']}/escalation", json={"escalated": True, "reason": "Urgent for payroll"})
    call("POST", f"/tickets/{manual['id']}/notes", json={"text": "Called Sara back about it", "responded": True})
    linked = call("POST", "/tickets/from-message", json={"source": "email", "ref_id": "emsg_9"}).json()
    first = call("POST", f"/tickets/{linked['id']}/replies", json={"body": "A first try that we will discard."}).json()["replies"][0]
    call("DELETE", f"/tickets/{linked['id']}/replies/{first['id']}")
    second = call("POST", f"/tickets/{linked['id']}/replies", json={"body": "Thanks Omar, the export works again."}).json()["replies"][-1]
    call("PATCH", f"/tickets/{linked['id']}/replies/{second['id']}", json={"body": "Thanks Omar, the export works again now."})
    call("POST", f"/tickets/{linked['id']}/replies/{second['id']}/request-approval")
    call("DELETE", f"/tickets/{manual['id']}")
    probe.assert_covered()
