"""Recurring invoices and the planned reminders (docs/business.md#recurring-invoices, #reminders-planned-by-the-daily-pass),
against the in-memory backend and a fake email tool.

A schedule drafts one ordinary invoice per period, once, through the invoice rules; asking to send it goes through the
owner's approval like a person's request. The daily pass plans reminders and invoice emails only where an owner turned
them on, each waiting for an owner's approval of its exact payload, once per invoice and step, never for a do not contact
account. Nothing reaches the fake email tool before an owner approves.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business import invoice_jobs
from anum_api.business.invoice_schedules import occurrence
from anum_api.business.store import business_store
from anum_api.dependencies import memory_repository
from anum_api.schemas import TenantContext, utc_now

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, VIEWER, approve, ok
from test_business_change_orders import project as make_project
from test_business_invoice_send import email_tool, make_overdue  # noqa: F401 - the fixture is used by name
from test_business_invoices import business_today, set_profile

client = TestClient(main.app)
SCOPE = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id="t", roles=["owner"])


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    business_store.clear()


def account_id() -> str:
    set_profile()
    return make_project(total=1000, tax=0)["account_id"]


def schedule_body(account: str, **changes: Any) -> dict[str, Any]:
    return {"title": "Monthly support", "account_id": account, "currency": "SAR", "frequency": "monthly", "start_date": business_today().isoformat(),
            "lines": [{"description": "Support retainer", "quantity": 1, "unit_price": 1000, "tax_rate_pct": 15}], "due_in_days": 14, **changes}


def create(headers: dict[str, str] = MEMBER, **body: Any) -> Any:
    return client.post(f"{BASE}/invoice-schedules", headers=headers, json=body)


def invoices_of(schedule_id: str) -> list[dict[str, Any]]:
    return ok(client.get(f"{BASE}/invoice-schedules/{schedule_id}", headers=MEMBER))["invoices"]


def backdate(schedule_id: str, start: date) -> None:
    """Move a schedule's start into the past, as time passing would."""
    scoped = business_store.scoped(SCOPE)
    item = scoped.get("invoice_schedules", schedule_id)
    scoped.save("invoice_schedules", item.model_copy(update={"start_date": start, "anchor_date": start, "next_date": start, "next_index": 0}))


def test_a_schedule_drafts_each_period_once_with_the_lines_and_the_terms() -> None:
    schedule = ok(create(**schedule_body(account_id(), customer_reference="PO-77", notes="Thanks")), 201)
    assert schedule["status"] == "active" and schedule["next_date"] == business_today().isoformat() and schedule["total"] == 1150
    assert len(schedule["upcoming"]) == 6 and schedule["invoices"] == []
    first = invoice_jobs.run()
    assert first["invoices_drafted"] == 1 and first["sent_for_approval"] == 0
    again = invoice_jobs.run()  # the same day again: nothing new
    assert again["invoices_drafted"] == 0
    made = invoices_of(schedule["id"])
    assert len(made) == 1 and made[0]["status"] == "draft" and made[0]["period_date"] == business_today().isoformat()
    invoice = ok(client.get(f"{BASE}/invoices/{made[0]['id']}", headers=MEMBER))
    assert invoice["schedule_id"] == schedule["id"] and invoice["total"] == 1150 and invoice["customer_reference"] == "PO-77"
    assert invoice["notes"] == "Thanks" and invoice["bill_to_name"] and invoice["can"]["request_send"] is True
    listed = ok(client.get(f"{BASE}/invoices", headers=MEMBER))["items"]
    assert listed[0]["schedule_id"] == schedule["id"]
    after = ok(client.get(f"{BASE}/invoice-schedules/{schedule['id']}", headers=MEMBER))
    expected_next = occurrence(business_store.scoped(SCOPE).get("invoice_schedules", schedule["id"]), 1)
    assert after["created_count"] == 1 and after["next_date"] == expected_next.isoformat()


def test_a_backlog_is_drafted_period_by_period_and_the_count_ends_it() -> None:
    schedule = ok(create(**schedule_body(account_id(), frequency="weeks", interval=1, max_count=3)), 201)
    backdate(schedule["id"], business_today() - timedelta(days=20))
    assert invoice_jobs.run()["invoices_drafted"] == 3
    after = ok(client.get(f"{BASE}/invoice-schedules/{schedule['id']}", headers=MEMBER))
    assert after["status"] == "ended" and after["end_reason"] == "count" and after["next_date"] is None and after["invoices_total"] == 3
    periods = sorted(item["period_date"] for item in after["invoices"])
    assert periods == [(business_today() - timedelta(days=20 - 7 * k)).isoformat() for k in range(3)]


