"""Read-only Tamara email recognition for Finance installment *source* history.

No arbitrary LLM extraction, no mutation of email, no bank payment confirmation.
Only deterministic fields from provider messages that can be traced to a Gmail ID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from .models import InstallmentRefundNotice, InstallmentStatementEntry

_AMOUNT = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
_ORDER = re.compile(r"\bOrder\s*(?:Number|ID|No\.?|Reference)\s*[:#]?\s*\n?\s*([\w-]{5,160})", re.I)
_LINES = re.compile(r"(Paid today|Due on\s+\d{2}[.\-/]\d{2}[.\-/]\d{4})\s*[:\-]?\s*(?:\r?\n\s*)?" + _AMOUNT + r"\s*SAR", re.I)
_PAID = re.compile(r"(?:received your payment of|payment of)\s*SAR\s*" + _AMOUNT + r"\s+for\s+the\s+(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+payment", re.I)
# "Refund 580 SAR", "Refunded amount: SAR 580.00", "Refund amount\nSAR 1,200": the currency on either side.
_REFUND = re.compile(r"\bRefund(?:ed)?(?:\s+amount)?\s*[:\-]?\s*(?:\r?\n\s*)?(?:SAR\s*" + _AMOUNT + r"|" + _AMOUNT + r"\s*SAR)", re.I)
# Wording that means the whole order was refunded or cancelled, not one item of it.
_FULL = re.compile(r"\b(?:full(?:y)?\s+refund(?:ed)?|refunded\s+in\s+full|order\s+(?:has\s+been|was|is)\s+(?:fully\s+)?(?:refunded|cancell?ed))\b", re.I)
_ORDINALS = dict(zip("first second third fourth fifth sixth seventh eighth ninth tenth".split(), range(1, 11)))
_TRUSTED_FROM = re.compile(r"^[^@\s]+@(?:[\w-]+\.)*tamara\.co$", re.I)


@dataclass(frozen=True)
class MailEvidence:
    message_id: str
    provider_message_id: str
    from_address: str
    subject: str
    text: str
    received_at: datetime


def _date(value: str, *, fallback: date) -> date | None:
    if value.lower() == "paid today":
        return fallback
    value = re.search(r"\d{2}[.\-/]\d{2}[.\-/]\d{4}", value)
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.group(), fmt).date()
        except ValueError:
            pass
    return None


def _money(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value.replace(",", ""))
        return parsed if 0 < parsed < 1_000_000 else None
    except Exception:
        return None


def _merchant(subject: str) -> str:
    result = re.search(r"(?:Split in\s+\d+\s+order confirmed|order confirmed)\s+at\s+(.+)", subject, re.I)
    if not result:
        return "Tamara purchase"
    return result.group(1).strip()[:160] or "Tamara purchase"


def _link(evidence: MailEvidence) -> str:
    # OAuth Gmail ID, not an ANUM internal ID; link is only shown inside owner's finance.
    return "https://mail.google.com/mail/u/0/#all/" + evidence.provider_message_id


def refund_notice(message: MailEvidence, order: str) -> InstallmentRefundNotice:
    """What one refund or order-updated email says: the refunded amount and whether it covers the whole order."""
    found = _REFUND.search(message.text)
    amount = _money(found.group(1) or found.group(2)) if found else None
    return InstallmentRefundNotice(
        provider="Tamara", order_id=order[:160], amount=amount, currency="SAR", full=bool(_FULL.search(message.text)),
        received_on=message.received_at.date(), message_ref=message.provider_message_id[:200], source_email=_link(message),
    )


def extract_tamara_refunds(messages: Iterable[MailEvidence]) -> list[InstallmentRefundNotice]:
    """Every refund or order-updated email from Tamara's own domain that names its order, oldest first."""
    found: dict[str, InstallmentRefundNotice] = {}
    for msg in sorted(messages, key=lambda m: (m.received_at, m.message_id)):
        if not _TRUSTED_FROM.fullmatch(msg.from_address.strip()) or not _is_refund(msg.subject):
            continue
        order = _ORDER.search(msg.text)
        if order:
            notice = refund_notice(msg, order.group(1))
            found.setdefault(notice.message_ref, notice)
    return list(found.values())


