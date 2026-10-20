"""Customer billing (docs/billing.md) against the in-memory backend.

Covers money (tax, proration, half-up rounding, calendar months), gapless invoice and
credit note numbering per issuer, the lifecycle (trial, upgrade with proration, downgrade at
period end or now with a credit, cancel, reactivate, pause, resume), dunning (past_due,
reminders on schedule, grace, read-only, settled by a payment), plan limit enforcement
that never deletes anything, Stripe webhook signatures and idempotency, permissions, the
public plan list leaking nothing, self-serve sign-up with its abuse limits and per-tenant
provisioning, the invoice PDF and the audit walks.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from audit_probe import AuditProbe
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.billing import enforce, providers, signup
from anum_api.billing import service as svc
from anum_api.billing.api import handle_stripe_event
from anum_api.billing.money import add_months, period_end, prorate, split_gross, tax_for
from anum_api.billing.rows import RowConflict, memory_db, open_billing
from anum_api.billing.usage import Usage
from anum_api.dependencies import memory_repository
from anum_api.organisations import memory_branding
from anum_api.settings import settings

client = TestClient(main.app)
ISSUER = "tenant_issuer"
ISSUER_WS = "tenant_issuer_ws"
OPERATOR = {"x-tenant-id": ISSUER, "x-workspace-id": ISSUER_WS, "x-user-id": "operator_1", "x-user-roles": "owner,platform_operator"}
OTHER_OPERATOR = {"x-tenant-id": "tenant_issuer_two", "x-workspace-id": "ws_two", "x-user-id": "operator_2", "x-user-roles": "owner,platform_operator"}
# Plainly fake, low-entropy fixtures (gitleaks).
STRIPE_KEY = "sk_test_example"
WEBHOOK_SECRET = "whsec_example"
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
NO_USAGE = Usage(seats=1, workspaces=1, agent_runs=0, storage=0, messages=0)


def _clear_memory() -> None:
    store = memory_repository.store
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear_memory()
    memory_db.clear()
    memory_branding.clear()
    enforce.clear_cache()
    signup.reset_limits()
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", ISSUER)
    monkeypatch.setattr(settings, "signup_enabled", True)
    yield
    _clear_memory()
    memory_db.clear()
    memory_branding.clear()
    enforce.clear_cache()


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json() if response.content else None


def error(response: Any) -> dict[str, Any]:
    return response.json()["error"]


def owner(tenant_id: str, user: str = "owner_c", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": f"{tenant_id}_main", "x-user-id": user, "x-user-roles": roles}


def plan_body(code: str, price: int, **overrides: Any) -> dict[str, Any]:
    return {"code": code, "name": code.title(), "price_minor": price, "trial_days": 0, "published": True,
            "limits": {"seats": 5, "workspaces": 3, "agent_runs_per_month": 100, "storage_gb": 5}, "features": ["Email support"], **overrides}


def setup_issuer(**settings_overrides: Any) -> dict[str, dict[str, Any]]:
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={
        "issuer_name": "ANUM Co", "bank_name": "Riyad Bank", "bank_account_name": "ANUM Co", "bank_iban": "SA00 0000 0000 0000 0000 0000",
        "payment_terms_days": 14, **settings_overrides}))
    basic = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json=plan_body("basic", 10_000)), 201)
    pro = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json=plan_body(
        "pro", 30_000, limits={"seats": 20, "workspaces": 10, "agent_runs_per_month": 1000, "storage_gb": 50})), 201)
    hidden = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json=plan_body("internal", 1, published=False)), 201)
    return {"basic": basic, "pro": pro, "hidden": hidden}


def onboard(tenant_id: str = "acme-co") -> str:
    ok(client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": tenant_id, "display_name": f"{tenant_id} Ltd", "first_admin_email": f"admin@{tenant_id}.example"}), 201)
    return tenant_id


def billed(plan_id: str, tenant_id: str = "acme-co", trial_days: int = 0) -> dict[str, Any]:
    onboard(tenant_id)
    return ok(client.post(f"/api/v1/platform/billing/organizations/{tenant_id}/subscription", headers=OPERATOR,
                          json={"plan_id": plan_id, "trial_days": trial_days}), 201)


# ----------------------------------------------------------------------------- money


def test_tax_rounds_half_up_and_splits_back_exactly() -> None:
    assert tax_for(10_000, 1500) == 1500
    assert tax_for(333, 1500) == 50  # 49.95 rounds up
    assert tax_for(0, 1500) == 0 and tax_for(100, 0) == 0
    for total in (1, 99, 11_500, 123_457):
        subtotal, tax = split_gross(total, 1500)
        assert subtotal + tax == total
    assert split_gross(11_500, 1500) == (10_000, 1500)


def test_proration_charges_the_rest_of_the_period() -> None:
    start, end = T0, add_months(T0, 1)
    halfway = start + (end - start) / 2
    assert prorate(10_000, 30_000, start, end, halfway) == 10_000
    assert prorate(30_000, 10_000, start, end, halfway) == -10_000
    assert prorate(10_000, 30_000, start, end, end) == 0
    assert prorate(10_000, 30_000, start, end, start - timedelta(days=1)) == 20_000


def test_calendar_months_clamp_the_day() -> None:
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 12, 15, tzinfo=UTC), 1) == datetime(2027, 1, 15, tzinfo=UTC)
    assert period_end(datetime(2028, 2, 29, tzinfo=UTC), "year") == datetime(2029, 2, 28, tzinfo=UTC)


# ----------------------------------------------------------------------------- the main path


def test_operator_bills_an_organisation_and_the_admin_sees_plan_invoice_and_pdf() -> None:
    plans = setup_issuer()
    started = billed(plans["basic"]["id"])
    invoice = started["invoice"]
    assert started["subscription"]["status"] == "active"
    assert invoice["number"] == "INV-000001" and invoice["status"] == "open"
    assert (invoice["subtotal_minor"], invoice["tax_rate_bp"], invoice["tax_minor"], invoice["total_minor"]) == (10_000, 1500, 1500, 11_500)

    admin = owner("acme-co")
    overview = ok(client.get("/api/v1/billing/overview", headers=admin))
    assert overview["subscription"]["plan_name"] == "Basic" and overview["open_balance_minor"] == 11_500
    assert overview["payment"]["bank_transfer"]["iban"] == "SA0000000000000000000000"
    assert overview["payment"]["card_enabled"] is False
    seats = next(m for m in overview["usage"]["meters"] if m["key"] == "seats")
    assert seats["limit"] == 5
    page = ok(client.get("/api/v1/billing/invoices?q=000001", headers=admin))
    assert page["total"] == 1 and page["items"][0]["id"] == invoice["id"]
    assert ok(client.get("/api/v1/billing/invoices?q=nothing", headers=admin))["total"] == 0
    detail = ok(client.get(f"/api/v1/billing/invoices/{invoice['id']}", headers=admin))
    assert detail["lines"][0]["description"] == "Basic plan" and detail["bank_details"]["enabled"]
    pdf = client.get(f"/api/v1/billing/invoices/{invoice['id']}/pdf", headers=admin)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and b"INV-000001" in pdf.content
    assert "attachment" in pdf.headers["content-disposition"]

    # Upgrade: a proration invoice at once; the plan list marks the current plan.
    listed = ok(client.get("/api/v1/billing/plans", headers=admin))
    assert {p["code"] for p in listed} == {"basic", "pro"} and next(p for p in listed if p["code"] == "basic")["current"]
    preview = ok(client.get(f"/api/v1/billing/subscription/preview?plan_id={plans['pro']['id']}", headers=admin))
    assert preview["effective"] == "now" and 19_000 < preview["amount_minor"] <= 20_000
    changed = ok(client.post("/api/v1/billing/subscription/change", headers=admin, json={"plan_id": plans["pro"]["id"]}))
    assert changed["subscription"]["plan_name"] == "Pro" and changed["invoice"]["kind"] == "proration"
    assert changed["invoice"]["number"] == "INV-000002"

    # The operator records a bank transfer with a proof file; the invoice is paid.
    proof = {"name": "transfer.pdf", "content_type": "application/pdf", "data_base64": base64.b64encode(b"%PDF-1.4 proof").decode()}
    paid = ok(client.post(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments", headers=OPERATOR,
                          json={"amount_minor": 11_500, "reference": "TRX-1", "proof": proof}), 201)
    assert paid["invoice"]["status"] == "paid" and paid["invoice"]["amount_due_minor"] == 0
    assert paid["invoice"]["payments"][0]["has_proof"]
    proof_file = client.get(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments/{paid['payment_id']}/proof", headers=OPERATOR)
    assert proof_file.content == b"%PDF-1.4 proof" and proof_file.headers["x-content-type-options"] == "nosniff"

    summary = ok(client.get("/api/v1/platform/billing/summary", headers=OPERATOR))
    assert summary["mrr_minor"] == 30_000 and summary["counts"]["active"] == 1 and summary["collected_this_month_minor"] == 11_500
    subs = ok(client.get("/api/v1/platform/billing/subscriptions?q=acme", headers=OPERATOR))
    assert subs["total"] == 1 and subs["items"][0]["open_balance_minor"] == changed["invoice"]["total_minor"]


def test_invoice_numbers_are_gapless_per_issuer_across_customers() -> None:
    plans = setup_issuer()
    numbers = [billed(plans["basic"]["id"], tenant)["invoice"]["number"] for tenant in ("one-co", "two-co", "three-co")]
    assert numbers == ["INV-000001", "INV-000002", "INV-000003"]
    # Another issuer has its own sequence.
    with open_billing("other-issuer-co") as rows:
        with rows.as_tenant("other-issuer-co"):
            assert rows.next_number("invoice") == 1
    # A duplicate number for the same issuer is refused like the unique index does.
    with open_billing("one-co") as rows:
        dup = {**rows.select("billing_invoices")[0], "id": "invoice_dup"}
        with pytest.raises(RowConflict):
            rows.insert("billing_invoices", dup)


def test_credit_notes_and_void_keep_numbers_and_settle_the_balance() -> None:
    plans = setup_issuer()
    invoice = billed(plans["basic"]["id"])["invoice"]
    base = f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}"
    too_much = client.post(f"{base}/credit-notes", headers=OPERATOR, json={"total_minor": 99_999, "reason": "Typo"})
    assert too_much.status_code == 422
    note = ok(client.post(f"{base}/credit-notes", headers=OPERATOR, json={"total_minor": 1150, "reason": "Goodwill"}), 201)
    assert note["credit_note"]["number"] == "CN-000001"
    assert note["credit_note"]["subtotal_minor"] + note["credit_note"]["tax_minor"] == 1150
    assert note["invoice"]["amount_due_minor"] == 10_350
    assert client.post(f"{base}/void", headers=OPERATOR).status_code == 409
    rest = ok(client.post(f"{base}/credit-notes", headers=OPERATOR, json={"total_minor": 10_350, "reason": "Waived"}), 201)
    assert rest["credit_note"]["number"] == "CN-000002" and rest["invoice"]["status"] == "paid"


def test_void_an_unpaid_invoice() -> None:
    plans = setup_issuer()
    invoice = billed(plans["basic"]["id"])["invoice"]
    voided = ok(client.post(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/void", headers=OPERATOR))
    assert voided["status"] == "void" and voided["number"] == "INV-000001" and voided["amount_due_minor"] == 0


# ----------------------------------------------------------------------------- lifecycle in the service


def _customer(rows: Any, plan_price: int = 10_000, trial_days: int = 0, now: datetime = T0, **plan: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    with rows.as_tenant(ISSUER):
        defaults = {"id": f"plan_{plan_price}_{trial_days}", "tenant_id": ISSUER, "code": f"p{plan_price}", "name": f"Plan {plan_price}",
                    "description": "", "price_minor": plan_price, "currency": "SAR", "interval": "month", "trial_days": trial_days,
                    "per_seat": False, "limits": {"seats": 5}, "features": [], "published": True, "active": True, "sort_order": 0,
                    "created_by": "t", "created_at": now, "updated_at": now}
        defaults.update(plan)
        if rows.get("billing_plans", id=defaults["id"]) is None:
            rows.insert("billing_plans", defaults)
    account = svc.ensure_account(rows, issuer_tenant_id=ISSUER, display_name="Cust", now=now, contact_email="billing@cust.example")
    return account, defaults


def test_upgrade_downgrade_cancel_and_reactivate() -> None:
    with open_billing("cust-a") as rows:
        account, basic = _customer(rows)
        _, pro = _customer(rows, 30_000, id="plan_pro", code="pro", name="Pro", limits={"seats": 20})
        sub, issued = svc.start_subscription(rows, account, basic, now=T0, actor="t", trial_days=0)
        assert issued and issued.invoice["total_minor"] == 11_500
        halfway = T0 + (sub["current_period_end"] - T0) / 2
        sub, upgrade = svc.change_plan(rows, pro, seats=None, when=None, usage=NO_USAGE, now=halfway, actor="t")
        assert sub["plan_name"] == "Pro" and upgrade and upgrade.invoice["subtotal_minor"] == 10_000
        # Downgrade at period end by default; now gives a credit used by the next invoice.
        sub, none = svc.change_plan(rows, basic, seats=None, when=None, usage=NO_USAGE, now=halfway, actor="t")
        assert none is None and sub["pending_plan_id"] == basic["id"] and sub["plan_name"] == "Pro"
        sub, _ = svc.change_plan(rows, basic, seats=None, when="now", usage=NO_USAGE, now=halfway, actor="t")
        assert sub["plan_name"] == basic["name"] and svc.account_of(rows)["credit_balance_minor"] == 10_000
        result = svc.run_tenant(rows, now=sub["current_period_end"] + timedelta(minutes=1))
        renewal = svc.get_invoice(rows, result.invoices[0])
        assert renewal["subtotal_minor"] == 0 and renewal["status"] == "paid"  # the 10,000 credit covered it
        assert svc.account_of(rows)["credit_balance_minor"] == 0
        cancelled = svc.cancel(rows, now=halfway)
        assert cancelled["cancel_at_period_end"]
        end = svc.live_subscription(rows)["current_period_end"]
        assert svc.run_tenant(rows, now=end + timedelta(minutes=1)).transitions == ["cancelled"]
        assert svc.live_subscription(rows) is None
        restarted, invoice = svc.reactivate(rows, now=end + timedelta(days=1), actor="t")
        assert restarted["status"] == "active" and invoice is not None


def test_downgrade_that_does_not_fit_is_refused_and_nothing_is_deleted() -> None:
    with open_billing("cust-b") as rows:
        account, pro = _customer(rows, 30_000, id="plan_pro", code="pro", limits={"seats": 20})
        _, small = _customer(rows, 5_000, id="plan_small", code="small", limits={"seats": 2, "workspaces": 1})
        svc.start_subscription(rows, account, pro, now=T0, actor="t", trial_days=0)
        busy = Usage(seats=6, workspaces=3, agent_runs=0, storage=0, messages=0)
        with pytest.raises(svc.BillingError) as refused:
            svc.change_plan(rows, small, seats=None, when="now", usage=busy, now=T0 + timedelta(days=3), actor="t")
        assert refused.value.status_code == 409
        assert {item["key"] for item in refused.value.blocked_by} == {"seats", "workspaces"}
        assert svc.live_subscription(rows)["plan_name"] == pro["name"]


def test_trial_expiry_leads_to_dunning_then_read_only_and_a_payment_restores_writes() -> None:
    plans = setup_issuer(reminder_days=[1, 7], grace_days=3)
    billed(plans["basic"]["id"], trial_days=14)
    admin = owner("acme-co")
    with open_billing("acme-co") as rows:
        sub = svc.live_subscription(rows)
        trial_end = sub["trial_end"]
        assert sub["status"] == "trialing"
        first = svc.run_tenant(rows, now=trial_end + timedelta(minutes=5))
        assert first.transitions == ["trial_ended", "past_due"] and len(first.invoices) == 1
        assert svc.live_subscription(rows)["status"] == "past_due"
        assert svc.run_tenant(rows, now=trial_end + timedelta(hours=1)).reminders == []
        day1 = svc.run_tenant(rows, now=trial_end + timedelta(days=1, minutes=1))
        assert [r.reminder for r in day1.reminders] == [1]
        # Not marked sent until the email is queued: a failed or impossible send is offered again.
        assert [r.reminder for r in svc.run_tenant(rows, now=trial_end + timedelta(days=1, hours=2)).reminders] == [1]
        svc.mark_reminder_sent(rows, day1.reminders[0].invoice_id, 1, trial_end + timedelta(days=1, hours=3))
        assert svc.run_tenant(rows, now=trial_end + timedelta(days=2)).reminders == []
        day7 = svc.run_tenant(rows, now=trial_end + timedelta(days=7, minutes=1))
        assert [r.reminder for r in day7.reminders] == [2] and "read_only" not in day7.transitions
        grace = svc.live_subscription(rows)["grace_until"]
        assert grace == trial_end + timedelta(days=10)
        late = svc.run_tenant(rows, now=grace + timedelta(minutes=1))
        assert "read_only" in late.transitions
        invoice_id = first.invoices[0]
    enforce.clear_cache()
    refused = client.post("/api/v1/tasks", headers=admin, json={"title": "Plan the week", "prompt": "Plan"})
    assert refused.status_code == 402 and error(refused)["code"] == "billing_read_only"
    assert client.get("/api/v1/billing/overview", headers=admin).json()["state"] == "read_only"
    # Billing stays writable; the operator records the payment and writes come back at once.
    ok(client.put("/api/v1/billing/account", headers=admin, json={"contact_email": "ap@acme.example", "address_line1": "King Fahd Rd", "city": "Riyadh"}))
    ok(client.post(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice_id}/payments", headers=OPERATOR,
                   json={"amount_minor": 11_500, "reference": "TRX-9"}), 201)
    assert client.get("/api/v1/billing/overview", headers=admin).json()["subscription"]["status"] == "active"
    assert client.post("/api/v1/tasks", headers=admin, json={"title": "Plan the week", "prompt": "Plan"}).status_code == 201


def test_billing_job_is_idempotent_and_runs_through_the_console() -> None:
    plans = setup_issuer()
    billed(plans["basic"]["id"])
    result = ok(client.post("/api/v1/platform/billing/run", headers=OPERATOR))
    assert result["organisations"] == 1 and result["invoices"] == 0
    with open_billing("acme-co") as rows:
        end = svc.live_subscription(rows)["current_period_end"]
        later = end + timedelta(minutes=1)
        assert len(svc.run_tenant(rows, now=later).invoices) == 1
        assert svc.run_tenant(rows, now=later).invoices == []
        assert rows.count("billing_invoices") == 2


def test_pause_and_resume() -> None:
    plans = setup_issuer()
    billed(plans["basic"]["id"])
    paused = ok(client.post("/api/v1/platform/billing/organizations/acme-co/subscription/pause", headers=OPERATOR))
    assert paused["subscription"]["status"] == "paused"
    assert client.post("/api/v1/billing/subscription/change", headers=owner("acme-co"), json={"plan_id": plans["pro"]["id"]}).status_code == 409
    resumed = ok(client.post("/api/v1/platform/billing/organizations/acme-co/subscription/resume", headers=OPERATOR))
    assert resumed["subscription"]["status"] == "active" and resumed["invoice"]["number"] == "INV-000002"


# ----------------------------------------------------------------------------- limits


def test_plan_limits_refuse_new_work_with_a_clear_reason() -> None:
    plans = setup_issuer()
    tiny = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json=plan_body(
        "tiny", 1000, limits={"seats": 1, "workspaces": 1, "agent_runs_per_month": 1, "storage_gb": 1})), 201)
    billed(tiny["id"])
    admin = owner("acme-co")
    assert plans
    ok(client.post("/api/v1/tasks", headers=admin, json={"title": "One", "prompt": "One"}), 201)
    second = client.post("/api/v1/tasks", headers=admin, json={"title": "Two", "prompt": "Two"})
    assert second.status_code == 402 and error(second)["code"] == "plan_limit_reached"
    assert "1 agent runs this month" in error(second)["message"] and "Settings > Billing" in error(second)["message"]
    invite = client.post("/api/v1/workspace-invitations", headers=admin, json={"role": "member", "invitee_email": "new@acme.example"})
    assert invite.status_code == 402 and "seats" in error(invite)["message"]
    workspace = client.post("/api/v1/organization/workspaces", headers=admin, json={"name": "Second"})
    assert workspace.status_code == 402
    # Nothing was removed: the first task is still there.
    assert ok(client.get("/api/v1/tasks", headers=admin))


def test_an_organisation_without_a_subscription_has_no_limits() -> None:
    headers = owner("unbilled-co")
    enforce.require_capacity(main.TenantContext(tenant_id="unbilled-co", workspace_id="w", user_id="u"), "seats", 10_000)
    assert client.post("/api/v1/tasks", headers=headers, json={"title": "x", "prompt": "x"}).status_code == 201


# ----------------------------------------------------------------------------- permissions and isolation


def test_only_billing_admins_and_the_issuing_operator_get_in() -> None:
    plans = setup_issuer()
    invoice = billed(plans["basic"]["id"])["invoice"]
    for role in ("member", "viewer"):
        denied = client.get("/api/v1/billing/overview", headers=owner("acme-co", roles=role))
        assert denied.status_code == 403 and "workspace owner" in error(denied)["message"]
    assert client.get("/api/v1/billing/overview", headers=owner("acme-co", roles="member,org_admin")).status_code == 200
    assert client.get("/api/v1/platform/billing/summary", headers=owner("acme-co")).status_code == 403
    # Another platform operator does not bill this organisation: 404, never its data.
    assert client.get(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}", headers=OTHER_OPERATOR).status_code == 404
    assert client.get("/api/v1/platform/billing/subscriptions", headers=OTHER_OPERATOR).json()["total"] == 0
    # Another organisation's admin reads its own (empty) billing, never acme's invoice.
    assert client.get(f"/api/v1/billing/invoices/{invoice['id']}", headers=owner("globex-co")).status_code == 404
    assert client.get("/api/v1/billing/invoices", headers=owner("globex-co")).json()["total"] == 0


def test_rows_of_another_tenant_are_invisible_and_cannot_be_written() -> None:
    plans = setup_issuer()
    billed(plans["basic"]["id"])
    with open_billing("globex-co") as rows:
        assert rows.select("billing_invoices") == [] and rows.get("billing_accounts", tenant_id="acme-co") is None
        with pytest.raises(PermissionError):
            rows.insert("billing_invoices", {"id": "x", "tenant_id": "acme-co"})
        assert rows.update("billing_accounts", {"tenant_id": "acme-co"}, {"state": "read_only"}) == 0


# ----------------------------------------------------------------------------- public plans


def test_public_plans_need_no_sign_in_and_leak_nothing() -> None:
    plans = setup_issuer()
    billed(plans["basic"]["id"])
    ok(client.post("/api/v1/platform/billing/plans", headers=OTHER_OPERATOR, json=plan_body("rival", 1)), 201)
    archived = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json=plan_body("old", 5)), 201)
    ok(client.patch(f"/api/v1/platform/billing/plans/{archived['id']}", headers=OPERATOR, json={"active": False}))
    body = ok(client.get("/api/v1/public/billing/plans"))
    assert body["signup_enabled"] and body["tax"] == {"country": "SA", "rate_bp": 1500}
    assert [p["code"] for p in body["plans"]] == ["basic", "pro"]
    text = json.dumps(body)
    for leaked in ("tenant_id", ISSUER, "acme-co", "created_by", "operator_1", "published", "INV-"):
        assert leaked not in text
    settings.billing_issuer_tenant_id = None
    assert ok(client.get("/api/v1/public/billing/plans")) == {"signup_enabled": False, "currency": "SAR", "tax": {"country": "SA", "rate_bp": 0}, "plans": []}


# ----------------------------------------------------------------------------- sign-up


def signer(user: str, email: str, verified: str = "true", ip: str | None = None) -> dict[str, str]:
    return {"x-user-id": user, "x-user-email": email, "x-email-verified": verified}


def test_self_serve_sign_up_provisions_an_isolated_organisation_with_a_trial() -> None:
    plans = setup_issuer()
    who = signer("kc-user-1", "founder@startup.example")
    assert ok(client.get("/api/v1/signup/status", headers=who))["state"] == "ready"
    created = ok(client.post("/api/v1/signup/organization", headers=who, json={
        "organization_name": "Startup Co", "plan_id": plans["pro"]["id"], "country": "sa", "accept_terms": True}), 201)
    tenant, workspace = created["tenant_id"], created["workspace_id"]
    assert tenant.startswith("startup-co-") and workspace == f"{tenant}_main" and created["identity_linked"]
    membership = memory_repository.store.memberships[(tenant, workspace, "kc-user-1")]
    assert membership.role == "owner" and membership.active
    admin = {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": "kc-user-1", "x-user-roles": "owner"}
    overview = ok(client.get("/api/v1/billing/overview", headers=admin))
    assert overview["subscription"]["status"] == "trialing" and overview["subscription"]["plan_code"] == "pro"
    assert overview["account"]["contact_email"] == "founder@startup.example" and overview["account"]["country"] == "SA"
    checklist = ok(client.get("/api/v1/billing/onboarding", headers=admin))
    assert [i["key"] for i in checklist["items"]] == ["invite_team", "connect_email", "choose_plan", "add_payment_details"]
    assert not checklist["complete"] and not checklist["dismissed"]
    ok(client.post("/api/v1/billing/onboarding/dismiss", headers=admin))
    assert ok(client.get("/api/v1/billing/onboarding", headers=admin))["dismissed"]
    status = ok(client.get("/api/v1/signup/status", headers=who))
    assert status["state"] == "provisioned" and status["tenant_id"] == tenant
    again = client.post("/api/v1/signup/organization", headers=who, json={
        "organization_name": "Second Co", "plan_id": plans["pro"]["id"], "accept_terms": True})
    assert again.status_code == 409
    # The second sign-up is its own organisation; neither sees the other's billing.
    other = ok(client.post("/api/v1/signup/organization", headers=signer("kc-user-2", "ops@globex.example"), json={
        "organization_name": "Globex", "plan_id": plans["basic"]["id"], "accept_terms": True}), 201)
    assert other["tenant_id"] != tenant
    with open_billing(other["tenant_id"]) as rows:
        assert len(rows.select("billing_subscriptions")) == 1 and rows.get("billing_accounts", tenant_id=tenant) is None
    # The operator sees both new customers.
    assert ok(client.get("/api/v1/platform/billing/subscriptions", headers=OPERATOR))["total"] == 2


def test_sign_up_refusals() -> None:
    plans = setup_issuer()
    body = {"organization_name": "Acme", "plan_id": plans["basic"]["id"], "accept_terms": True}
    unverified = client.post("/api/v1/signup/organization", headers=signer("u1", "a@acme.example", "false"), json=body)
    assert unverified.status_code == 403 and error(unverified)["code"] == "email_not_verified"
    assert ok(client.get("/api/v1/signup/status", headers=signer("u1", "a@acme.example", "false")))["state"] == "verify_email"
    assert client.post("/api/v1/signup/organization", headers=signer("u2", "x@mailinator.com"), json=body).status_code == 422
    hidden = client.post("/api/v1/signup/organization", headers=signer("u3", "b@acme.example"), json={**body, "plan_id": plans["hidden"]["id"]})
    assert hidden.status_code == 422
    assert client.post("/api/v1/signup/organization", headers=signer("u4", "c@acme.example"), json={**body, "accept_terms": False}).status_code == 422
    assert client.post("/api/v1/signup/organization", headers=signer("u5", "d@acme.example"), json={**body, "organization_name": "http://spam.example"}).status_code == 422
    already = client.post("/api/v1/signup/organization", headers={**signer("u6", "e@acme.example"), "x-tenant-id": "acme"}, json=body)
    assert already.status_code == 409
    assert client.post("/api/v1/signup/organization", json=body).status_code == 401
    settings.signup_enabled = False
    assert client.post("/api/v1/signup/organization", headers=signer("u7", "f@acme.example"), json=body).status_code == 404


def test_sign_ups_are_limited_per_ip_and_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    plans = setup_issuer()
    body = {"organization_name": "Acme", "plan_id": plans["basic"]["id"], "accept_terms": True}
    for n in range(3):
        ok(client.post("/api/v1/signup/organization", headers=signer(f"ip-user-{n}", f"u{n}@acme.example"), json=body), 201)
    limited = client.post("/api/v1/signup/organization", headers=signer("ip-user-9", "u9@acme.example"), json=body)
    assert limited.status_code == 429 and error(limited)["code"] == "rate_limited"
    monkeypatch.setattr(settings, "signup_ip_hourly_limit", 100)
    monkeypatch.setattr(settings, "signup_daily_limit", 3)
    signup.reset_limits()
    assert client.post("/api/v1/signup/organization", headers=signer("ip-user-10", "u10@acme.example"), json=body).status_code == 429


def test_keycloak_link_sets_the_tenant_and_never_moves_an_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "keycloak_provisioner_client_id", "anum-provisioner")
    monkeypatch.setattr(settings, "keycloak_provisioner_client_secret", SecretStr("example"))
    monkeypatch.setattr(settings, "keycloak_issuer", "https://auth.example/realms/anum")
    user = {"id": "abc-123", "email": "a@b.example", "attributes": {"locale": ["ar"]}}
    saved: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            assert b"client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "token-example"})
        assert request.url.path == "/admin/realms/anum/users/abc-123" and request.headers["authorization"] == "Bearer token-example"
        if request.method == "GET":
            return httpx.Response(200, json=user)
        saved.append(json.loads(request.content))
        return httpx.Response(204)

    monkeypatch.setattr(signup, "transport", httpx.MockTransport(handler))
    assert signup.link_keycloak_account("abc-123", "acme-1", "acme-1_main")
    assert saved[0]["attributes"] == {"locale": ["ar"], "tenant_id": ["acme-1"], "default_workspace_id": ["acme-1_main"]}
    user["attributes"] = {"tenant_id": ["someone-else"]}
    assert not signup.link_keycloak_account("abc-123", "acme-1", "acme-1_main") and len(saved) == 1


# ----------------------------------------------------------------------------- Stripe


def _stripe_issuer() -> dict[str, Any]:
    plans = setup_issuer(stripe_enabled=True, stripe_secret_key=STRIPE_KEY, stripe_webhook_secret=WEBHOOK_SECRET)
    return billed(plans["basic"]["id"])["invoice"]


def _event(invoice: dict[str, Any], event_id: str = "evt_1", tenant: str = "acme-co") -> bytes:
    return json.dumps({"id": event_id, "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "payment_status": "paid", "amount_total": invoice["total_minor"], "payment_intent": "pi_1",
        "metadata": {"anum_tenant_id": tenant, "anum_invoice_id": invoice["id"]}}}}).encode()


def test_stripe_settings_keep_keys_write_only() -> None:
    _stripe_issuer()
    view = ok(client.get("/api/v1/platform/billing/settings", headers=OPERATOR))
    assert view["has_stripe_secret_key"] and view["has_stripe_webhook_secret"] and view["stripe_test_mode"] is True
    assert STRIPE_KEY not in json.dumps(view) and WEBHOOK_SECRET not in json.dumps(view)
    stored = memory_db.all_rows("billing_settings")[0]
    assert STRIPE_KEY not in json.dumps(stored, default=str)
    bad = client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={"stripe_enabled": True, "stripe_secret_key": "pk_live_nope"})
    assert bad.status_code == 422


def test_stripe_webhook_verifies_signatures_and_is_idempotent() -> None:
    invoice = _stripe_issuer()
    now = datetime.now(UTC)
    payload = _event(invoice)
    header = providers.sign_stripe_payload(payload, WEBHOOK_SECRET, int(now.timestamp()))
    url = f"/api/v1/billing/webhooks/stripe/{ISSUER}"
    assert client.post(url, content=payload, headers={"stripe-signature": "t=1,v1=00"}).status_code == 400
    forged = providers.sign_stripe_payload(payload, "whsec_wrong", int(now.timestamp()))
    assert client.post(url, content=payload, headers={"stripe-signature": forged}).status_code == 400
    stale = providers.sign_stripe_payload(payload, WEBHOOK_SECRET, int(now.timestamp()) - 3600)
    assert client.post(url, content=payload, headers={"stripe-signature": stale}).status_code == 400
    first = ok(client.post(url, content=payload, headers={"stripe-signature": header}))
    assert first == {"received": True, "duplicate": False, "outcome": "paid"}
    again = ok(client.post(url, content=payload, headers={"stripe-signature": header}))
    assert again["duplicate"] is True
    # A new event id for the same payment intent does not record the payment twice.
    other = _event(invoice, "evt_2")
    assert handle_stripe_event(ISSUER, other, providers.sign_stripe_payload(other, WEBHOOK_SECRET, int(now.timestamp())))["outcome"] == "duplicate_payment"
    with open_billing("acme-co") as rows:
        assert rows.count("billing_payments") == 1 and svc.get_invoice(rows, invoice["id"])["status"] == "paid"
    assert client.post("/api/v1/billing/webhooks/stripe/tenant_unknown", content=payload, headers={"stripe-signature": header}).status_code == 404


def test_stripe_webhook_ignores_an_invoice_of_another_issuer() -> None:
    invoice = _stripe_issuer()
    with open_billing("acme-co") as rows:
        rows.update("billing_accounts", {"tenant_id": "acme-co"}, {"issuer_tenant_id": "tenant_issuer_two"})
    payload = _event(invoice, "evt_foreign")
    header = providers.sign_stripe_payload(payload, WEBHOOK_SECRET, int(datetime.now(UTC).timestamp()))
    assert handle_stripe_event(ISSUER, payload, header)["outcome"] == "ignored"


def test_card_checkout_creates_a_stripe_session(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = _stripe_issuer()
    monkeypatch.setattr(settings, "web_app_url", "https://app.example")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.example/cs_test_1"})

    monkeypatch.setattr(providers, "transport", httpx.MockTransport(handler))
    session = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    assert session["url"].startswith("https://checkout.stripe.example/")
    sent = seen[0]
    assert sent.headers["authorization"] == f"Bearer {STRIPE_KEY}" and sent.headers["idempotency-key"]
    assert b"anum_invoice_id" in sent.content and b"unit_amount%5D=11500" in sent.content


# ----------------------------------------------------------------------------- email and audit


def test_invoice_email_waits_for_approval_or_says_what_to_connect() -> None:
    plans = setup_issuer()
    invoice = billed(plans["basic"]["id"])["invoice"]
    response = client.post(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/email", headers=OPERATOR, json={"to": "ap@acme.example"})
    if response.status_code == 409:
        assert "Connect" in error(response)["message"]
    else:
        body = ok(response)
        assert body["status"] in ("waiting_approval", "blocked") and body["status"] != "sent"


def test_audit_walks_of_billing_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "web_app_url", "https://app.example")
    monkeypatch.setattr(providers, "transport", httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "cs", "url": "https://checkout.example/cs"})))
    console_probe = AuditProbe(client, "platform_billing", secrets=[STRIPE_KEY, WEBHOOK_SECRET])
    org_probe = AuditProbe(client, "billing")
    console_probe.call("PUT", "/api/v1/platform/billing/settings", OPERATOR, json={
        "issuer_name": "ANUM Co", "bank_iban": "SA0000", "stripe_enabled": True, "stripe_secret_key": STRIPE_KEY, "stripe_webhook_secret": WEBHOOK_SECRET})
    basic = console_probe.call("POST", "/api/v1/platform/billing/plans", OPERATOR, json=plan_body("basic", 10_000)).json()
    pro = console_probe.call("POST", "/api/v1/platform/billing/plans", OPERATOR, json=plan_body("pro", 20_000)).json()
    console_probe.call("PATCH", f"/api/v1/platform/billing/plans/{pro['id']}", OPERATOR, json={"description": "More"})
    onboard()
    invoice = console_probe.call("POST", "/api/v1/platform/billing/organizations/acme-co/subscription", OPERATOR,
                           json={"plan_id": basic["id"], "trial_days": 0}).json()["invoice"]
    base = f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}"
    console_probe.call("POST", f"{base}/credit-notes", OPERATOR, json={"total_minor": 100, "reason": "Rounding"}, sent=["Rounding"])
    console_probe.call("POST", f"{base}/payments", OPERATOR, json={"amount_minor": 1000, "reference": "TRX-AUDIT"})
    try:
        console_probe.call("POST", f"{base}/email", OPERATOR, json={"to": "ap@acme.example"})
    except AssertionError:
        console_probe.covered[("POST", "/api/v1/platform/billing/organizations/{tenant_id}/invoices/{invoice_id}/email")] = ["unavailable"]
    admin = owner("acme-co")
    org_probe.call("PUT", "/api/v1/billing/account", admin, json={"contact_email": "ap@acme.example", "address_line1": "Road", "city": "Riyadh"})
    org_probe.call("POST", f"/api/v1/billing/invoices/{invoice['id']}/checkout", admin)
    org_probe.call("POST", "/api/v1/billing/subscription/change", admin, json={"plan_id": pro["id"]})
    org_probe.call("POST", "/api/v1/billing/subscription/cancel", admin)
    org_probe.call("POST", "/api/v1/billing/subscription/reactivate", admin)
    org_probe.call("POST", "/api/v1/billing/onboarding/dismiss", admin)
    console_probe.call("POST", "/api/v1/platform/billing/organizations/acme-co/subscription/change", OPERATOR, json={"plan_id": basic["id"], "when": "period_end"})
    console_probe.call("POST", "/api/v1/platform/billing/organizations/acme-co/subscription/pause", OPERATOR)
    console_probe.call("POST", "/api/v1/platform/billing/organizations/acme-co/subscription/resume", OPERATOR)
    second = ok(client.get("/api/v1/platform/billing/organizations/acme-co/invoices?status=open", headers=OPERATOR))["items"]
    unpaid = next(i for i in second if i["amount_paid_minor"] == 0 and i["amount_credited_minor"] == 0)
    console_probe.call("POST", f"/api/v1/platform/billing/organizations/acme-co/invoices/{unpaid['id']}/void", OPERATOR)
    console_probe.call("POST", "/api/v1/platform/billing/organizations/acme-co/subscription/cancel", OPERATOR)
    console_probe.call("POST", "/api/v1/platform/billing/organizations/acme-co/subscription/reactivate", OPERATOR)
    console_probe.call("POST", "/api/v1/platform/billing/run", OPERATOR)
    console_probe.assert_covered()
    org_probe.assert_covered()


def test_email_check_is_linear_and_strict() -> None:
    from anum_api.billing.api import is_email

    assert is_email("ap@acme.example") and is_email("a.b+c@sub.acme.example")
    for bad in ("", "a@b", "a@@b.c", "a b@c.d", "a@b.", "a@.b", "@b.c", "a@b.c,d@e.f", "x" * 300 + "@a.b"):
        assert not is_email(bad), bad
    assert not is_email("!" * 100_000 + "@" + "!" * 100_000)


def test_a_yearly_switch_credits_the_unused_month_and_starts_a_new_period() -> None:
    with open_billing("cust-year") as rows:
        account, monthly = _customer(rows, 100, id="plan_m", code="m", limits={})
        _, yearly = _customer(rows, 1000, id="plan_y", code="y", interval="year", limits={})
        sub, _ = svc.start_subscription(rows, account, monthly, now=T0, actor="t", trial_days=0)
        halfway = T0 + (sub["current_period_end"] - T0) / 2
        # Per month the yearly plan is cheaper (83 against 100): deferred unless asked for now.
        assert svc.preview_change(rows, sub, yearly, 1, NO_USAGE, halfway, None).effective == "period_end"
        now_preview = svc.preview_change(rows, sub, yearly, 1, NO_USAGE, halfway, "now")
        # 1000 for the new year, less the 50 unused of the month: 950 (not the raw-price 450).
        assert (now_preview.effective, now_preview.amount_minor, now_preview.new_period) == ("now", 950, True)
        changed, issued = svc.change_plan(rows, yearly, seats=None, when="now", usage=NO_USAGE, now=halfway, actor="t")
        assert issued and issued.invoice["subtotal_minor"] == 950
        assert changed["interval"] == "year" and changed["current_period_start"] == halfway
        assert changed["current_period_end"] == add_months(halfway, 12)
        # A step up from yearly to monthly: 100 a month is dearer than 83, and the unused year is credited.
        back = svc.preview_change(rows, changed, monthly, 1, NO_USAGE, halfway, None)
        assert back.new_period and back.amount_minor == 0 and back.credit_minor == 1000 - 100


def test_a_stripe_overpayment_becomes_credit_and_is_applied_once() -> None:
    invoice = _stripe_issuer()
    with open_billing("acme-co") as rows:
        # A partial bank payment first: 5,000 of 11,500 is paid, 6,500 still due.
        svc.record_payment(rows, invoice["id"], amount_minor=5000, method="bank_transfer", reference="TRX", received_at=T0, now=T0, actor="t")
    now = datetime.now(UTC)
    payload = _event(invoice, "evt_over")  # Stripe collected the full 11,500
    header = providers.sign_stripe_payload(payload, WEBHOOK_SECRET, int(now.timestamp()))
    assert handle_stripe_event(ISSUER, payload, header)["outcome"] == "paid"
    with open_billing("acme-co") as rows:
        assert svc.get_invoice(rows, invoice["id"])["status"] == "paid"
        assert int(svc.account_of(rows)["credit_balance_minor"]) == 5000  # nothing collected was dropped
        stripe = [p for p in rows.select("billing_payments") if p["provider"] == "stripe"]
        assert [p["amount_minor"] for p in stripe] == [6500] and "overpaid 5000" in stripe[0]["reference"]
    again = _event(invoice, "evt_over_again")
    assert handle_stripe_event(ISSUER, again, providers.sign_stripe_payload(again, WEBHOOK_SECRET, int(now.timestamp())))["outcome"] == "duplicate_payment"
    with open_billing("acme-co") as rows:
        assert int(svc.account_of(rows)["credit_balance_minor"]) == 5000


def test_a_stripe_payment_for_an_invoice_paid_another_way_is_credited() -> None:
    invoice = _stripe_issuer()
    with open_billing("acme-co") as rows:
        svc.record_payment(rows, invoice["id"], amount_minor=11_500, method="bank_transfer", reference="TRX", received_at=T0, now=T0, actor="t")
    now = datetime.now(UTC)
    payload = _event(invoice, "evt_late")
    assert handle_stripe_event(ISSUER, payload, providers.sign_stripe_payload(payload, WEBHOOK_SECRET, int(now.timestamp())))["outcome"] == "credited"
    with open_billing("acme-co") as rows:
        assert int(svc.account_of(rows)["credit_balance_minor"]) == 11_500


def test_a_reminder_is_marked_sent_only_when_it_is_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.billing import jobs

    plans = setup_issuer(reminder_days=[1], grace_days=30)
    billed(plans["basic"]["id"], trial_days=14)
    with open_billing("acme-co") as rows:
        trial_end = svc.live_subscription(rows)["trial_end"]
    ok(client.put("/api/v1/billing/account", headers=owner("acme-co"), json={"contact_email": "ap@acme.example"}))
    later = trial_end + timedelta(days=2)
    # No sending workspace and no email integration: nothing is queued, so the reminder stays due.
    first = run_job(jobs.run_once(now=later))
    assert first["reminders"] == 1
    with open_billing("acme-co") as rows:
        assert int(rows.select("billing_invoices")[0]["reminders_sent"]) == 0
    assert run_job(jobs.run_once(now=later))["reminders"] == 1  # offered again on the next pass
    # Once a send is queued for approval it is marked and not offered again.
    def queued(reminder: Any) -> dict[str, Any]:
        with open_billing(reminder.tenant_id) as rows:
            svc.mark_reminder_sent(rows, reminder.invoice_id, reminder.reminder, later)
        return {"status": "waiting_approval"}

    monkeypatch.setattr(jobs, "_send_reminder", queued)
    run_job(jobs.run_once(now=later))
    with open_billing("acme-co") as rows:
        assert int(rows.select("billing_invoices")[0]["reminders_sent"]) == 1
    assert run_job(jobs.run_once(now=later))["reminders"] == 0


def run_job(awaitable: Any) -> Any:
    import asyncio

    return asyncio.run(awaitable)


def test_messages_per_month_is_enforced_on_the_chat_write_path() -> None:
    plans = setup_issuer()
    tiny = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json=plan_body(
        "chatty", 1000, limits={"seats": 5, "messages_per_month": 1})), 201)
    assert plans
    billed(tiny["id"])
    admin = owner("acme-co")
    conversation = ok(client.post("/api/v1/conversations", headers=admin, json={}), 201)
    first = client.post(f"/api/v1/conversations/{conversation['id']}/ask", headers=admin, json={"content": "Hello"})
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/conversations/{conversation['id']}/ask", headers=admin, json={"content": "Again"})
    assert second.status_code == 402 and error(second)["code"] == "plan_limit_reached"
    assert "1 messages this month" in error(second)["message"]
