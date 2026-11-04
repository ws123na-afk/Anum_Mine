"""Bank SMS parsing for the iPhone SMS integration (docs/finance-sms.md#parsing).

Pure and deterministic: no clock, no store, no network, no AI. One function, :func:`parse`,
turns a forwarded message (sender plus text) into a :class:`Parsed` result:

* **Refused first.** One-time codes, verification codes, passwords and PINs, and promotions
  are refused before anything else is read (``rejected``); the caller never stores their text.
* **Owner rules next.** Rules the owner taught ANUM from a corrected message (``learned``)
  are tried first for that sender; they are the owner's own verified format.
* **Then the bilingual engine.** Arabic and English keywords decide the kind of transaction;
  regular expressions read the amount and currency, a converted amount, the card or account's
  last four digits, the merchant or other party, a reference, a date and time and the balance.

**No bank format is claimed.** ANUM has no real bank messages to validate against, so the
bank registry below lists only the sender names a message is expected to come from, each
marked ``verified=False``. Every bank shares the same generic engine: there are no invented
per-bank templates. A bank becomes trusted for one person only when they confirm a real
message from it (``verified_senders``) or teach a rule from one. Until then its messages wait
in the review queue.

Amounts are exact ``Decimal`` values, rounded once to the currency's minor unit
(``money.quantize``). Account and card numbers are masked to their last four digits
(:func:`mask`) before any text is kept.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from .money import MoneyError, quantize

MAX_TEXT = 1_000
MAX_SENDER = 60


class Kind(StrEnum):
    """What a message says happened (docs/finance-sms.md#kinds)."""

    SALARY = "salary"
    DEPOSIT = "deposit"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    OWN_TRANSFER = "own_transfer"
    CARD_PURCHASE = "card_purchase"
    ATM_WITHDRAWAL = "atm_withdrawal"
    CARD_REPAYMENT = "card_repayment"
    LOAN_DISBURSEMENT = "loan_disbursement"
    LOAN_REPAYMENT = "loan_repayment"
    BANK_FEE = "bank_fee"
    REFUND = "refund"
    REVERSAL = "reversal"
    UNKNOWN = "unknown"


# Money in for the account the message is about; everything else is money out.
MONEY_IN = frozenset({Kind.SALARY, Kind.DEPOSIT, Kind.TRANSFER_IN, Kind.LOAN_DISBURSEMENT, Kind.REFUND, Kind.REVERSAL})


class Rejection(StrEnum):
    OTP = "otp"  # one-time, verification or activation code
    PASSWORD = "password"  # password, PIN or login details
    PROMOTION = "promotion"  # an offer or advert
    NOT_FINANCIAL = "not_financial"  # no money in it at all


@dataclass(frozen=True)
class Bank:
    key: str
    name: str
    country: str  # SA or AE
    currency: str
    senders: tuple[str, ...]
    # True only once a real message from this bank was checked by its owner in a release.
    # Nothing is verified today: see the module docstring.
    verified: bool = False


# Expected sender names only (unconfirmed). Bank-specific wording is never assumed.
BANKS: tuple[Bank, ...] = (
    Bank("al_rajhi", "Al Rajhi Bank", "SA", "SAR", ("alrajhibank", "alrajhi", "rajhibank")),
    Bank("snb", "Saudi National Bank (SNB)", "SA", "SAR", ("snb", "snbalahli", "alahli", "alahlibank")),
    Bank("sab", "Saudi Awwal Bank (SAB)", "SA", "SAR", ("sab", "sabb")),
    Bank("riyad", "Riyad Bank", "SA", "SAR", ("riyadbank", "riyad")),
    Bank("alinma", "Alinma Bank", "SA", "SAR", ("alinma", "alinmabank")),
    Bank("albilad", "Bank Albilad", "SA", "SAR", ("albilad", "bankalbilad")),
    Bank("anb", "Arab National Bank", "SA", "SAR", ("anb", "arabnationalbank")),
    Bank("bsf", "Banque Saudi Fransi", "SA", "SAR", ("bsf", "alfransi")),
    Bank("enbd", "Emirates NBD", "AE", "AED", ("emiratesnbd", "enbd")),
    Bank("fab", "First Abu Dhabi Bank", "AE", "AED", ("fab", "fabbank")),
    Bank("adcb", "ADCB", "AE", "AED", ("adcb",)),
    Bank("mashreq", "Mashreq", "AE", "AED", ("mashreq", "mashreqbank")),
    Bank("dib", "Dubai Islamic Bank", "AE", "AED", ("dib", "dubaiislamic")),
    Bank("adib", "Abu Dhabi Islamic Bank", "AE", "AED", ("adib",)),
    Bank("cbd", "Commercial Bank of Dubai", "AE", "AED", ("cbd",)),
    Bank("rakbank", "RAKBANK", "AE", "AED", ("rakbank",)),
)
BANK_BY_KEY = {bank.key: bank for bank in BANKS}
_SENDER_INDEX = {sender: bank for bank in BANKS for sender in bank.senders}

# Currency words to ISO codes. Longer spellings first so "SAR" wins over "SR".
CURRENCY_WORDS: tuple[tuple[str, str], ...] = (
    ("ر.س", "SAR"), ("ريال سعودي", "SAR"), ("ريال", "SAR"), ("رس", "SAR"), ("SAR", "SAR"), ("SR", "SAR"),
    ("د.إ", "AED"), ("درهم", "AED"), ("AED", "AED"), ("DHS", "AED"), ("DH", "AED"),
    ("USD", "USD"), ("US$", "USD"), ("$", "USD"), ("دولار", "USD"),
    ("EUR", "EUR"), ("€", "EUR"), ("يورو", "EUR"), ("GBP", "GBP"), ("£", "GBP"),
    ("KWD", "KWD"), ("د.ك", "KWD"), ("QAR", "QAR"), ("BHD", "BHD"), ("OMR", "OMR"), ("EGP", "EGP"),
    ("INR", "INR"), ("PKR", "PKR"), ("JOD", "JOD"), ("TRY", "TRY"), ("CHF", "CHF"), ("JPY", "JPY"),
)
_CURRENCY_ALT = "|".join(re.escape(word) for word, _ in sorted(CURRENCY_WORDS, key=lambda item: -len(item[0])))
_CURRENCY_CODE = {word.upper(): code for word, code in CURRENCY_WORDS}
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# A currency word, then the number; or the number, then the currency word.
_AMOUNT = re.compile(
    rf"(?:(?P<c1>{_CURRENCY_ALT})\s?(?P<n1>{_NUMBER}))|(?:(?<![\d.,])(?P<n2>{_NUMBER})\s?(?P<c2>{_CURRENCY_ALT}))",
    re.IGNORECASE,
)

# Refusals. Checked on the lower-cased, normalized text.
_OTP = re.compile(
    r"\b(?:otp|one[\s-]?time (?:password|passcode|pin|code)|verification code|verify code|activation code|"
    r"security code|passcode|auth(?:entication|orization)? code|confirmation code|"
    r"(?:your|the) code is|code:\s*\d{4,8}\b|do not share)|"
    r"رمز التحقق|رمز التفعيل|رمز الدخول|كلمة المرور لمرة واحدة|الرمز السري|رمز التأكيد|لا تشارك|رمز المصادقة|رمز تحقق",
    re.IGNORECASE,
)
_PASSWORD = re.compile(
    r"\b(?:password|passwd|\bpin\b|login details|user ?name and)\b|كلمة المرور|كلمة السر|الرقم السري",
    re.IGNORECASE,
)
_PROMO = re.compile(
    r"\b(?:offer|promo(?:tion)?|win|winner|discount|cashback offer|apply now|limited time|congratulations|"
    r"pre-?approved|click here|unsubscribe|reply stop)\b|عرض|خصم حتى|اربح|مبروك|تهانينا|سارع|لفترة محدودة",
    re.IGNORECASE,
)
# Words that make a promotion-looking message a real transaction after all.
_TXN_WORDS = re.compile(
    r"\b(?:purchase|debited|credited|withdrawal|withdrawn|deposit(?:ed)?|transfer(?:red)?|payment|paid|refund(?:ed)?|"
    r"reversed|reversal|salary|spent|received|sent|charged|installment|instalment|fee)\b|"
    r"شراء|خصم مبلغ|تم خصم|إيداع|ايداع|سحب|تحويل|حوالة|سداد|تسديد|استرداد|راتب|قسط|رسوم|مدين|دائن",
    re.IGNORECASE,
)

# (kind, pattern). Earlier entries win: the specific ones come first.
_KIND_RULES: tuple[tuple[Kind, re.Pattern[str]], ...] = (
    (Kind.REVERSAL, re.compile(r"\b(?:revers(?:ed|al)|chargeback)\b|عكس|إلغاء العملية|الغاء العملية", re.IGNORECASE)),
    (Kind.REFUND, re.compile(r"\b(?:refund(?:ed)?|returned to your card)\b|استرداد|مسترد|مرتجع", re.IGNORECASE)),
    (Kind.SALARY, re.compile(r"\b(?:salary|payroll|wps)\b|راتب|الراتب|رواتب", re.IGNORECASE)),
    (Kind.LOAN_DISBURSEMENT, re.compile(r"\b(?:loan|finance|financing) (?:disbursed|disbursement|credited)\b|صرف التمويل|إيداع التمويل|ايداع التمويل|صرف القرض", re.IGNORECASE)),
    (Kind.LOAN_REPAYMENT, re.compile(r"\b(?:loan|finance|financing) (?:installment|instalment|repayment|payment)\b|\binstall?ment\b|قسط|سداد التمويل|سداد القرض", re.IGNORECASE)),
    (Kind.CARD_REPAYMENT, re.compile(r"\b(?:credit card (?:payment|repayment)|card (?:payment|repayment) received|payment (?:received|credited) (?:to|on|for) (?:your )?(?:credit )?card|paid (?:to|towards) (?:your )?credit card)\b|سداد (?:مستحقات )?البطاقة|تسديد البطاقة|سداد بطاقة|سداد بطاقة ائتمان", re.IGNORECASE)),
    (Kind.ATM_WITHDRAWAL, re.compile(r"\b(?:atm|cash withdrawal|cash wdl|withdrawn from atm)\b|سحب نقدي|صراف|الصراف", re.IGNORECASE)),
    (Kind.BANK_FEE, re.compile(r"\b(?:fee|fees|charges|service charge|commission|annual fee)\b(?!.*\b(?:purchase|at)\b)|رسوم|عمولة", re.IGNORECASE)),
    (Kind.OWN_TRANSFER, re.compile(r"\b(?:between your accounts|to your own account|own account transfer|internal transfer)\b|بين حساباتك|إلى حسابك الآخر|الى حسابك الاخر", re.IGNORECASE)),
    (Kind.TRANSFER_OUT, re.compile(r"\b(?:transfer(?:red)? to|sent to|outgoing transfer|remittance to|transfer out)\b|حوالة صادرة|تحويل إلى|تحويل الى|حوالة إلى|حوالة الى|تحويل صادر", re.IGNORECASE)),
    (Kind.TRANSFER_IN, re.compile(r"\b(?:transfer(?:red)? from|received from|incoming transfer|remittance from|transfer in)\b|حوالة واردة|تحويل من|حوالة من|تحويل وارد", re.IGNORECASE)),
    (Kind.CARD_PURCHASE, re.compile(r"\b(?:purchase|pos|spent|online payment|e-?commerce|card transaction|apple pay|mada)\b|شراء|عملية شراء|مشتريات|نقاط البيع|مدى", re.IGNORECASE)),
    (Kind.DEPOSIT, re.compile(r"\b(?:deposit(?:ed)?|cash deposit|credited to your (?:account|a/c))\b|إيداع|ايداع|أودع", re.IGNORECASE)),
)
# Generic debit and credit words: a weak signal only, so the message waits for review.
_DEBIT = re.compile(r"\b(?:debited|debit|deducted|withdrawn)\b|خصم|مدين|سحب", re.IGNORECASE)
_CREDIT = re.compile(r"\b(?:credited|credit(?:ed)? to)\b|دائن|أضيف|اضيف", re.IGNORECASE)

_LAST4 = re.compile(
    r"(?:card|a/c|acct|account|ending(?: in| with)?|ends with|no\.?|بطاقة|بطاقتك|حساب|حسابك|رقم|المنتهية ب|المنتهي ب)"
    r"[^\d\n]{0,24}?(?:\*+|x{2,}|X{2,}|\.{2,}|…)\s?(?P<l1>\d{4})\b"
    r"|(?:card|a/c|acct|account|بطاقة|بطاقتك|حساب|حسابك)\s?(?:ending(?: in| with)?|ends with|المنتهية ب|المنتهي ب)?\s?(?P<l2>\d{4})\b"
    r"|(?:\*{2,}|x{3,}|X{3,})(?P<l3>\d{4})\b",
    re.IGNORECASE,
)
_ACCOUNT_WORDS = r"(?:your\s+)?(?:own\s+)?(?:credit\s+)?(?:account|a/c|acct|card|حساب|حسابك|بطاقة|بطاقتك|بطاقة الائتمان)?"
_FROM_ACCT = re.compile(rf"(?:\bfrom\b|من)\s+{_ACCOUNT_WORDS}\s*(?:no\.?|ending(?: in)?|المنتهية ب)?\s*(?:\*+|x{{2,}}|\.{{2,}})?(?P<l>\d{{4}})\b", re.IGNORECASE)
_TO_ACCT = re.compile(rf"(?:\bto\b|\btowards\b|إلى|الى|لـ?)\s+{_ACCOUNT_WORDS}\s*(?:no\.?|ending(?: in)?|المنتهية ب)?\s*(?:\*+|x{{2,}}|\.{{2,}})?(?P<l>\d{{4}})\b", re.IGNORECASE)
_REFERENCE = re.compile(
    r"(?:ref(?:erence)?\.?(?:\s?(?:no|number|#)\.?)?|txn id|transaction id|رقم المرجع|المرجع|مرجع|رقم العملية)\s*[:#]?\s*(?P<ref>[A-Za-z0-9][A-Za-z0-9-]{3,29})",
    re.IGNORECASE,
)
_BALANCE = re.compile(
    rf"(?:avl\.?\s?bal(?:ance)?|available balance|avail(?:able)?\.?\s?bal|current balance|balance|bal\.?|الرصيد المتاح|الرصيد|رصيدك|رصيد)"
    rf"\s*(?:is|:|=)?\s*(?:(?P<c1>{_CURRENCY_ALT})\s?(?P<n1>{_NUMBER})|(?P<n2>{_NUMBER})\s?(?P<c2>{_CURRENCY_ALT}))",
    re.IGNORECASE,
)
# The other party: "at X", "from X", "to X", "لدى X", "من X", "إلى X".
_PARTY = re.compile(
    r"(?:\bat\b|\bfrom\b|\bto\b|\bmerchant\b:?|\bbeneficiary\b:?|لدى|عند|من|إلى|الى|المستفيد:?|التاجر:?)\s+"
    r"(?P<party>[^\n,;]{2,60}?)"
    r"(?=\s+(?:on|using|via|with|by|for|to|from|of|card|ref|reference|avl|available|bal|balance|dated|at\s\d|في|بتاريخ|الرصيد|رصيد|بطاقة|عبر|المرجع|مرجع|رقم|من|إلى|الى)\b|\s+\d|\s*\*|[,;\n(]|\.(?:\s|$)|$)",
    re.IGNORECASE,
)
_DATE = re.compile(r"(?P<d>\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b)(?:\s*(?:at\s)?(?P<t>\d{1,2}:\d{2})(?::\d{2})?)?", re.IGNORECASE)
# "to your account", "from card": the person's own account, not another party.
_NOT_A_PARTY = re.compile(r"^(?:your|you|the|card|account|a/c|acct|حسابك|بطاقتك|حساب|بطاقة)\b", re.IGNORECASE)
_TIME = re.compile(r"\b(?P<t>\d{1,2}:\d{2})(?::\d{2})?\b")
_INTEREST = re.compile(rf"(?:interest|profit|ربح|فائدة|الأرباح)\s*(?::)?\s*(?:(?P<c1>{_CURRENCY_ALT})\s?(?P<n1>{_NUMBER})|(?P<n2>{_NUMBER})\s?(?P<c2>{_CURRENCY_ALT}))", re.IGNORECASE)
_FEE_AMOUNT = re.compile(rf"(?:fee|fees|charges|رسوم)\s*(?:of|:)?\s*(?:(?P<c1>{_CURRENCY_ALT})\s?(?P<n1>{_NUMBER})|(?P<n2>{_NUMBER})\s?(?P<c2>{_CURRENCY_ALT}))", re.IGNORECASE)

# Long digit runs (account, card and IBAN numbers), with spaces or dashes between groups.
_LONG_NUMBER = re.compile(r"(?<![\d.,])(?:[A-Z]{2}\d{2}[ -]?)?\d(?:[ -]?\d){5,}(?![\d]|[.,]\d)")
_INVISIBLE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁦-⁩﻿]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


