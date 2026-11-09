"""Apple Wallet (Apple Pay) transaction events and matching them with bank SMS (docs/finance-sms.md#apple-wallet).

Apple Pay and a bank SMS often report the **same** payment. They are two sources of one
canonical transaction (a journal entry), never two transactions. This module is pure: it reads
what the Wallet automation sends, finds the person's account for the card or pass, and decides
whether two reports are the same payment. The service holds the owner's lock while it matches
and writes, so two reports arriving at once cannot both post.

Matching never rests on the amount alone. Two reports are one payment only when all hold:

* the same account (a Wallet card or pass is mapped by the owner to the account its payments
  hit; a bank message names the physical card, which the owner maps to the same account);
* the same direction, amount and currency;
* close in time: within 30 minutes when both carry a time (the Wallet event is stamped with the
  server's reception time), else within 2 days **and** the merchants agree;
* merchants that do not disagree (when both name one, they must share a word), and references
  that do not conflict;
* the existing transaction has no source of the same kind yet (a second Wallet event is a
  second purchase, not a copy).

One candidate attaches; more than one goes to the review queue.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from .money import MoneyError, minor_digits, quantize
from .sms_parser import CURRENCY_WORDS, normalize

PRECISE_WINDOW = timedelta(minutes=30)
LOOSE_DAYS = 2
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_SYMBOLS = {word.upper(): code for word, code in CURRENCY_WORDS}
_STOP = frozenset({"the", "and", "llc", "co", "ltd", "store", "shop", "www", "com", "ksa", "uae", "sa", "ae", "pay", "apple"})


class WalletError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WalletAmount:
    amount: Decimal  # positive
    currency: str | None  # None when the text named none
    refund: bool  # a negative amount: money back


def parse_amount(raw: object) -> WalletAmount:
    """``"SAR 125.00"``, ``"125,00 ر.س"``, ``"$1,234.56"``, ``"1.234,56 €"``, ``"-20"``, Arabic-Indic digits, or a number."""
    if isinstance(raw, bool) or raw is None:
        raise WalletError("missing_amount", "The amount is missing. Map Shortcut Input, Amount to the amount field.")
    if isinstance(raw, float):
        raw = repr(raw)
    text = unicodedata.normalize("NFKC", str(raw)).translate(_ARABIC_DIGITS).strip()
    if not text or len(text) > 40:
        raise WalletError("missing_amount", "The amount is missing or too long.")
    currency = None
    for word, code in sorted(CURRENCY_WORDS, key=lambda item: -len(item[0])):
        if word.upper() in text.upper():
            currency = code
            text = re.sub(re.escape(word), " ", text, flags=re.IGNORECASE)
            break
    negative = "-" in text or "−" in text or (text.startswith("(") and text.endswith(")"))
    digits = re.sub(r"[^\d.,]", "", text)
    if not re.search(r"\d", digits):
        raise WalletError("missing_amount", "The amount could not be read.")
    if "," in digits and "." in digits:
        decimal_mark = "," if digits.rfind(",") > digits.rfind(".") else "."
    elif "," in digits or "." in digits:
        decimal_mark = _single_mark(digits, "," if "," in digits else ".", currency)
    else:
        decimal_mark = ""
    thousands = {",", "."} - {decimal_mark}
    for mark in thousands:
        digits = digits.replace(mark, "")
    if decimal_mark:
        digits = digits.replace(decimal_mark, ".")
    try:
        value = Decimal(digits)
    except InvalidOperation as exc:
        raise WalletError("missing_amount", "The amount could not be read.") from exc
    if value <= 0:
        raise WalletError("missing_amount", "The amount must be more than zero.")
    return WalletAmount(amount=value, currency=currency, refund=negative)


def _single_mark(digits: str, mark: str, currency: str | None) -> str:
    """The decimal mark when only one kind of separator is written, or "" when it groups thousands.

    One or two digits after it are decimals. Three digits are decimals only for a currency with three
    (KWD 1.250), thousands for the others (SAR 1.250 is one thousand two hundred and fifty), and a guess
    without a currency: that goes to review instead. Repeated separators with three-digit groups are
    thousands ("1,234,567")."""
    groups = digits.split(mark)
    if len(groups) > 2:
        if all(len(g) == 3 for g in groups[1:]) and groups[0]:
            return ""
        raise WalletError("ambiguous_amount", "The amount could not be read for certain. Check it in Review.")
    places = len(groups[1])
    if places in (1, 2):
        return mark
    if places == 3:
        if currency is None:
            raise WalletError("ambiguous_amount", "The amount could be decimals or thousands. Check it in Review.")
        return mark if minor_digits(currency) == 3 else ""
    raise WalletError("ambiguous_amount", "The amount could not be read for certain. Check it in Review.")


def rounded(amount: Decimal, currency: str) -> Decimal:
    try:
        return quantize(amount, currency)
    except MoneyError as exc:
        raise WalletError("missing_amount", str(exc)) from exc


def card_key(value: str) -> str:
    """A Wallet card or pass name as a lookup key: lower case words, single spaces."""
    return " ".join(normalize(value).lower().split())[:80]


def find_card(card: str, mappings: list[tuple[str, str | None, str]]) -> str | None:
    """The account id for a Wallet card or pass. ``mappings`` are (card key, virtual last four, account id)."""
    key = card_key(card)
    if not key:
        return None
    exact = [account for name, _, account in mappings if name and name == key]
    if len(set(exact)) == 1:
        return exact[0]
    digits = set(re.findall(r"(?<!\d)\d{4}(?!\d)", key))
    by_digits = [account for _, last4, account in mappings if last4 and last4 in digits]
    if len(set(by_digits)) == 1:
        return by_digits[0]
    return None


def merchant_words(value: str | None) -> set[str]:
    if not value:
        return set()
    words = re.findall(r"[^\W_]+", normalize(value).lower())
    return {w for w in words if len(w) >= 3 and w not in _STOP and not w.isdigit()}


def merchants_agree(a: str | None, b: str | None) -> bool | None:
    """True when both name a merchant and share a word, False when both name one and share none,
    None when either names none."""
    left, right = merchant_words(a), merchant_words(b)
    if not left or not right:
        return None
    return bool(left & right)


@dataclass(frozen=True)
class Report:
    """One side of a possible match: what a source says, already resolved to an account."""

    source: str  # "sms" or "wallet"
    when: datetime
    precise: bool  # ``when`` carries a real time (not only a day)
    merchant: str | None
    reference: str | None


@dataclass(frozen=True)
class Candidate:
    entry_id: str
    when: datetime | None  # the entry's occurred_at, else None
    day: datetime  # the entry's posted day at noon
    merchant: str | None
    reference: str | None
    sources: frozenset[str]  # "sms", "wallet"


def same_payment(report: Report, candidate: Candidate) -> str:
    """``"match"``, ``"weak"`` (same account, amount and day, but nothing else agrees: the person decides)
    or ``"no"``."""
    if report.source == "wallet" and "wallet" in candidate.sources:
        return "no"  # a second Wallet event is a second purchase
    if report.source in candidate.sources and report.source == "statement":
        return "no"  # statement rows are deduplicated by the import itself
    if report.reference and candidate.reference and report.reference != candidate.reference:
        return "no"
    statement = report.source == "statement" or bool(candidate.sources & {"statement", "manual"})
    merchants = merchants_agree(report.merchant, candidate.merchant)
    if merchants is False and not statement:
        return "no"  # Apple Pay and SMS naming different merchants are two purchases
    if report.precise and candidate.when is not None:
        return "match" if abs(candidate.when - report.when) <= PRECISE_WINDOW and merchants is not False else "no"
    if abs((candidate.day.date() - report.when.date()).days) > LOOSE_DAYS:
        return "no"
    if merchants is True or (report.reference and report.reference == candidate.reference):
        return "match"
    if report.source in candidate.sources and merchants is not False:
        return "match"  # the same bank sent the same message twice
    return "weak"


def choose(report: Report, candidates: list[Candidate]) -> tuple[str, Candidate | None]:
    """``("new", None)``, ``("attach", candidate)`` or ``("ambiguous", None)``: more than one match, or
    only weak candidates (same account, amount and dates but no merchant or reference agreement)."""
    verdicts = [(same_payment(report, c), c) for c in candidates]
    found = [c for v, c in verdicts if v == "match"]
    if len(found) == 1:
        return "attach", found[0]
    if found or any(v == "weak" for v, _ in verdicts):
        return "ambiguous", None
    return "new", None


__all__ = [
    "Candidate",
    "Report",
    "WalletAmount",
    "WalletError",
    "card_key",
    "choose",
    "find_card",
    "merchant_words",
    "merchants_agree",
    "parse_amount",
    "rounded",
    "same_payment",
]
