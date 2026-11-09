"""Statement facts printed outside the table (docs/finance-statements.md#statement-facts).

Banks print a summary as labels with values: beside them ("Opening Balance  0.00 SAR", a label on the
left and its Arabic twin on the right) or under them (a box of labels with one line of figures below,
as card statements do). ``facts`` reads both from the positioned lines: a value takes the label just
left of it on its own line, or the labels right above it that overlap it horizontally.

Facts used: statement date, payment due date, period, opening and closing balance, previous balance
and total amount due (card statements), the number and total of deposits and withdrawals. Each is a
check on the rows, never a source of rows.

Also here: the parts of a long transaction description some banks print in one cell,
``<type>Time:HH:MM:SS**Note:<details>``, and masked card numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

from . import amounts, dates
from .columns import Line, Token, fold

_LABELS: dict[str, tuple[str, ...]] = {
    "statement_date": ("statement date", "date of statement", "تاريخ الكشف", "تاريخ كشف الحساب"),
    "due_date": ("payment due date", "due date", "تاريخ استحقاق الدفع", "تاريخ الاستحقاق"),
    "opening": ("opening balance", "الرصيد الافتتاحي", "رصيد الحساب الإفتتاحي", "رصيد الحساب الافتتاحي"),
    "closing": ("closing balance", "رصيد الإقفال", "رصيد الاقفال", "الرصيد الختامي"),
    "previous_balance": ("previous balance", "الرصيد السابق"),
    "total_due": ("total amount due", "المبلغ الكامل المستحق", "total due"),
    "new_balance": ("new balance", "الرصيد الجديد"),
    "minimum_due": ("minimum payment due", "minimum amount due", "المبلغ الأدنى المستحق"),
    "deposits_count": ("number of deposits", "no of deposits", "عدد الإيداعات", "عدد الايداعات"),
    "withdrawals_count": ("number of withdrawals", "no of withdrawals", "عدد السحوبات"),
    "deposits_total": ("total deposits", "total credits", "إجمالي الإيداعات", "اجمالي الايداعات"),
    "withdrawals_total": ("total withdrawals", "total debits", "إجمالي السحوبات", "اجمالي السحوبات"),
}
CARD_WORDS = re.compile(r"credit card|card statement|بطاقة ائتمان|البطاقة الائتمانية|االئتمانية|الائتمانية", re.IGNORECASE)
# A line about paying a card ("سداد بطاقة ائتمانية", "credit card payment") names a card without being a card statement.
_CARD_NAME = re.compile(r"card|بطاقة|البطاقة", re.IGNORECASE)
_ACCOUNT_NAME = re.compile(r"account|\ba/c\b|iban|حساب|الآيبان|ايبان", re.IGNORECASE)
_CARD_PAYMENT = re.compile(r"payment|repayment|transfer|سداد|دفعة|تحويل|حوالة", re.IGNORECASE)
MASKED_CARD = re.compile(r"(?<![\w*])(?:\d{4,6})?[Xx*•]{4,12}(\d{4})(\d{0,2})(?!\w)")
_INTEGER = re.compile(r"^\d{1,6}$")


@dataclass
class Facts:
    values: dict[str, str] = field(default_factory=dict)
    statement_date: date | None = None
    due_date: date | None = None

    def money(self, key: str) -> Decimal | None:
        text = self.values.get(key)
        return amounts.parse(text) if text else None

    def count(self, key: str) -> int | None:
        text = self.values.get(key, "")
        return int(text) if _INTEGER.match(text) else None


def _value(token: Token, money: re.Pattern[str]) -> bool:
    return token.kind in ("money", "date") or (token.kind == "text" and bool(_INTEGER.match(token.text))) or \
        (token.kind == "text" and amounts.is_money(token.text, money))


def facts(lines: list[Line], money: re.Pattern[str], order: str) -> Facts:
    found = Facts()
    for index, line in enumerate(lines):
        for position, token in enumerate(line.tokens):
            if not _value(token, money):
                continue
            labels: list[str] = []
            left = [t for t in line.tokens[:position] if t.kind == "text" and not _INTEGER.match(t.text)]
            if left:
                labels.append(left[-1].text)
            right = [t for t in line.tokens[position + 1:] if t.kind == "text" and not _INTEGER.match(t.text)]
            if right:
                labels.append(right[0].text)  # the Arabic label on the right
            above = []
            closest: tuple[float, str] | None = None
            centre = (token.x0 + token.x1) / 2
            for other in reversed(lines[max(0, index - 4): index]):
                if line.top - other.bottom > line.height * 4:
                    break
                for t in other.tokens:
                    if t.kind != "text":
                        continue
                    if min(t.x1, token.x1) - max(t.x0, token.x0) > 0:
                        above.append(t.text)
                    gap = abs((t.x0 + t.x1) / 2 - centre)
                    if closest is None or gap < closest[0]:
                        closest = (gap, t.text)
            if not above and closest is not None and closest[0] < max(token.x1 - token.x0, 20.0) * 2:
                above.append(closest[1])  # a label set a little to the side of its figure
            if above:
                labels.append(" ".join(reversed(above)))
            for label in labels:
                key = _key(label)
                if key and key not in found.values:
                    found.values[key] = token.text
    for key in ("statement_date", "due_date"):
        text = found.values.get(key)
        if text:
            value, _ = dates.to_date(text, order, None)
            setattr(found, key, value)
    return found


def _key(label: str) -> str | None:
    folded = fold(label)
    best: tuple[int, str] | None = None
    for key, names in _LABELS.items():
        for name in names:
            name_folded = fold(name)
            if name_folded and name_folded in folded and (best is None or len(name_folded) > best[0]):
                best = (len(name_folded), key)
    return best[1] if best else None


def is_card_statement(header_lines: list[str], info: Facts, card_bands: list[str], *, has_balance: bool) -> bool:
    """A credit card statement, told from its header and facts, never from a transaction's description: a title or
    header line that names a credit card (and is not about paying one), a minimum payment or an amount due without a
    closing balance, or a masked number band that names a card (not an account) over a table with no running balance. A current account
    whose rows say "سداد بطاقة ائتمانية" stays a current account and keeps its balance check."""
    if any(CARD_WORDS.search(text) and not _CARD_PAYMENT.search(text) for text in header_lines):
        return True
    if "minimum_due" in info.values:
        return True
    if "total_due" in info.values and "closing" not in info.values:
        return True
    # A masked number band counts only when it names a card (an account number masked the same way does not).
    return not has_balance and any(_CARD_NAME.search(text) and not _ACCOUNT_NAME.search(text) for text in card_bands)


def masked_card(text: str) -> str | None:
    """The last four digits of a masked card number ("XXXXXXXXXX469525" or "484783******8747")."""
    found = MASKED_CARD.search(text.replace(" ", ""))
    if not found:
        return None
    digits = found.group(1) + found.group(2)
    return digits[-4:]


@dataclass
class Detail:
    kind: str | None = None
    time: time | None = None
    counterparty: str | None = None


_DETAIL = re.compile(r"^(?P<kind>.*?)\s*Time\s*:\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s*\**\s*(?:Note\s*:\s*(?P<note>.*))?$", re.IGNORECASE)


def detail(description: str) -> Detail:
    """``<type>Time:HH:MM:SS**Note:<details>`` split into the type, the time and the other party."""
    found = _DETAIL.match(description.strip())
    if not found:
        return Detail()
    hh_mm_ss = [int(x) for x in found.group("time").split(":")] + [0]
    try:
        at = time(hh_mm_ss[0], hh_mm_ss[1], hh_mm_ss[2])
    except ValueError:
        at = None
    note = (found.group("note") or "").strip()
    # The other party is the readable part of the note: after a "/", a ":" or a bracketed id.
    party = re.split(r"\)\s*|/|:\s", note)[-1].strip(" ,-") if note else ""
    if not re.search(r"[A-Za-z؀-ۿ]{3}", party):
        party = ""
    return Detail(found.group("kind").strip() or None, at, party[:80] or None)


__all__ = ["CARD_WORDS", "MASKED_CARD", "Detail", "Facts", "detail", "facts", "is_card_statement", "masked_card"]