def test_request_approval_mode_asks_the_owner_and_nothing_is_sent_by_itself() -> None:
    schedule = ok(create(**schedule_body(account_id(), issue_mode="request_approval")), 201)
    outcome = invoice_jobs.run()
    assert outcome["invoices_drafted"] == 1 and outcome["sent_for_approval"] == 1
    invoice = ok(client.get(f"{BASE}/invoices/{invoices_of(schedule['id'])[0]['id']}", headers=OWNER))
    assert invoice["status"] == "pending_approval" and invoice["approval"]["requested_by"] == "system:business-invoices"
    assert invoice["number"] is None  # numbered only when the owner approves
    assert approve(invoice["approval"]).status_code == 200
    assert ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))["status"] == "sent"


def test_a_draft_that_cannot_be_sent_waits_with_the_reason() -> None:
    account = account_id()
    client.put(f"{BASE}/billing-profile", headers=OWNER, json={"legal_name": ""})  # no seller name: sending is refused
    schedule = ok(create(**schedule_body(account, issue_mode="request_approval")), 201)
    outcome = invoice_jobs.run()
    assert outcome["invoices_drafted"] == 1 and outcome["approval_failed"] == 1
    after = ok(client.get(f"{BASE}/invoice-schedules/{schedule['id']}", headers=MEMBER))
    assert "billing profile" in after["last_error"] and after["invoices"][0]["status"] == "draft"


def test_pause_resume_skips_the_periods_passed_and_edit_changes_only_the_future() -> None:
    schedule = ok(create(**schedule_body(account_id())), 201)
    invoice_jobs.run()
    url = f"{BASE}/invoice-schedules/{schedule['id']}"
    current = ok(client.get(url, headers=MEMBER))
    paused = ok(client.post(f"{url}/pause", headers=MEMBER, json={"version": current["version"]}))
    assert paused["status"] == "paused" and paused["upcoming"] == []
    stale = client.post(f"{url}/resume", headers=MEMBER, json={"version": current["version"]})
    assert stale.status_code == 409 and "changed since you opened it" in stale.text
    resumed = ok(client.post(f"{url}/resume", headers=MEMBER, json={"version": paused["version"]}))
    assert resumed["status"] == "active" and resumed["next_date"] > business_today().isoformat()
    edited = ok(client.patch(url, headers=MEMBER, json={"version": resumed["version"], "lines": [{"description": "Support", "unit_price": 2000}]}))
    assert edited["total"] == 2000
    first_invoice = ok(client.get(f"{BASE}/invoices/{invoices_of(schedule['id'])[0]['id']}", headers=MEMBER))
    assert first_invoice["total"] == 1150  # already drafted: unchanged
    # A new schedule cannot start on or before an invoice already made.
    moved = client.patch(url, headers=MEMBER, json={"version": edited["version"], "start_date": (business_today() - timedelta(days=1)).isoformat()})
    assert moved.status_code == 422 and "stay as they are" in moved.text
    ended = ok(client.post(f"{url}/end", headers=MEMBER, json={"version": edited["version"]}))
    assert ended["status"] == "ended" and ended["end_reason"] == "owner"
    assert client.patch(url, headers=MEMBER, json={"version": ended["version"], "title": "x"}).status_code == 409


def test_delete_only_before_anything_was_drafted_and_run_now() -> None:
    account = account_id()
    unused = ok(create(**schedule_body(account, start_date=(business_today() + timedelta(days=5)).isoformat())), 201)
    nothing = ok(client.post(f"{BASE}/invoice-schedules/{unused['id']}/run", headers=MEMBER))
    assert nothing["drafted"] == 0 and "Nothing is due yet" in nothing["note"]
    assert client.delete(f"{BASE}/invoice-schedules/{unused['id']}", headers=MEMBER).status_code == 204
    used = ok(create(**schedule_body(account)), 201)
    ran = ok(client.post(f"{BASE}/invoice-schedules/{used['id']}/run", headers=MEMBER))
    assert ran["drafted"] == 1 and ran["schedule"]["invoices_total"] == 1
    refused = client.delete(f"{BASE}/invoice-schedules/{used['id']}", headers=MEMBER)
    assert refused.status_code == 409 and "End it instead" in refused.text