def _is_refund(subject: str) -> bool:
    return bool(re.search(r"refund|refunded|order status has been updated", subject, re.I))


def extract_tamara_statement(messages: Iterable[MailEvidence]) -> tuple[list[InstallmentStatementEntry], list[str]]:
    """Extract only explicit schedules and provider-reported payments.

    A refund email that names an amount, or says the whole order was refunded, is refund evidence
    (``extract_tamara_refunds``): the schedule stays as Tamara sent it and the owner applies the refund
    in Finance (docs/finance.md#tamara-refunds). A refund email with neither sets the unresolved
    installments to review, never silently assuming a credit.
    Payment-confirmation lines alone cannot create an installment or infer a due date.
    """
    filtered = sorted(
        [m for m in messages if _TRUSTED_FROM.fullmatch(m.from_address.strip())], key=lambda m: (m.received_at, m.message_id)
    )
    schedules: dict[str, list[InstallmentStatementEntry]] = {}
    payments: dict[str, dict[int, tuple[Decimal, MailEvidence]]] = {}
    refunds: dict[str, list[MailEvidence]] = {}
    skipped: list[str] = []
    for msg in filtered:
        subject = msg.subject.strip()
        found = _ORDER.search(msg.text)
        if not found:
            skipped.append(msg.message_id + ": no identifiable Tamara order reference")
            continue
        order = found.group(1)
        if _is_refund(subject):
            refunds.setdefault(order, []).append(msg)
        if re.search(r"order confirmed", subject, re.I):
            rows = []
            for i, line in enumerate(_LINES.finditer(msg.text), start=1):
                due_on = _date(line.group(1), fallback=msg.received_at.date())
                value = _money(line.group(2))
                if due_on is None or value is None:
                    continue
                rows.append(InstallmentStatementEntry(
                    provider="Tamara", order_id=order, merchant=_merchant(subject),
                    purchase_on=msg.received_at.date(), due_on=due_on,
                    installment=i, total_installments=1, amount=value, currency="SAR",
                    source_status="paid" if "paid today" in line.group(1).lower() else "scheduled",
                    source_email=_link(msg), review_notes=None,
                ))
            if rows:
                # The original schedule is the baseline; later updated confirmation replaces it.
                schedules[order] = [r.model_copy(update={"total_installments": len(rows)}) for r in rows]
            else:
                skipped.append(msg.message_id + ": no valid due amounts")
        elif re.search(r"repayment confirmation", subject, re.I):
            match = _PAID.search(msg.text)
            if match:
                value = _money(match.group(1))
                index = _ORDINALS.get(match.group(2).lower())
                if value is not None and index:
                    payments.setdefault(order, {})[index] = (value, msg)

    output: list[InstallmentStatementEntry] = []
    for order, rows in schedules.items():
        # Only refund emails that say neither how much nor "the whole order" still need a person.
        notices = [m for m in refunds.get(order, []) if not (lambda n: n.amount is not None or n.full)(refund_notice(m, order))]
        for row in rows:
            payment = payments.get(order, {}).get(row.installment)
            if payment and payment[0] == row.amount:
                row = row.model_copy(update={"source_status": "paid", "source_email": _link(payment[1])})
            elif payment and payment[0] != row.amount:
                row = row.model_copy(update={"source_status": "review", "review_notes": "Repayment amount differs from original schedule; verify refund or adjustment."})
            if notices and row.source_status != "paid":
                row = row.model_copy(update={"source_status": "review", "review_notes": "Tamara refund or order adjustment email found; confirm final installment amount before charging."})
            output.append(row)
    return sorted(output, key=lambda x: (x.purchase_on or x.due_on, x.order_id, x.installment)), skipped