@dataclass
class Parsed:
    """What :func:`parse` read. Amounts are positive; the kind says the direction."""

    status: str  # "ok", "review" or "rejected"
    reason: str | None = None  # the review or refusal reason code
    kind: Kind = Kind.UNKNOWN
    amount: Decimal | None = None
    currency: str | None = None
    converted_amount: Decimal | None = None  # the amount in the account's currency, when the message gives both
    converted_currency: str | None = None
    fee: Decimal | None = None
    interest: Decimal | None = None
    merchant: str | None = None
    last4: str | None = None
    second_last4: str | None = None  # the other card or account a repayment or transfer names
    from_last4: str | None = None  # "from account ***1234": where the money left
    to_last4: str | None = None  # "to card ***5678": where it went
    reference: str | None = None
    occurred_on: date | None = None
    occurred_at_time: time | None = None
    balance: Decimal | None = None
    balance_currency: str | None = None
    bank_key: str | None = None
    rule: str = "generic"  # "generic", "learned:<id>" or "none"
    confidence: str = "low"  # "high" when every field the posting needs was read
    notes: list[str] = field(default_factory=list)

    def fields(self) -> dict[str, Any]:
        """The parsed fields as JSON-safe values (decimal strings, ISO dates)."""
        def value(item: Any) -> Any:
            if isinstance(item, Decimal):
                return str(item)
            if isinstance(item, (date, time)):
                return item.isoformat()
            return item.value if isinstance(item, StrEnum) else item

        keys = ("kind", "amount", "currency", "converted_amount", "converted_currency", "fee", "interest", "merchant", "last4",
                "second_last4", "from_last4", "to_last4", "reference", "occurred_on", "occurred_at_time", "balance", "balance_currency", "bank_key")
        return {key: value(getattr(self, key)) for key in keys if getattr(self, key) is not None}


