"""Provider-specific deterministic parsing tests, no mailbox credentials or paid API calls."""
from datetime import datetime, timezone

from anum_api.finance.tamara_email import MailEvidence, extract_tamara_statement

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


def test_refund_marks_open_installments_review_and_does_not_create_credit() -> None:
    confirmation = message("m1", "Split in 3 order confirmed at Amazon",
                           "Paid today\n290 SAR\nDue on 01.11.2026\n290 SAR\nDue on 01.12.2026\n290 SAR\n"
                           "Order Number\n404-9235252-0416348")
    refund = message("m3", "Your order status has been updated - refund",
                     "Refund 580 SAR\nOrder Number\n404-9235252-0416348")
    rows, _ = extract_tamara_statement([confirmation, refund])
    assert [r.source_status for r in rows] == ["paid", "review", "review"]
    assert "refund" in (rows[1].review_notes or "").lower()


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
