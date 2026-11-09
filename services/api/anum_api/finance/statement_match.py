"""The statement matching engine (docs/finance.md#statement-matching).

One engine for every statement format (CSV and PDF), run when the preview is built and again, under the
owner's lock, when the rows are imported. It compares each statement row with what the person's ledger
already holds on that account, from any source (an earlier statement, an iPhone SMS, Apple Pay, a hand
entry), and gives every row one status:

* ``exact_duplicate``: the same transaction is already recorded: the same statement row was imported or
  confirmed before (same dedupe hash), is waiting in Review, or another statement already recorded the same
  day, amount and merchant. Left out of the import by default.
* ``matched_existing``: an SMS, Apple Pay or hand-entered record of the same purchase (same direction and
  amount, or the same original foreign amount; the statement posted 0 to 3 days later; merchants or
  references agree). No second transaction: the statement row is attached to it as confirming evidence.
  When the bank billed a different amount (the FX settled differently) the record is corrected through
  the normal audited path, and the preview says so first.
* ``possible_duplicate``: a weaker match (same amount and dates, but merchants do not agree or say nothing,
  or two different records fit). Imported only if the person ticks it; otherwise held for Review.
* ``new``: nothing recorded fits.

Rules that keep it honest: deterministic (rows in date then line order, candidates in a fixed order),
explainable (every match has a reason code and the record it matched), one to one (a ledger record absorbs
at most one statement row) and two identical purchases on one day stay two rows.

The same pass looks for **bill payments** (a saved bill's payee in the row, the amount within the bill's
tolerance, the date in the due window) and **own transfers** (money out of one of the person's accounts and
the same money into another of them within 0 to 5 days, with a transfer word or the other account's name
or last four digits in the text). Strong ones are recorded on import; weak ones wait for the person.

Pure functions here; ``gather`` reads what they need in the caller's unit of work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import fx_text
from .wallet import merchant_words as _wallet_words

# Bank descriptor noise removed before words are compared: card-network and channel prefixes, card numbers,
# dates and times, and the country or city suffixes banks add ("POS VIS 1234 CARREFOUR RIYADH SA 03/11").
_NOISE_WORDS = frozenset({
    "pos", "vis", "visa", "deb", "debit", "mc", "mastercard", "mada", "atm", "purchase", "purch", "txn", "trx", "card", "ecom",
    "online", "intl", "international", "ref", "riyadh", "jeddah", "dubai", "abudhabi", "sharjah", "dammam", "khobar", "makkah",
    "madinah", "uae", "ksa", "sau", "are", "usa", "gbr", "irl", "nld", "lux", "sgp",
})
_NOISE = re.compile(r"\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{1,2}:\d{2}(?::\d{2})?|[x*#]{2,}\d{2,4}|\d{4,}")


def normalize_descriptor(text: str | None) -> str:
    """A bank descriptor without card numbers, dates, times and masked digits: what the words are read from."""
    return _NOISE.sub(" ", fx_text.fold(text or ""))


def merchant_words(text: str | None) -> set[str]:
    return {w for w in _wallet_words(normalize_descriptor(text)) if w not in _NOISE_WORDS and not any(c.isdigit() for c in w)}


def merchants_agree(a: str | None, b: str | None) -> bool | None:
    """True when both name a merchant and share a word, False when both do and share none, None otherwise."""
    left, right = merchant_words(a), merchant_words(b)
    if not left or not right:
        return None
    return bool(left & right)

LAG_DAYS = 3  # a bank posts a card purchase 0 to 3 days after the SMS or Apple Pay notice
TRANSFER_DAYS = 5  # the money in arrives 0 to 5 days after the money out
BILL_EARLY_DAYS = 7  # a bill paid up to a week before its due date
BILL_LATE_DAYS = 10  # or up to ten days after it
BILL_TOLERANCE = Decimal("0.10")  # within 10 % of the bill's expected amount
FX_SPREAD = Decimal("0.10")  # the same original amount billed within 10 % of what was recorded
PRECISE_MINUTES = 30

Status = str  # "exact_duplicate" | "matched_existing" | "possible_duplicate" | "new"

_TRANSFER_WORDS = re.compile(
    r"\b(?:transfer|trf|tfr|xfer|own account|own acc|card payment|cc payment|credit card payment|payment to card|"
    r"card repayment|repayment|payment received|payment thank you|thank you for your payment|ipp|aani|sarie|standing order)\b|"
    r"تحويل|سداد|دفعة البطاقة|سداد البطاقة",
    re.IGNORECASE,
)
_GENERIC_NAME_WORDS = frozenset({"account", "current", "savings", "saving", "card", "credit", "debit", "bank", "main", "salary", "wallet"})


# -- inputs ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One statement row, already read: the signed amount the bank billed, in the account's currency."""

    key: int  # the row's line in the file (CSV) or its position (PDF)
    posted_on: date
    amount: Decimal
    description: str
    dedupe_hash: str
    reference: str | None = None
    original_amount: Decimal | None = None  # positive, the foreign amount the row prints
    original_currency: str | None = None
    at: datetime | None = None  # the time a PDF row prints


