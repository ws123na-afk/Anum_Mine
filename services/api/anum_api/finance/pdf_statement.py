"""PDF bank statements: reading the file and turning its table into rows (docs/finance.md#pdf-statements).

The PDF is untrusted input. It is read in memory only, capped in size and pages, opened with
pypdf (pure Python, already used by document extraction) and never written to disk, a store or a
log. A password, when the bank protects the file, is used to open it in memory and then dropped:
it is never stored, logged or echoed.

Two ways in:

* **Text PDFs** (what online banking exports): pypdf's layout mode keeps every line's horizontal
  positions, so the table's column headers tell which number is a debit, a credit or a balance.
* **Scanned PDFs** (a page photo inside a PDF): the page images go to the document vault's OCR
  service (local Tesseract, English and Arabic, ``vault/ocr_client.py``). OCR gives lines without
  positions, so the reader falls back to the line rules below.

Reading (``parse``) is pure and deterministic: no clock, store or network. It finds the header row
(English and Arabic column names), reads each row's date (several formats, Arabic-Indic digits),
description (joined across wrapped lines), money (either decimal mark, ``CR``/``DR``, minus signs and
brackets), balance and reference; skips page headers and footers, totals and carried-forward lines;
and finds the statement period, the account's last four digits (everything else of the number is
never returned), the opening and the closing balance. It then **verifies** the rows: the opening
balance plus every row must equal the closing balance, and each row must move the running balance by
its own amount. A mismatch is reported, never imported silently.

No bank's layout is verified: the repository has no real statements and none may be committed, so
the reader is generic and the owner's first statements validate it (stated in docs/finance.md).
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import csv_import
from .money import minor_digits

MAX_PDF_BYTES = 10_000_000
MAX_PAGES = 60
MAX_OCR_PAGES = 20
MAX_ROWS = csv_import.MAX_ROWS
MAX_LINE = 600
MAX_COLUMNS = 12
MAX_PASSWORD = 128
# Under this many letters and digits on every page together, the PDF counts as a scan.
SCAN_THRESHOLD = 40

ROLES = ("date", "description", "debit", "credit", "amount", "balance", "reference", "skip")
MONEY_ROLES = ("debit", "credit", "amount", "balance")


class PdfStatementError(ValueError):
    """The PDF cannot be read. ``code`` is stable for the client; the message says how to fix it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# -- opening the file ---------------------------------------------------------------------------


@dataclass
class PageImage:
    data: bytes
    content_type: str


@dataclass
class Extracted:
    page_count: int
    source: str  # "text" or "ocr"
    pages: list[list[str]] = field(default_factory=list)
    images: list[PageImage] = field(default_factory=list)
    sha256: str = ""


_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".tif": "image/tiff", ".tiff": "image/tiff",
                ".webp": "image/webp", ".gif": "image/gif"}


