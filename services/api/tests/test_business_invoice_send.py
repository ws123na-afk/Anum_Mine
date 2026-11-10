"""Business invoices: Arabic PDFs, receipts and credit note PDFs, emailing an invoice, payment
reminders, idempotent payments and Moyasar payment links (docs/business.md), against the
in-memory backend, a fake email tool and a fake Moyasar (``test_selling_readiness.FakeMoyasar``).

Every send is checked to wait for an owner's approval of its exact payload: nothing reaches the
fake email tool before the approval, and a workspace that would let the email tool send without
asking is refused.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from anum_api import delivery, main
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolResult
from anum_api.billing import moyasar, providers
from anum_api.billing.rows import memory_db
from anum_api.business.store import business_store
from anum_api.dependencies import memory_repository
from anum_api.schemas import RiskLevel, TenantContext, utc_now
from anum_api.settings import settings
from anum_api.suppression import suppression_store

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, TENANT, VIEWER, ok
from test_business_change_orders import project as make_project
from test_business_invoices import business_today, create, send, set_profile
from test_selling_readiness import MOYASAR_KEY, WEBHOOK_TOKEN, FakeMoyasar
from test_stripe_payments import STRIPE_KEY, FakeStripe, form_of
from test_stripe_payments import WEBHOOK_SECRET as STRIPE_WEBHOOK_SECRET

client = TestClient(main.app)
TODAY = business_today().isoformat()
ARABIC_COMPANY = "شركة الأمل للتجارة"
ARABIC_LINE = "استشارة إدارية"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    memory_db.clear()
    memory_repository.store.audit_records.clear()
    yield
    business_store.clear()
    suppression_store.clear()
    memory_db.clear()


@pytest.fixture
def email_tool() -> Iterator[list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    async def send_email(call: ToolCall, context: TenantContext) -> ToolResult:
        attachment = delivery.resolve_attachment(call.arguments["attachments"][0], context)
        sent.append({**call.arguments, "bytes": attachment.content, "filename": attachment.filename})
        return ToolResult(status="succeeded", summary=f"Emailed {call.arguments['to']}.")

    registry = main.tool_registry
    registry.register(ToolDefinition(name="test.invoice_email", description="fake email", risk_level=RiskLevel.HIGH, delivery_channel="email"), send_email)
    yield sent
    registry._definitions.pop("test.invoice_email")
    registry._handlers.pop("test.invoice_email")


@pytest.fixture
def fake_moyasar(monkeypatch: pytest.MonkeyPatch) -> FakeMoyasar:
    fake = FakeMoyasar()
    monkeypatch.setattr(moyasar, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(settings, "web_app_url", "https://app.example")
    return fake


def arabic_invoice(**line: Any) -> dict[str, Any]:
    """A sent invoice to an Arabic-named customer: 1000 net + 15% = 1150."""
    set_profile()
    project = make_project(total=1000, tax=0)
    drafted = ok(create(account_id=project["account_id"], bill_to_name=ARABIC_COMPANY, bill_to_email="finance@amal.example",
                        lines=[{"description": ARABIC_LINE, "quantity": 1, "unit_price": 1000, "tax_rate_pct": 15, **line}]), 201)
    return send(drafted)


def pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def make_overdue(invoice_id: str, days: int) -> None:
    """Move a sent invoice's due date into the past (as time passing would)."""
    scoped = business_store.scoped(TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id="t", roles=["owner"]))
    invoice = scoped.get("invoices", invoice_id)
    scoped.save("invoices", invoice.model_copy(update={"due_date": utc_now().date() - timedelta(days=days)}))


def pay(invoice_id: str, amount: float, key: str | None = None, headers: dict[str, str] = MEMBER, **extra: Any) -> Any:
    head = {**headers, **({"Idempotency-Key": key} if key else {})}
    return client.post(f"{BASE}/invoices/{invoice_id}/payments", headers=head, json={"amount": amount, "paid_on": TODAY, **extra})


