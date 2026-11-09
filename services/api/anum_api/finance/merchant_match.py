"""Do two descriptions name the same merchant? One helper for every matcher (docs/finance.md#statement-matching).

The SMS and Apple Pay matcher (``wallet``), the statement matching engine (``statement_match``) and bill and
transfer detection all ask the same question, so they share one answer here. A bank descriptor is mostly
noise: the channel ("POS", "mada", "online purchase", "شراء عبر نقاط البيع"), the kind of transaction
("transfer", "payment", "حوالة", "سداد"), card numbers, dates, times, cities and country codes. Two
descriptions agree only when they share a **meaningful** word: at least three letters, no digits, and not
one of those generic banking words in English or Arabic. Two rows that share only "purchase" or "حوالة"
are not the same merchant.

``SAME_PAYMENT_DAYS`` is the one window, in days, within which two records of the same amount can be the
same payment when no exact time says otherwise: a bank posts a card purchase 0 to 3 days after the SMS or
Apple Pay notice. The statement engine and the ledger's possible-duplicate check both read it.
"""

from __future__ import annotations

import re
import unicodedata

SAME_PAYMENT_DAYS = 3

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_INVISIBLE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff\\u0640]")
# Card numbers, masked digits, dates and times are removed before words are read.
_NOISE = re.compile(r"\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{1,2}:\d{2}(?::\d{2})?|[x*#•]{2,}\d{2,4}|\d{4,}", re.IGNORECASE)
_ARABIC_LETTER = re.compile("[\\u0600-\\u06ff]")

# Generic banking words: the channel, the kind of transaction, legal forms, card networks, cities and
# country codes. None of them names a merchant.
STOP_WORDS = frozenset({
    # English: articles and legal forms
    "the", "and", "for", "from", "with", "llc", "ltd", "inc", "est", "co", "company", "trading", "group", "store", "stores", "shop", "www", "com", "net",
    # English: channel and card network
    "pos", "vis", "visa", "deb", "debit", "mc", "mastercard", "master", "mada", "atm", "card", "cards", "ecom", "online", "internet", "web", "app",
    "apple", "pay", "applepay", "google", "gpay", "samsung", "contactless", "nfc", "intl", "international", "local", "domestic",
    # English: kind of transaction
    "purchase", "purch", "purchases", "txn", "trx", "trn", "transaction", "transfer", "trf", "tfr", "xfer", "payment", "payments", "pmt", "paid",
    "sadad", "bill", "bills", "fee", "fees", "charge", "charges", "commission", "vat", "refund", "reversal", "reversed", "cashback", "cash",
    "withdrawal", "deposit", "credit", "outgoing", "incoming", "inward", "outward", "own", "account", "acct", "bank", "beneficiary", "ref",
    "reference", "instant", "sarie", "ipp", "aani", "swift", "standing", "order", "auth", "authorised", "authorized", "settlement", "via",
    # English: countries and cities
    "ksa", "uae", "sa", "ae", "sau", "are", "usa", "gbr", "irl", "nld", "lux", "sgp", "riyadh", "jeddah", "dubai", "abudhabi", "sharjah",
    "dammam", "khobar", "makkah", "madinah", "mecca", "medina",
    # Arabic: channel, kind of transaction and connectors
    "شراء", "مشتريات", "حوالة", "حواله", "حوالات", "تحويل", "تحويلات", "سداد", "دفع", "دفعة", "مدفوعات", "نقاط", "بيع", "البيع", "مدى", "عبر",
    "انترنت", "الانترنت", "الإنترنت", "إنترنت", "بطاقة", "بطاقه", "البطاقة", "ائتمان", "ائتمانية", "ائتمانيه", "الائتمانية", "الائتمان", "داخلية",
    "داخلي", "محلية", "محلي", "دولية", "دولي", "صادرة", "صادره", "واردة", "وارده", "رسوم", "عمولة", "ضريبة", "خصم", "ايداع", "إيداع", "سحب",
    "صراف", "آلي", "الي", "مبلغ", "عملية", "عمليه", "فاتورة", "فواتير", "الى", "إلى", "من", "في", "على", "عن", "شركة", "مؤسسة", "مؤسسه",
    "لحساب", "حساب", "الحساب", "مستفيد", "المستفيد", "استرداد", "مرتجع", "ابل", "أبل", "باي", "الرياض", "جدة", "جده", "الدمام", "مكة", "المدينة",
    "السعودية", "المملكة",
})


def _fold(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", text or "").translate(_ARABIC_DIGITS)
    return _INVISIBLE.sub(" ", cleaned)


def _stem(word: str) -> str:
    """An Arabic word without its definite article ("الدانوب" and "دانوب" are one name)."""
    if _ARABIC_LETTER.match(word) and word.startswith("ال") and len(word) > 4:
        return word[2:]
    return word


def merchant_words(text: str | None) -> set[str]:
    """The meaningful words of a description: lower case, at least three letters, no digits, no generic banking word."""
    if not text:
        return set()
    words = re.findall(r"[^\W_]+", _NOISE.sub(" ", _fold(text)).lower())
    out = set()
    for word in words:
        if len(word) < 3 or any(ch.isdigit() for ch in word) or word in STOP_WORDS:
            continue
        stem = _stem(word)
        if stem in STOP_WORDS or len(stem) < 3:
            continue
        out.add(stem)
    return out


def merchants_agree(a: str | None, b: str | None) -> bool | None:
    """True when both name a merchant and share a meaningful word, False when both name one and share none,
    None when either names none (only generic banking words, numbers or nothing)."""
    left, right = merchant_words(a), merchant_words(b)
    if not left or not right:
        return None
    return bool(left & right)


def overlap(a: str | None, b: str | None) -> int:
    return len(merchant_words(a) & merchant_words(b))


__all__ = ["SAME_PAYMENT_DAYS", "STOP_WORDS", "merchant_words", "merchants_agree", "overlap"]