def extract(data: bytes, password: str | None = None) -> Extracted:
    """The text lines of every page, or the page images of a scan. Blocking: run it in a thread."""
    from pypdf import PasswordType, PdfReader
    from pypdf.errors import PdfReadError

    if len(data) > MAX_PDF_BYTES:
        raise PdfStatementError("too_large", f"The PDF is larger than {MAX_PDF_BYTES // 1_000_000} MB. Download one month at a time.")
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise PdfStatementError("not_pdf", "This file is not a PDF. Pick the statement PDF your bank gave you.")
    sha = hashlib.sha256(data).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            opened = PasswordType.NOT_DECRYPTED
            try:
                opened = reader.decrypt("")
                if opened == PasswordType.NOT_DECRYPTED and password:
                    opened = reader.decrypt(password[:MAX_PASSWORD])
            except Exception:  # noqa: BLE001 - pypdf raises many types for a bad key or an unsupported cipher
                opened = PasswordType.NOT_DECRYPTED
            if opened == PasswordType.NOT_DECRYPTED:
                if password:
                    raise PdfStatementError("password_wrong", "That password did not open the PDF. Check it and try again.")
                raise PdfStatementError(
                    "password_required", "This PDF is protected with a password. Type the password your bank gave you to open it."
                )
        page_count = len(reader.pages)
        if page_count == 0:
            raise PdfStatementError("damaged", "This PDF has no pages.")
        if page_count > MAX_PAGES:
            raise PdfStatementError("too_many_pages", f"This PDF has more than {MAX_PAGES} pages. Download one month at a time.")
        pages: list[list[str]] = []
        letters = 0
        for page in reader.pages:
            text = page.extract_text(extraction_mode="layout") or ""
            lines = [line.rstrip()[:MAX_LINE] for line in text.splitlines()]
            letters += sum(1 for line in lines for ch in line if ch.isalnum())
            pages.append(lines)
        if letters >= SCAN_THRESHOLD:
            return Extracted(page_count, "text", pages=pages, sha256=sha)
        if page_count > MAX_OCR_PAGES:
            raise PdfStatementError("too_many_pages", f"A scanned statement can have at most {MAX_OCR_PAGES} pages. Split it and import each part.")
        images: list[PageImage] = []
        for page in reader.pages:
            found = [(len(image.data), image) for image in page.images]
            if not found:
                continue
            _, biggest = max(found, key=lambda item: item[0])
            suffix = "." + biggest.name.rsplit(".", 1)[-1].lower() if "." in biggest.name else ""
            content_type = _IMAGE_TYPES.get(suffix)
            data_out = biggest.data
            if content_type is None:
                buffer = io.BytesIO()
                biggest.image.save(buffer, format="PNG")
                data_out, content_type = buffer.getvalue(), "image/png"
            images.append(PageImage(data_out, content_type))
        if not images:
            raise PdfStatementError("no_text", "This PDF has no text and no page images, so there is nothing to read.")
        return Extracted(page_count, "ocr", images=images, sha256=sha)
    except PdfStatementError:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, IndexError, OSError, RecursionError, AttributeError) as exc:
        raise PdfStatementError("damaged", "This PDF is damaged or not a standard PDF. Download it again from your bank.") from exc


OcrReader = Callable[[bytes, str], Awaitable[list[str]]]


async def ocr_page(data: bytes, content_type: str) -> list[str]:
    """One scanned page's lines from the vault's OCR service (the seam tests replace)."""
    from ..vault.ocr_client import read_text

    result = await read_text(data, content_type)
    return [line.text for line in result.lines]


async def read_scanned(images: list[PageImage], reader: OcrReader | None = None) -> list[list[str]]:
    """The lines of each scanned page. Raises ``PdfStatementError("ocr_unavailable")`` with the reason."""
    from ..vault.ocr_client import OcrUnavailable

    read = reader or ocr_page
    pages: list[list[str]] = []
    for image in images:
        try:
            pages.append([line[:MAX_LINE] for line in await read(image.data, image.content_type)])
        except OcrUnavailable as exc:
            raise PdfStatementError("ocr_unavailable", f"This PDF is a scan and could not be read: {exc}") from None
    return pages


# -- normalizing --------------------------------------------------------------------------------

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_INVISIBLE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]")


def normalize(line: str) -> str:
    """Western digits, no control or direction characters; one character for one, so columns keep
    their positions (NFKC per character only where it stays one character)."""
    out = []
    for ch in _INVISIBLE.sub(" ", line.translate(_DIGITS)).replace("\t", "    "):
        folded = unicodedata.normalize("NFKC", ch)
        out.append(folded if len(folded) == 1 else ch)
    return "".join(out).replace("−", "-")


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s/]", " ", text.lower()).split())


# -- header ------------------------------------------------------------------------------------