def approval_of(approval_id: str) -> dict[str, Any]:
    return next(a for a in ok(client.get("/api/v1/approvals", headers=OWNER)) if a["id"] == approval_id)


# -- Arabic in the PDFs ---------------------------------------------------------------------------------


def test_the_invoice_pdf_prints_arabic_as_arabic_with_bilingual_labels() -> None:
    sent = arabic_invoice()
    response = client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=MEMBER)
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    text = pdf_text(response.content)
    # The customer's Arabic name and the Arabic line come back out of the file, not "?".
    assert ARABIC_COMPANY in text and ARABIC_LINE in text and "?" not in text
    for label in ("Tax invoice", "فاتورة ضريبية", "Invoice number", "رقم الفاتورة", "Bill to", "فاتورة إلى", "Total", "الإجمالي", sent["number"], "1,150.00"):
        assert label in text, label
    # The Arabic font is embedded (subset) with a ToUnicode map, once per weight used.
    assert b"/FontFile2" in response.content and b"NotoSansArabic" in response.content and b"/ToUnicode" in response.content
    assert len(response.content) < 80_000


def test_a_latin_only_invoice_embeds_no_font() -> None:
    set_profile()
    project = make_project(total=1000, tax=0)
    drafted = ok(create(account_id=project["account_id"], bill_to_name="Gulf Mixers LLC", lines=[{"description": "Consulting", "unit_price": 100}]), 201)
    content = client.get(f"{BASE}/invoices/{drafted['id']}/pdf", headers=MEMBER).content
    # The bilingual labels are Arabic, so the font is there; a tyre report without Arabic has none (test_pdf_text.py).
    assert b"NotoSansArabic" in content and "DRAFT" in pdf_text(content) and "مسودة" in pdf_text(content)


def test_a_receipt_pdf_for_each_payment_with_permissions_and_scope() -> None:
    sent = arabic_invoice()
    paid = ok(pay(sent["id"], 400, reference="TRX-1"), 201)
    payment = paid["payments"][0]
    url = f"{BASE}/invoices/{sent['id']}/payments/{payment['id']}/receipt"
    response = client.get(url, headers=MEMBER)
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith('attachment; filename="receipt-') and response.headers["cache-control"] == "no-store"
    text = pdf_text(response.content)
    for needle in ("Payment receipt", "إيصال دفع", ARABIC_COMPANY, "400.00 SAR", "TRX-1", sent["number"], "750.00 SAR"):
        assert needle in text, needle
    assert client.get(url, headers=VIEWER).status_code == 403
    assert client.get(url, headers=OTHER_WORKSPACE).status_code == 404
    assert client.get(url, headers=OTHER_TENANT).status_code == 404
    assert client.get(f"{BASE}/invoices/{sent['id']}/payments/missing/receipt", headers=OWNER).status_code == 404


def test_a_credit_note_pdf_in_english_and_arabic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "business_credit_notes_enabled", True)
    sent = arabic_invoice()
    view = ok(client.post(f"{BASE}/invoices/{sent['id']}/credit-notes", headers=OWNER, json={"amount": 115, "reason": "تصحيح السعر"}), 201)
    note = view["credit_notes"][0]
    url = f"{BASE}/invoices/{sent['id']}/credit-notes/{note['id']}/pdf"
    response = client.get(url, headers=MEMBER)
    assert response.status_code == 200 and response.headers["content-disposition"] == f'attachment; filename="{note["number"]}.pdf"'
    text = pdf_text(response.content)
    for needle in ("Credit note", "إشعار دائن", note["number"], sent["number"], "تصحيح السعر", ARABIC_COMPANY, "115.00 SAR", "15.00 SAR"):
        assert needle in text, needle
    assert client.get(url, headers=VIEWER).status_code == 403 and client.get(url, headers=OTHER_TENANT).status_code == 404
    assert client.get(f"{BASE}/invoices/{sent['id']}/credit-notes/missing/pdf", headers=OWNER).status_code == 404


