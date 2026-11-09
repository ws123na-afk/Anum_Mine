"""Provider-specific deterministic parsing tests, no mailbox credentials or paid API calls."""
from datetime import datetime, timezone

from decimal import Decimal

from anum_api.finance.tamara_email import MailEvidence, extract_tamara_refunds, extract_tamara_statement

WHEN = datetime(2026, 10, 15, tzinfo=timezone.utc)


def message(id: str, subject: str, body: str, sender: str = "notifications@tamara.co") -> MailEvidence:
    return MailEvidence(message_id=id, provider_message_id=id, from_address=sender,
                        subject=subject, text=body, received_at=WHEN)


def test_confirmation_and_repayment_reconstruct_schedule_without_bank_payment() -> None:
    confirmation = message("m1", "Split in 4 order confirmed at Jarir Bookstore",
                           "Paid today\n602 SAR\nDue on 01.11.2026\n602 SAR\n"
                           "Due on 01.12.2026\n602 SAR\nDue on 01.01.2027\n602 SAR\n"
                           "Order Number\ncom_12345_P001")
    repayment = message("m2", "Repayment Confirmation",
                        "We've received your payment of SAR 602 for the second payment.\n"
                        "Order Number\ncom_12345_P001")
    rows, warnings = extract_tamara_statement([repayment, confirmation])
    assert not warnings
    assert len(rows) == 4
    assert [r.source_status for r in rows] == ["paid", "paid", "scheduled", "scheduled"]
    assert rows[0].amount == rows[3].amount
    assert rows[1].source_email and rows[1].source_email.endswith("m2")
    assert rows[3].due_on.isoformat() == "2027-01-01"
    assert all(r.total_installments == 4 for r in rows)


def test_refund_with_an_amount_keeps_the_schedule_and_becomes_refund_evidence() -> None:
    """A refund email naming its amount is evidence for Finance > Refunds; the schedule stays as Tamara sent it."""
    confirmation = message("m1", "Split in 3 order confirmed at Amazon",
                           "Paid today\n290 SAR\nDue on 01.11.2026\n290 SAR\nDue on 01.12.2026\n290 SAR\n"
                           "Order Number\nexample-order-1")
    refund = message("m3", "Your order status has been updated - refund",
                     "Refund 580 SAR\nOrder Number\nexample-order-1")
    rows, _ = extract_tamara_statement([confirmation, refund])
    assert [r.source_status for r in rows] == ["paid", "scheduled", "scheduled"]
    notices = extract_tamara_refunds([confirmation, refund])
    assert len(notices) == 1
    assert notices[0].order_id == "example-order-1" and notices[0].amount == Decimal("580") and not notices[0].full
    assert notices[0].message_ref == "m3" and notices[0].source_email.endswith("m3")


def test_refund_without_an_amount_still_marks_open_installments_review() -> None:
    confirmation = message("m1", "Split in 3 order confirmed at Amazon",
                           "Paid today\n290 SAR\nDue on 01.11.2026\n290 SAR\nDue on 01.12.2026\n290 SAR\n"
                           "Order Number\nexample-order-2")
    refund = message("m3", "Your order status has been updated", "We are processing a refund.\nOrder Number\nexample-order-2")
    rows, _ = extract_tamara_statement([confirmation, refund])
    assert [r.source_status for r in rows] == ["paid", "review", "review"]
    assert "refund" in (rows[1].review_notes or "").lower()
    notices = extract_tamara_refunds([refund])
    assert notices[0].amount is None and not notices[0].full


def test_full_refund_wording_and_amount_formats_are_read() -> None:
    full = message("m4", "Refund processed", "Your order has been fully refunded.\nRefunded amount: SAR 1,200.50\nOrder Number\nexample-order-3")
    spoof = message("m5", "Refund processed", "Refund 50 SAR\nOrder Number\nexample-order-3", "fraud@not-tamara.co")
    notices = extract_tamara_refunds([full, spoof, full])
    assert len(notices) == 1
    assert notices[0].full and notices[0].amount == Decimal("1200.50")


def test_repayment_amount_mismatch_is_review_not_paid() -> None:
    confirmation = message("m1", "Split in 2 order confirmed at Noon",
                           "Paid today\n100 SAR\nDue on 01.11.2026\n100 SAR\nOrder Number\nnoon-order-001")
    repayment = message("m2", "Repayment Confirmation",
                        "received your payment of SAR 80 for the second payment\nOrder Number\nnoon-order-001")
    rows, _ = extract_tamara_statement([confirmation, repayment])
    assert rows[1].source_status == "review"
    assert "differs" in (rows[1].review_notes or "")


def test_untrusted_sender_and_unidentified_payment_do_not_create_schedule() -> None:
    spoof = message("fake", "Split in 2 order confirmed at Jarir",
                    "Paid today\n602 SAR\nDue on 01.11.2026\n602 SAR\nOrder Number\njarir-12345",
                    "fraud@not-tamara.co")
    only_repayment = message("m2", "Repayment Confirmation",
                             "received your payment of SAR 602 for the second payment\n"
                             "Order Number\njarir-12345")
    rows, _ = extract_tamara_statement([spoof, only_repayment])
    assert rows == []


def test_preview_route_reads_only_the_callers_own_gmail_off_the_event_loop() -> None:
    """The preview's mailbox reads run in a worker thread; a mailbox the caller did not connect is not found."""
    from fastapi.testclient import TestClient

    from anum_api import main
    from anum_api.email_inbox import (
        EmailAccount,
        EmailMessage,
        Visibility,
        email_backend,
        open_email_store,
    )
    from anum_api.email_providers import EmailProvider
    from anum_api.schemas import TenantContext, utc_now

    tenant, workspace = "tenant_tamara", "workspace_tamara"
    owner = {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": "owner_tamara", "x-user-roles": "owner"}
    other = {**owner, "x-user-id": "other_tamara"}
    client = TestClient(main.app)
    email_backend.clear()
    try:
        now = utc_now()
        context = TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="owner_tamara", roles=["owner"])
        with open_email_store(context) as store:
            store.add_account(EmailAccount(id="acct_tamara", tenant_id=tenant, workspace_id=workspace, provider=EmailProvider.GMAIL,
                                           address="owner@gmail.example", connected_by="owner_tamara",
                                           visibility=Visibility.WORKSPACE, created_at=now, updated_at=now))
            store.upsert_messages([EmailMessage(id="acct_tamara_m1", tenant_id=tenant, workspace_id=workspace, account_id="acct_tamara",
                                                provider_message_id="p1", thread_id="t1", subject="tamara offer",
                                                from_address="offers@shop.example", snippet="tamara", received_at=now,
                                                is_read=False, created_at=now, updated_at=now)])
        preview = client.get("/api/v1/finance/installment-email/preview", headers=owner, params={"account_id": "acct_tamara"})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        # Only mail from Tamara's own domain counts as evidence; nothing is imported by the preview.
        assert body["account_id"] == "acct_tamara" and body["messages_matched"] == 0 and body["rows"] == []
        missing = client.get("/api/v1/finance/installment-email/preview", headers=owner, params={"account_id": "acct_none"})
        assert missing.status_code == 404, missing.text
        not_theirs = client.get("/api/v1/finance/installment-email/preview", headers=other, params={"account_id": "acct_tamara"})
        assert not_theirs.status_code == 404, not_theirs.text
    finally:
        email_backend.clear()