@dataclass(frozen=True)
class Recorded:
    """One record already in the ledger, on one account (a live journal entry, or a row without one)."""

    record_id: str
    entry_id: str | None
    canonical_id: str | None
    transaction_id: str | None
    account_id: str
    posted_on: date
    amount: Decimal  # signed, on this account
    currency: str
    description: str
    category: str | None
    sources: frozenset[str]  # "sms", "wallet", "manual", "statement"
    primary: str  # the source that recorded it first
    reference: str | None = None
    last4: str | None = None
    original_amount: Decimal | None = None
    original_currency: str | None = None
    occurred_at: datetime | None = None
    statement_hashes: frozenset[str] = frozenset()  # statement rows it already absorbed
    reconciled: bool = False  # a statement already confirmed it
    plain: bool = False  # a statement or hand-entered record: corrected by reposting it
    transfer_paired: bool = False


@dataclass(frozen=True)
class Waiting:
    """A statement row from an earlier import that waits in Review."""

    message_id: str
    dedupe_hash: str


@dataclass(frozen=True)
class AccountInfo:
    id: str
    name: str
    kind: str
    currency: str
    last4: str | None = None


@dataclass(frozen=True)
class BillDue:
    bill_id: str
    name: str
    amount: Decimal  # expected, positive
    currency: str
    due_on: date
    account_id: str | None


# -- outputs ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BillHit:
    bill: BillDue
    strength: str  # "strong": recorded as the bill's payment; "weak": waits for the person under Bills
    reasons: tuple[str, ...]  # "payee", "amount", "due_window"


@dataclass(frozen=True)
class TransferHit:
    other: Recorded  # the other side, on another of the person's accounts
    other_account: AccountInfo
    direction: str  # "to": this row is the money out; "from": this row is the money in
    strength: str  # "strong" or "weak"
    reasons: tuple[str, ...]  # "transfer_words", "account_named", "same_amount", "fx_amount"
    lag_days: int


@dataclass
class Match:
    line: Line
    status: Status
    code: str
    record: Recorded | None = None
    candidates: list[Recorded] = field(default_factory=list)
    lag_days: int | None = None
    amount_difference: Decimal | None = None  # statement billed minus recorded (matched FX rows)
    waiting: Waiting | None = None
    bill: BillHit | None = None
    transfer: TransferHit | None = None

    @property
    def default_import(self) -> bool:
        """Whether the row adds a new transaction unless the person says otherwise."""
        return self.status == "new"


# -- helpers ---------------------------------------------------------------------------------------------


def _same_sign(a: Decimal, b: Decimal) -> bool:
    return (a > 0) == (b > 0)


def _overlap(a: str | None, b: str | None) -> int:
    return len(merchant_words(a) & merchant_words(b))


