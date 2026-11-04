"""Read receipt fields and document dates from OCR lines (docs/documents.md#scanning-and-extraction).

Pure functions over the lines the OCR service returns (or a PDF's text layer). The text is
untrusted data: it is only ever matched against fixed patterns, never interpreted, never sent to a
model, and every value it yields is validated (dates are real dates, amounts are decimals, the
currency is one of a fixed list, text is short and stripped of control and direction characters).
Nothing is applied until the person confirms it. Text that reads like instructions to an assistant
adds the ``instructions_in_text`` warning and is never used as a value.

Arabic and English: Arabic-Indic digits and separators are read as Western ones, and the keywords
below come in both languages. Day-first dates are the default (Gulf usage); an ambiguous date gets
a lower confidence. Hijri dates are not converted (``hijri_date_seen`` warning).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    # 0 to 100 as Tesseract reports it; 100 for a PDF text layer.
    confidence: float = 100.0


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_INVISIBLE = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_SPACES = re.compile(r"\s+")


def clean_text(value: str) -> str:
    """Western digits, no control or direction characters, single spaces."""
    text = unicodedata.normalize("NFKC", value).translate(_DIGITS)
    return _SPACES.sub(" ", _INVISIBLE.sub("", text)).strip()


def safe_value(value: str, limit: int = 120) -> str:
    """A short display value: cleaned and without a leading spreadsheet formula character."""
    return clean_text(value).lstrip("=+@\t")[:limit].strip()


# -- prompt-injection markers (warning only; the parser never follows text) -------------------

_INSTRUCTIONS = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)?\s*(instructions|rules|prompts?)"
    r"|system\s*prompt|you\s+are\s+now|act\s+as\s+(an?\s+)?(assistant|admin)|new\s+instructions"
    r"|(send|transfer|wire)\s+(the\s+)?(money|funds|payment)|approve\s+(this|the)\s+(payment|transfer|request)"
    r"|<<\s*(end-)?untrusted|\bassistant\s*:|\bsystem\s*:"
    r"|تجاهل\s+(كل\s+|جميع\s+)?(التعليمات|الأوامر)|أنت\s+الآن|تعليمات\s+جديدة|حول\s+(المبلغ|الأموال)",
    re.IGNORECASE,
)


def has_instructions(text: str) -> bool:
    return bool(_INSTRUCTIONS.search(clean_text(text)))


# -- amounts and currencies -------------------------------------------------------------------

_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:([.,])(\d{1,3}))?(?![\d%])(?!\s*%)")
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%")

CURRENCY_CODES = ("SAR", "AED", "USD", "EUR", "GBP", "KWD", "QAR", "BHD", "OMR", "EGP", "JOD")
_CURRENCY_WORDS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"\b(" + "|".join(CURRENCY_CODES) + r")\b", re.IGNORECASE), "", 0.9),
    # "رلاس" is how Tesseract often reads the dotted abbreviation "ر.س".
    (re.compile(r"ريال\s*سعودي|ر\.\s?س|رلاس|\bSR\b", re.IGNORECASE), "SAR", 0.85),
    (re.compile(r"ريال\s*قطري|ر\.\s?ق"), "QAR", 0.85),
    (re.compile(r"ريال\s*عماني|ر\.\s?ع"), "OMR", 0.85),
    (re.compile(r"درهم|د\.\s?إ|\bDHS?\b", re.IGNORECASE), "AED", 0.8),
    (re.compile(r"دينار\s*كويتي|د\.\s?ك"), "KWD", 0.8),
    (re.compile(r"دينار\s*بحريني|د\.\s?ب"), "BHD", 0.8),
    (re.compile(r"ريال"), "SAR", 0.5),
    (re.compile(r"\$"), "USD", 0.5),
    (re.compile(r"€"), "EUR", 0.7),
    (re.compile(r"£"), "GBP", 0.7),
)
_THREE_DECIMALS = frozenset({"KWD", "BHD", "OMR", "JOD"})


def amounts_in(text: str) -> list[Decimal]:
    """Every amount in a line, percentages left out, in reading order."""
    line = _PERCENT.sub(" ", text)
    found: list[Decimal] = []
    for match in _AMOUNT.finditer(line):
        whole, sep, frac = match.group(1), match.group(2), match.group(3)
        try:
            if sep == "," and frac is not None and len(frac) == 3 and "," not in whole:
                # 1,234 is a thousands separator, not a decimal comma.
                value = Decimal(whole + frac)
            else:
                value = Decimal(whole.replace(",", "") + (f".{frac}" if frac is not None else ""))
        except InvalidOperation:
            continue
        found.append(value)
    return found


def currency_in(text: str) -> tuple[str, float] | None:
    for pattern, code, confidence in _CURRENCY_WORDS:
        match = pattern.search(text)
        if match:
            return (code or match.group(1).upper()), confidence
    return None


def money(value: Decimal, currency: str | None) -> str:
    places = Decimal("0.001") if currency in _THREE_DECIMALS else Decimal("0.01")
    return str(value.quantize(places, rounding=ROUND_HALF_UP))


# -- dates ---------------------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
    "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}
_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_DMY = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?![\d])")
_NAMED = re.compile(r"(?<!\d)(\d{1,2})\s*[-\s]\s*([A-Za-z؀-ۿ]{3,9})\.?\s*[-,\s]\s*(\d{4})(?!\d)")
_NAMED_US = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})(?!\d)")
_HIJRI = re.compile(r"\d\s*هـ|\bAH\b|هجري")


@dataclass(frozen=True, slots=True)
class FoundDate:
    value: date
    confidence: float


def _make(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    if not 1900 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def dates_in(text: str) -> list[FoundDate]:
    """Every Gregorian date in a line with how sure the reading is."""
    found: list[FoundDate] = []
    taken: list[tuple[int, int]] = []

    def add(value: date | None, confidence: float, span: tuple[int, int]) -> None:
        if value is not None and not any(a < span[1] and span[0] < b for a, b in taken):
            found.append(FoundDate(value, confidence))
            taken.append(span)

    for match in _ISO.finditer(text):
        add(_make(int(match.group(1)), int(match.group(2)), int(match.group(3))), 0.9, match.span())
    for match in _NAMED.finditer(text):
        month = _MONTHS.get(match.group(2).lower())
        if month:
            add(_make(int(match.group(3)), month, int(match.group(1))), 0.9, match.span())
    for match in _NAMED_US.finditer(text):
        month = _MONTHS.get(match.group(1).lower())
        if month:
            add(_make(int(match.group(3)), month, int(match.group(2))), 0.85, match.span())
    for match in _DMY.finditer(text):
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if first > 12 or second <= 12 and first <= 12 and first == second:
            add(_make(year, second, first), 0.85, match.span())
        elif second > 12:
            # Month first (US order): possible, but not the local habit.
            add(_make(year, first, second), 0.6, match.span())
        else:
            add(_make(year, second, first), 0.6, match.span())
    return found


# -- keywords --------------------------------------------------------------------------------

def _words(*items: str) -> re.Pattern[str]:
    return re.compile("|".join(items), re.IGNORECASE)


_TOTAL = _words(r"grand\s*total", r"total\s*(amount|due|payable|incl\.?|inc\.?|including|with)?", r"amount\s*(due|payable)", r"net\s*(amount|total)",
                r"balance\s*due", r"الإجمالي", r"الاجمالي", r"إجمالي", r"اجمالي", r"المجموع(?!\s*الفرعي)", r"المبلغ\s*المستحق", r"المبلغ\s*الإجمالي", r"صافي")
_SUBTOTAL = _words(r"sub\s*-?\s*total", r"المجموع\s*الفرعي", r"before\s*(vat|tax)", r"قبل\s*الضريبة")
_TAX = _words(r"\bvat\b", r"\btax\b", r"\bgst\b", r"ضريبة\s*القيمة\s*المضافة", r"الضريبة", r"ضريبة")
# Lines that carry a registration number, not an amount.
_TAX_NUMBER = _words(r"(vat|tax|trn)\s*(no|number|reg|registration|id|#)", r"\btrn\b", r"الرقم\s*الضريبي", r"رقم\s*التسجيل\s*الضريبي",
                     r"الرقم\s*الضريبي\s*للمنشأة", r"cr\s*no", r"السجل\s*التجاري")
_DATE_WORD = _words(r"\bdate\b", r"التاريخ", r"تاريخ", r"\bdated\b")
_REFERENCE = re.compile(
    r"(invoice|inv|receipt|bill|order|ref(?:erence)?|transaction|txn|رقم\s*الفاتورة|رقم\s*الإيصال|رقم\s*الايصال|رقم\s*المرجع|المرجع|رقم\s*الطلب)"
    r"\s*(?:no\.?|number|#|id|رقم)?\s*[:#.\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,39})",
    re.IGNORECASE,
)
_REFERENCE_WORD = re.compile(r"^(invoice|receipt|bill|order)\s*(no\.?|number|#)?\s*:?$|^(رقم\s*الفاتورة|رقم\s*الإيصال|رقم\s*الايصال|رقم\s*المرجع|رقم\s*الطلب)\s*:?$", re.IGNORECASE)
_REFERENCE_TOKEN = re.compile(r"\b[A-Za-z]{1,6}[-/]?\d{2,20}\b|\b\d{4,20}\b")
_HEADER = _words(r"tax\s*invoice", r"simplified", r"\binvoice\b", r"\breceipt\b", r"فاتورة", r"ضريبية", r"مبسطة", r"إيصال", r"ايصال",
                 r"welcome", r"thank\s*you", r"مرحبا", r"شكرا")
_EXPIRY = _words(r"expir", r"\bexp\b", r"valid\s*(until|till|thru|through|to)", r"date\s*of\s*expiry", r"تاريخ\s*الانتهاء", r"تاريخ\s*الإنتهاء",
                 r"ينتهي", r"صالح\s*حتى", r"صالحة\s*حتى", r"تاريخ\s*الانتهاء\s*من\s*الصلاحية", r"end\s*date", r"to\s*date")
_ISSUE = _words(r"\bissue", r"issued", r"date\s*of\s*issue", r"تاريخ\s*الإصدار", r"تاريخ\s*الاصدار", r"start\s*date", r"from\s*date",
                r"effective")
_BIRTH = _words(r"birth", r"\bdob\b", r"الميلاد")
_QTY = re.compile(r"^(\d{1,3})\s*[xX×@]\s*|\s(\d{1,3})\s*[xX×@]\s*\d")
_UNIT_PRICE = re.compile(r"(?:^|\s)\d{1,3}\s*[xX×@]\s*(?:\d[\d.,]*)?")


def _field(value: Any, confidence: float, line: OcrLine | None, *, keep_source: bool = True) -> dict[str, Any]:
    ocr = (line.confidence if line is not None else 100.0) / 100.0
    score = round(max(0.0, min(1.0, confidence * (0.5 + 0.5 * ocr))), 2)
    source = safe_value(line.text, 200) if (line is not None and keep_source) else None
    return {"value": value, "confidence": score, "source": source}


@dataclass
class Extraction:
    fields: dict[str, dict[str, Any]]
    items: list[dict[str, str]]
    warnings: list[str]


def _prepare(lines: list[OcrLine]) -> tuple[list[OcrLine], list[str]]:
    cleaned: list[OcrLine] = []
    warnings: list[str] = []
    for line in lines:
        text = clean_text(line.text)
        if not text:
            continue
        if has_instructions(text):
            if "instructions_in_text" not in warnings:
                warnings.append("instructions_in_text")
            # The line is kept out of every value.
            continue
        cleaned.append(OcrLine(text[:300], max(0.0, min(100.0, line.confidence))))
    if any(_HIJRI.search(line.text) for line in cleaned):
        warnings.append("hijri_date_seen")
    if not cleaned:
        warnings.append("no_text")
    return cleaned, warnings


def _last_amount(line: OcrLine) -> Decimal | None:
    values = [v for v in amounts_in(line.text) if v < Decimal(100000000)]
    return values[-1] if values else None


def _amount_near(lines: list[OcrLine], index: int) -> tuple[Decimal, OcrLine] | None:
    """The amount on a keyword line, or on the next line when the keyword line has none."""
    value = _last_amount(lines[index])
    if value is not None:
        return value, lines[index]
    if index + 1 < len(lines) and not _TOTAL.search(lines[index + 1].text) and not _TAX.search(lines[index + 1].text):
        value = _last_amount(lines[index + 1])
        if value is not None:
            return value, lines[index + 1]
    return None


def read_receipt(lines: list[OcrLine]) -> Extraction:
    """Merchant, date, total, currency, tax, reference and, when they add up, the line items."""
    rows, warnings = _prepare(lines)
    fields: dict[str, dict[str, Any]] = {}

    currency: tuple[str, float, OcrLine] | None = None
    for line in rows:
        found = currency_in(line.text)
        if found and (currency is None or found[1] > currency[1]):
            currency = (found[0], found[1], line)
    code = currency[0] if currency else None
    if currency:
        fields["currency"] = _field(code, currency[1], currency[2])

    totals: list[tuple[Decimal, OcrLine, int]] = []
    subtotal: Decimal | None = None
    tax: tuple[Decimal, OcrLine] | None = None
    first_total_index: int | None = None
    for index, line in enumerate(rows):
        text = line.text
        if _TAX_NUMBER.search(text):
            continue
        if _SUBTOTAL.search(text):
            near = _amount_near(rows, index)
            if near:
                subtotal = near[0]
            continue
        if _TOTAL.search(text):
            near = _amount_near(rows, index)
            if near:
                totals.append((near[0], near[1], index))
                first_total_index = index if first_total_index is None else first_total_index
            continue
        if _TAX.search(text):
            near = _amount_near(rows, index)
            if near and (tax is None or near[0] > 0):
                tax = near
    if totals:
        # The largest keyword total is the amount paid (a "total" line before VAT is smaller).
        value, line, _ = max(totals, key=lambda item: (item[0], -item[2]))
        confidence = 0.9 if len({t[0] for t in totals}) == 1 else 0.75
        fields["total"] = _field(money(value, code), confidence, line)
    elif subtotal is not None:
        fields["total"] = _field(money(subtotal + (tax[0] if tax else Decimal(0)), code), 0.4, None)
        warnings.append("total_from_subtotal")
    if tax is not None:
        total_value = Decimal(fields["total"]["value"]) if "total" in fields else None
        if total_value is None or tax[0] < total_value:
            fields["tax"] = _field(money(tax[0], code), 0.8, tax[1])

    receipt_date: tuple[FoundDate, OcrLine] | None = None
    for line in rows:
        found = dates_in(line.text)
        if not found:
            continue
        keyword = bool(_DATE_WORD.search(line.text))
        best = max(found, key=lambda item: item.confidence)
        if receipt_date is None or (keyword and not _DATE_WORD.search(receipt_date[1].text)):
            receipt_date = (FoundDate(best.value, min(1.0, best.confidence + (0.05 if keyword else 0))), line)
    if receipt_date:
        fields["date"] = _field(receipt_date[0].value.isoformat(), receipt_date[0].confidence, receipt_date[1])

    for index, line in enumerate(rows):
        if _TAX_NUMBER.search(line.text):
            continue
        match = _REFERENCE.search(line.text)
        if match and any(ch.isdigit() for ch in match.group(2)):
            fields["reference"] = _field(safe_value(match.group(2), 40), 0.7, line)
            break
        if _REFERENCE_WORD.search(line.text):
            # Right-to-left layouts often put the number on its own line next to the label.
            for near in (rows[index - 1] if index else None, rows[index + 1] if index + 1 < len(rows) else None):
                token = _REFERENCE_TOKEN.search(near.text) if near is not None else None
                if token and near is not None and not _TAX_NUMBER.search(near.text):
                    fields["reference"] = _field(safe_value(token.group(0), 40), 0.5, near)
                    break
            if "reference" in fields:
                break

    for line in rows[:4]:
        text = line.text
        letters = sum(ch.isalpha() for ch in text)
        if currency_in(text) and len(text.split()) == 1:
            continue  # a lone currency mark ("ر.س") is not a shop name
        if letters < 3 or letters < len(text) * 0.5 or dates_in(text) or _TOTAL.search(text) or _HEADER.search(text) or _TAX_NUMBER.search(text):
            continue
        fields["merchant"] = _field(safe_value(text, 120), 0.6, line)
        break

    items = _items(rows, first_total_index, subtotal, fields, code)
    if items is None:
        warnings.append("items_unreliable")
        items = []
    if "total" not in fields:
        warnings.append("total_not_found")
    return Extraction(fields, items, warnings)


def _items(rows: list[OcrLine], end: int | None, subtotal: Decimal | None, fields: dict[str, dict[str, Any]], code: str | None) -> list[dict[str, str]] | None:
    """Item lines between the header and the first total, kept only when they add up."""
    if end is None:
        return []
    found: list[tuple[str, int, Decimal]] = []
    for line in rows[:end]:
        text = line.text
        if _SUBTOTAL.search(text) or _TAX.search(text) or _TAX_NUMBER.search(text) or dates_in(text) or _REFERENCE.search(text):
            continue
        amount = _last_amount(line)
        letters = sum(ch.isalpha() for ch in text)
        if amount is None or letters < 2:
            continue
        quantity = 1
        qty = _QTY.search(text)
        if qty:
            quantity = int(qty.group(1) or qty.group(2))
        last = list(_AMOUNT.finditer(_PERCENT.sub(lambda m: " " * len(m.group(0)), text)))[-1]
        name = _UNIT_PRICE.sub(" ", text[: last.start()] + text[last.end():])
        name = re.sub(r"\b(" + "|".join(CURRENCY_CODES) + r")\b", "", name, flags=re.IGNORECASE)
        name = safe_value(re.sub(r"[\s:.\-@x×]+$", "", name), 80)
        if len(name) < 2:
            continue
        found.append((name, quantity, amount))
    if not found:
        return []
    total_items = sum((amount for _, _, amount in found), Decimal(0))
    targets = []
    if subtotal is not None:
        targets.append(subtotal)
    if "total" in fields:
        total = Decimal(fields["total"]["value"])
        targets.append(total)
        if "tax" in fields:
            targets.append(total - Decimal(fields["tax"]["value"]))
    if not any(abs(total_items - target) <= Decimal("0.02") for target in targets):
        return None
    return [{"description": name, "quantity": str(quantity), "amount": money(amount, code)} for name, quantity, amount in found]


def read_dates(lines: list[OcrLine]) -> Extraction:
    """Issue and expiry dates of an identity, insurance or registration document. Numbers are never read."""
    rows, warnings = _prepare(lines)
    fields: dict[str, dict[str, Any]] = {}
    loose: list[tuple[FoundDate, OcrLine]] = []
    for index, line in enumerate(rows):
        if _BIRTH.search(line.text):
            continue
        found = dates_in(line.text)
        target = line
        if not found and index + 1 < len(rows) and (_EXPIRY.search(line.text) or _ISSUE.search(line.text)):
            found = dates_in(rows[index + 1].text)
            target = rows[index + 1]
        if not found:
            continue
        best = max(found, key=lambda item: item.confidence)
        # Sources are left out: a line on an identity document can hold its number.
        if _EXPIRY.search(line.text) and "expiry_date" not in fields:
            fields["expiry_date"] = _field(best.value.isoformat(), best.confidence, target, keep_source=False)
        elif _ISSUE.search(line.text) and "issue_date" not in fields:
            fields["issue_date"] = _field(best.value.isoformat(), best.confidence, target, keep_source=False)
        else:
            loose.extend((item, target) for item in found)
    if "expiry_date" not in fields and len(loose) >= 2:
        ordered = sorted(loose, key=lambda item: item[0].value)
        fields.setdefault("issue_date", _field(ordered[0][0].value.isoformat(), 0.4, ordered[0][1], keep_source=False))
        fields["expiry_date"] = _field(ordered[-1][0].value.isoformat(), 0.4, ordered[-1][1], keep_source=False)
        warnings.append("dates_without_labels")
    issue, expiry = fields.get("issue_date"), fields.get("expiry_date")
    if issue and expiry and issue["value"] > expiry["value"]:
        fields["issue_date"], fields["expiry_date"] = expiry, issue
        warnings.append("dates_swapped")
    if not fields:
        warnings.append("dates_not_found")
    return Extraction(fields, [], warnings)


def lines_from_text(text: str) -> list[OcrLine]:
    """A PDF text layer as lines (full confidence)."""
    return [OcrLine(line) for line in text.splitlines() if line.strip()]


__all__ = ["Extraction", "OcrLine", "amounts_in", "clean_text", "currency_in", "dates_in", "has_instructions", "lines_from_text",
           "read_dates", "read_receipt", "safe_value"]
