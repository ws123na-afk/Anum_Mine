"""Layers 2 and 4: rows, signs, confidence and the balance checks (docs/finance-statements.md).

``parse`` is pure and deterministic: positioned words in, a ``Statement`` out. No clock, store or
network. Order of work:

1. Lines and tokens per page; the header (repeated on every page is fine) gives the columns. Without a
   header, columns come from the content (``columns.cluster``).
2. Every line is classified: header, page header or footer, totals, opening balance ("brought
   forward"), closing balance ("carried forward" on a page, "closing balance" at the end), statement
   period, a transaction row, or more description for the row above (wrapped lines are merged).
3. Dates: one order for the whole statement from all its dates (``dates.decide_order``).
4. Signs: debit and credit columns; a signed amount (minus, brackets, CR/DR); else the running balance
   (previous balance plus or minus the amount must give the printed balance). When the reading with
   debit and credit swapped makes every balance agree and the other does not, the columns are swapped
   and the owner is told.
5. Checks: opening plus every row must equal closing; every printed balance must follow from the row
   before; a page's balance brought forward must equal the previous page's last balance. Every row
   gets a confidence (0 to 1) and the reasons that lowered it. Nothing that fails is imported silently:
   the preview shows it and the import asks the owner to confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .. import csv_import
from ..money import minor_digits
from . import amounts, dates
from .columns import MONEY_ROLES, ROLES, Column, Line, Token, cluster, fingerprint, fold, header_columns, lines_of, nearest, tokenize
from .errors import PdfStatementError
from .words import Word

MAX_ROWS = csv_import.MAX_ROWS
LOW_CONFIDENCE = 0.6

_OPENING = re.compile(
    r"opening balance|balance brought forward|brought forward|\bb/f\b|previous balance|balance b/f|beginning balance|start balance|"
    r"الرصيد الافتتاحي|رصيد افتتاحي|الرصيد السابق|رصيد سابق|رصيد أول المدة|الرصيد المرحل من|رصيد مدور",
    re.IGNORECASE,
)
_CLOSING = re.compile(
    r"closing balance|balance carried forward|carried forward|\bc/f\b|ending balance|balance c/f|end balance|"
    r"الرصيد الختامي|رصيد ختامي|رصيد آخر المدة|الرصيد المرحل إلى|الرصيد الحالي|الرصيد النهائي",
    re.IGNORECASE,
)
_CARRIED = re.compile(r"carried forward|\bc/f\b|الرصيد المرحل إلى", re.IGNORECASE)
_BROUGHT = re.compile(r"brought forward|\bb/f\b|الرصيد المرحل من|رصيد مدور", re.IGNORECASE)
_TOTAL = re.compile(r"^\W*(total|totals|grand total|sub ?total|المجموع|الإجمالي|اجمالي|مجموع)", re.IGNORECASE)
_FOOTER = re.compile(
    r"\bpage\s*\d+(\s*(of|/)\s*\d+)?\b|صفحة\s*\d+|this is a (computer|system)[- ]generated|does not require (a )?signature|"
    r"end of statement|نهاية الكشف",
    re.IGNORECASE,
)
_PERIOD = re.compile(r"period|statement (date|from)|\bfrom\b|للفترة|الفترة|من تاريخ|from date", re.IGNORECASE)
_ACCOUNT = re.compile(
    r"(account|a/c|acct|iban|card)\s*(no\.?|number|#)?\s*[:.\-]?\s*([A-Z]{2}\d{2})?[\s\-]*([\dXx*•\s\-]{4,40}\d{4})"
    r"|(رقم الحساب|رقم البطاقة|الآيبان|ايبان)\s*[:.\-]?\s*([A-Z]{2}\d{2})?[\s\-]*([\dXx*•\s\-]{4,40}\d{4})",
    re.IGNORECASE,
)
_IBAN = re.compile(r"\b(?:AE|SA)\d{2}\s?(?:\d\s?){15,26}\b")
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
    currency: str | None = None
    flags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    explicit_sign: bool = False
    top: float = 0.0
    bottom: float = 0.0
    ocr: float = 100.0
    money: list[Token] = field(default_factory=list)


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
    page_breaks: list[int] = field(default_factory=list)


@dataclass
class Statement:
    columns: list[Column]
    layout: str  # "table" (header found), "columns" (from the content) or "lines" (no positions)
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
    fingerprint: str | None = None
    confidence: float = 1.0
    skipped_lines: int = 0
    warnings: list[str] = field(default_factory=list)


# -- the owner's mapping -----------------------------------------------------------------------


def apply_roles(columns: list[Column], roles: list[str] | None) -> list[Column]:
    """The owner's (or a saved template's) column choice, one role per detected column, in order."""
    if not roles:
        return columns
    if len(roles) != len(columns) or any(r not in ROLES for r in roles):
        raise PdfStatementError("bad_mapping", "Pick one role for every column.")
    if roles.count("date") != 1 or not any(r in MONEY_ROLES for r in roles):
        raise PdfStatementError("bad_mapping", "Pick exactly one date column and at least one money column.")
    if "amount" in roles and ("debit" in roles or "credit" in roles):
        raise PdfStatementError("bad_mapping", "Pick either an amount column or debit and credit columns, not both.")
    for role in ("description", "balance", "reference", "amount", "debit", "credit", "currency"):
        if roles.count(role) > 1:
            raise PdfStatementError("bad_mapping", "Each role can be picked for one column only.")
    return [Column(c.index, c.label, r, c.x0, c.x1) for c, r in zip(columns, roles, strict=True)]


# -- parsing ------------------------------------------------------------------------------------


def parse(
    pages: list[list[Word]],
    *,
    currency: str,
    roles: list[str] | None = None,
    date_format: str | None = None,
    positioned: bool = True,
    page_widths: list[float] | None = None,
) -> Statement:
    decimals = minor_digits(currency) or 2
    money = amounts.money_pattern(decimals)
    page_lines: list[list[Line]] = []
    for words in pages:
        lines = lines_of(words)
        for line in lines:
            line.tokens = tokenize(line, money)
        page_lines.append(lines)
    every = [line for lines in page_lines for line in lines]
    year = _year_hint(every)

    columns: list[Column] = []
    header_page = 0
    if positioned:
        for page_number, lines in enumerate(page_lines):
            for index, line in enumerate(lines):
                found = header_columns(line, lines[index + 1] if index + 1 < len(lines) else None)
                if found:
                    columns, header_page = found, page_number
                    break
            if columns:
                break
    layout = "table" if columns else ("columns" if positioned else "lines")
    width = (page_widths or [0.0])[header_page] if page_widths and header_page < len(page_widths) else 0.0
    print_ = fingerprint(columns, width) if columns else None
    if layout == "table":
        columns = apply_roles(columns, roles)
    elif roles and layout == "lines":
        raise PdfStatementError("bad_mapping", "No columns were found in this PDF, so there are no columns to map.")

    header_keys = {fold(" ".join(c.label for c in columns))} if columns else set()
    rows: list[Row] = []
    opening: Decimal | None = None
    closing: Decimal | None = None
    brought: dict[int, Decimal] = {}
    carried: dict[int, Decimal] = {}
    period: tuple[str, str] | None = None
    preamble: list[str] = []
    skipped = 0
    current: Row | None = None
    in_table = not columns
    counter = 0
    page_columns = columns

    for page_number, lines in enumerate(page_lines, start=1):
        for index, line in enumerate(lines):
            text = line.text.strip()
            if not text:
                continue
            if columns:
                repeat = header_columns(line, lines[index + 1] if index + 1 < len(lines) else None)
                if repeat and fold(" ".join(c.label for c in repeat)) in header_keys | {fold(" ".join(c.label for c in columns))}:
                    # The header repeats on this page: take this page's own positions with the owner's roles.
                    page_columns = [Column(c.index, c.label, columns[i].role if i < len(columns) else c.role, c.x0, c.x1) for i, c in enumerate(repeat)]
                    current = _finish(current, rows)
                    in_table = True
                    continue
                if repeat or fold(text) in header_keys:
                    current = _finish(current, rows)
                    in_table = True
                    continue
            money_tokens = [t for t in line.tokens if t.kind == "money"]
            if _OPENING.search(text) and not _CLOSING.search(text):
                current = _finish(current, rows)
                if money_tokens:
                    value = amounts.parse(money_tokens[-1].text)
                    if _BROUGHT.search(text) and value is not None:
                        brought.setdefault(page_number, value)
                    if opening is None:
                        opening = value
                skipped += 1
                continue
            if _CLOSING.search(text):
                current = _finish(current, rows)
                if money_tokens:
                    value = amounts.parse(money_tokens[-1].text)
                    if _CARRIED.search(text) and value is not None:
                        carried[page_number] = value
                    # A page's "carried forward" is the balance so far; the last one, or "closing balance", wins.
                    closing = value
                skipped += 1
                continue
            if _FOOTER.search(text) or _TOTAL.search(text):
                current = _finish(current, rows)
                skipped += 1
                continue
            date_tokens = [t for t in line.tokens if t.kind == "date"]
            starts = line.tokens[0].kind == "date" if line.tokens else False
            if page_columns and not starts and date_tokens:
                date_column = next((c for c in page_columns if c.role == "date"), None)
                starts = date_column is not None and nearest(page_columns, date_tokens[0].x0, date_tokens[0].x1, ("date",)) is date_column and \
                    date_tokens[0].x0 <= min(t.x0 for t in line.tokens) + line.height * 0.5
            if period is None and len(date_tokens) >= 2 and not money_tokens and (_PERIOD.search(text) or not starts):
                period = (date_tokens[0].text, date_tokens[1].text)
                skipped += 1
                continue
            if starts and (money_tokens or in_table):
                current = _finish(current, rows)
                counter += 1
                current = Row(line=counter, page=page_number, raw_date=date_tokens[0].text, top=line.top, bottom=line.bottom)
                _fill(current, line, page_columns, skip_first_date=True)
                continue
            if current is not None and not money_tokens and current.page == page_number and line.top - current.bottom < line.height * 2.5:
                # A wrapped description: the line under a row carries more of it.
                _fill(current, line, page_columns, skip_first_date=False, text_only=True)
                current.bottom = line.bottom
                continue
            if current is not None and money_tokens and not current.money and current.page == page_number:
                _fill(current, line, page_columns, skip_first_date=False)
                current.bottom = line.bottom
                continue
            current = _finish(current, rows)
            if not in_table and not rows:
                preamble.append(text)
            else:
                skipped += 1
    _finish(current, rows)
    if len(rows) > MAX_ROWS:
        raise PdfStatementError("too_many_rows", f"The statement has more than {MAX_ROWS} rows. Import one month at a time.")

    warnings: list[str] = []
    if layout == "columns":
        columns = _content_columns(rows, roles, warnings)
    elif layout == "lines":
        for row in rows:
            _line_money(row)
    else:
        _table_money(rows, columns)

    # Dates: one order for the whole statement.
    raw = [r.raw_date for r in rows] + (list(period) if period else [])
    order, guessed = dates.decide_order(raw)
    chosen = dates.order_of(date_format) if date_format else None
    if date_format and chosen is None:
        raise PdfStatementError("bad_date_format", "Unknown date format. Pick one of the listed formats.")
    if chosen:
        order, guessed = chosen, False
    period_start = dates.to_date(period[0], order, year)[0] if period else None
    period_end = dates.to_date(period[1], order, year)[0] if period else None
    period_year = period_end.year if period_end else year
    for row in rows:
        row.posted_on, hijri = dates.to_date(row.raw_date, order, period_year)
        if hijri:
            row.flags.append("hijri_date")
        if row.posted_on is None:
            row.flags.append("date_unreadable")
        elif period_start and period_end and period_start.month > period_end.month and row.posted_on.month >= period_start.month and \
                dates.split(row.raw_date) is not None and dates.split(row.raw_date).kind == "short":  # type: ignore[union-attr]
            row.posted_on = row.posted_on.replace(year=period_start.year)

    # Newest first: put the rows in time order so balances run forward.
    dated = [r.posted_on for r in rows if r.posted_on]
    if len(dated) >= 2 and dated[0] > dated[-1]:
        rows.reverse()
        for number, row in enumerate(rows, start=1):
            row.line = number

    _signs(rows, opening)
    if layout == "table" and not roles:
        _maybe_swap(rows, opening, warnings)
    check = verify([(r.line, r.amount, r.balance) for r in rows], opening, closing, unsure=any("sign_unsure" in r.flags for r in rows))
    check.page_breaks = _page_breaks(rows, brought, carried)
    statement_currency = _currency_in([line.text for line in every])
    for row in rows:
        if row.currency and row.currency != currency:
            row.flags.append("foreign_currency")
        if row.line in check.breaks:
            row.flags.append("balance_break")
        _confidence(row, layout, guessed)
    if statement_currency and statement_currency != currency:
        warnings.append("currency_differs")
    if not rows:
        warnings.append("no_rows")
    if check.page_breaks:
        warnings.append("page_breaks")
    if any(row.confidence < LOW_CONFIDENCE for row in rows):
        warnings.append("low_confidence")
    fmt = dates.FORMATS[order]
    return Statement(
        columns=columns, layout=layout, rows=rows, date_format=fmt,
        date_formats=[dates.FORMATS["dmy"], dates.FORMATS["mdy"]] if guessed and not chosen else [fmt],
        date_format_ambiguous=guessed and not chosen, period_start=period_start, period_end=period_end,
        account_last4=_last4(preamble + [line.text for line in every[:80]]), currency=statement_currency, opening_balance=opening,
        closing_balance=closing, check=check, fingerprint=print_, confidence=min((r.confidence for r in rows), default=0.0),
        skipped_lines=skipped, warnings=warnings,
    )


def _finish(current: Row | None, rows: list[Row]) -> None:
    if current is not None:
        current.description = mask_numbers(csv_import.clean_text(current.description))
        if current.reference is None:
            found = _REFERENCE.search(current.description)
            if found:
                current.reference = found.group(1)[:60]
        rows.append(current)
    return None


def _fill(row: Row, line: Line, columns: list[Column], *, skip_first_date: bool, text_only: bool = False) -> None:
    """Put a line's tokens into the row: money by column (kept for the money pass), text into the
    description, reference or currency column."""
    first_date = True
    parts: list[str] = []
    row.ocr = min(row.ocr, min((t.conf for t in line.tokens), default=100.0))
    for token in line.tokens:
        if token.kind == "date":
            if skip_first_date and first_date:
                first_date = False
                continue
            if columns and nearest(columns, token.x0, token.x1, ROLES) in [c for c in columns if c.role in ("date", "skip")]:
                continue  # the value date
            parts.append(token.text)
            continue
        if token.kind == "money":
            if not text_only:
                row.money.append(token)
            continue
        if token.kind == "currency":
            row.currency = row.currency or token.text
            continue
        target = nearest(columns, token.x0, token.x1, ("description", "reference", "currency", "skip")) if columns else None
        if target is not None and target.role == "reference":
            row.reference = csv_import.clean_text(token.text, 60)
        elif target is not None and target.role == "currency":
            code = token.text.strip().upper()
            row.currency = code if len(code) == 3 else row.currency
        elif target is not None and target.role == "skip" and columns and _is_far(token, columns):
            continue
        else:
            parts.append(token.text)
    if parts:
        row.description = f"{row.description} {' '.join(parts)}".strip()


def _is_far(token: Token, columns: list[Column]) -> bool:
    description = next((c for c in columns if c.role == "description"), None)
    return description is not None and min(token.x1, description.x1) - max(token.x0, description.x0) <= 0


def _table_money(rows: list[Row], columns: list[Column]) -> None:
    for row in rows:
        for token in row.money:
            column = nearest(columns, token.x0, token.x1, MONEY_ROLES)
            value = amounts.parse(token.text)
            if column is None or value is None:
                continue
            _assign(row, column.role, value, token.text)


def _assign(row: Row, role: str, value: Decimal, token: str) -> None:
    if role == "balance":
        row.balance = value
    elif role == "debit":
        row.debit = abs(value)
        if value < 0 or re.search(r"(?i)cr$|دائن$", token.strip()):
            # A debit column cell marked CR (or negative) is money in.
            row.credit, row.debit = abs(value), None
    elif role == "credit":
        row.credit = abs(value)
    else:
        row.amount, row.explicit_sign = value, amounts.explicit_sign(token)


def _line_money(row: Row) -> None:
    """No positions (a scan read as plain lines): the last amount is the balance when there are two."""
    values = [(t, amounts.parse(t.text)) for t in row.money]
    values = [(t, v) for t, v in values if v is not None]
    if len(values) >= 2:
        token, value = values[-2]
        row.amount, row.explicit_sign = value, amounts.explicit_sign(token.text)
        row.balance = values[-1][1]
    elif values:
        row.amount, row.explicit_sign = values[0][1], amounts.explicit_sign(values[0][0].text)


def _content_columns(rows: list[Row], roles: list[str] | None, warnings: list[str]) -> list[Column]:
    """No header: money columns from the right edges of the amounts, roles from what reconciles."""
    edges = [t.x1 for r in rows for t in r.money]
    if not edges:
        return []
    height = sorted(r.bottom - r.top for r in rows)[len(rows) // 2] or 1.0
    centres = cluster(edges, max(height * 2.0, 4.0))[-3:]
    columns = [Column(i, f"#{i + 1}", "skip", c - height, c) for i, c in enumerate(centres)]
    present = [sum(1 for r in rows if any(_closest(t.x1, centres) == i for t in r.money)) for i in range(len(centres))]
    n = max(len(rows), 1)
    if roles:
        if len(roles) != len(columns) or any(r not in MONEY_ROLES + ("skip",) for r in roles):
            raise PdfStatementError("bad_mapping", "Pick one role for every column.")
        picked = list(roles)
    elif len(centres) >= 3:
        picked = ["debit", "credit", "balance"]
    elif len(centres) == 2:
        both = sum(1 for r in rows if len({_closest(t.x1, centres) for t in r.money}) == 2)
        picked = ["amount", "balance"] if both >= 0.6 * n or present[1] >= 0.9 * n else ["debit", "credit"]
    else:
        picked = ["amount"]
    columns = [Column(c.index, c.label, role, c.x0, c.x1) for c, role in zip(columns, picked, strict=True)]
    for row in rows:
        for token in row.money:
            value = amounts.parse(token.text)
            if value is not None:
                _assign(row, columns[_closest(token.x1, centres)].role, value, token.text)
    if not roles and picked == ["debit", "credit", "balance"]:
        warnings.append("columns_from_content")
    return columns


def _closest(x: float, centres: list[float]) -> int:
    return min(range(len(centres)), key=lambda i: abs(centres[i] - x))


def _signs(rows: list[Row], opening: Decimal | None) -> None:
    """Each row's signed amount: debit and credit; an explicit sign or CR/DR; else the balance's move."""
    previous = opening
    for row in rows:
        if row.debit is not None or row.credit is not None:
            row.amount = (row.credit or Decimal(0)) - (row.debit or Decimal(0))
        elif row.amount is not None and not row.explicit_sign:
            start = previous  # None before the first row without an opening balance: nothing to compare with
            if start is not None and row.balance is not None:
                if start + abs(row.amount) == row.balance:
                    row.amount = abs(row.amount)
                    row.flags.append("sign_from_balance")
                elif start - abs(row.amount) == row.balance:
                    row.amount = -abs(row.amount)
                    row.flags.append("sign_from_balance")
                else:
                    row.amount = -abs(row.amount)
                    row.flags.append("sign_unsure")
            else:
                # No sign and no balance to tell: most statement lines are payments out. The owner checks it.
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


def _maybe_swap(rows: list[Row], opening: Decimal | None, warnings: list[str]) -> None:
    """Debit and credit headers the other way round (or misread): swap when only the swapped reading
    makes the printed balances agree."""
    split = [r for r in rows if r.debit is not None or r.credit is not None]
    if len(split) < 2 or not any(r.balance is not None for r in rows):
        return
    as_read = verify([(r.line, r.amount, r.balance) for r in rows], opening, None)
    swapped = verify([(r.line, -r.amount if (r.debit is not None or r.credit is not None) and r.amount is not None else r.amount, r.balance)
                      for r in rows], opening, None)
    if as_read.breaks and not swapped.breaks:
        for row in split:
            row.debit, row.credit = row.credit, row.debit
            row.amount = -row.amount if row.amount is not None else None
            row.flags.append("columns_swapped")
        warnings.append("debit_credit_swapped")


def _confidence(row: Row, layout: str, guessed: bool) -> None:
    score = min(1.0, row.ocr / 100.0)
    weights = {"sign_unsure": 0.5, "sign_from_balance": 0.95, "balance_break": 0.4, "date_unreadable": 0.3, "amount_missing": 0.2,
               "amount_zero": 0.5, "hijri_date": 0.9, "foreign_currency": 0.6, "columns_swapped": 0.85}
    for flag in row.flags:
        score *= weights.get(flag, 1.0)
    if layout == "columns":
        score *= 0.9
    elif layout == "lines":
        score *= 0.85
    if guessed:
        score *= 0.95
    if not row.description:
        score *= 0.7
    row.confidence = round(max(0.0, min(1.0, score)), 2)
    if row.confidence < LOW_CONFIDENCE and "low_confidence" not in row.flags:
        row.flags.append("low_confidence")


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


def _page_breaks(rows: list[Row], brought: dict[int, Decimal], carried: dict[int, Decimal]) -> list[int]:
    """Pages whose balance brought forward is not the balance the page before ended with."""
    out: list[int] = []
    last_balance: dict[int, Decimal] = {}
    running: Decimal | None = None
    for row in rows:
        if row.balance is not None:
            running = row.balance
        elif running is not None and row.amount is not None:
            running += row.amount
        if running is not None:
            last_balance[row.page] = running
    for page, value in sorted(brought.items()):
        before = carried.get(page - 1, last_balance.get(page - 1))
        if page > 1 and before is not None and before != value:
            out.append(page)
    for page, value in sorted(carried.items()):
        if page in last_balance and last_balance[page] != value and page not in out:
            out.append(page)
    return sorted(out)


def _year_hint(lines: list[Line]) -> int | None:
    for line in lines:
        found = re.search(r"\b(19\d{2}|20\d{2})\b", line.text)
        if found:
            return int(found.group(1))
    return None


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
            return re.sub(r"\D", "", iban.group(0))[-4:]
        found = _ACCOUNT.search(line)
        if found:
            digits = re.sub(r"\D", "", found.group(4) or found.group(7) or "")
            if len(digits) >= 4:
                return digits[-4:]
    return None


__all__ = ["LOW_CONFIDENCE", "Check", "Row", "Statement", "apply_roles", "mask_numbers", "parse", "verify"]