def test_validation_permissions_and_isolation() -> None:
    account = account_id()
    assert create(**schedule_body(account, lines=[])).status_code == 422
    assert create(**schedule_body(account, lines=[{"description": "Free", "unit_price": 0}])).status_code == 422
    assert create(**schedule_body(account, frequency="days", interval=400)).status_code == 422
    too_old = create(**schedule_body(account, start_date=(business_today() - timedelta(days=200)).isoformat()))
    assert too_old.status_code == 422 and "in the past" in too_old.text
    assert create(**schedule_body(account, end_date=(business_today() - timedelta(days=1)).isoformat())).status_code == 422
    assert create(**schedule_body("bacc_missing")).status_code == 404
    assert create(VIEWER, **schedule_body(account)).status_code == 403
    made = ok(create(**schedule_body(account)), 201)
    assert client.get(f"{BASE}/invoice-schedules", headers=VIEWER).status_code == 403  # Business is for owners and members
    assert client.get(f"{BASE}/invoice-schedules/{made['id']}", headers=OTHER_WORKSPACE).status_code == 404
    assert client.get(f"{BASE}/invoice-schedules/{made['id']}", headers=OTHER_TENANT).status_code == 404
    assert ok(client.get(f"{BASE}/invoice-schedules", headers=OTHER_WORKSPACE))["total"] == 0
    assert client.post(f"{BASE}/invoice-schedules/{made['id']}/pause", headers=VIEWER, json={"version": 1}).status_code == 403
    found = ok(client.get(f"{BASE}/invoice-schedules?q=monthly&status=active", headers=MEMBER))
    assert found["total"] == 1
    actions = [r.action for r in memory_repository.store.audit_records]
    assert "business.invoice_schedule.created" in actions
    # Audit rows carry ids and counts, never the lines or prices.
    assert all("Support retainer" not in str(r.metadata) for r in memory_repository.store.audit_records)


# -- the daily pass: planned reminders and invoice emails -------------------------------------------------


def sent_invoice(account: str) -> dict[str, Any]:
    schedule = ok(create(**schedule_body(account, issue_mode="request_approval")), 201)
    invoice_jobs.run()
    invoice = ok(client.get(f"{BASE}/invoices/{invoices_of(schedule['id'])[0]['id']}", headers=OWNER))
    assert approve(invoice["approval"]).status_code == 200
    return ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))


def automation(headers: dict[str, str] = OWNER, **body: Any) -> Any:
    return client.put(f"{BASE}/invoice-automation", headers=headers, json={"auto_reminders": True, "email_scheduled": True, "language": "en", **body})


def reset_day() -> None:
    scoped = business_store.scoped(SCOPE)
    row = scoped.first("invoice_automation")
    scoped.save("invoice_automation", row.model_copy(update={"last_run_on": None}))


def test_only_an_owner_turns_the_planned_emails_on() -> None:
    state = ok(client.get(f"{BASE}/invoice-automation", headers=MEMBER))
    assert state["auto_reminders"] is False and state["email_scheduled"] is False and state["can_change"] is False
    assert automation(MEMBER).status_code == 403
    assert automation(VIEWER).status_code == 403
    on = ok(automation())
    assert on["auto_reminders"] is True and on["can_change"] is True
    assert ok(client.get(f"{BASE}/invoice-automation", headers=OTHER_WORKSPACE))["auto_reminders"] is False


def test_nothing_is_planned_while_the_switches_are_off(email_tool: list[dict[str, Any]]) -> None:
    invoice = sent_invoice(account_id())
    make_overdue(invoice["id"], 3)
    outcome = invoice_jobs.run()
    assert outcome["reminders_planned"] == 0 and outcome["emails_planned"] == 0
    assert ok(client.get(f"{BASE}/invoices/{invoice['id']}/messages", headers=MEMBER)) == []


