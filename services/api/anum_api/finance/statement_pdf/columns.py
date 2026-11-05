"""Layer 2: lines, tokens and columns (docs/finance-statements.md#columns).

* Words are grouped into lines by their vertical middle and, inside a line, into tokens: a date
  (one to three words, ``05 Nov 2026``), an amount (with a following ``CR``/``DR``), a currency code,
  or text.
* A header line is found by its words (English and Arabic synonyms for date, description, debit,
  credit, amount, balance, reference and currency), also when it wraps over two lines. Each header
  cell is a column band; a token belongs to the band it overlaps most (numbers are usually right
  aligned, labels left aligned or centred, so edges and centres are both compared).
* No header at all: columns come from the content. The right edges of every row's amounts are
  clustered into money columns, and the engine decides which is the balance, the debit and the
  credit by testing which reading makes the running balance agree.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from statistics import median

from . import amounts, dates
from .words import Word, is_arabic, reorder_rtl

ROLES = ("date", "description", "debit", "credit", "amount", "balance", "reference", "currency", "foreign_amount", "fx_rate", "fee", "vat",
         "skip")
MONEY_ROLES = ("debit", "credit", "amount", "balance")
# Extra figures some statements (credit cards) print on each row: kept on the row, never the posted amount.
DETAIL_ROLES = ("foreign_amount", "fx_rate", "fee", "vat")
CURRENCIES = ("AED", "SAR", "USD", "EUR", "GBP", "KWD", "QAR", "BHD", "OMR", "INR", "PKR", "EGP", "JOD")
_ARABIC_CURRENCY = {"د.إ": "AED", "درهم": "AED", "ر.س": "SAR", "ريال": "SAR"}
MAX_COLUMNS = 12

# Checked in this order: "Debit Amount" is a debit, "Transaction Date" a date, "Balance" never an amount.
_ROLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fx_rate", ("conversion rate", "exchange rate", "fx rate", "rate", "سعر التحويل", "سعر تحويل", "سعر الصرف")),
    ("foreign_amount", ("international transaction amount", "foreign amount", "original amount", "transaction currency amount",
                        "مبلغ العملية الدولية", "المبلغ الأصلي", "المبلغ بالعملة الأجنبية")),
    ("vat", ("vat", "vat amount", "tax", "ضريبة", "قيمة الضريبة", "الضريبة")),
    ("fee", ("fees", "other fees", "charges", "fee", "الرسوم", "رسوم")),
    ("balance", ("balance", "running balance", "available balance", "book balance", "الرصيد", "رصيد")),
    ("debit", ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "dr", "مدين", "سحب", "مسحوبات", "المدين", "خصم",
               "الخصم", "سحوبات", "المسحوبات")),
    ("credit", ("credit", "credits", "deposit", "deposits", "money in", "paid in", "cr", "دائن", "إيداع", "ايداع", "إيداعات", "ايداعات",
                "الدائن", "الإيداعات", "الايداعات")),
    ("date", ("date", "transaction date", "posting date", "posted date", "txn date", "value date", "booking date", "trans date", "التاريخ",
              "تاريخ", "تاريخ العملية", "تاريخ القيد", "تاريخ المعاملة")),
    ("currency", ("currency", "ccy", "curr", "العملة", "عملة")),
    ("reference", ("reference", "ref", "ref no", "reference no", "reference number", "cheque", "cheque no", "chq", "chq no", "transaction id",
                   "txn id", "المرجع", "رقم المرجع", "رقم الشيك", "الرقم المرجعي")),
    ("amount", ("amount", "transaction amount", "المبلغ", "مبلغ", "قيمة العملية")),
    ("description", ("description", "details", "transaction details", "narration", "narrative", "particulars", "remarks", "memo",
                     "transaction", "transaction description", "البيان", "الوصف", "التفاصيل", "تفاصيل", "وصف", "تفاصيل العملية", "بيان")),
)


def fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s/]", " ", text.lower()).split())


def role_of(label: str) -> str | None:
    folded = fold(label)
    for currency in ("aed", "sar", "usd"):
        folded = folded.replace(f" {currency}", "").replace(f"{currency} ", "")
    words = folded.split()
    for role, names in _ROLE_WORDS:
        for name in names:
            if folded == name or (len(name) > 3 and name in folded) or name in words:
                return role
    return None


# -- lines and tokens ---------------------------------------------------------------------------


@dataclass
class Token:
    kind: str  # "date", "money", "currency", "text"
    text: str
    x0: float
    x1: float
    conf: float


@dataclass
class Line:
    page: int
    top: float
    bottom: float
    words: list[Word]
    tokens: list[Token] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.1)


def lines_of(words: list[Word]) -> list[Line]:
    if not words:
        return []
    height = median(w.height for w in words) or 1.0
    ordered = sorted(words, key=lambda w: (w.middle, w.x0))
    groups: list[list[Word]] = []
    for word in ordered:
        if groups and abs(word.middle - sum(w.middle for w in groups[-1]) / len(groups[-1])) <= height * 0.45:
            groups[-1].append(word)
        else:
            groups.append([word])
    lines = []
    for group in groups:
        group.sort(key=lambda w: w.x0)
        lines.append(Line(group[0].page, min(w.top for w in group), max(w.bottom for w in group), group))
    lines.sort(key=lambda line: line.top)
    return lines


def tokenize(line: Line, money: re.Pattern[str]) -> list[Token]:
    """Dates (up to three words), amounts (with a following CR/DR), currency codes and text runs.
    Text words close together form one run; a wide gap starts a new one (a new cell)."""
    words = line.words
    out: list[Token] = []
    i = 0
    gap_limit = line.height * 0.9
    while i < len(words):
        joined = None
        # "45.50" reads as a day and month too: away from the start of a line, an amount wins.
        money_first = i > 0 and amounts.is_money(words[i].text, money)
        for size in (() if money_first else (3, 2, 1)):
            if i + size <= len(words):
                text = " ".join(w.text for w in words[i: i + size])
                if dates.WHOLE_DATE.match(text) and dates.plausible(text):
                    joined = (size, text)
                    break
        if joined:
            size, text = joined
            part = words[i: i + size]
            out.append(Token("date", text, part[0].x0, part[-1].x1, min(w.conf for w in part)))
            i += size
            continue
        word = words[i]
        if amounts.is_money(word.text, money):
            text, x1, conf = word.text, word.x1, word.conf
            if i + 1 < len(words) and re.fullmatch(r"(?i)cr|dr|دائن|مدين", words[i + 1].text) and words[i + 1].x0 - word.x1 < gap_limit:
                text, x1, conf = f"{text} {words[i + 1].text}", words[i + 1].x1, min(conf, words[i + 1].conf)
                i += 1
            out.append(Token("money", text, word.x0, x1, conf))
            i += 1
            continue
        code = word.text.upper().strip(".:")
        if code in CURRENCIES or word.text in _ARABIC_CURRENCY:
            out.append(Token("currency", code if code in CURRENCIES else _ARABIC_CURRENCY[word.text], word.x0, word.x1, word.conf))
            i += 1
            continue
        if out and out[-1].kind == "text" and word.x0 - out[-1].x1 <= gap_limit:
            last = out[-1]
            out[-1] = Token("text", f"{last.text} {word.text}", last.x0, word.x1, min(last.conf, word.conf))
        else:
            out.append(Token("text", word.text, word.x0, word.x1, word.conf))
        i += 1
    for token in out:
        if token.kind == "text" and is_arabic(token.text):
            # Arabic words of one cell were placed right to left: read them in that order.
            token.text = " ".join(reorder_rtl(token.text.split()))
    return out


# -- header and columns -------------------------------------------------------------------------


@dataclass
class Column:
    index: int
    label: str
    role: str
    x0: float
    x1: float

    @property
    def middle(self) -> float:
        return (self.x0 + self.x1) / 2


def header_columns(line: Line, below: Line | None = None) -> list[Column] | None:
    """The columns when ``line`` (optionally with the line under it) is a table header."""
    found = header_block([line] + ([below] if below is not None else []), 0)
    return found[0] if found else None


def header_block(lines: list[Line], index: int, *, depth: int = 6) -> tuple[list[Column], int] | None:
    """The table header starting at ``lines[index]``, and how many lines it takes. A header can be one
    line, or several stacked lines (an Arabic label above its English one, "Transaction" above "Date"):
    labels that overlap horizontally across those lines form one column."""
    first = lines[index]
    if not first.tokens or any(t.kind != "text" for t in first.tokens):
        return None
    single = _header(first.tokens)
    block = [first]
    for line in lines[index + 1: index + depth]:
        if line.top - block[-1].bottom >= max(line.height, block[-1].height) * 1.2 or any(t.kind != "text" for t in line.tokens):
            break
        if not any(role_of(t.text) for t in line.tokens):
            break  # the first row's text right under the header ("Internal Transfer"), not more header
        block.append(line)
    best: tuple[list[Column], int] | None = (single, 1) if single else None
    for size in range(2, len(block) + 1):
        merged = _merge_lines(block[:size])
        found = _header(merged)
        if found and (best is None or _known(found) >= _known(best[0])):
            best = (found, size)
    return best


def _known(columns: list[Column]) -> int:
    return sum(1 for c in columns if c.role != "skip")


def _merge_lines(block: list[Line]) -> list[Token]:
    """Tokens of stacked lines joined into columns where they overlap horizontally."""
    merged: list[Token] = []
    for line in block:
        for token in line.tokens:
            hits = [m for m in merged if min(m.x1, token.x1) - max(m.x0, token.x0) > 0]
            if not hits:
                merged.append(Token(token.kind, token.text, token.x0, token.x1, token.conf))
                continue
            target = hits[0]
            for other in hits[1:]:
                target.text, target.x0, target.x1 = f"{target.text} {other.text}", min(target.x0, other.x0), max(target.x1, other.x1)
                merged.remove(other)
            target.text, target.x0, target.x1 = f"{target.text} {token.text}", min(target.x0, token.x0), max(target.x1, token.x1)
    merged.sort(key=lambda t: t.x0)
    return merged


def _header(cells: list[Token]) -> list[Column] | None:
    if not 3 <= len(cells) <= MAX_COLUMNS:
        return None
    if any(t.kind != "text" for t in cells):
        return None
    roles = [role_of(t.text) for t in cells]
    if "date" not in roles or not any(r in MONEY_ROLES for r in roles):
        return None
    if sum(1 for r in roles if r is None) > len(roles) // 2:
        return None
    columns: list[Column] = []
    seen: set[str] = set()
    for index, (token, role) in enumerate(zip(cells, roles, strict=True)):
        picked = role or "skip"
        if picked in seen and picked != "skip":
            # A second date (value date next to the transaction date) or a repeated role is not needed.
            picked = "skip"
        seen.add(picked)
        columns.append(Column(index, token.text[:60], picked, token.x0, token.x1))
    return columns


def nearest(columns: list[Column], x0: float, x1: float, roles: tuple[str, ...]) -> Column | None:
    best: tuple[float, Column] | None = None
    centre = (x0 + x1) / 2
    for column in columns:
        if column.role not in roles:
            continue
        overlap = min(x1, column.x1) - max(x0, column.x0)
        if overlap > 0:
            score = -overlap
        else:
            score = min(abs(x1 - column.x1), abs(x0 - column.x0), abs(centre - column.middle))
        if best is None or score < best[0]:
            best = (score, column)
    return best[1] if best else None


def cluster(values: list[float], tolerance: float) -> list[float]:
    """Centres of groups of right edges that lie within ``tolerance`` of each other."""
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(g) / len(g) for g in groups]


def fingerprint(columns: list[Column], width: float) -> str:
    """A bank layout's identity: its header words and where its columns sit (5 % steps of the page
    width). The same bank's next statement has the same one; nothing personal goes into it."""
    width = width or 1.0
    parts = [f"{fold(c.label)}@{round(c.middle / width * 20)}" for c in columns]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


__all__ = [
    "CURRENCIES", "MONEY_ROLES", "ROLES", "Column", "Line", "Token", "cluster", "fingerprint", "fold", "header_columns", "lines_of", "nearest",
    "role_of", "tokenize",
]