# Checked in this order: "Debit Amount" is a debit, "Transaction Date" a date, "Balance" never an amount.
_ROLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("balance", ("balance", "running balance", "available balance", "الرصيد", "رصيد")),
    ("debit", ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "dr", "مدين", "سحب", "مسحوبات", "المدين", "خصم")),
    ("credit", ("credit", "credits", "deposit", "deposits", "money in", "paid in", "cr", "دائن", "إيداع", "ايداع", "إيداعات", "ايداعات", "الدائن")),
    ("date", ("date", "transaction date", "posting date", "posted date", "txn date", "value date", "booking date", "التاريخ", "تاريخ")),
    ("reference", ("reference", "ref", "ref no", "reference no", "cheque", "cheque no", "chq", "chq no", "المرجع", "رقم المرجع", "رقم الشيك")),
    ("amount", ("amount", "transaction amount", "المبلغ", "مبلغ")),
    ("description", ("description", "details", "transaction details", "narration", "narrative", "particulars", "remarks", "memo",
                     "transaction", "transaction description", "البيان", "الوصف", "التفاصيل", "تفاصيل", "وصف")),
)


def _role_of(label: str) -> str | None:
    folded = _fold(label).replace(" aed", "").replace(" sar", "")
    words = folded.split()
    for role, names in _ROLE_WORDS:
        for name in names:
            if folded == name or (len(name) > 3 and name in folded) or name in words:
                return role
    return None


@dataclass
class Column:
    index: int
    label: str
    role: str
    start: int
    end: int