# -- normalization and masking ------------------------------------------------------------------


def normalize(text: str) -> str:
    """NFKC, Western digits, no control or direction characters, single spaces, at most 1,000 characters."""
    cleaned = unicodedata.normalize("NFKC", text or "").translate(_ARABIC_DIGITS)
    cleaned = _INVISIBLE.sub(" ", cleaned)
    return " ".join(cleaned.split())[:MAX_TEXT]


def normalize_sender(sender: str) -> str:
    """A sender name as a lookup key: lower case letters and digits only (``"AlRajhi-Bank"`` -> ``"alrajhibank"``)."""
    cleaned = unicodedata.normalize("NFKC", sender or "").translate(_ARABIC_DIGITS).lower()
    return "".join(ch for ch in cleaned if ch.isalnum())[:MAX_SENDER]


def mask(text: str) -> str:
    """Every account, card or IBAN number cut to ``***`` and its last four digits."""

    def cut(match: re.Match[str]) -> str:
        digits = "".join(ch for ch in match.group(0) if ch.isdigit())
        return "***" + digits[-4:]

    return _LONG_NUMBER.sub(cut, text)


def bank_for_sender(sender_key: str, extra: dict[str, str] | None = None) -> Bank | None:
    """The bank a sender belongs to: the person's own sender list first, then the registry."""
    if extra and sender_key in extra:
        return BANK_BY_KEY.get(extra[sender_key]) or Bank(extra[sender_key], extra[sender_key], "", "", (sender_key,))
    return _SENDER_INDEX.get(sender_key)