def test_the_portal_pdf_also_prints_arabic() -> None:
    sent = arabic_invoice()
    account = ok(client.get(f"{BASE}/accounts/{sent['account_id']}", headers=OWNER))
    link = ok(client.post(f"{BASE}/portal/access", headers=OWNER, json={"account_id": sent["account_id"], "contact_id": account["contacts"][0]["id"]}), 201)
    portal = {"authorization": f"Bearer {link['token']}"}
    response = client.get(f"/api/v1/portal/invoices/{sent['id']}/pdf", headers=portal)
    assert response.status_code == 200 and ARABIC_COMPANY in pdf_text(response.content)
    assert ok(client.get(f"/api/v1/portal/invoices/{sent['id']}", headers=portal))["payment_url"] is None


# -- payments are idempotent ------------------------------------------------------------------------


def test_a_repeated_payment_submit_records_the_money_once() -> None:
    sent = arabic_invoice()
    first = pay(sent["id"], 300, key="form-submit-1", reference="TRX-9")
    assert first.status_code == 201 and len(first.json()["payments"]) == 1
    again = pay(sent["id"], 300, key="form-submit-1", reference="TRX-9")
    assert again.status_code == 200 and len(again.json()["payments"]) == 1 and again.json()["amount_paid"] == 300
    clash = pay(sent["id"], 500, key="form-submit-1", reference="TRX-9")
    assert clash.status_code == 409 and "different payment" in clash.text
    other = pay(sent["id"], 300, key="form-submit-2", reference="TRX-10")
    assert other.status_code == 201 and other.json()["amount_paid"] == 600
    assert pay(sent["id"], 10, key="bad key!").status_code == 422
    # The same key on another workspace's invoice is not this one.
    assert pay(sent["id"], 300, key="form-submit-1", headers=OTHER_WORKSPACE).status_code == 404


# -- emailing the invoice --------------------------------------------------------------------------


def test_emailing_needs_an_integration(email_tool: list[dict[str, Any]]) -> None:
    sent = arabic_invoice()
    main.tool_registry._definitions["test.invoice_email"].delivery_channel = None
    try:
        refused = client.post(f"{BASE}/invoices/{sent['id']}/email", headers=MEMBER, json={})
        assert refused.status_code == 409 and "No email integration" in refused.text
    finally:
        main.tool_registry._definitions["test.invoice_email"].delivery_channel = "email"
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}/messages", headers=MEMBER)) == []


def test_an_invoice_email_waits_for_an_owner_and_sends_the_pdf_it_was_approved_with(email_tool: list[dict[str, Any]]) -> None:
    sent = arabic_invoice()
    assert sent["can"]["email"] is True and sent["can"]["remind"] is False
    requested = client.post(f"{BASE}/invoices/{sent['id']}/email", headers=MEMBER, json={"language": "ar"})
    assert requested.status_code == 201, requested.text
    body = requested.json()
    message = body["message"]
    assert message["status"] == "waiting_approval" and message["to_email"] == "finance@amal.example" and message["kind"] == "invoice"
    assert message["subject"].startswith("فاتورة") and body["payload_hash"]
    assert email_tool == []  # nothing leaves before the approval
    approval = approval_of(message["approval_id"])
    assert approval["action"] == "test.invoice_email" and approval["arguments"]["attachments"][0]["kind"] == "business_invoice"
    # A member cannot approve their own send.
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=MEMBER, json={"payload_hash": body["payload_hash"]}).status_code == 403
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": body["payload_hash"]}).status_code == 200
    assert len(email_tool) == 1 and email_tool[0]["to"] == "finance@amal.example"
    assert email_tool[0]["bytes"].startswith(b"%PDF") and ARABIC_COMPANY in pdf_text(email_tool[0]["bytes"])
    assert email_tool[0]["filename"] == f"{sent['number']}.pdf" and sent["number"] in email_tool[0]["text"]
    view = ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))
    assert [item["status"] for item in view["messages"]] == ["sent"]
    activities = [item.kind for item in business_store._rows["activities"].values()]
    assert "invoice_email_requested" in activities and "invoice_emailed" in activities