def _fx_same(line: Line, record: Recorded) -> bool:
    """The same original foreign amount, billed within ``FX_SPREAD`` of what was recorded."""
    if line.original_amount is None or record.original_amount is None or not line.original_currency:
        return False
    if line.original_currency != record.original_currency or abs(line.original_amount) != abs(record.original_amount):
        return False
    if not _same_sign(line.amount, record.amount) or record.amount == 0:
        return False
    return abs(abs(line.amount) - abs(record.amount)) <= abs(record.amount) * FX_SPREAD


def _interchangeable(a: Recorded, b: Recorded) -> bool:
    """Two records nobody could tell apart for this row: same day, amount and merchant words."""
    return a.posted_on == b.posted_on and a.amount == b.amount and merchant_words(a.description) == merchant_words(b.description)


def _order(record: Recorded) -> tuple[Any, ...]:
    """Day, then untimed before timed, then the instant (aware times compared as UTC timestamps), then the id.
    Never a datetime against a placeholder: SMS times are timezone aware, statement and hand entries have none."""
    when = record.occurred_at
    instant = when.timestamp() if when is not None else 0.0
    return (record.posted_on, when is not None, instant, record.record_id)


# -- the row pass ------------------------------------------------------------------------------------------


def match_lines(
    lines: list[Line],
    recorded: list[Recorded],
    *,
    waiting: list[Waiting] | None = None,
    bills: list[BillDue] | None = None,
    account: AccountInfo | None = None,
    others: list[Recorded] | None = None,
    accounts: list[AccountInfo] | None = None,
) -> list[Match]:
    """Every row's status, in the rows' own order. ``recorded`` are the account's records; ``others`` the
    records on the person's other accounts (for transfers)."""
    by_hash: dict[str, Recorded] = {}
    for record in sorted(recorded, key=_order):
        for digest in record.statement_hashes:
            by_hash.setdefault(digest, record)
    waiting_by_hash = {w.dedupe_hash: w for w in waiting or []}
    taken: set[str] = set()
    results: dict[int, Match] = {}
    # 1. The same statement row seen before: by its hash.
    for line in lines:
        record = by_hash.get(line.dedupe_hash)
        if record is not None and record.record_id not in taken:
            taken.add(record.record_id)
            code = "imported_before" if record.primary == "statement" and not record.sources - {"statement"} else "reconciled_before"
            results[line.key] = Match(line, "exact_duplicate", code, record=record)
        elif line.dedupe_hash in waiting_by_hash:
            results[line.key] = Match(line, "exact_duplicate", "waiting_review", waiting=waiting_by_hash[line.dedupe_hash])
    # 2. Everything else, oldest first, against records no row has absorbed yet.
    pool = sorted(recorded, key=_order)
    for line in sorted((l for l in lines if l.key not in results), key=lambda l: (l.posted_on, l.key)):
        results[line.key] = _match_one(line, [r for r in pool if r.record_id not in taken], taken)
    ordered = [results[line.key] for line in lines]
    if bills:
        _bills(ordered, bills, account)
    if others is not None and account is not None:
        _row_transfers(ordered, account, others, accounts or [])
    return ordered