def _cells(line: str) -> list[tuple[int, int, str]]:
    """Runs of text separated by two or more spaces, with their character spans."""
    return [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def find_header(line: str) -> list[Column] | None:
    """The table's columns when ``line`` is a header row: a date column and at least one money column."""
    cells = _cells(normalize(line))
    if not 3 <= len(cells) <= MAX_COLUMNS:
        return None
    roles = [_role_of(text) for _, _, text in cells]
    if any(_DATE_AT_START.match(text) or _MONEY.search(text) for _, _, text in cells):
        return None
    if "date" not in roles or not any(r in MONEY_ROLES for r in roles):
        return None
    if sum(1 for r in roles if r is None) > len(roles) // 2:
        return None
    columns: list[Column] = []
    seen_date = False
    for index, ((start, end, text), role) in enumerate(zip(cells, roles, strict=True)):
        picked = role or "skip"
        if picked == "date":
            # A second date column (value date next to the transaction date) is not needed.
            picked = "skip" if seen_date else "date"
            seen_date = True
        columns.append(Column(index, csv_import.clean_text(text, 60), picked, start, end))
    return columns


def apply_roles(columns: list[Column], roles: list[str] | None) -> list[Column]:
    """The owner's own column choice, one role per detected column, in order."""
    if not roles:
        return columns
    if len(roles) != len(columns) or any(r not in ROLES for r in roles):
        raise PdfStatementError("bad_mapping", "Pick one role for every column.")
    if roles.count("date") != 1 or not any(r in MONEY_ROLES for r in roles):
        raise PdfStatementError("bad_mapping", "Pick exactly one date column and at least one money column.")
    if "amount" in roles and ("debit" in roles or "credit" in roles):
        raise PdfStatementError("bad_mapping", "Pick either an amount column or debit and credit columns, not both.")
    for role in ("description", "balance", "reference", "amount", "debit", "credit"):
        if roles.count(role) > 1:
            raise PdfStatementError("bad_mapping", "Each role can be picked for one column only.")
    return [Column(c.index, c.label, r, c.start, c.end) for c, r in zip(columns, roles, strict=True)]


# -- tokens ------------------------------------------------------------------------------------

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DATE = (
    r"(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}|\d{1,2}[ \-]" + _MONTHS + r"[ \-,]+\d{2,4}"
    r"|" + _MONTHS + r" \d{1,2},? \d{4}|\d{1,2}[/\-.]\d{1,2}(?![/\-.\d]))"
)
_DATE_AT_START = re.compile(r"^\s*(" + _DATE + r")(?![\w])", re.IGNORECASE)
_ANY_DATE = re.compile(r"(?<![\w/.\-])(" + _DATE + r")(?![\w])", re.IGNORECASE)
_MARK = r"(?:\s?(?:CR|DR|Cr|Dr|cr|dr)\b)?"


def _money_pattern(decimals: int) -> re.Pattern[str]:
    places = r"\d{%d}" % decimals if decimals else r"\d{2}"
    return re.compile(
        r"(?<![\w.,/:])(\(?[-+]?\(?\d{1,3}(?:[,.']\d{3})*[.,]" + places + r"\)?-?|\(?[-+]?\d+[.,]" + places + r"\)?-?)" + _MARK + r"(?![\w.,/])"
    )


_MONEY = _money_pattern(2)

_OPENING = re.compile(
    r"opening balance|balance brought forward|brought forward|\bb/f\b|previous balance|balance b/f|beginning balance|"
    r"الرصيد الافتتاحي|رصيد افتتاحي|الرصيد السابق|رصيد سابق|رصيد أول المدة|الرصيد المرحل من",
    re.IGNORECASE,
)
_CLOSING = re.compile(
    r"closing balance|balance carried forward|carried forward|\bc/f\b|ending balance|balance c/f|"
    r"الرصيد الختامي|رصيد ختامي|رصيد آخر المدة|الرصيد المرحل إلى|الرصيد الحالي",
    re.IGNORECASE,
)
_CARRIED = re.compile(r"carried forward|\bc/f\b|الرصيد المرحل إلى|brought forward|\bb/f\b|الرصيد المرحل من", re.IGNORECASE)
_TOTAL = re.compile(r"^\W*(total|totals|grand total|sub ?total|المجموع|الإجمالي|اجمالي|مجموع)\b", re.IGNORECASE)
_FOOTER = re.compile(
    r"\bpage\s*\d+(\s*(of|/)\s*\d+)?\b|صفحة\s*\d+|this is a (computer|system)[- ]generated|does not require (a )?signature|"
    r"end of statement|نهاية الكشف",
    re.IGNORECASE,
)
_PERIOD_WORDS = re.compile(r"period|statement (date|from)|\bfrom\b|للفترة|الفترة|من تاريخ|from date", re.IGNORECASE)
_ACCOUNT = re.compile(
    r"(account|a/c|acct|iban|card)\s*(no\.?|number|#)?\s*[:.\-]?\s*([A-Z]{2}\d{2})?[\s\-]*([\dXx*•\s\-]{4,40}\d{4})"
    r"|(رقم الحساب|رقم البطاقة|الآيبان|ايبان)\s*[:.\-]?\s*([A-Z]{2}\d{2})?[\s\-]*([\dXx*•\s\-]{4,40}\d{4})",
    re.IGNORECASE,
)
_IBAN = re.compile(r"\b(?:AE|SA)\d{2}[\s]?(?:\d[\s]?){15,26}\b")
_CURRENCY = re.compile(r"\b(AED|SAR|USD|EUR|GBP|KWD|QAR|BHD|OMR|INR|PKR)\b|د\.إ|درهم|ر\.س|ريال")
_ARABIC_CURRENCY = {"د.إ": "AED", "درهم": "AED", "ر.س": "SAR", "ريال": "SAR"}
_REFERENCE = re.compile(r"\b(?:ref(?:erence)?|chq|cheque|txn id|rrn)\s*(?:no\.?|#)?\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,30})", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<![\d.,])\d(?:[ \-]?\d){7,}(?![\d]|[.,]\d)")


def mask_numbers(text: str) -> str:
    """Card and account numbers in a description cut to ``***`` and their last four digits."""

    def cut(match: re.Match[str]) -> str:
        digits = "".join(ch for ch in match.group(0) if ch.isdigit())
        return "***" + digits[-4:]

    text = re.sub(r"\b\d{4,6}[Xx*•]{4,10}(\d{4})\b", lambda m: "***" + m.group(1), text)
    return _LONG_NUMBER.sub(cut, text)


# -- parsing -----------------------------------------------------------------------------------


@dataclass
class Row:
    line: int
    page: int
    raw_date: str
    posted_on: date | None = None
    description: str = ""
    amount: Decimal | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None
    reference: str | None = None
    flags: list[str] = field(default_factory=list)
    explicit_sign: bool = False


@dataclass
class Check:
    status: str  # "matched", "mismatch" or "unknown"
    opening: Decimal | None
    closing: Decimal | None
    rows_total: Decimal
    expected_closing: Decimal | None
    difference: Decimal | None
    opening_source: str  # "statement", "rows" or "none"
    closing_source: str
    breaks: list[int] = field(default_factory=list)


@dataclass
class Statement:
    columns: list[Column]
    layout: str  # "table" (column positions) or "lines" (no header found, or a scan)
    rows: list[Row]
    date_format: str
    date_formats: list[str]
    date_format_ambiguous: bool
    period_start: date | None
    period_end: date | None
    account_last4: str | None
    currency: str | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    check: Check
    skipped_lines: int = 0
    warnings: list[str] = field(default_factory=list)


def _money_tokens(line: str, pattern: re.Pattern[str]) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in pattern.finditer(line)]