def test_every_send_is_checked(email_tool: list[dict[str, Any]]) -> None:
    sent = arabic_invoice()
    url = f"{BASE}/invoices/{sent['id']}/email"
    assert client.post(url, headers=VIEWER, json={}).status_code == 403
    assert client.post(url, headers=OTHER_WORKSPACE, json={}).status_code == 404
    assert client.post(url, headers=OTHER_TENANT, json={}).status_code == 404
    assert client.post(url, headers=MEMBER, json={"to": "not an address"}).status_code == 422
    assert client.post(url, headers=MEMBER, json={"language": "fr"}).status_code == 422
    ok(client.post(url, headers=MEMBER, json={"to": "ap@amal.example"}), 201)
    twice = client.post(url, headers=MEMBER, json={})
    assert twice.status_code == 409 and "already waiting" in twice.text
    # A draft is never emailed: it is sent (numbered) through its own approval first.
    draft = ok(create(account_id=sent["account_id"], lines=[{"description": "Work", "unit_price": 100}]), 201)
    assert client.post(f"{BASE}/invoices/{draft['id']}/email", headers=MEMBER, json={"to": "a@b.example"}).status_code == 409
    assert email_tool == []


def test_a_rejected_email_is_never_sent(email_tool: list[dict[str, Any]]) -> None:
    sent = arabic_invoice()
    message = ok(client.post(f"{BASE}/invoices/{sent['id']}/email", headers=MEMBER, json={}), 201)["message"]
    rejected = client.post(f"/api/v1/approvals/{message['approval_id']}/reject", headers=OWNER, json={"reason": "Wrong contact"})
    assert rejected.status_code == 200, rejected.text
    view = ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))
    assert view["messages"][0]["status"] == "rejected" and "Wrong contact" in view["messages"][0]["detail"] and email_tool == []
    # Asking again is possible once the first is decided.
    ok(client.post(f"{BASE}/invoices/{sent['id']}/email", headers=MEMBER, json={}), 201)


def test_a_workspace_that_lets_email_send_unasked_is_refused(email_tool: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """Autonomy or a lowered rule could let a delivery run without asking; an invoice email is then refused, never sent."""
    from anum_api.agent_tools import ToolPolicyDecision, ToolPolicyOutcome
    from anum_api.runtime import AgentRuntime

    sent = arabic_invoice()
    allow = ToolPolicyDecision(outcome=ToolPolicyOutcome.ALLOW, reason="Autonomy acts.", risk_level=RiskLevel.HIGH)
    monkeypatch.setattr(AgentRuntime, "evaluate", lambda self, call, context, task=None, run=None: allow)
    refused = client.post(f"{BASE}/invoices/{sent['id']}/email", headers=OWNER, json={})
    assert refused.status_code == 409 and "always need an owner's approval" in refused.text and email_tool == []
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}/messages", headers=MEMBER)) == []


# -- payment reminders ----------------------------------------------------------------------------------