def _match_one(line: Line, pool: list[Recorded], taken: set[str]) -> Match:
    strong: list[tuple[tuple[Any, ...], Recorded]] = []
    weak: list[Recorded] = []
    for record in pool:
        if record.currency and not _same_sign(line.amount, record.amount):
            continue
        lag = (line.posted_on - record.posted_on).days
        if "statement" in record.sources or record.reconciled:
            # Already a statement row (or confirmed by one): only the very same row from another statement matches.
            if lag == 0 and record.amount == line.amount and merchants_agree(line.description, record.description) is True:
                taken.add(record.record_id)
                return Match(line, "exact_duplicate", "same_statement_row", record=record, lag_days=0)
            continue
        if not 0 <= lag <= LAG_DAYS:
            continue
        same_amount = record.amount == line.amount
        fx_same = _fx_same(line, record)
        if not (same_amount or fx_same):
            continue
        if line.reference and record.reference and line.reference != record.reference:
            continue  # two different bank references are two transactions
        merchants = merchants_agree(line.description, record.description)
        same_reference = bool(line.reference and line.reference == record.reference)
        # Stage 1: the bank's own reference agrees. Stage 2: amount, date window and merchant agree (or the same
        # original foreign amount with merchants that do not disagree). Anything weaker waits for the person.
        if same_reference or merchants is True or (fx_same and merchants is not False):
            rank = (lag, -_overlap(line.description, record.description), *_order(record))
            strong.append((rank, record))
        elif same_amount:
            weak.append(record)
    if strong:
        strong.sort(key=lambda item: item[0])
        best = strong[0][1]
        rivals = [r for _, r in strong[1:] if not _interchangeable(best, r)]
        if rivals and line.at is not None:
            # The time the row prints narrows it down; it never turns a match into a new transaction.
            near = [r for _, r in strong if r.occurred_at is not None and abs(r.occurred_at - line.at) <= timedelta(minutes=PRECISE_MINUTES)]
            if len(near) == 1:
                best, rivals = near[0], []
        if rivals:
            return Match(line, "possible_duplicate", "two_candidates", candidates=[best, *rivals][:5])
        taken.add(best.record_id)
        difference = line.amount - best.amount if line.amount != best.amount else None
        code = "same_original_amount" if difference is not None else ("same_reference" if line.reference and line.reference == best.reference else "same_amount_merchant")
        return Match(line, "matched_existing", code, record=best, lag_days=(line.posted_on - best.posted_on).days, amount_difference=difference)
    if weak:
        weak.sort(key=_order)
        return Match(line, "possible_duplicate", "two_candidates" if len(weak) > 1 else "amount_date_only", candidates=weak[:5],
                     lag_days=(line.posted_on - weak[0].posted_on).days)
    return Match(line, "new", "no_match")


# -- bills -----------------------------------------------------------------------------------------------


def bill_hit(amount: Decimal, posted_on: date, description: str, currency: str, account_id: str | None, bill: BillDue) -> BillHit | None:
    """Does a payment of ``amount`` on ``posted_on`` pay this due date of the bill? Money out only."""
    if amount >= 0 or bill.currency != currency or (bill.account_id and account_id and bill.account_id != account_id):
        return None
    payee = merchants_agree(bill.name, description) is True
    close = abs(abs(amount) - bill.amount) <= bill.amount * BILL_TOLERANCE
    offset = (posted_on - bill.due_on).days
    in_window = -BILL_EARLY_DAYS <= offset <= BILL_LATE_DAYS
    reasons = tuple(name for name, ok in (("payee", payee), ("amount", close), ("due_window", in_window)) if ok)
    if payee and close and in_window:
        return BillHit(bill, "strong", reasons)
    if payee and (close or in_window) and -30 <= offset <= 30:
        return BillHit(bill, "weak", reasons)
    return None


def _bills(matches: list[Match], bills: list[BillDue], account: AccountInfo | None) -> None:
    """One due date absorbs at most one row; strong hits are given out first, then weak ones."""
    used: set[tuple[str, date]] = set()
    for wanted in ("strong", "weak"):
        for match in matches:
            if match.bill is not None or match.status == "exact_duplicate":
                continue
            hits = []
            for bill in bills:
                hit = bill_hit(match.line.amount, match.line.posted_on, match.line.description, account.currency if account else bill.currency,
                               account.id if account else None, bill)
                if hit is not None and hit.strength == wanted and (bill.bill_id, bill.due_on) not in used:
                    hits.append(hit)
            if hits:
                hits.sort(key=lambda h: (abs((match.line.posted_on - h.bill.due_on).days), h.bill.name.lower(), h.bill.bill_id))
                match.bill = hits[0]
                used.add((hits[0].bill.bill_id, hits[0].bill.due_on))


