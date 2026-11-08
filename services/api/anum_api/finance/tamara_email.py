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

from .models import InstallmentStatementEntry

_AMOUNT = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
_ORDER = re.compile(r"\bOrder\s*(?:Number|ID|No\.?|Reference)\s*[:#]?\s*\n?\s*([\w-]{5,160})", re.I)
_LINES = re.compile(r"(Paid today|Due on\s+\d{2}[.\-/]\d{2}[.\-/]\d{4})\s*[:\-]?\s*(?:\r?\n\s*)?" + _AMOUNT + r"\s*SAR", re.I)
_PAID = re.compile(r"(?:received your payment of|payment of)\s*SAR\s*" + _AMOUNT + r"\s+for\s+the\s+(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+payment", re.I)
_REFUND = re.compile(r"(?:Refund(?:ed)?(?:\s+Amount)?\s*[:\-]?\s*(?:SAR\s*)?)" + _AMOUNT + r"\s*SAR", re.I)
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


def extract_tamara_statement(messages: Iterable[MailEvidence]) -> tuple[list[InstallmentStatementEntry], list[str]]:
    """Extract only explicit schedules and provider-reported payments.

    Refunds set unresolved installment statuses to review, never silently assume a credit.
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
        if re.search(r"refund|refunded|order status has been updated", subject, re.I):
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
        notices = refunds.get(order, [])
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