def test_reminders_follow_the_schedule_and_each_waits_for_approval(email_tool: list[dict[str, Any]]) -> None:
    sent = arabic_invoice()
    url = f"{BASE}/invoices/{sent['id']}/reminders"
    early = client.post(url, headers=MEMBER, json={})
    assert early.status_code == 409 and "not overdue" in early.text
    make_overdue(sent["id"], 2)
    view = ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))
    assert view["overdue"] is True and view["next_reminder"] == 1 and view["can"]["remind"] is True
    first = ok(client.post(url, headers=MEMBER, json={"language": "en"}), 201)
    assert first["message"]["reminder_number"] == 1 and first["message"]["subject"] == f"Reminder: invoice {sent['number']} is overdue"
    assert first["invoice"]["next_reminder"] is None and email_tool == []
    assert client.post(url, headers=MEMBER, json={}).status_code == 409  # one waiting at a time
    approval = approval_of(first["message"]["approval_id"])
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}).status_code == 200
    assert len(email_tool) == 1 and "1,150.00 is still open" in email_tool[0]["text"]
    # Two days late: reminder 2 is due only from day 7; a person can still ask for it.
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))["next_reminder"] is None
    make_overdue(sent["id"], 8)
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))["next_reminder"] == 2
    second = ok(client.post(url, headers=MEMBER, json={"language": "ar"}), 201)["message"]
    assert second["reminder_number"] == 2 and second["subject"].startswith("تذكير")
    # A paid invoice gets no reminder.
    approval = approval_of(second["approval_id"])
    client.post(f"/api/v1/approvals/{approval['id']}/reject", headers=OWNER, json={"reason": "Customer called"})
    ok(pay(sent["id"], 1150), 201)
    assert client.post(url, headers=MEMBER, json={}).status_code == 409
    assert client.post(url, headers=VIEWER, json={}).status_code == 403


# -- Moyasar payment links ------------------------------------------------------------------------------


def turn_on_moyasar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner of this organisation turns Moyasar on in Platform billing (the issuer is this tenant)."""
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", TENANT)
    operator = {**OWNER, "x-user-roles": "owner,platform_operator"}
    ok(client.put("/api/v1/platform/billing/providers/moyasar", headers=operator,
                  json={"enabled": True, "secret_key": MOYASAR_KEY, "webhook_secret": WEBHOOK_TOKEN}))


def webhook(payment_id: str, event_id: str) -> Any:
    return client.post(f"/api/v1/billing/webhooks/moyasar/{TENANT}", content=json.dumps({
        "id": event_id, "type": "payment_paid", "secret_token": WEBHOOK_TOKEN,
        "data": {"id": payment_id, "status": "paid", "amount": 999999, "currency": "SAR"}}).encode())


def test_card_payment_links_are_off_until_the_owner_turns_moyasar_on(fake_moyasar: FakeMoyasar, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = arabic_invoice()
    assert ok(client.get(f"{BASE}/payment-provider", headers=MEMBER)) == {"moyasar": "off", "stripe": "off", "provider": None, "mode": None}
    refused = client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER)
    assert refused.status_code == 409 and "Moyasar" in refused.text and fake_moyasar.requests == []
    turn_on_moyasar(monkeypatch)
    assert ok(client.get(f"{BASE}/payment-provider", headers=MEMBER)) == {"moyasar": "on", "stripe": "off", "provider": "moyasar", "mode": "test"}
    assert client.get(f"{BASE}/payment-provider", headers=VIEWER).status_code == 403


def test_a_payment_link_is_paid_once_through_the_webhook(fake_moyasar: FakeMoyasar, monkeypatch: pytest.MonkeyPatch, email_tool: list[dict[str, Any]]) -> None:
    turn_on_moyasar(monkeypatch)
    sent = arabic_invoice()
    view = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)
    link = view["payment_link"]
    assert link["url"].startswith("https://checkout.moyasar.example/") and link["amount"] == 1150 and link["status"] == "open"
    body = json.loads(fake_moyasar.requests[-1].content)
    assert body["amount"] == 115000 and body["currency"] == "SAR" and body["metadata"]["anum_business_invoice_id"] == sent["id"]
    assert "anum_invoice_id" not in body["metadata"]  # never mistaken for an ANUM subscription invoice
    # The same link while the balance is unchanged; no second Moyasar invoice.
    again = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)
    assert again["payment_link"]["id"] == link["id"] and len(fake_moyasar.invoices) == 1
    # The link is in the PDF, the email and the customer's portal.
    assert link["url"] in pdf_text(client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=MEMBER).content).replace("\n", "")
    message = ok(client.post(f"{BASE}/invoices/{sent['id']}/email", headers=MEMBER, json={}), 201)["message"]
    approval = approval_of(message["approval_id"])
    assert link["url"] in approval["arguments"]["text"]
    # The customer pays on Moyasar's page; the amount comes from Moyasar's API, not the delivery.
    fake_moyasar.pay(link["provider_invoice_id"], payment_id="pay_biz_1")
    assert ok(webhook("pay_biz_1", "evt_1"))["outcome"] == "paid"
    assert ok(webhook("pay_biz_1", "evt_1"))["duplicate"] is True
    assert ok(webhook("pay_biz_1", "evt_2"))["outcome"] == "duplicate_payment"
    paid = ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))
    assert paid["status"] == "paid" and paid["amount_paid"] == 1150 and len(paid["payments"]) == 1
    payment = paid["payments"][0]
    assert payment["method"] == "card" and payment["provider"] == "moyasar" and payment["provider_payment_id"] == "pay_biz_1"
    assert paid["payment_link"] is None and paid["can"]["payment_link"] is False


def test_a_payment_link_for_another_tenant_is_ignored(fake_moyasar: FakeMoyasar, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_moyasar(monkeypatch)
    sent = arabic_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    # A Moyasar invoice whose metadata names another tenant (made outside ANUM) changes nothing here.
    fake_moyasar.invoices[link["provider_invoice_id"]]["metadata"]["anum_tenant_id"] = "tenant_someone_else"
    fake_moyasar.pay(link["provider_invoice_id"], payment_id="pay_foreign")
    assert ok(webhook("pay_foreign", "evt_f"))["outcome"] == "ignored"
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))["amount_paid"] == 0
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=VIEWER).status_code == 403
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=OTHER_TENANT).status_code == 404


# -- Stripe payment links (a UAE company's Stripe account) ----------------------------------------------


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(providers, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(settings, "web_app_url", "https://app.example")
    return fake


def turn_on_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner of this organisation turns Stripe on in Platform billing (the issuer is this tenant); Moyasar stays off."""
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", TENANT)
    operator = {**OWNER, "x-user-roles": "owner,platform_operator"}
    ok(client.put("/api/v1/platform/billing/settings", headers=operator,
                  json={"issuer_name": "ANUM FZ-LLC", "stripe_enabled": True, "stripe_secret_key": STRIPE_KEY, "stripe_webhook_secret": STRIPE_WEBHOOK_SECRET}))