def refusal(text: str) -> Rejection | None:
    """Why a message is refused without being stored, or None."""
    lowered = text.lower()
    if _OTP.search(lowered):
        return Rejection.OTP
    if _PASSWORD.search(lowered):
        return Rejection.PASSWORD
    if _PROMO.search(lowered) and not _TXN_WORDS.search(lowered):
        return Rejection.PROMOTION
    return None


# -- field readers ------------------------------------------------------------------------------


def _number(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _currency(raw: str) -> str:
    return _CURRENCY_CODE.get(raw.upper(), _CURRENCY_CODE.get(raw, raw.upper()))


def _money_match(match: re.Match[str]) -> tuple[Decimal, str] | None:
    number = match.group("n1") or match.group("n2")
    word = match.group("c1") or match.group("c2")
    value = _number(number or "")
    if value is None or not word:
        return None
    currency = _currency(word)
    try:
        return quantize(value, currency), currency
    except MoneyError:
        return None


def _amounts(text: str, skip: list[tuple[int, int]]) -> list[tuple[Decimal, str, int]]:
    """Every amount with a currency, in order, except those inside ``skip`` spans."""
    found = []
    for match in _AMOUNT.finditer(text):
        if any(start <= match.start() < end for start, end in skip):
            continue
        read = _money_match(match)
        if read is not None:
            found.append((read[0], read[1], match.start()))
    return found


def _date(raw: str, day_first: bool = True) -> date | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    parts = re.split(r"[/.-]", raw)
    if len(parts) != 3:
        return None
    a, b, c = (int(p) for p in parts)
    year = c + 2000 if c < 100 else c
    day, month = (a, b) if day_first else (b, a)
    if month > 12 and day <= 12:
        day, month = month, day
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _clean_party(raw: str) -> str | None:
    party = " ".join(raw.strip(" :-*").split())
    party = re.sub(r"\s*(?:\*{2,}|x{3,})\d{4}$", "", party, flags=re.IGNORECASE)
    if not party or _AMOUNT.fullmatch(party) or party.replace(" ", "").isdigit() or len(party) < 2:
        return None
    if _NOT_A_PARTY.match(party):
        return None
    return party[:60]


def kind_of(text: str) -> tuple[Kind, bool]:
    """The kind a message describes and whether the words were specific (not just debited/credited)."""
    for kind, pattern in _KIND_RULES:
        if pattern.search(text):
            return kind, True
    if _DEBIT.search(text):
        return Kind.TRANSFER_OUT, False
    if _CREDIT.search(text):
        return Kind.DEPOSIT, False
    return Kind.UNKNOWN, False


# -- parsing ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LearnedRule:
    """An owner-taught rule (``finance_sms_rules``), as the parser needs it."""

    id: str
    sender: str
    pattern: str
    kind: Kind
    currency: str | None = None
    category: str | None = None


def parse(
    sender: str,
    text: str,
    *,
    learned: list[LearnedRule] | None = None,
    extra_senders: dict[str, str] | None = None,
    verified_senders: set[str] | None = None,
) -> Parsed:
    """Read one message. ``text`` may be raw; it is normalized here."""
    body = normalize(text)
    sender_key = normalize_sender(sender)
    if not body:
        return Parsed(status="rejected", reason=Rejection.NOT_FINANCIAL.value, rule="none")
    refused = refusal(body)
    if refused is not None:
        return Parsed(status="rejected", reason=refused.value, rule="none")
    bank = bank_for_sender(sender_key, extra_senders) if sender_key else None
    for rule in learned or []:
        if rule.sender == sender_key:
            result = apply_learned(rule, body)
            if result is not None:
                result.bank_key = bank.key if bank else None
                return result
    result = _generic(body)
    result.bank_key = bank.key if bank else None
    if result.status == "rejected":
        return result
    if bank is None:
        result.status, result.reason = "review", "unknown_sender"
        result.confidence = "low"
    elif result.status == "ok" and sender_key not in (verified_senders or set()):
        # The engine read it, but this person has not confirmed a real message from this bank yet.
        result.status, result.reason = "review", "format_unverified"
    return result


def _generic(body: str) -> Parsed:
    result = Parsed(status="review")
    balance_span: list[tuple[int, int]] = []
    balance = _BALANCE.search(body)
    if balance is not None:
        read = _money_match(balance)
        if read is not None:
            result.balance, result.balance_currency = read
            balance_span.append((balance.start(), balance.end()))
    skip = list(balance_span)
    for pattern in (_INTEREST, _FEE_AMOUNT):
        match = pattern.search(body)
        if match is not None:
            read = _money_match(match)
            if read is not None:
                if pattern is _INTEREST:
                    result.interest = read[0]
                else:
                    result.fee = read[0]
                skip.append((match.start(), match.end()))
    amounts = _amounts(body, skip)
    kind, specific = kind_of(body)
    result.kind = kind
    if not amounts:
        if kind == Kind.UNKNOWN and not _TXN_WORDS.search(body):
            return Parsed(status="rejected", reason=Rejection.NOT_FINANCIAL.value, rule="none")
        result.reason = "missing_amount"
        _fill_details(result, body)
        return result
    result.amount, result.currency = amounts[0][0], amounts[0][1]
    # A second amount in another currency is the converted (billed) amount: "USD 20.00 (SAR 75.40)".
    for amount, currency, _ in amounts[1:]:
        if currency != result.currency:
            result.converted_amount, result.converted_currency = amount, currency
            break
    if result.kind == Kind.BANK_FEE and result.fee is None:
        result.fee = result.amount
    _fill_details(result, body)
    if result.amount is not None and result.amount <= 0:
        result.reason = "missing_amount"
        return result
    if kind == Kind.UNKNOWN:
        result.reason = "unknown_kind"
        return result
    if not specific:
        result.reason = "ambiguous_kind"
        return result
    result.status, result.reason, result.confidence = "ok", None, "high"
    return result


def _fill_details(result: Parsed, body: str) -> None:
    cards = [m.group("l1") or m.group("l2") or m.group("l3") for m in _LAST4.finditer(body)]
    cards = [c for c in cards if c]
    if cards:
        result.last4 = cards[0]
        other = next((c for c in cards[1:] if c != cards[0]), None)
        result.second_last4 = other
    for pattern, name in ((_FROM_ACCT, "from_last4"), (_TO_ACCT, "to_last4")):
        found_account = pattern.search(body)
        if found_account is not None:
            setattr(result, name, found_account.group("l"))
    reference = _REFERENCE.search(body)
    if reference is not None:
        result.reference = reference.group("ref")[:30]
    party = _PARTY.search(body)
    if party is not None:
        result.merchant = _clean_party(party.group("party"))
    when = _DATE.search(body)
    if when is not None:
        result.occurred_on = _date(when.group("d"))
        clock = when.group("t")
    else:
        found = _TIME.search(body)
        clock = found.group("t") if found else None
    if clock:
        hour, minute = (int(part) for part in clock.split(":"))
        if hour < 24 and minute < 60:
            result.occurred_at_time = time(hour, minute)


# -- owner-taught rules -------------------------------------------------------------------------

FIELD_PATTERNS = {
    "amount": r"(?P<amount>\d[\d,]*(?:\.\d+)?)",
    "balance": r"(?P<balance>\d[\d,]*(?:\.\d+)?)",
    "merchant": r"(?P<merchant>.{1,80}?)",
    "last4": r"(?P<last4>\d{4})",
    "reference": r"(?P<reference>[A-Za-z0-9][A-Za-z0-9-]{2,29})",
}
MAX_PATTERN = 4_000


class TeachError(ValueError):
    """A rule cannot be learned from this message and these corrections."""


def _variants(amount: Decimal) -> list[str]:
    plain = format(amount, "f")
    whole = plain.split(".")[0]
    texts = {plain, f"{amount:,}", whole, f"{int(amount):,}" if amount == amount.to_integral() else plain}
    if "." in plain:
        texts.add(plain.rstrip("0").rstrip("."))
    return sorted(texts, key=len, reverse=True)


def _find(text: str, needles: list[str], taken: list[tuple[int, int]], *, word: bool) -> tuple[int, int] | None:
    lowered = text.lower()
    for needle in needles:
        if not needle:
            continue
        start = 0
        while True:
            index = lowered.find(needle.lower(), start)
            if index < 0:
                break
            end = index + len(needle)
            before_ok = index == 0 or not (text[index - 1].isdigit() if word else False)
            after_ok = end == len(text) or not (text[end].isdigit() if word else False)
            if before_ok and after_ok and not any(index < e and end > s for s, e in taken):
                return index, end
            start = index + 1
    return None


def build_pattern(body: str, corrected: dict[str, Any]) -> str:
    """A rule pattern for ``body`` (normalized, masked text) with the corrected fields as groups.

    Literal text is escaped; other digit runs become ``\\d+`` and spaces ``\\s+``, so the rule
    also reads the next message of the same format with other dates and numbers. The pattern
    is built only from fixed pieces: no text from the client becomes regular-expression syntax.
    """
    spans: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    order = ("amount", "balance", "last4", "reference", "merchant")
    for name in order:
        value = corrected.get(name)
        if value in (None, ""):
            continue
        if name in ("amount", "balance"):
            needles = _variants(Decimal(str(value)))
            found = _find(body, needles, taken, word=True)
        elif name == "last4":
            found = _find(body, [str(value)], taken, word=True)
        else:
            found = _find(body, [str(value).strip()], taken, word=False)
        if found is None:
            if name in ("amount",):
                raise TeachError(f"The {name} you entered does not appear in the message.")
            continue
        spans.append((found[0], found[1], name))
        taken.append(found)
    if not any(name == "amount" for _, _, name in spans):
        raise TeachError("Enter the amount exactly as the message shows it.")
    spans.sort()
    pieces: list[str] = []
    cursor = 0
    for start, end, name in spans:
        pieces.append(_literal(body[cursor:start]))
        pieces.append(FIELD_PATTERNS[name])
        cursor = end
    pieces.append(_literal(body[cursor:]))
    pattern = "".join(pieces)
    if len(pattern) > MAX_PATTERN:
        raise TeachError("This message is too long to learn a rule from.")
    compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    match = compiled.fullmatch(body)
    if match is None:
        raise TeachError("ANUM could not build a rule that reads this message again.")
    return pattern


def _literal(text: str) -> str:
    out: list[str] = []
    for token in re.split(r"(\d+|\s+)", text):
        if not token:
            continue
        if token.isdigit():
            out.append(r"\d+")
        elif token.isspace():
            out.append(r"\s+")
        else:
            out.append(re.escape(token))
    return "".join(out)


def apply_learned(rule: LearnedRule, body: str) -> Parsed | None:
    """The fields an owner rule reads from ``body``, or None when it does not match."""
    try:
        compiled = re.compile(rule.pattern, re.IGNORECASE | re.DOTALL)
    except re.error:
        return None
    match = compiled.fullmatch(body)
    if match is None:
        return None
    groups = match.groupdict()
    currency = rule.currency
    if not currency:
        found = _AMOUNT.search(body)
        read = _money_match(found) if found else None
        currency = read[1] if read else None
    result = Parsed(status="review", kind=rule.kind, rule=f"learned:{rule.id}")
    if currency:
        result.currency = currency
        amount = _number(groups.get("amount") or "")
        if amount is not None:
            try:
                result.amount = quantize(amount, currency)
            except MoneyError:
                result.amount = None
        balance = _number(groups.get("balance") or "")
        if balance is not None:
            result.balance, result.balance_currency = quantize(balance, currency), currency
    result.merchant = _clean_party(groups["merchant"]) if groups.get("merchant") else None
    result.last4 = groups.get("last4")
    result.reference = groups.get("reference")
    details = Parsed(status="review")
    _fill_details(details, body)
    result.occurred_on, result.occurred_at_time = details.occurred_on, details.occurred_at_time
    if result.last4 is None:
        result.last4 = details.last4
    if result.amount is None or result.amount <= 0 or result.currency is None:
        result.reason = "missing_amount"
        return result
    result.status, result.reason, result.confidence = "ok", None, "high"
    return result


def occurred(parsed: Parsed, received: datetime) -> datetime:
    """When the transaction happened: the message's own date and time, else when it arrived."""
    day = parsed.occurred_on or received.date()
    clock = parsed.occurred_at_time or (received.timetz() if parsed.occurred_on is None else time(12, 0))
    return datetime.combine(day, clock.replace(tzinfo=None), tzinfo=received.tzinfo)


__all__ = [
    "BANKS",
    "BANK_BY_KEY",
    "Bank",
    "Kind",
    "LearnedRule",
    "MONEY_IN",
    "Parsed",
    "Rejection",
    "TeachError",
    "apply_learned",
    "bank_for_sender",
    "build_pattern",
    "kind_of",
    "mask",
    "normalize",
    "normalize_sender",
    "occurred",
    "parse",
    "refusal",
]