def test_the_invoice_email_and_each_reminder_step_are_planned_once_and_wait_for_the_owner(email_tool: list[dict[str, Any]]) -> None:
    invoice = sent_invoice(account_id())
    ok(automation())
    first = invoice_jobs.run()
    assert first["emails_planned"] == 1 and first["reminders_planned"] == 0
    messages = ok(client.get(f"{BASE}/invoices/{invoice['id']}/messages", headers=MEMBER))
    assert [(m["kind"], m["status"]) for m in messages] == [("invoice", "waiting_approval")] and email_tool == []
    # Once a day: the same day plans nothing more.
    assert invoice_jobs.run()["emails_planned"] == 0
    # Overdue by 2 days: step 1. The owner rejects it; the pass never asks for step 1 again.
    make_overdue(invoice["id"], 2)
    reset_day()
    second = invoice_jobs.run()
    assert second["reminders_planned"] == 1
    reminder = [m for m in ok(client.get(f"{BASE}/invoices/{invoice['id']}/messages", headers=MEMBER)) if m["kind"] == "reminder"][0]
    assert reminder["schedule_step"] == 1 and reminder["status"] == "waiting_approval" and reminder["requested_by"] == "system:business-invoices"
    assert client.post(f"/api/v1/approvals/{reminder['approval_id']}/reject", headers=OWNER, json={"reason": "Customer called"}).status_code == 200
    reset_day()
    assert invoice_jobs.run()["reminders_planned"] == 0
    # Overdue by 8 days: step 2 is planned; the owner approves and only then does it go out.
    make_overdue(invoice["id"], 8)
    reset_day()
    assert invoice_jobs.run()["reminders_planned"] == 1
    step2 = [m for m in ok(client.get(f"{BASE}/invoices/{invoice['id']}/messages", headers=MEMBER)) if m["schedule_step"] == 2][0]
    approval = next(a for a in ok(client.get("/api/v1/approvals", headers=OWNER)) if a["id"] == step2["approval_id"])
    assert email_tool == []
    assert approve(approval).status_code == 200
    assert len(email_tool) == 1 and "is still open" in email_tool[0]["text"]
    reset_day()
    assert invoice_jobs.run()["reminders_planned"] == 0  # step 2 done; step 3 starts at 14 days


def test_a_step_a_person_already_asked_for_is_not_planned_again(email_tool: list[dict[str, Any]]) -> None:
    invoice = sent_invoice(account_id())
    ok(automation(email_scheduled=False))
    make_overdue(invoice["id"], 2)
    ok(client.post(f"{BASE}/invoices/{invoice['id']}/reminders", headers=MEMBER, json={}), 201)
    assert invoice_jobs.run()["reminders_planned"] == 0


def test_a_do_not_contact_account_gets_nothing(email_tool: list[dict[str, Any]]) -> None:
    invoice = sent_invoice(account_id())
    scoped = business_store.scoped(SCOPE)
    account = scoped.get("accounts", invoice["account_id"])
    scoped.save("accounts", account.model_copy(update={"do_not_contact": True}))
    ok(automation())
    make_overdue(invoice["id"], 3)
    outcome = invoice_jobs.run()
    assert outcome["skipped_do_not_contact"] == 1 and outcome["reminders_planned"] == 0 and outcome["emails_planned"] == 0
    summary = ok(client.get(f"{BASE}/invoice-automation", headers=OWNER))["last_run_summary"]
    assert summary["skipped_do_not_contact"] == 1


def test_without_an_email_integration_the_run_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = sent_invoice(account_id())
    ok(automation(email_scheduled=False))
    make_overdue(invoice["id"], 3)
    ran = ok(client.post(f"{BASE}/invoice-automation/run", headers=OWNER))
    assert ran["last_run_summary"]["failed"] == 1 and "No email integration" in ran["last_run_summary"]["note"]
    assert client.post(f"{BASE}/invoice-automation/run", headers=MEMBER).status_code == 403


def test_due_step_counts_thresholds() -> None:
    from anum_api.business.invoice_models import Invoice, InvoiceStatus

    base = Invoice(id="i", tenant_id="t", workspace_id="w", account_id="a", status=InvoiceStatus.SENT, total=100, created_by="x",
                   created_at=utc_now(), updated_at=utc_now())
    today = date(2026, 11, 20)
    assert invoice_jobs.due_step(base.model_copy(update={"due_date": today}), today) == 0
    assert invoice_jobs.due_step(base.model_copy(update={"due_date": today - timedelta(days=1)}), today) == 1
    assert invoice_jobs.due_step(base.model_copy(update={"due_date": today - timedelta(days=13)}), today) == 2
    assert invoice_jobs.due_step(base.model_copy(update={"due_date": today - timedelta(days=40)}), today) == 4
    assert invoice_jobs.due_step(base.model_copy(update={"due_date": today - timedelta(days=40), "amount_paid": 100}), today) == 0


def test_a_period_that_cannot_be_drafted_says_why_without_new_versions() -> None:
    """A schedule whose account is gone keeps the reason; repeated passes do not bump its version (an edit is not interrupted)."""
    schedule = ok(create(**schedule_body(account_id())), 201)
    business_store.scoped(SCOPE).delete("accounts", schedule["account_id"])
    assert invoice_jobs.run()["invoices_drafted"] == 0
    first = ok(client.get(f"{BASE}/invoice-schedules/{schedule['id']}", headers=MEMBER))
    assert "account" in first["last_error"] and first["created_count"] == 0
    invoice_jobs.run()
    again = ok(client.get(f"{BASE}/invoice-schedules/{schedule['id']}", headers=MEMBER))
    assert again["version"] == first["version"] == schedule["version"]