def aed_invoice() -> dict[str, Any]:
    """A sent AED invoice: 1000 net + 5% UAE VAT = 1050, so 105000 fils."""
    set_profile()
    project = make_project(total=1000, tax=0)
    drafted = ok(create(account_id=project["account_id"], bill_to_name="Al Amal Trading LLC", bill_to_email="finance@amal.example", currency="AED",
                        lines=[{"description": "Consulting", "quantity": 1, "unit_price": 1000, "tax_rate_pct": 5}]), 201)
    return send(drafted)


def stripe_session(link: dict[str, Any], event_id: str, payment_intent: str, amount: int, metadata: dict[str, str] | None, currency: str = "aed") -> Any:
    obj: dict[str, Any] = {"id": f"cs_{event_id}", "payment_status": "paid", "amount_total": amount, "currency": currency,
                           "payment_intent": payment_intent, "payment_link": link["provider_invoice_id"]}
    if metadata is not None:
        obj["metadata"] = metadata
    payload = json.dumps({"id": event_id, "type": "checkout.session.completed", "data": {"object": obj}}).encode()
    header = providers.sign_stripe_payload(payload, STRIPE_WEBHOOK_SECRET, int(utc_now().timestamp()))
    return client.post(f"/api/v1/billing/webhooks/stripe/{TENANT}", content=payload, headers={"stripe-signature": header})