def _to_amount(token: str) -> Decimal | None:
    return csv_import.parse_amount(token)


def _has_sign(token: str) -> bool:
    text = token.strip()
    return bool(re.search(r"(?i)\b(cr|dr)$", text) or text.startswith(("-", "(", "+")) or text.endswith("-"))


def _nearest(columns: list[Column], start: int, end: int, roles: tuple[str, ...]) -> Column | None:
    """The column a number sits under: numbers are usually right-aligned, labels left-aligned or centered."""
    best: tuple[float, Column] | None = None
    center = (start + end) / 2
    for column in columns:
        if column.role not in roles:
            continue
        if column.start <= center <= column.end + 2:
            score = 0.0
        else:
            score = min(abs(end - column.end), abs(start - column.start), abs(center - (column.start + column.end) / 2))
        if best is None or score < best[0]:
            best = (score, column)
    return best[1] if best else None


def _year_hint(lines: list[str]) -> int | None:
    for line in lines:
        found = re.search(r"\b(19\d{2}|20\d{2})\b", line)
        if found:
            return int(found.group(1))
    return None


def _parse_date(text: str, fmt: str, year: int | None) -> date | None:
    value = re.sub(r"\s+", " ", text.strip().replace(",", ", ")).replace(" ,", ",")
    value = re.sub(r",\s+", ", ", value)
    if re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}", value):
        if year is None:
            return None
        sep = re.search(r"[/\-.]", value).group(0)  # type: ignore[union-attr]
        value = f"{value}{sep}{year}"
        fmt = fmt if fmt.count(sep) == 2 else f"%d{sep}%m{sep}%Y"
    for candidate in (fmt, fmt.replace("%b", "%B"), fmt.replace("%B", "%b")):
        found = csv_import._parse_date(value.replace("Sept", "Sep"), candidate)
        if found is not None:
            return found
    return None


_EXTRA_FORMATS = ("%d %b %y", "%d-%b-%Y", "%b %d %Y")


def _formats_for(values: list[str]) -> list[str]:
    """Every known format that reads all the row dates (day first wins a tie, the Gulf convention)."""
    full = [v for v in values if not re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}", v.strip())]
    if not full:
        sep = re.search(r"[/\-.]", values[0]).group(0) if values else "/"  # type: ignore[union-attr]
        day_first = all(int(re.split(r"[/\-.]", v.strip())[1]) <= 12 for v in values) if values else True
        return [f"%d{sep}%m{sep}%Y"] if day_first else [f"%m{sep}%d{sep}%Y"]
    candidates = list(csv_import.DATE_FORMATS) + [f for f in _EXTRA_FORMATS if f not in csv_import.DATE_FORMATS]
    cleaned = [re.sub(r"\s+", " ", v.strip()).replace("Sept", "Sep") for v in full]
    return [f for f in candidates if all(_parse_date(v, f, None) is not None for v in cleaned)]


