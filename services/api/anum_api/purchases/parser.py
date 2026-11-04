"""Read a shared order confirmation or delivery message (docs/purchases.md#iphone-share).

The text a person shares from Mail, Messages or a shop's app is **data, never instructions**. It is
read here with fixed patterns only: no model, no tool, no link is opened, nothing in the text can
choose what ANUM does. What it can yield is limited to:

* an order number, a tracking number, a carrier and a shop from fixed lists;
* a delivery status, and only one of ``ordered``, ``confirmed``, ``shipped``, ``out_for_delivery`` or
  ``delivered``: a message that says "cancelled" or "refunded" is noted, never applied (a refund is
  a Finance transaction linked by the person; a cancellation is the person's own choice);
* an amount and currency (a **detail** of the purchase, never money in the ledger);
* an expected delivery date.

The raw text is never stored; only these fields are. Arabic and English, Arabic-Indic digits included.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_TEXT = 4_000
DELIVERY_STATUSES = ("ordered", "confirmed", "shipped", "out_for_delivery", "delivered")

CARRIERS = (
    ("Aramex", r"aramex|ارامكس|أرامكس"), ("SMSA", r"smsa|سمسا"), ("DHL", r"\bdhl\b"), ("FedEx", r"fedex|fed ex"), ("UPS", r"\bups\b"),
    ("Naqel", r"naqel|ناقل"), ("SPL", r"\bspl\b|saudi post|البريد السعودي|سبل"), ("J&T Express", r"j\s?&\s?t"), ("iMile", r"\bimile\b"),
    ("Emirates Post", r"emirates post|بريد الإمارات"), ("Zajil", r"zajil|زاجل"), ("AyMakan", r"aymakan"), ("Amazon Logistics", r"amazon logistics"),
)
MERCHANTS = (
    ("Amazon", r"\bamazon(?:\.(?:sa|ae|com))?\b|أمازون|امازون"), ("noon", r"\bnoon(?:\.com)?\b"), ("Namshi", r"namshi|نمشي"), ("SHEIN", r"\bshein\b|شي ?إن"),
    ("AliExpress", r"aliexpress"), ("Temu", r"\btemu\b|تيمو"), ("IKEA", r"\bikea\b|ايكيا|إيكيا"), ("Jarir", r"\bjarir\b|جرير"),
    ("eXtra", r"\bextra stores?\b|\bextra\.com\b|اكسترا"), ("Carrefour", r"carrefour|كارفور"), ("LuLu", r"\blulu\b|لولو"), ("Apple", r"\bapple store\b|\bapple\.com\b"),
    ("Nike", r"\bnike\b"), ("Zara", r"\bzara\b"), ("H&M", r"\bh\s?&\s?m\b"), ("Ounass", r"ounass"), ("Sephora", r"sephora"), ("Max", r"\bmax fashion\b"),
)
CURRENCY_WORDS = (("SAR", r"sar|ر\.?\s?س|ريال"), ("AED", r"aed|د\.?\s?إ|درهم"), ("USD", r"usd|\$"), ("EUR", r"eur|€"), ("GBP", r"gbp|£"),
                  ("KWD", r"kwd|د\.?\s?ك"), ("QAR", r"qar|ر\.?\s?ق"), ("BHD", r"bhd"), ("OMR", r"omr|ر\.?\s?ع"))

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_REF = r"[:#\s.\-]*\(?([A-Z0-9][A-Z0-9\-]{3,39})"
ORDER_RE = re.compile(r"(?:\border\b|\bord\b\.?|طلب|الطلب|رقم الطلب)\s*(?:number|num|no\.?|id|ref(?:erence)?|رقم)?" + _REF, re.IGNORECASE)
TRACKING_RE = re.compile(r"(?:\btracking\b|\btrack\b|\bawb\b|\bwaybill\b|رقم التتبع|التتبع|بوليصة|رقم الشحنة)\s*(?:number|num|no\.?|id|code|رقم)?" + _REF, re.IGNORECASE)
CANCELLED_RE = re.compile(r"\bcancel+ed\b|\bcancellation\b|تم إلغاء|تم الغاء|ملغي", re.IGNORECASE)
REFUND_RE = re.compile(r"\brefund(?:ed)?\b|استرداد|مسترد", re.IGNORECASE)
STATUS_PATTERNS = (
    ("out_for_delivery", re.compile(r"\bout for delivery\b|with (?:the|our) (?:courier|driver)|خرج(?:ت)? للتوصيل|مع المندوب|جار[يى] التوصيل|في الطريق إليك", re.IGNORECASE)),
    ("delivered", re.compile(r"\b(?:has been|was|been|successfully|now)\s+delivered\b|\bdelivered\s+(?:to|at|on|today)\b|^\s*delivered\b"
                             r"|\bdelivered[.!]|تم التوصيل|تم توصيل|تم التسليم|تم تسليم", re.IGNORECASE | re.MULTILINE)),
    ("shipped", re.compile(r"\bshipped\b|\bdispatched\b|\bon (?:its|the) way\b|\bin transit\b|\bhas left\b|تم الشحن|تم شحن|قيد الشحن", re.IGNORECASE)),
    ("confirmed", re.compile(r"\border (?:is |has been )?confirmed\b|\bconfirmed your order\b|تم تأكيد", re.IGNORECASE)),
    ("ordered", re.compile(r"thank you for (?:your )?(?:order|shopping)|order (?:placed|received)|we(?:'ve| have) received your order|تم استلام طلبك|شكرا لطلبك|شكراً لطلبك",
                           re.IGNORECASE)),
)
EXPECTED_RE = re.compile(r"(?:\barriv\w*|\bexpected\b|\bestimated\b|\beta\b|\bdeliver(?:y|ed)? (?:by|on|date)\b|يصل|متوقع|موعد التوصيل)[^\n]{0,40}", re.IGNORECASE)
MONTHS = {name: number for number, names in enumerate((
    ("jan", "january", "يناير"), ("feb", "february", "فبراير"), ("mar", "march", "مارس"), ("apr", "april", "أبريل", "ابريل"), ("may", "مايو"),
    ("jun", "june", "يونيو"), ("jul", "july", "يوليو"), ("aug", "august", "أغسطس", "اغسطس"), ("sep", "sept", "september", "سبتمبر"),
    ("oct", "october", "أكتوبر", "اكتوبر"), ("nov", "november", "نوفمبر"), ("dec", "december", "ديسمبر")), start=1) for name in names}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_PATTERNS = (
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})"),
    re.compile(rf"(\d{{1,2}})\s+({_MONTH})\.?,?\s*(\d{{4}})?", re.IGNORECASE),
    re.compile(rf"({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?", re.IGNORECASE),
)


@dataclass
class Shared:
    order_ref: str | None = None
    tracking_number: str | None = None
    carrier: str | None = None
    merchant: str | None = None
    status: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    expected_on: date | None = None
    mentions_cancellation: bool = False
    mentions_refund: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def recognised(self) -> bool:
        return bool(self.order_ref or self.tracking_number or self.merchant)


def clean_text(text: str) -> str:
    """Printable text only (no control or direction characters), Western digits, at most MAX_TEXT characters."""
    normal = unicodedata.normalize("NFKC", text[:MAX_TEXT * 2]).translate(_DIGITS)
    kept = "".join(ch if (ch in "\n\t" or unicodedata.category(ch)[0] != "C") else " " for ch in normal)
    return kept[:MAX_TEXT]


def _first(patterns: tuple[tuple[str, str], ...], text: str) -> str | None:
    for label, pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def _ref(pattern: re.Pattern[str], text: str, *, avoid: str | None = None) -> str | None:
    for match in pattern.finditer(text):
        value = match.group(1).strip("-").upper()
        if any(ch.isdigit() for ch in value) and value != avoid and 4 <= len(value) <= 40:
            return value
    return None


def _amount(text: str) -> tuple[Decimal | None, str | None]:
    number = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    found: list[tuple[int, Decimal, str]] = []
    for code, word in CURRENCY_WORDS:
        for pattern in (rf"(?<![A-Za-z])(?:{word})\s?{number}", rf"{number}\s?(?:{word})(?![A-Za-z])"):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                try:
                    value = Decimal(match.group(1).replace(",", ""))
                except InvalidOperation:
                    continue
                if 0 < value < Decimal("100000000"):
                    # A figure after "total" wins; otherwise the largest one.
                    before = text[max(0, match.start() - 30):match.start()].casefold()
                    weight = 1 if ("total" in before or "المجموع" in before or "الإجمالي" in before) else 0
                    found.append((weight, value, code))
    if not found:
        return None, None
    weight, value, code = max(found, key=lambda item: (item[0], item[1]))
    return value.quantize(Decimal("0.01")), code


def _date(text: str, today: date) -> date | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        parts = match.groups()
        try:
            if pattern is DATE_PATTERNS[0]:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            elif pattern is DATE_PATTERNS[1]:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])  # the Gulf writes the day first
            elif pattern is DATE_PATTERNS[2]:
                day, month, year = int(parts[0]), MONTHS[parts[1].casefold()], int(parts[2] or today.year)
            else:
                month, day, year = MONTHS[parts[0].casefold()], int(parts[1]), int(parts[2] or today.year)
            found = date(year, month, day)
        except (ValueError, KeyError):
            continue
        if abs((found - today).days) <= 400:
            return found
    return None


FUTURE_RE = re.compile(r"(?:will|to|should|expected to|estimated to|going to)\s+be\s*$|\bwill\s*$|سيتم\s*$", re.IGNORECASE)


def _future(text: str, start: int) -> bool:
    """"Will be delivered on Monday" is a promise, not a delivery."""
    return bool(FUTURE_RE.search(text[max(0, start - 24):start]))


def parse(text: str, *, today: date) -> Shared:
    """The fields a shared message yields. Never raises on odd text."""
    body = clean_text(text)
    out = Shared()
    out.tracking_number = _ref(TRACKING_RE, body)
    out.order_ref = _ref(ORDER_RE, body, avoid=out.tracking_number)
    out.carrier = _first(CARRIERS, body)
    out.merchant = _first(MERCHANTS, body)
    for status, pattern in STATUS_PATTERNS:
        if any(not _future(body, match.start()) for match in pattern.finditer(body)):
            out.status = status
            break
    out.amount, out.currency = _amount(body)
    window = EXPECTED_RE.search(body)
    if window is not None and out.status != "delivered":
        out.expected_on = _date(window.group(0), today)
    out.mentions_cancellation = bool(CANCELLED_RE.search(body))
    out.mentions_refund = bool(REFUND_RE.search(body))
    return out


__all__ = ["DELIVERY_STATUSES", "MAX_TEXT", "Shared", "clean_text", "parse"]