# -- transfers -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Side:
    """One side of a possible transfer: a statement row or a record, on one account."""

    key: str
    account_id: str
    posted_on: date
    amount: Decimal
    currency: str
    description: str
    original_amount: Decimal | None = None
    original_currency: str | None = None


def side_of(record: Recorded) -> Side:
    return Side(record.record_id, record.account_id, record.posted_on, record.amount, record.currency, record.description,
                record.original_amount, record.original_currency)


def _named(text: str, account: AccountInfo) -> bool:
    """The other account's last four digits or its own name appear in the text."""
    folded = fx_text.fold(text)
    if account.last4 and re.search(rf"(?<!\d){re.escape(account.last4)}(?!\d)", folded):
        return True
    words = {w for w in merchant_words(account.name) if w not in _GENERIC_NAME_WORDS and len(w) >= 4}
    return bool(words and words <= merchant_words(text))


def transfer_amounts_agree(out: Side, into: Side) -> str | None:
    """``same_amount``, ``fx_amount`` (the other currency's amount is stated on one side) or None."""
    if out.amount >= 0 or into.amount <= 0:
        return None
    if out.currency == into.currency:
        return "same_amount" if -out.amount == into.amount else None
    if out.original_currency == into.currency and out.original_amount is not None and abs(out.original_amount) == into.amount:
        return "fx_amount"
    if into.original_currency == out.currency and into.original_amount is not None and abs(into.original_amount) == -out.amount:
        return "fx_amount"
    return None


def transfer_pair(out: Side, into: Side, out_account: AccountInfo, in_account: AccountInfo) -> tuple[str, tuple[str, ...], int] | None:
    """(strength, reasons, lag) when ``out`` and ``into`` look like one transfer between two of the person's accounts."""
    if out.account_id == into.account_id:
        return None
    amounts = transfer_amounts_agree(out, into)
    if amounts is None:
        return None
    lag = (into.posted_on - out.posted_on).days
    if not 0 <= lag <= TRANSFER_DAYS:
        return None
    reasons = [amounts]
    if _TRANSFER_WORDS.search(fx_text.fold(out.description)) or _TRANSFER_WORDS.search(fx_text.fold(into.description)):
        reasons.append("transfer_words")
    if _named(out.description, in_account) or _named(into.description, out_account):
        reasons.append("account_named")
    strength = "strong" if len(reasons) > 1 else "weak"
    return strength, tuple(reasons), lag


def _row_transfers(matches: list[Match], account: AccountInfo, others: list[Recorded], accounts: list[AccountInfo]) -> None:
    by_id = {a.id: a for a in accounts}
    used: set[str] = set()
    for wanted in ("strong", "weak"):
        for match in matches:
            if match.transfer is not None or match.status in ("exact_duplicate",):
                continue
            if match.record is not None and match.record.transfer_paired:
                continue
            line = match.line
            mine = Side(f"line:{line.key}", account.id, line.posted_on, line.amount, account.currency, line.description,
                        line.original_amount, line.original_currency)
            found: list[tuple[tuple[Any, ...], TransferHit]] = []
            for other in others:
                if other.record_id in used or other.transfer_paired or other.account_id == account.id:
                    continue
                other_account = by_id.get(other.account_id)
                if other_account is None:
                    continue
                theirs = side_of(other)
                out, into = (mine, theirs) if line.amount < 0 else (theirs, mine)
                out_acc, in_acc = (account, other_account) if line.amount < 0 else (other_account, account)
                pair = transfer_pair(out, into, out_acc, in_acc)
                if pair is None or pair[0] != wanted:
                    continue
                strength, reasons, lag = pair
                hit = TransferHit(other, other_account, "to" if line.amount < 0 else "from", strength, reasons, lag)
                found.append(((lag, -len(reasons), *_order(other)), hit))
            if found:
                found.sort(key=lambda item: item[0])
                match.transfer = found[0][1]
                used.add(found[0][1].other.record_id)