def _currency_in(lines: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for line in lines:
        for match in _CURRENCY.finditer(line):
            code = match.group(1) or _ARABIC_CURRENCY.get(match.group(0))
            if code:
                counts[code] = counts.get(code, 0) + 1
    return max(counts, key=lambda c: counts[c]) if counts else None


def _last4(lines: list[str]) -> str | None:
    for line in lines:
        iban = _IBAN.search(line)
        if iban:
            digits = re.sub(r"\D", "", iban.group(0))
            return digits[-4:]
        found = _ACCOUNT.search(line)
        if found:
            number = found.group(4) or found.group(7) or ""
            digits = re.sub(r"\D", "", number)
            if len(digits) >= 4:
                return digits[-4:]
    return None


def parse(
    pages: list[list[str]],
    *,
    currency: str,
    roles: list[str] | None = None,
    date_format: str | None = None,
    positioned: bool = True,
) -> Statement:
    """Rows, period, balances and the balance check from a statement's lines (pure)."""
    decimals = minor_digits(currency) or 2
    money = _money_pattern(decimals)
    normalized = [[normalize(line) for line in page] for page in pages]
    every_line = [line for page in normalized for line in page]
    year = _year_hint(every_line)
    columns: list[Column] = []
    if positioned:
        for line in every_line:
            found = find_header(line)
            if found:
                columns = found
                break
    if roles and not columns:
        raise PdfStatementError("bad_mapping", "No column headers were found in this PDF, so there are no columns to map.")
    columns = apply_roles(columns, roles) if columns else []
    header_keys = {_fold(" ".join(c.label for c in columns))} if columns else set()
    layout = "table" if columns else "lines"

    rows: list[Row] = []
    opening: Decimal | None = None
    closing: Decimal | None = None
    period: tuple[str, str] | None = None
    preamble: list[str] = []
    skipped = 0
    current: Row | None = None
    seen_table = False
    counter = 0

    def finish() -> None:
        nonlocal current
        if current is not None:
            current.description = mask_numbers(csv_import.clean_text(current.description))
            rows.append(current)
        current = None

    for page_number, page in enumerate(normalized, start=1):
        for line in page:
            stripped = line.strip()
            if not stripped:
                continue
            if columns and _fold(" ".join(text for _, _, text in _cells(line))) in header_keys:
                finish()
                seen_table = True
                continue
            if not columns and find_header(line):
                finish()
                seen_table = True
                continue
            tokens = _money_tokens(line, money)
            if _OPENING.search(stripped) and not _CLOSING.search(stripped):
                finish()
                if tokens and opening is None:
                    opening = _to_amount(tokens[-1][2])
                skipped += 1
                continue
            if _CLOSING.search(stripped):
                finish()
                if tokens:
                    value = _to_amount(tokens[-1][2])
                    # A page's "carried forward" is the balance so far; the last one, or "closing balance", wins.
                    closing = value
                skipped += 1
                continue
            if _FOOTER.search(stripped) or _TOTAL.search(stripped):
                finish()
                skipped += 1
                continue
            dates = _ANY_DATE.findall(stripped)
            starts = _DATE_AT_START.match(line)
            if period is None and len(dates) >= 2 and not tokens and (_PERIOD_WORDS.search(stripped) or not starts):
                period = (dates[0], dates[1])
                skipped += 1
                continue
            if starts and (tokens or seen_table or not columns):
                finish()
                if not tokens and not seen_table:
                    preamble.append(stripped)
                    continue
                counter += 1
                current = Row(line=counter, page=page_number, raw_date=starts.group(1))
                rest_start = starts.end()
                _fill(current, line, rest_start, tokens, columns)
                continue
            if current is not None and not tokens:
                # A wrapped description: the next line carries more of it.
                if len(current.description) < csv_import.MAX_DESCRIPTION:
                    current.description = f"{current.description} {_text_outside(line, [], columns)}".strip()
                continue
            if current is not None and tokens and current.amount is None and current.debit is None and current.credit is None:
                _fill(current, line, 0, tokens, columns)
                continue
            finish()
            if not seen_table:
                preamble.append(stripped)
            else:
                skipped += 1
    finish()
    if len(rows) > MAX_ROWS:
        raise PdfStatementError("too_many_rows", f"The statement has more than {MAX_ROWS} rows. Import one month at a time.")

    raw_dates = [r.raw_date for r in rows]
    formats = _formats_for(raw_dates) if raw_dates else []
    if date_format is not None:
        if date_format not in csv_import.DATE_FORMATS and date_format not in _EXTRA_FORMATS:
            raise PdfStatementError("bad_date_format", "Unknown date format. Pick one of the listed formats.")
        fmt = date_format
    else:
        fmt = formats[0] if formats else "%d/%m/%Y"
    ambiguous = date_format is None and any(csv_import._DAY_FIRST_TWIN.get(other) == fmt for other in formats)
    period_start = _parse_date(period[0], fmt, year) if period else None
    period_end = _parse_date(period[1], fmt, year) if period else None
    period_year = period_end.year if period_end else year
    for row in rows:
        row.posted_on = _parse_date(row.raw_date, fmt, period_year)
        if row.posted_on is None:
            row.flags.append("date_unreadable")
        if period_start and period_end and row.posted_on and period_start.month > period_end.month and row.posted_on.month >= period_start.month:
            # A December to January statement whose rows carry no year.
            if not re.search(r"\d{4}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2}", row.raw_date):
                row.posted_on = row.posted_on.replace(year=period_start.year)

    # Newest first: put the rows in time order so balances run forward.
    dated = [r.posted_on for r in rows if r.posted_on]
    if len(dated) >= 2 and dated[0] > dated[-1]:
        rows.reverse()
        for number, row in enumerate(rows, start=1):
            row.line = number
    _signs(rows, opening)
    check = _check(rows, opening, closing)
    statement_currency = _currency_in(every_line)
    warnings: list[str] = []
    if statement_currency and statement_currency != currency:
        warnings.append("currency_differs")
    if not rows:
        warnings.append("no_rows")
    return Statement(
        columns=columns, layout=layout, rows=rows, date_format=fmt, date_formats=formats, date_format_ambiguous=ambiguous,
        period_start=period_start, period_end=period_end, account_last4=_last4(preamble + every_line[:80]), currency=statement_currency,
        opening_balance=opening, closing_balance=closing, check=check, skipped_lines=skipped, warnings=warnings,
    )


def _text_outside(line: str, spans: list[tuple[int, int]], columns: list[Column]) -> str:
    """The description part of a line: what is left after the date, the money and the reference."""
    chars = list(line)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = " "
    if columns:
        for start, end, text in _cells("".join(chars)):
            column = _nearest(columns, start, end, ("description", "reference", "skip", "date") + MONEY_ROLES)
            if column is not None and column.role in ("reference",):
                for i in range(start, end):
                    chars[i] = " "
    return " ".join("".join(chars).split())


def _fill(row: Row, line: str, offset: int, tokens: list[tuple[int, int, str]], columns: list[Column]) -> None:
    spans: list[tuple[int, int]] = [(0, offset)] if offset else []
    # A second date right after the first (the value date) is not part of the description.
    second = _DATE_AT_START.match(line[offset:]) if offset else None
    if second:
        spans.append((offset, offset + second.end()))
    money_tokens = [t for t in tokens if t[0] >= offset]
    if columns:
        for start, end, token in money_tokens:
            column = _nearest(columns, start, end, MONEY_ROLES)
            value = _to_amount(token)
            if column is None or value is None:
                continue
            spans.append((start, end))
            if column.role == "balance":
                row.balance = value
            elif column.role == "debit":
                row.debit = abs(value)
            elif column.role == "credit":
                row.credit = abs(value)
            else:
                row.amount, row.explicit_sign = value, _has_sign(token)
        reference_column = next((c for c in columns if c.role == "reference"), None)
        if reference_column is not None:
            for start, end, text in _cells(line):
                if start < offset:
                    continue
                if _nearest(columns, start, end, ("description", "reference", "skip") + MONEY_ROLES) is reference_column and not _MONEY.fullmatch(text):
                    row.reference = csv_import.clean_text(text, 60)
                    spans.append((start, end))
    else:
        values = [(s, e, t, _to_amount(t)) for s, e, t in money_tokens]
        values = [v for v in values if v[3] is not None]
        if len(values) >= 2:
            row.amount, row.explicit_sign = values[-2][3], _has_sign(values[-2][2])
            row.balance = values[-1][3]
            spans.extend((s, e) for s, e, _, _ in values[-2:])
        elif values:
            row.amount, row.explicit_sign = values[0][3], _has_sign(values[0][2])
            spans.append((values[0][0], values[0][1]))
    text = _text_outside(line, spans, columns)
    if row.reference is None:
        found = _REFERENCE.search(text)
        if found:
            row.reference = found.group(1)[:60]
    row.description = f"{row.description} {text}".strip()


def _signs(rows: list[Row], opening: Decimal | None) -> None:
    """Each row's signed amount: debit and credit columns, an explicit sign or CR/DR, else the balance's move."""
    previous = opening
    for row in rows:
        if row.debit is not None or row.credit is not None:
            row.amount = (row.credit or Decimal(0)) - (row.debit or Decimal(0))
        elif row.amount is not None and not row.explicit_sign:
            if previous is not None and row.balance is not None:
                if previous + abs(row.amount) == row.balance:
                    row.amount = abs(row.amount)
                elif previous - abs(row.amount) == row.balance:
                    row.amount = -abs(row.amount)
                else:
                    row.amount = -abs(row.amount)
                    row.flags.append("sign_unsure")
                if "sign_unsure" not in row.flags:
                    row.flags.append("sign_from_balance")
            else:
                # No sign, no balance to tell: most statement lines are payments out. The owner checks it.
                row.amount = -abs(row.amount)
                row.flags.append("sign_unsure")
        if row.amount is None:
            row.flags.append("amount_missing")
        elif row.amount == 0:
            row.flags.append("amount_zero")
        if row.balance is not None:
            previous = row.balance
        elif previous is not None and row.amount is not None:
            previous = previous + row.amount


def _check(rows: list[Row], opening: Decimal | None, closing: Decimal | None) -> Check:
    unsure = any("sign_unsure" in r.flags for r in rows)
    return verify([(r.line, r.amount, r.balance) for r in rows], opening, closing, unsure=unsure)


def verify(
    items: list[tuple[int, Decimal | None, Decimal | None]], opening: Decimal | None, closing: Decimal | None, *, unsure: bool = False,
) -> Check:
    """Opening plus every row must equal closing; each row must move the running balance by its amount.

    With no stated opening or closing balance, the first and last row balances stand in for them, so a
    missing or misread row in between still shows. ``unsure``: some row's sign is a guess, so rows that
    add up prove nothing and the answer is at best "unknown"."""
    total = sum((amount for _, amount, _ in items if amount is not None), Decimal(0))
    opening_source = "statement" if opening is not None else "none"
    closing_source = "statement" if closing is not None else "none"
    if opening is None and not unsure and items and items[0][1] is not None and items[0][2] is not None:
        opening, opening_source = items[0][2] - items[0][1], "rows"
    if closing is None and items and items[-1][2] is not None:
        closing, closing_source = items[-1][2], "rows"
    breaks: list[int] = []
    running = opening
    for line, amount, balance in items:
        if running is not None and amount is not None:
            running = running + amount
        if balance is not None:
            if running is not None and running != balance:
                breaks.append(line)
            running = balance
    if opening is None or closing is None or not items:
        return Check("unknown", opening, closing, total, None, None, opening_source, closing_source, breaks)
    expected = opening + total
    difference = closing - expected
    matched = difference == 0 and not breaks and not any(amount is None for _, amount, _ in items)
    outcome = ("unknown" if unsure else "matched") if matched else "mismatch"
    return Check(outcome, opening, closing, total, expected, difference, opening_source, closing_source, breaks)


__all__ = [
    "MAX_OCR_PAGES",
    "MAX_PAGES",
    "MAX_PDF_BYTES",
    "ROLES",
    "Check",
    "Column",
    "Extracted",
    "PdfStatementError",
    "Row",
    "Statement",
    "extract",
    "mask_numbers",
    "parse",
    "read_scanned",
    "verify",
]
