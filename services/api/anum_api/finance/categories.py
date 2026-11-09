"""Rule-based transaction categories and types (docs/finance.md#categories, #transaction-types). No AI, no network.

Order of precedence: the owner's own rules (``FinanceSettings.category_rules``, first
match wins), then the built-in keyword rules below (common UAE and Saudi merchants and statement
wording). Money that no rule recognises is **uncategorized**: category ``other``, type
``uncategorized``, held for review and counted as neither income nor spend (a credit is not
automatically income, a debit is not automatically spending). A category or type the owner
picks by hand (``manual``) is never changed by a rule or a match afterwards.

Money in on a card or loan account is the payment from the person's own bank (a card payment),
not income, unless a rule files it as cashback, a refund or a merchant's credit
(docs/finance.md#how-figures-are-counted). Salary and payroll words win over "transfer", a
specific spending word (rent, school) wins over "transfer", and interest received is income
(category ``interest``) while interest charged is spending (type ``interest``).

A transfer to another person (statement words such as "transfer to", "IPP", "remittance", or an SMS
transfer out) is spending: category ``other`` unless a spending word names what it paid for (rent is
housing). Only words that name the person's own accounts, or a match with the other side, make it an
internal transfer. Money in with transfer words and no own-account words is uncategorized.

Matching is on the cleaned, lower-case description: a keyword (built in or the owner's) matches as a
whole word or phrase, so "du" matches "DU TELECOM" but not "dubai". An owner's rule for income, cashback
or refunds never matches money out (the same sign guard as the built-in rules).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from . import txn_types as TT
from .flows import LIABILITY_KINDS, NOT_SPEND
from .models import AccountKind, Category, CategoryRule, CategorySource, TransactionType, TypeSource
from .rules import ActiveRule, AliasBook, Row

# Paying a credit card from a bank account, in statement and SMS wording (English and Arabic).
CARD_PAYMENT_WORDS = (
    "credit card payment", "cc payment", "card repayment", "card settlement", "card bill payment", "autopay", "auto pay",
    "payment thank you", "thank you for your payment", "payment received thank you", "payment received - thank you",
    "سداد بطاقة", "سداد البطاقة", "سداد بطاقة ائتمان", "سداد بطاقة الائتمان", "تسديد بطاقة", "تسديد البطاقة", "دفعة بطاقة الائتمان",
)
# Words that name the person's own accounts: only these (or a match with the other side) make a transfer internal.
OWN_TRANSFER_WORDS = (
    "own account", "own accounts", "own acc", "between your accounts", "between own accounts", "to own", "from own", "self transfer",
    "بين حساباتك", "إلى حسابك", "الى حسابك",
)
GENERIC_TRANSFER_WORDS = ("transfer", "trf", "ipp", "aani", "remittance", "تحويل", "حوالة")

# (category, keywords). Earlier entries win, so the specific ones come first. Income words only ever
# match money in (a rule that finds them on money out is skipped), so they can come early.
BUILT_IN: tuple[tuple[Category, tuple[str, ...]], ...] = (
    (Category.CASHBACK, ("cashback", "cash back", "cash-back", "cashback earned", "cashback credit", "cash back reward", "مكافأة نقدية", "استرداد نقدي")),
    (Category.REFUNDS, ("refund", "refunded", "merchant credit", "purchase reversal", "reversal of purchase", "رد المبلغ", "استرجاع")),
    (Category.INTEREST, ("interest earned", "interest credit", "interest credited", "credit interest", "interest paid to you",
                         "profit credit", "profit distribution", "savings profit", "فوائد دائنة", "أرباح التوفير", "توزيع الأرباح")),
    (Category.TRANSFER, CARD_PAYMENT_WORDS),
    (Category.FEES, ("foreign transaction fee", "international transaction fee", "cross border fee", "currency conversion charge", "fx markup", "fx fee", "atm fee", "annual fee", "monthly fee", "late fee", "vat charge", "ضريبة رسوم")),
    # Interest or profit charged on a card or loan is interest (spend), never a bank fee; money in with these words is interest received.
    (Category.INTEREST, ("interest", "finance charge", "interest charge", "profit charge", "فائدة", "فوائد")),
    # Explicit personal expenses must win over generic transfer/service-charge keywords.
    (Category.ROOM_RENT, ("room rent", "rent room", "room rental", "monthly room", "room payment",
                          "bedspace", "bed space", "accommodation rent", "room sharing",
                          "ايجار غرفة", "إيجار غرفة", "إيجار السكن", "سكن مشترك", "kiraya room")),
    (Category.FOOD, ("food", "food order", "food delivery", "meal", "meals", "lunch", "dinner", "breakfast",
                     "canteen", "mess bill", "mess food", "وجبات", "طعام", "غداء", "عشاء", "فطور", "کھانا")),
    # Salary paid by transfer (WPS) is salary, and rent or school fees paid by transfer are spending.
    (Category.INCOME, ("salary", "payroll", "wps", "dividend", "راتب")),
    (Category.HOUSING, ("house rent", "flat rent", "apartment rent", "ejari", "landlord", "rent", "إيجار", "اجار")),
    (Category.EDUCATION, ("school fee", "school fees", "tuition")),
    (Category.TRANSFER, OWN_TRANSFER_WORDS),
    (Category.TRANSFER, GENERIC_TRANSFER_WORDS),
    (Category.FEES, ("fee", "fees", "charges", "commission", "late payment", "vat on", "رسوم")),
    (Category.CASH, ("atm", "cash withdrawal", "cash wdl", "سحب نقدي")),
    (Category.FUEL, ("enoc", "adnoc", "eppco", "emarat", "petrol", "fuel")),
    (Category.TRANSPORT, ("careem", "uber", "salik", "rta", "nol", "parking", "mawaqif", "taxi", "metro")),
    (Category.GROCERIES, ("panda", "hyper panda", "othaim", "al othaim", "tamimi", "danube", "bin dawood", "carrefour", "lulu", "spinneys", "choithrams", "waitrose", "union coop", "grandiose",
                          "al maya", "viva", "west zone", "supermarket", "hypermarket", "grocery")),
    (Category.DINING, ("talabat", "deliveroo", "zomato", "noon food", "hungerstation", "jahez", "the chefz", "starbucks", "costa", "tim hortons",
                       "mcdonald", "kfc", "restaurant", "cafe", "coffee", "bakery")),
    (Category.UTILITIES, ("dewa", "sewa", "addc", "aadc", "fewa", "etihad water", "empower", "tabreed", "lootah")),
    (Category.TELECOM, ("etisalat", "e&", "du", "virgin mobile", "internet")),
    (Category.HOUSING, ("rent", "rental", "house rent", "flat rent", "apartment rent", "ejari", "landlord", "real estate", "property management", "إيجار", "اجار")),
    (Category.SUBSCRIPTIONS, ("netflix", "spotify", "shahid", "osn", "anghami", "apple.com", "google storage",
                              "icloud", "youtube premium", "amazon prime", "subscription")),
    (Category.SHOPPING, ("amazon", "noon", "namshi", "ikea", "sharaf dg", "jumbo", "ace hardware", "centrepoint",
                         "home centre", "max fashion", "mall")),
    (Category.HEALTH, ("pharmacy", "aster", "life pharmacy", "boots", "clinic", "hospital", "medical", "dental",
                       "mediclinic", "nmc")),
    (Category.EDUCATION, ("school", "tuition", "gems", "taaleem", "university", "college", "course")),
    (Category.ENTERTAINMENT, ("vox", "reel cinemas", "novo", "cinema", "dubai parks", "img worlds", "ticketmaster",
                              "platinumlist", "playstation", "steam")),
    (Category.TRAVEL, ("emirates airline", "flydubai", "etihad airways", "air arabia", "booking.com", "airbnb",
                       "hotel", "expedia", "agoda")),
    (Category.INSURANCE, ("insurance", "takaful", "axa", "oman insurance", "sukoon", "daman", "adnic")),
    # Last: "card payment" alone, so a merchant named in the same line ("CARREFOUR CARD PAYMENT") wins.
    (Category.TRANSFER, ("card payment", "payment to card", "card pmt")),
)

_BOUNDARY_START, _BOUNDARY_END = r"(?<![\w&])(?:", r")(?![\w&])"
_COMPILED = tuple(
    (category, words, re.compile(_BOUNDARY_START + "|".join(re.escape(word) for word in words) + _BOUNDARY_END, re.IGNORECASE))
    for category, words in BUILT_IN
)
# Categories a rule may give only to money in (the sign guard): on money out they are skipped.
_MONEY_IN_ONLY = frozenset({Category.INCOME, Category.CASHBACK, Category.REFUNDS})


@dataclass(frozen=True)
class Classified:
    """What the rules make of one row: its category and type, and what decided each."""

    category: Category
    source: CategorySource
    typed: TT.Typed
    subcategory_id: str | None = None
    rule_id: str | None = None  # the owner's rule that decided it (``rules.FinanceRule.rule_id``), when one did
    rule_version: int | None = None

    @property
    def type(self) -> TransactionType:
        return self.typed.type


def _rule_pattern(match: str) -> re.Pattern[str]:
    words = " ".join(match.lower().split())
    return re.compile(_BOUNDARY_START + re.escape(words) + _BOUNDARY_END, re.IGNORECASE)


def _typed(kind: TransactionType, source: TypeSource, confidence: str, reason: str) -> TT.Typed:
    return TT.Typed(kind, source, Decimal(confidence), reason)


def _owner_rule(rule: ActiveRule, amount: Decimal, account_kind: AccountKind | None) -> Classified:
    content = rule.content
    if content.transaction_type is not None:
        typed = TT.Typed(content.transaction_type, TypeSource.RULE, rule.confidence, "owner_rule")
    else:
        typed = TT.for_category(content.category, amount, account_kind, TypeSource.RULE, str(rule.confidence), "owner_rule")
    return Classified(content.category, CategorySource.OWNER_RULE, typed, content.subcategory_id, rule.rule_id, rule.version)


def classify_text(description: str, amount: Decimal, rules: list[CategoryRule] | list[ActiveRule] | None = None,
                  account_kind: AccountKind | None = None, *, account_id: str | None = None, currency: str | None = None,
                  sender: str | None = None, reference: str | None = None, aliases: AliasBook | None = None) -> Classified:
    """The category and the type of a transaction from its description, sign and account kind.

    ``account_kind`` is the kind of account the row is on: money in on a card or loan is a card payment
    unless a rule files it as cashback, a refund, interest or a merchant's credit. ``rules`` are the owner's
    (``rules_store.owner_rules``: approved versioned rules, or the older plain phrase list); a rule scoped to an
    account, a sender or an amount range matches only when the caller passes that account, sender or currency."""
    text = " ".join(description.lower().split())
    liability_credit = amount > 0 and account_kind in LIABILITY_KINDS
    row = Row(description=description, amount=amount, currency=currency, account_id=account_id, sender=sender, reference=reference)
    for rule in rules or []:
        if isinstance(rule, CategoryRule):
            if rule.category in _MONEY_IN_ONLY and amount < 0:
                continue  # the owner's income, cashback or refund rule never files money out
            if _rule_pattern(rule.match).search(text):
                return Classified(rule.category, CategorySource.OWNER_RULE,
                                  TT.for_category(rule.category, amount, account_kind, TypeSource.RULE, "0.9", "owner_rule"))
            continue
        if rule.matches(row, aliases):
            return _owner_rule(rule, amount, account_kind)
    for category, words, pattern in _COMPILED:
        if not pattern.search(text):
            continue
        # "refund" or "cashback" on money out is a fee reversal at most: keep looking.
        if category in _MONEY_IN_ONLY and amount < 0:
            continue
        if category == Category.INCOME and liability_credit:
            return Classified(Category.TRANSFER, CategorySource.RULE, _typed(TransactionType.CARD_PAYMENT, TypeSource.RULE, "0.8", "card_payment_received"))
        if category == Category.TRANSFER:
            if words is CARD_PAYMENT_WORDS or liability_credit:
                reason = "card_payment_words" if words is CARD_PAYMENT_WORDS else "card_payment_received"
                return Classified(Category.TRANSFER, CategorySource.RULE, _typed(TransactionType.CARD_PAYMENT, TypeSource.RULE, "0.9", reason))
            if words is OWN_TRANSFER_WORDS:
                return Classified(Category.TRANSFER, CategorySource.RULE,
                                  _typed(TransactionType.INTERNAL_TRANSFER, TypeSource.RULE, "0.8", "own_account_words"))
            if words is GENERIC_TRANSFER_WORDS and amount < 0:
                # A transfer to another person with no words saying what for: a loan, money sent home or a payment are all
                # possible, so it waits for the person (a debit is not automatically an expense), until a match pairs it.
                return Classified(Category.OTHER, CategorySource.NONE, _typed(TransactionType.UNCATEGORIZED, TypeSource.RULE, "0.3", "transfer_out_third_party"))
            if words is GENERIC_TRANSFER_WORDS:
                return Classified(Category.OTHER, CategorySource.NONE, _typed(TransactionType.UNCATEGORIZED, TypeSource.RULE, "0.3", "transfer_in_unknown"))
            if words is not GENERIC_TRANSFER_WORDS and words is not OWN_TRANSFER_WORDS and amount < 0:
                # "card payment" alone (the last rule) on money out: paying a card.
                return Classified(Category.TRANSFER, CategorySource.RULE, _typed(TransactionType.CARD_PAYMENT, TypeSource.RULE, "0.7", "card_payment_words"))
            return Classified(Category.TRANSFER, CategorySource.RULE, _typed(TransactionType.INTERNAL_TRANSFER, TypeSource.RULE, "0.6", "transfer_words"))
        if category == Category.INTEREST:
            if amount > 0:
                if liability_credit:
                    return Classified(Category.INTEREST, CategorySource.RULE, _typed(TransactionType.REFUND, TypeSource.RULE, "0.7", "interest_reversed"))
                return Classified(Category.INTEREST, CategorySource.RULE, _typed(TransactionType.INCOME, TypeSource.RULE, "0.8", "interest_received"))
            return Classified(Category.INTEREST, CategorySource.RULE, _typed(TransactionType.INTEREST, TypeSource.RULE, "0.8", "interest_charged"))
        found = TT.for_category(category, amount, account_kind, TypeSource.RULE, "0.8", "keyword")
        if found.type == TransactionType.REFUND and category not in NOT_SPEND:
            found = TT.Typed(found.type, found.source, found.confidence, "merchant_credit")
        return Classified(category, CategorySource.RULE, found)
    if liability_credit:
        return Classified(Category.TRANSFER, CategorySource.RULE, _typed(TransactionType.CARD_PAYMENT, TypeSource.RULE, "0.7", "card_payment_received"))
    # Nothing recognised it: held for review, neither income nor spend (audit C1, C2).
    return Classified(Category.OTHER, CategorySource.NONE, _typed(TransactionType.UNCATEGORIZED, TypeSource.RULE, "0", "no_rule_matched"))


def categorize(description: str, amount: Decimal, rules: list[CategoryRule] | list[ActiveRule] | None = None,
               account_kind: AccountKind | None = None, **scope: object) -> tuple[Category, CategorySource]:
    """The category for a transaction, and what decided it (:func:`classify_text` without the type)."""
    found = classify_text(description, amount, rules, account_kind, **scope)  # type: ignore[arg-type]
    return found.category, found.source


# Categories that are not spending: excluded from "spend by category" and "spend" (finance/flows.py).
__all__ = ["BUILT_IN", "CARD_PAYMENT_WORDS", "GENERIC_TRANSFER_WORDS", "NOT_SPEND", "OWN_TRANSFER_WORDS", "Classified", "categorize", "classify_text",
           "suspected_direction"]

# Do not mutate account money based only on a merchant label. Incoming rent or
# outgoing cashback should be reviewed against the actual debit/credit column.
_PAYMENT_WORDS = re.compile(
    r"(?i)(?:room[\s-]*rent|room[\s-]*rental|bed[\s-]*space|rent[\s-]*payment|"
    r"monthly[\s-]*rent|accommodation[\s-]*rent|rent[\s-]*room|"
    r"إيجار[\s-]*غرفة|ايجار[\s-]*غرفة)"
)


def suspected_direction(description: str, amount: Decimal) -> str | None:
    """A review hint only. Never flip a ledger entry without human confirmation."""
    if amount > 0 and _PAYMENT_WORDS.search(description):
        return "Room rent is shown as incoming. Check the original bank debit/credit and correct to money out if paid."
    return None