@dataclass(frozen=True)
class Pair:
    out: Recorded
    into: Recorded
    strength: str
    reasons: tuple[str, ...]
    lag_days: int


def find_transfers(records: list[Recorded], accounts: list[AccountInfo]) -> list[Pair]:
    """Own transfers among records already in the ledger: one to one, strong pairs given out first."""
    by_id = {a.id: a for a in accounts}
    live = [r for r in records if not r.transfer_paired and r.account_id in by_id]
    outs = sorted((r for r in live if r.amount < 0), key=_order)
    ins = sorted((r for r in live if r.amount > 0), key=_order)
    used: set[str] = set()
    pairs: list[Pair] = []
    for wanted in ("strong", "weak"):
        for out in outs:
            if out.record_id in used:
                continue
            found = []
            for into in ins:
                if into.record_id in used:
                    continue
                pair = transfer_pair(side_of(out), side_of(into), by_id[out.account_id], by_id[into.account_id])
                if pair is None or pair[0] != wanted:
                    continue
                found.append(((pair[2], -len(pair[1]), *_order(into)), into, pair))
            if found:
                found.sort(key=lambda item: item[0])
                _, into, (strength, reasons, lag) = found[0]
                used.update({out.record_id, into.record_id})
                pairs.append(Pair(out, into, strength, reasons, lag))
    return sorted(pairs, key=lambda p: (p.out.posted_on, p.out.record_id))


# -- explanations ----------------------------------------------------------------------------------------

_SOURCE_NAMES = {"sms": "SMS", "wallet": "Apple Pay", "manual": "an entry you made", "statement": "a statement"}


def source_label(record: Recorded) -> str:
    name = _SOURCE_NAMES.get(record.primary, record.primary)
    return f"{name} from card ending {record.last4}" if record.last4 and record.primary in ("sms", "wallet") else name


def explain(match: Match) -> str:
    """The plain English reason for a row's status (the web shows its own translation of the code)."""
    record = match.record
    if match.code == "imported_before" and record is not None:
        return f"Already recorded: imported from a statement on {record.posted_on.isoformat()}."
    if match.code == "reconciled_before" and record is not None:
        return f"Already recorded: a statement confirmed {source_label(record)} on {record.posted_on.isoformat()}."
    if match.code == "waiting_review":
        return "Already waiting in Finance, iPhone SMS, Review from an earlier import."
    if match.code == "same_statement_row" and record is not None:
        return f"Already recorded: the same day, amount and merchant from {source_label(record)}."
    if match.status == "matched_existing" and record is not None:
        lag = match.lag_days or 0
        when = "the same day" if lag == 0 else f"{lag} day{'s' if lag != 1 else ''} later"
        if match.code == "same_original_amount":
            return (f"Same {match.line.original_currency} {match.line.original_amount} purchase as {source_label(record)}, posted {when}; "
                    f"the bank billed {abs(match.line.amount)} instead of {abs(record.amount)}.")
        what = "same reference" if match.code == "same_reference" else "same amount and merchant"
        return f"{what.capitalize()}, posted {when}: {source_label(record)}."
    if match.code == "two_candidates":
        return "More than one recorded payment fits. Tick it only if it is a separate payment."
    if match.code == "amount_date_only":
        other = match.candidates[0] if match.candidates else None
        return (f"Same amount and date as {source_label(other)}, but the merchant does not match. Tick it only if it is a separate payment."
                if other else "Same amount and date as a recorded payment.")
    return "Nothing recorded matches this row."


__all__ = [
    "AccountInfo",
    "BillDue",
    "BillHit",
    "Line",
    "Match",
    "Pair",
    "Recorded",
    "Side",
    "TransferHit",
    "Waiting",
    "bill_hit",
    "explain",
    "find_transfers",
    "match_lines",
    "source_label",
    "transfer_pair",
]
