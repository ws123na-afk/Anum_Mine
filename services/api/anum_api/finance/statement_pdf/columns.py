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

ROLES = ("date", "description", "debit", "credit", "amount", "balance", "reference", "currency", "skip")
MONEY_ROLES = ("debit", "credit", "amount", "balance")
CURRENCIES = ("AED", "SAR", "USD", "EUR", "GBP", "KWD", "QAR", "BHD", "OMR", "INR", "PKR", "EGP", "JOD")
_ARABIC_CURRENCY = {"د.إ": "AED", "درهم": "AED", "ر.س": "SAR", "ريال": "SAR"}
MAX_COLUMNS = 12

# Checked in this order: "Debit Amount" is a debit, "Transaction Date" a date, "Balance" never an amount.
_ROLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
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
    gap_limit = line.height * 1.2
    while i < len(words):
        joined = None
        # "45.50" reads as a day and month too: away from the start of a line, an amount wins.
        money_first = i > 0 and amounts.is_money(words[i].text, money)
        for size in (() if money_first else (3, 2, 1)):
            if i + size <= len(words):
                text = " ".join(w.text for w in words[i: i + size])
                if dates.WHOLE_DATE.match(text) and dates.split(text) is not None:
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
    """The columns when ``line`` is a table header: a date column, at least one money column, and most
    cells known words. A header wrapped over two lines ("Transaction" over "Date") is read when every
    cell of the first line sits over a cell of the second."""
    found = _header(line.tokens)
    if found is not None or below is None or below.top - line.bottom >= line.height * 0.8:
        return found
    if not line.tokens or not all(any(min(t.x1, b.x1) - max(t.x0, b.x0) > 0 for b in below.tokens) for t in line.tokens):
        return None
    if _header(below.tokens) is not None:
        return None  # the next line is the header on its own
    return _header(_merge_two(line.tokens, below.tokens))


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


def _merge_two(top: list[Token], bottom: list[Token]) -> list[Token]:
    """A header wrapped over two lines ("Transaction" over "Date"): cells that overlap join."""
    merged = [Token(t.kind, t.text, t.x0, t.x1, t.conf) for t in top]
    for token in bottom:
        target = next((m for m in merged if min(m.x1, token.x1) - max(m.x0, token.x0) > 0), None)
        if target is None:
            merged.append(Token(token.kind, token.text, token.x0, token.x1, token.conf))
        else:
            target.text, target.x0, target.x1 = f"{target.text} {token.text}", min(target.x0, token.x0), max(target.x1, token.x1)
    merged.sort(key=lambda t: t.x0)
    return merged


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