def test_stripe_payment_links_when_stripe_is_on_and_moyasar_off(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_stripe(monkeypatch)
    assert ok(client.get(f"{BASE}/payment-provider", headers=MEMBER)) == {"moyasar": "off", "stripe": "on", "provider": "stripe", "mode": "test"}
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    assert link["provider"] == "stripe" and link["url"].startswith("https://buy.stripe.example/") and link["amount"] == 1050
    price = form_of(fake_stripe.calls("POST", "/v1/prices")[0])
    assert price["currency"] == "aed" and price["unit_amount"] == "105000"  # fils
    made = fake_stripe.calls("POST", "/v1/payment_links")[0]
    form = form_of(made)
    assert made.headers["idempotency-key"] and form["restrictions[completed_sessions][limit]"] == "1"
    assert form["after_completion[redirect][url]"] == "https://app.example/business?tab=invoices"
    assert form["metadata[anum_business_invoice_id]"] == sent["id"] and form["payment_intent_data[metadata][anum_tenant_id]"] == TENANT
    assert "metadata[anum_invoice_id]" not in form  # never mistaken for an ANUM subscription invoice
    # The same link while the balance is unchanged; nothing new at Stripe.
    again = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    assert again["id"] == link["id"] and len(fake_stripe.calls("POST", "/v1/payment_links")) == 1
    # Viewers cannot make one; another organisation cannot see the invoice.
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=VIEWER).status_code == 403
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=OTHER_TENANT).status_code == 404


def test_a_stripe_payment_link_is_paid_once_through_the_signed_webhook(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = fake_stripe.links[link["provider_invoice_id"]]["metadata"]
    # A forged delivery (wrong signing secret) changes nothing.
    payload = json.dumps({"id": "evt_forged", "type": "checkout.session.completed", "data": {"object": {"payment_status": "paid"}}}).encode()
    forged = providers.sign_stripe_payload(payload, "whsec_wrong", int(utc_now().timestamp()))
    assert client.post(f"/api/v1/billing/webhooks/stripe/{TENANT}", content=payload, headers={"stripe-signature": forged}).status_code == 400
    assert ok(stripe_session(link, "evt_1", "pi_biz_1", 105000, metadata))["outcome"] == "paid"
    assert ok(stripe_session(link, "evt_1", "pi_biz_1", 105000, metadata))["duplicate"] is True
    assert ok(stripe_session(link, "evt_2", "pi_biz_1", 105000, metadata))["outcome"] == "duplicate_payment"
    paid = ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))
    assert paid["status"] == "paid" and paid["amount_paid"] == 1050 and len(paid["payments"]) == 1
    payment = paid["payments"][0]
    assert payment["method"] == "card" and payment["provider"] == "stripe" and payment["provider_payment_id"] == "pi_biz_1"
    assert paid["payment_link"] is None


def test_a_stripe_session_without_metadata_reads_it_from_the_payment_link(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    assert ok(stripe_session(link, "evt_nometa", "pi_biz_2", 105000, None))["outcome"] == "paid"
    assert fake_stripe.calls("GET", f"/v1/payment_links/{link['provider_invoice_id']}")
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))["status"] == "paid"


def test_a_stripe_payment_for_another_tenant_or_currency_is_ignored(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = dict(fake_stripe.links[link["provider_invoice_id"]]["metadata"])
    assert ok(stripe_session(link, "evt_f", "pi_f", 105000, {**metadata, "anum_tenant_id": "tenant_someone_else"}))["outcome"] == "ignored"
    assert ok(stripe_session(link, "evt_c", "pi_c", 105000, metadata, currency="usd"))["outcome"] == "ignored"
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=MEMBER))["amount_paid"] == 0


def test_a_replaced_stripe_link_is_turned_off(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    first = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    ok(pay(sent["id"], 50), 201)  # the balance changes, so the next link is for a new amount
    second = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    assert second["id"] != first["id"] and second["amount"] == 1000
    assert fake_stripe.links[first["provider_invoice_id"]]["active"] is False
    assert fake_stripe.links[second["provider_invoice_id"]]["active"] is True
