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
from dataclasses import dataclass, replace
from decimal import Decimal

from . import txn_types as TT
from .flows import LIABILITY_KINDS, NOT_SPEND
from .models import AccountKind, Category, CategoryRule, CategorySource, TransactionType, TypeSource
from .rules import LEARNED_PREFIX, ActiveRule, AliasBook, Row, fold, phrase_pattern

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
    # Religious giving and money sent to family name what a transfer was for, so they win over the generic transfer words.
    (Category.CHARITY, ("zakat", "zakah", "sadaqah", "sadaqa", "sadaka", "donation", "donations", "charity", "red crescent", "dubai cares",
                        "awqaf", "umrah", "hajj", "زكاة", "الزكاة", "صدقة", "تبرع", "عمرة")),
    (Category.FAMILY_SUPPORT, ("al ansari exchange", "al ansari", "al fardan exchange", "al fardan", "lulu exchange", "lulu money", "western union",
                               "moneygram", "xpress money", "remitly", "exchange house", "money home", "family support",
                               "تحويل للأهل", "للأهل", "مصروف الأهل")),
    (Category.TRANSFER, OWN_TRANSFER_WORDS),
    (Category.TRANSFER, GENERIC_TRANSFER_WORDS),
    (Category.FEES, ("fee", "fees", "charges", "commission", "late payment", "vat on", "رسوم")),
    (Category.CASH, ("atm", "cash withdrawal", "cash wdl", "سحب نقدي")),
    (Category.FUEL, ("enoc", "adnoc", "eppco", "emarat", "petrol", "fuel")),
    (Category.TRANSPORT, ("careem", "uber", "bolt", "yango", "salik", "darb", "rta", "nol", "parking", "mawaqif", "taxi", "metro", "traffic fine",
                          "mulkiya", "tasjeel", "car wash", "tyre", "tyres")),
    (Category.GROCERIES, ("panda", "hyper panda", "othaim", "al othaim", "tamimi", "danube", "bin dawood", "carrefour", "lulu", "spinneys", "choithrams", "waitrose", "union coop", "grandiose",
                          "al maya", "viva", "west zone", "supermarket", "hypermarket", "grocery")),
    (Category.DINING, ("talabat", "deliveroo", "zomato", "noon food", "hungerstation", "jahez", "the chefz", "starbucks", "costa", "tim hortons",
                       "mcdonald", "kfc", "restaurant", "cafe", "coffee", "bakery")),
    (Category.UTILITIES, ("dewa", "sewa", "addc", "aadc", "fewa", "etihad water", "empower", "tabreed", "lootah")),
    (Category.TELECOM, ("etisalat", "e&", "du", "virgin mobile", "internet", "mobile recharge", "prepaid recharge", "stc", "mobily", "zain")),
    (Category.HOUSING, ("rent", "rental", "house rent", "flat rent", "apartment rent", "ejari", "landlord", "real estate", "property management", "إيجار", "اجار")),
    (Category.SUBSCRIPTIONS, ("netflix", "spotify", "shahid", "osn", "anghami", "apple.com", "google storage",
                              "icloud", "youtube premium", "amazon prime", "disney", "starzplay", "google one", "dropbox", "chatgpt", "openai",
                              "subscription")),
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
    (Category.CLOTHING, ("laundry", "dry clean", "dry cleaning", "ironing", "tailor", "tailoring", "alteration", "max fashion", "splash", "h&m",
                         "zara", "r&b fashion", "clothes", "clothing", "shoes", "footwear", "مغسلة", "خياط", "ملابس", "أحذية")),
    (Category.FAMILY, ("diaper", "diapers", "pampers", "huggies", "baby formula", "infant formula", "aptamil", "mothercare", "babyshop",
                       "baby shop", "nursery", "daycare", "childcare", "toys r us", "hamleys", "حفاضات", "حضانة")),
    (Category.PERSONAL_CARE, ("barber", "barbershop", "gents salon", "ladies salon", "salon", "spa", "grooming", "faces", "sephora", "cosmetics",
                              "fitness first", "gym", "حلاق", "صالون")),
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
    # A learned merchant mapping (``rules.LearnedMapping``) says so in its reason, so the register can show "learned".
    reason = "learned_merchant" if rule.rule_id.startswith(LEARNED_PREFIX) else "owner_rule"
    if content.transaction_type is not None:
        typed = TT.Typed(content.transaction_type, TypeSource.RULE, rule.confidence, reason)
    else:
        typed = TT.for_category(content.category, amount, account_kind, TypeSource.RULE, str(rule.confidence), reason)
    return Classified(content.category, CategorySource.OWNER_RULE, typed, content.subcategory_id, rule.rule_id, rule.version)


def classify_text(description: str, amount: Decimal, rules: list[CategoryRule] | list[ActiveRule] | None = None,
                  account_kind: AccountKind | None = None, *, account_id: str | None = None, currency: str | None = None,
                  sender: str | None = None, reference: str | None = None, aliases: AliasBook | None = None) -> Classified:
    """The category and the type of a transaction, and the built-in subcategory a built-in rule's words name
    (docs/finance.md#category-ontology). See :func:`_classify`."""
    found = _classify(description, amount, rules, account_kind, account_id=account_id, currency=currency, sender=sender,
                      reference=reference, aliases=aliases)
    if found.subcategory_id is None and found.source == CategorySource.RULE:
        sub = built_in_subcategory(found.category, description)
        if sub is not None:
            return replace(found, subcategory_id=sub)
    return found


def _classify(description: str, amount: Decimal, rules: list[CategoryRule] | list[ActiveRule] | None = None,
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


# -- the category ontology (docs/finance.md#category-ontology) -------------------------------------------------------
#
# Three levels, all optional below the category: a **group** (the parent the owner thinks in: "Food and drinks"), the
# **category** a row is stored under (``Category``: it decides how the row counts, with its type) and a **subcategory**
# (built in, ``sys:<slug>``, or the owner's own, ``fin_sub_...``). Built-in subcategories are code, not rows: they are
# the same for everyone, never renamed or deleted, and a row or rule may name one as it names the owner's. A group is
# read from the category (and, for transfers, from the type: a loan repayment or a card settlement is a debt flow).
#
# ``words`` of a built-in subcategory pick it once the row's category is decided by a built-in rule; they never decide
# the category themselves. ``essential`` is the default for budgets (essential or optional spending).

BUILT_IN_SUB_PREFIX = "sys:"


@dataclass(frozen=True)
class Group:
    id: str
    en: str
    ar: str
    categories: tuple[Category, ...]


@dataclass(frozen=True)
class BuiltInSub:
    id: str
    parent: Category
    en: str
    ar: str
    essential: bool
    words: tuple[str, ...] = ()


GROUPS: tuple[Group, ...] = (
    Group("food_drinks", "Food and drinks", "الطعام والمشروبات", (Category.GROCERIES, Category.DINING, Category.FOOD)),
    Group("housing", "Housing", "السكن", (Category.HOUSING, Category.ROOM_RENT, Category.UTILITIES)),
    Group("clothing", "Clothing and laundry", "الملابس والغسيل", (Category.CLOTHING,)),
    Group("communication", "Mobile and communication", "الجوال والاتصالات", (Category.TELECOM,)),
    Group("transport", "Transport", "المواصلات", (Category.TRANSPORT, Category.FUEL)),
    Group("family", "Family and children", "الأسرة والأطفال", (Category.FAMILY,)),
    Group("health", "Healthcare", "الرعاية الصحية", (Category.HEALTH,)),
    Group("personal_care", "Personal care", "العناية الشخصية", (Category.PERSONAL_CARE,)),
    Group("shopping", "Shopping", "التسوق", (Category.SHOPPING, Category.SUBSCRIPTIONS)),
    Group("travel", "Travel", "السفر", (Category.TRAVEL,)),
    Group("family_support", "Family transfers and support", "تحويلات ودعم الأسرة", (Category.FAMILY_SUPPORT,)),
    Group("charity", "Religious and charitable", "الديني والخيري", (Category.CHARITY,)),
    Group("entertainment", "Entertainment", "الترفيه", (Category.ENTERTAINMENT,)),
    Group("education", "Education", "التعليم", (Category.EDUCATION,)),
    Group("insurance", "Insurance", "التأمين", (Category.INSURANCE,)),
    Group("banking", "Banking and financial costs", "الرسوم والتكاليف البنكية", (Category.FEES, Category.INTEREST)),
    Group("debt", "Debt flows", "تدفقات الديون", ()),
    Group("income", "Income", "الدخل", (Category.INCOME, Category.CASHBACK, Category.REFUNDS)),
    Group("cash_transfers", "Cash and transfers", "النقد والتحويلات", (Category.CASH, Category.TRANSFER)),
    Group("other", "Other", "أخرى", (Category.OTHER,)),
)
GROUP_OF: dict[Category, str] = {c: g.id for g in GROUPS for c in g.categories}
# Transaction types that make a transfer row a debt flow (money lent, borrowed, repaid, a card settled).
DEBT_TYPES = frozenset({TransactionType.LOAN_RECEIVED, TransactionType.LOAN_PRINCIPAL, TransactionType.CARD_PAYMENT,
                        TransactionType.RECEIVABLE_COLLECTION, TransactionType.PAYABLE_SETTLEMENT})

CATEGORY_NAMES: dict[Category, tuple[str, str]] = {
    Category.INCOME: ("Income", "الدخل"), Category.CASHBACK: ("Cashback", "استرداد نقدي"), Category.REFUNDS: ("Refunds", "المبالغ المستردة"),
    Category.TRANSFER: ("Transfers", "التحويلات"), Category.GROCERIES: ("Groceries", "البقالة"), Category.DINING: ("Eating out", "المطاعم"),
    Category.FOOD: ("Food", "الطعام"), Category.ROOM_RENT: ("Room rent", "إيجار الغرفة"), Category.TRANSPORT: ("Transport", "المواصلات"),
    Category.FUEL: ("Fuel", "الوقود"), Category.UTILITIES: ("Utilities", "المرافق"), Category.TELECOM: ("Phone and internet", "الهاتف والإنترنت"),
    Category.HOUSING: ("Housing", "السكن"), Category.SHOPPING: ("Shopping", "التسوق"), Category.HEALTH: ("Health", "الصحة"),
    Category.EDUCATION: ("Education", "التعليم"), Category.ENTERTAINMENT: ("Entertainment", "الترفيه"),
    Category.SUBSCRIPTIONS: ("Subscriptions", "الاشتراكات"), Category.TRAVEL: ("Travel", "السفر"), Category.INSURANCE: ("Insurance", "التأمين"),
    Category.CLOTHING: ("Clothing and laundry", "الملابس والغسيل"), Category.FAMILY: ("Family and children", "الأسرة والأطفال"),
    Category.PERSONAL_CARE: ("Personal care", "العناية الشخصية"), Category.FAMILY_SUPPORT: ("Family support", "دعم الأسرة"),
    Category.CHARITY: ("Religious and charitable", "الديني والخيري"), Category.FEES: ("Bank fees", "الرسوم البنكية"),
    Category.INTEREST: ("Interest", "الفوائد"), Category.CASH: ("Cash", "النقد"), Category.OTHER: ("Other", "أخرى"),
}
ESSENTIAL_CATEGORIES = frozenset({
    Category.GROCERIES, Category.FOOD, Category.HOUSING, Category.ROOM_RENT, Category.UTILITIES, Category.TELECOM, Category.TRANSPORT,
    Category.FUEL, Category.HEALTH, Category.EDUCATION, Category.INSURANCE, Category.FAMILY, Category.FEES, Category.INTEREST,
})


def _s(slug: str, parent: Category, en: str, ar: str, essential: bool | None = None, *words: str) -> BuiltInSub:
    return BuiltInSub(BUILT_IN_SUB_PREFIX + slug, parent, en, ar, parent in ESSENTIAL_CATEGORIES if essential is None else essential, words)


_C = Category
BUILT_IN_SUBCATEGORIES: tuple[BuiltInSub, ...] = (
    # Food and drinks
    _s("supermarket", _C.GROCERIES, "Supermarket", "سوبرماركت", None, "supermarket", "hypermarket", "carrefour", "lulu", "panda", "spinneys",
       "danube", "tamimi", "union coop", "othaim", "bin dawood", "choithrams", "grandiose", "viva", "west zone", "al maya", "waitrose"),
    _s("vegetables", _C.GROCERIES, "Vegetables", "خضار", None, "vegetable", "vegetables", "خضار", "خضروات"),
    _s("fruit", _C.GROCERIES, "Fruit", "فواكه", None, "fruit", "fruits", "فواكه", "فاكهة"),
    _s("meat", _C.GROCERIES, "Meat and fish", "لحوم وأسماك", None, "butcher", "meat", "fish market", "ملحمة", "لحوم", "سمك"),
    _s("water", _C.GROCERIES, "Drinking water", "مياه الشرب", None, "mai dubai", "masafi", "al ain water", "water delivery", "مياه"),
    _s("groceries_other", _C.GROCERIES, "Other groceries", "بقالة أخرى", None, "grocery", "baqala", "بقالة"),
    _s("restaurants", _C.DINING, "Restaurants", "مطاعم", False, "restaurant", "restaurants", "مطعم"),
    _s("fast_food", _C.DINING, "Fast food", "وجبات سريعة", False, "mcdonald", "mcdonalds", "kfc", "burger king", "hardees", "subway",
       "pizza hut", "dominos", "shawarma", "شاورما"),
    _s("cafeteria", _C.DINING, "Cafeteria", "كافتيريا", False, "cafeteria", "كافتيريا"),
    _s("coffee", _C.DINING, "Coffee", "قهوة", False, "starbucks", "costa", "tim hortons", "coffee", "cafe", "قهوة"),
    _s("tea", _C.DINING, "Tea", "شاي", False, "karak", "chai", "tea", "شاي", "كرك"),
    _s("snacks", _C.DINING, "Snacks and sweets", "وجبات خفيفة وحلويات", False, "snack", "snacks", "bakery", "sweets", "حلويات", "مخبز"),
    _s("delivery", _C.DINING, "Food delivery", "توصيل الطعام", False, "talabat", "deliveroo", "zomato", "noon food", "hungerstation", "jahez",
       "the chefz", "careem food", "keeta"),
    _s("work_meals", _C.FOOD, "Work meals", "وجبات العمل", True, "canteen", "mess bill", "mess food", "work meal", "office lunch"),
    _s("meals", _C.FOOD, "Meals", "وجبات", True, "meal", "meals", "lunch", "dinner", "breakfast", "غداء", "عشاء", "فطور"),
    # Housing
    _s("rent", _C.HOUSING, "Apartment rent", "إيجار الشقة", True, "apartment rent", "flat rent", "house rent", "ejari", "landlord", "rent", "إيجار"),
    _s("deposit", _C.HOUSING, "Deposits", "تأمينات", True, "security deposit", "rent deposit", "deposit"),
    _s("furniture", _C.HOUSING, "Furniture", "أثاث", False, "ikea", "home centre", "pan emirates", "furniture", "أثاث"),
    _s("appliances", _C.HOUSING, "Appliances", "أجهزة منزلية", False, "appliance", "appliances", "washing machine", "fridge"),
    _s("repairs", _C.HOUSING, "Repairs and maintenance", "إصلاحات وصيانة", True, "maintenance", "repair", "plumber", "electrician", "ac service"),
    _s("household_supplies", _C.HOUSING, "Household supplies", "مستلزمات المنزل", True, "household"),
    _s("cleaning", _C.HOUSING, "Cleaning", "تنظيف", False, "cleaning", "maid", "justmop", "تنظيف"),
    _s("bed_space", _C.ROOM_RENT, "Bed space", "سرير مشترك", True, "bedspace", "bed space", "room sharing", "سكن مشترك"),
    _s("room_deposit", _C.ROOM_RENT, "Room deposit", "تأمين الغرفة", True, "room deposit"),
    _s("electricity", _C.UTILITIES, "Electricity", "كهرباء", True, "dewa", "sewa", "addc", "aadc", "fewa", "etihad water", "electricity", "كهرباء"),
    _s("water_bill", _C.UTILITIES, "Water", "مياه", True, "water bill", "water"),
    _s("gas", _C.UTILITIES, "Gas", "غاز", True, "lootah", "gas cylinder", "gas", "غاز"),
    _s("cooling", _C.UTILITIES, "District cooling", "التبريد المركزي", True, "empower", "tabreed", "chiller", "cooling"),
    # Clothing and laundry
    _s("clothes", _C.CLOTHING, "Clothes", "ملابس", False, "max fashion", "splash", "h&m", "zara", "r&b fashion", "clothes", "clothing", "ملابس"),
    _s("shoes", _C.CLOTHING, "Shoes", "أحذية", False, "shoes", "footwear", "shoe", "أحذية"),
    _s("tailoring", _C.CLOTHING, "Tailoring", "خياطة", False, "tailor", "tailoring", "خياط"),
    _s("laundry", _C.CLOTHING, "Laundry", "غسيل", True, "laundry", "مغسلة"),
    _s("dry_cleaning", _C.CLOTHING, "Dry cleaning", "تنظيف جاف", False, "dry clean", "dry cleaning"),
    _s("ironing", _C.CLOTHING, "Ironing", "كي الملابس", False, "ironing"),
    _s("bedding", _C.CLOTHING, "Bedding", "مفارش", False, "bedding", "blanket", "pillow"),
    _s("clothing_repairs", _C.CLOTHING, "Repairs and alterations", "تصليح وتعديل", False, "alteration", "shoe repair"),
    # Mobile and communication
    _s("recharge", _C.TELECOM, "Mobile recharge", "شحن الرصيد", True, "mobile recharge", "prepaid recharge", "recharge", "شحن"),
    _s("prepaid", _C.TELECOM, "Prepaid plan", "باقة مسبقة الدفع", True, "prepaid"),
    _s("postpaid", _C.TELECOM, "Postpaid bill", "فاتورة آجلة", True, "postpaid"),
    _s("internet", _C.TELECOM, "Internet", "إنترنت", True, "internet", "broadband", "إنترنت"),
    _s("home_wifi", _C.TELECOM, "Home Wi-Fi", "واي فاي المنزل", True, "home wifi", "home wi-fi", "home internet", "wifi"),
    _s("international_calls", _C.TELECOM, "International calls", "مكالمات دولية", False, "international call", "calling card", "botim"),
    _s("phone_accessories", _C.TELECOM, "Phone accessories", "إكسسوارات الهاتف", False, "charger", "phone case", "screen protector"),
    _s("phones", _C.TELECOM, "Phones", "هواتف", False, "iphone", "phone purchase", "mobile phone"),
    _s("phone_repairs", _C.TELECOM, "Phone repairs", "تصليح الهاتف", False, "phone repair", "mobile repair"),
    # Transport
    _s("car_payment", _C.TRANSPORT, "Car payment", "قسط السيارة", True, "car payment", "car installment", "auto loan"),
    _s("car_repairs", _C.TRANSPORT, "Car repairs", "تصليح السيارة", True, "garage", "car repair", "auto repair", "workshop"),
    _s("servicing", _C.TRANSPORT, "Servicing", "صيانة دورية", True, "car service", "oil change", "servicing"),
    _s("tyres", _C.TRANSPORT, "Tyres", "إطارات", True, "tyre", "tyres", "tire", "tires", "إطارات"),
    _s("car_insurance", _C.TRANSPORT, "Car insurance", "تأمين السيارة", True, "car insurance", "motor insurance"),
    _s("registration", _C.TRANSPORT, "Registration", "تسجيل المركبة", True, "registration", "mulkiya", "tasjeel"),
    _s("parking", _C.TRANSPORT, "Parking", "مواقف", True, "parking", "mawaqif", "مواقف"),
    _s("tolls", _C.TRANSPORT, "Tolls", "رسوم الطرق", True, "salik", "darb", "toll"),
    _s("taxi", _C.TRANSPORT, "Taxi", "تاكسي", True, "taxi", "dtc", "arabia taxi", "تاكسي"),
    _s("ride_hailing", _C.TRANSPORT, "Ride-hailing", "تطبيقات النقل", True, "careem", "uber", "bolt", "yango"),
    _s("bus", _C.TRANSPORT, "Bus", "حافلة", True, "bus"),
    _s("train", _C.TRANSPORT, "Metro and train", "المترو والقطار", True, "metro", "train", "tram", "nol"),
    _s("fines", _C.TRANSPORT, "Traffic fines", "مخالفات مرورية", True, "traffic fine", "fine", "مخالفة"),
    _s("car_wash", _C.TRANSPORT, "Car wash", "غسيل السيارة", False, "car wash"),
    # Family and children
    _s("formula", _C.FAMILY, "Formula and baby food", "حليب الأطفال", True, "baby formula", "infant formula", "aptamil", "formula"),
    _s("diapers", _C.FAMILY, "Diapers", "حفاضات", True, "diaper", "diapers", "pampers", "huggies", "حفاضات"),
    _s("baby_clothes", _C.FAMILY, "Baby clothes", "ملابس الأطفال", False, "mothercare", "babyshop", "baby shop", "baby clothes"),
    _s("toys", _C.FAMILY, "Toys", "ألعاب", False, "toys r us", "hamleys", "toys", "toy"),
    _s("childcare", _C.FAMILY, "Childcare", "رعاية الأطفال", True, "nursery", "daycare", "childcare", "حضانة"),
    _s("child_essentials", _C.FAMILY, "Children's essentials", "مستلزمات الأطفال", True, "baby essentials", "kids essentials"),
    _s("child_health", _C.FAMILY, "Children's health", "صحة الأطفال", True, "pediatric", "paediatric", "kids clinic"),
    _s("child_support", _C.FAMILY, "Child support", "نفقة الأطفال", True, "child support", "نفقة"),
    # Healthcare
    _s("doctor", _C.HEALTH, "Doctor", "طبيب", True, "doctor", "clinic", "consultation", "عيادة"),
    _s("medicine", _C.HEALTH, "Medicine", "أدوية", True, "medicine", "medication", "دواء"),
    _s("pharmacy", _C.HEALTH, "Pharmacy", "صيدلية", True, "pharmacy", "aster pharmacy", "life pharmacy", "boots", "صيدلية"),
    _s("dental", _C.HEALTH, "Dental", "أسنان", True, "dental", "dentist", "أسنان"),
    _s("optical", _C.HEALTH, "Optical", "بصريات", True, "optical", "optician", "magrabi", "yateem", "glasses"),
    _s("hospital", _C.HEALTH, "Hospital", "مستشفى", True, "hospital", "mediclinic", "nmc", "مستشفى"),
    _s("lab", _C.HEALTH, "Lab tests", "تحاليل", True, "laboratory", "lab test", "pcr", "تحاليل"),
    _s("health_insurance", _C.HEALTH, "Health insurance", "تأمين صحي", True, "health insurance", "medical insurance"),
    _s("health_emergency", _C.HEALTH, "Emergency care", "طوارئ", True, "emergency", "ambulance", "طوارئ"),
    # Personal care
    _s("barber", _C.PERSONAL_CARE, "Barber", "حلاق", False, "barber", "barbershop", "gents salon", "حلاق"),
    _s("salon", _C.PERSONAL_CARE, "Salon and spa", "صالون وسبا", False, "ladies salon", "salon", "spa", "صالون"),
    _s("grooming", _C.PERSONAL_CARE, "Grooming", "عناية شخصية", False, "grooming"),
    _s("toiletries", _C.PERSONAL_CARE, "Toiletries", "أدوات النظافة", True, "toiletries", "shampoo", "soap"),
    _s("hygiene", _C.PERSONAL_CARE, "Hygiene", "النظافة الشخصية", True, "hygiene"),
    _s("cosmetics", _C.PERSONAL_CARE, "Cosmetics", "مستحضرات التجميل", False, "faces", "sephora", "cosmetics", "makeup"),
    _s("fitness", _C.PERSONAL_CARE, "Fitness", "لياقة", False, "fitness first", "gym", "fitness"),
    # Shopping
    _s("electronics", _C.SHOPPING, "Electronics", "إلكترونيات", False, "sharaf dg", "emax", "jumbo", "virgin megastore", "electronics"),
    _s("accessories", _C.SHOPPING, "Accessories", "إكسسوارات", False, "accessories", "watch"),
    _s("online", _C.SHOPPING, "Online shopping", "تسوق إلكتروني", False, "amazon", "noon", "namshi", "aliexpress", "shein", "temu"),
    _s("household", _C.SHOPPING, "Household items", "أدوات منزلية", False, "ace hardware", "dragon mart", "daiso"),
    _s("gifts", _C.SHOPPING, "Gifts", "هدايا", False, "gift", "gifts", "flowers", "هدية"),
    _s("installment_purchase", _C.SHOPPING, "Installment payment", "دفعة تقسيط", False, "tamara", "tabby", "postpay", "cashew", "تمارا", "تابي"),
    _s("general", _C.SHOPPING, "General shopping", "تسوق عام", False, "mall", "centrepoint"),
    _s("streaming", _C.SUBSCRIPTIONS, "Streaming", "البث", False, "netflix", "shahid", "osn", "disney", "youtube premium", "starzplay"),
    _s("music", _C.SUBSCRIPTIONS, "Music", "الموسيقى", False, "spotify", "anghami", "apple music"),
    _s("cloud", _C.SUBSCRIPTIONS, "Cloud storage", "التخزين السحابي", False, "icloud", "google storage", "google one", "dropbox"),
    _s("software", _C.SUBSCRIPTIONS, "Software and apps", "البرامج والتطبيقات", False, "microsoft", "adobe", "chatgpt", "openai", "apple.com"),
    _s("memberships", _C.SUBSCRIPTIONS, "Memberships", "العضويات", False, "membership", "amazon prime", "noon vip", "careem plus"),
    # Travel
    _s("flights", _C.TRAVEL, "Flights", "رحلات الطيران", False, "emirates airline", "flydubai", "etihad airways", "air arabia", "saudia", "indigo",
       "airline", "airlines", "flight", "طيران"),
    _s("hotels", _C.TRAVEL, "Hotels", "فنادق", False, "hotel", "booking.com", "agoda", "airbnb", "expedia", "فندق"),
    _s("visa", _C.TRAVEL, "Visa", "تأشيرة", False, "visa fee", "visa application", "vfs", "تأشيرة"),
    _s("baggage", _C.TRAVEL, "Baggage", "أمتعة", False, "baggage", "excess baggage"),
    _s("travel_transport", _C.TRAVEL, "Local transport abroad", "المواصلات في السفر", False),
    _s("travel_insurance", _C.TRAVEL, "Travel insurance", "تأمين السفر", False, "travel insurance"),
    _s("trips", _C.TRAVEL, "Trips and tours", "رحلات وجولات", False, "tour", "tours", "excursion", "trip"),
    # Family transfers and support
    _s("money_home", _C.FAMILY_SUPPORT, "Money home", "مصروف الأهل", True, "money home", "تحويل للأهل", "للأهل", "مصروف الأهل"),
    _s("parents", _C.FAMILY_SUPPORT, "Parents", "الوالدين", True, "father", "mother", "parents", "الوالد", "الوالدة"),
    _s("spouse", _C.FAMILY_SUPPORT, "Spouse", "الزوج أو الزوجة", True, "wife", "husband", "spouse", "الزوجة", "الزوج"),
    _s("relatives", _C.FAMILY_SUPPORT, "Relatives", "الأقارب", False, "brother", "sister", "relative", "cousin"),
    _s("family_gifts", _C.FAMILY_SUPPORT, "Gifts to family", "هدايا للأهل", False, "family gift"),
    _s("remittance", _C.FAMILY_SUPPORT, "Remittance", "حوالة", True, "al ansari exchange", "al ansari", "al fardan", "lulu exchange", "lulu money",
       "western union", "moneygram", "xpress money", "remitly", "exchange house", "remittance", "حوالة"),
    # Religious and charitable
    _s("zakat", _C.CHARITY, "Zakat", "الزكاة", True, "zakat", "zakah", "زكاة", "الزكاة"),
    _s("sadaqah", _C.CHARITY, "Sadaqah", "الصدقة", False, "sadaqah", "sadaqa", "sadaka", "صدقة"),
    _s("donations", _C.CHARITY, "Donations", "التبرعات", False, "donation", "donations", "charity", "red crescent", "dubai cares", "awqaf", "تبرع"),
    _s("umrah", _C.CHARITY, "Umrah", "العمرة", False, "umrah", "عمرة"),
    _s("hajj", _C.CHARITY, "Hajj", "الحج", False, "hajj"),
    _s("religious_purchases", _C.CHARITY, "Religious purchases", "مشتريات دينية", False, "quran", "prayer mat", "مصحف"),
    # Entertainment
    _s("cinema", _C.ENTERTAINMENT, "Cinema", "السينما", False, "vox", "reel cinemas", "novo", "cinema"),
    _s("events", _C.ENTERTAINMENT, "Events and tickets", "الفعاليات والتذاكر", False, "ticketmaster", "platinumlist", "concert", "tickets"),
    _s("games", _C.ENTERTAINMENT, "Games", "الألعاب", False, "playstation", "steam", "xbox", "nintendo"),
    _s("outings", _C.ENTERTAINMENT, "Outings and parks", "النزهات والمنتزهات", False, "dubai parks", "img worlds", "theme park", "park"),
    _s("sports", _C.ENTERTAINMENT, "Sports", "الرياضة", False, "padel", "football", "sports"),
    # Education
    _s("school_fees", _C.EDUCATION, "School fees", "الرسوم المدرسية", True, "school fee", "school fees", "gems", "taaleem", "school"),
    _s("tuition", _C.EDUCATION, "Tuition", "الدروس", True, "tuition"),
    _s("university", _C.EDUCATION, "University", "الجامعة", True, "university", "college"),
    _s("courses", _C.EDUCATION, "Courses", "الدورات", False, "course", "udemy", "coursera", "training"),
    _s("books", _C.EDUCATION, "Books and supplies", "الكتب والقرطاسية", True, "books", "stationery", "bookshop", "magrudy", "kinokuniya"),
    # Insurance
    _s("insurance_health", _C.INSURANCE, "Health insurance", "التأمين الصحي", True, "health insurance", "medical insurance", "daman"),
    _s("insurance_car", _C.INSURANCE, "Car insurance", "تأمين السيارة", True, "car insurance", "motor insurance"),
    _s("insurance_life", _C.INSURANCE, "Life and takaful", "الحياة والتكافل", True, "life insurance", "takaful"),
    _s("insurance_home", _C.INSURANCE, "Home insurance", "تأمين المنزل", True, "home insurance"),
    # Banking and financial costs
    _s("bank_fee", _C.FEES, "Bank fees", "رسوم بنكية", True, "service charge", "monthly fee", "account fee", "bank charge"),
    _s("atm_fee", _C.FEES, "ATM fees", "رسوم الصراف", True, "atm fee"),
    _s("annual_card_fee", _C.FEES, "Annual card fee", "رسوم البطاقة السنوية", True, "annual fee", "card fee"),
    _s("fx_markup", _C.FEES, "FX markup", "رسوم صرف العملات", True, "foreign transaction fee", "international transaction fee", "cross border fee",
       "currency conversion charge", "fx markup", "fx fee"),
    _s("late_fee", _C.FEES, "Late fees", "رسوم التأخير", True, "late fee", "late payment"),
    _s("cash_advance_fee", _C.FEES, "Cash advance fee", "رسوم السحب النقدي", True, "cash advance"),
    _s("financing_fee", _C.FEES, "Financing fees", "رسوم التمويل", True, "processing fee", "financing fee", "admin fee"),
    _s("interest_charged", _C.INTEREST, "Interest charged", "فوائد مدفوعة", True, "finance charge", "interest charge", "profit charge"),
    _s("interest_received", _C.INTEREST, "Interest received", "فوائد مستلمة", True, "interest earned", "interest credit", "profit credit"),
    # Income
    _s("salary", _C.INCOME, "Salary", "الراتب", True, "salary", "payroll", "wps", "راتب"),
    _s("overtime", _C.INCOME, "Overtime", "العمل الإضافي", True, "overtime"),
    _s("bonus", _C.INCOME, "Bonus", "المكافأة", True, "bonus", "incentive", "مكافأة"),
    _s("allowances", _C.INCOME, "Allowances", "البدلات", True, "allowance", "بدل"),
    _s("reimbursement", _C.INCOME, "Reimbursement", "استرداد المصاريف", True, "reimbursement", "expense claim"),
    _s("gifts_received", _C.INCOME, "Gifts received", "هدايا مستلمة", True, "gift received"),
    _s("side_income", _C.INCOME, "Side income", "دخل إضافي", True, "freelance", "side income"),
    _s("dividends", _C.INCOME, "Dividends", "توزيعات الأرباح", True, "dividend"),
    _s("other_income", _C.INCOME, "Other income", "دخل آخر", True),
    # Cash and transfers, debt flows
    _s("atm_withdrawal", _C.CASH, "Cash withdrawal", "سحب نقدي", True, "atm", "cash withdrawal", "cash wdl", "سحب نقدي"),
    _s("cash_deposit", _C.CASH, "Cash deposit", "إيداع نقدي", True, "cash deposit", "cdm", "إيداع نقدي"),
    _s("cash_spending", _C.CASH, "Cash spending", "إنفاق نقدي", True),
    _s("own_transfer", _C.TRANSFER, "Own-account transfer", "تحويل بين حساباتي", True, "own account", "own accounts", "between your accounts"),
    _s("wallet_transfer", _C.TRANSFER, "Wallet transfer", "تحويل محفظة", True, "wallet", "payit", "apple cash", "careem pay"),
    _s("card_settlement", _C.TRANSFER, "Card settlement", "سداد البطاقة", True, "credit card payment", "card payment", "card repayment", "cc payment"),
    _s("loan_repayment", _C.TRANSFER, "Loan repayment", "سداد قرض", True, "loan repayment", "loan installment", "emi"),
    _s("loan_received", _C.TRANSFER, "Loan received", "قرض مستلم", True, "loan disbursement", "loan credit"),
    _s("money_lent", _C.TRANSFER, "Money lent", "مال أقرضته", True),
    _s("money_borrowed", _C.TRANSFER, "Money borrowed", "مال اقترضته", True),
    _s("collection", _C.TRANSFER, "Money collected back", "مال مسترد", True),
    _s("installment_settlement", _C.TRANSFER, "Installment plan settlement", "تسوية خطة التقسيط", True),
    # Other
    _s("pets", _C.OTHER, "Pets", "الحيوانات الأليفة", False, "pet shop", "vet", "veterinary", "pets"),
    _s("legal", _C.OTHER, "Legal", "قانوني", False, "lawyer", "legal", "notary", "محامي"),
    _s("government", _C.OTHER, "Government fees", "رسوم حكومية", True, "amer", "tasheel", "tawjeeh", "gdrfa", "icp", "emirates id", "absher",
       "muqeem", "jawazat", "dubai police"),
    _s("documents", _C.OTHER, "Documents", "المستندات", True, "attestation", "typing centre", "typing center", "passport", "translation"),
    _s("emergency", _C.OTHER, "Emergency", "الطوارئ", True),
    _s("uncategorised", _C.OTHER, "Uncategorised", "غير مصنف", False),
)
BUILT_IN_SUB_BY_ID: dict[str, BuiltInSub] = {item.id: item for item in BUILT_IN_SUBCATEGORIES}


def _sub_patterns() -> dict[Category, tuple[tuple[str, re.Pattern[str]], ...]]:
    out: dict[Category, tuple[tuple[str, re.Pattern[str]], ...]] = {}
    for item in BUILT_IN_SUBCATEGORIES:
        if item.words:
            pattern = re.compile("|".join(phrase_pattern(w).pattern for w in item.words))
            out[item.parent] = out.get(item.parent, ()) + ((item.id, pattern),)
    return out


_SUB_PATTERNS = _sub_patterns()


def is_built_in_sub(sub_id: str | None) -> bool:
    return bool(sub_id) and str(sub_id).startswith(BUILT_IN_SUB_PREFIX)


def built_in_subcategory(category: Category, description: str) -> str | None:
    """The built-in subcategory under ``category`` that the description's words name, else ``None`` (first in order wins)."""
    text = fold(description)
    for sub_id, pattern in _SUB_PATTERNS.get(category, ()):
        if pattern.search(text):
            return sub_id
    return None


def subcategory_parent(sub_id: str) -> Category | None:
    found = BUILT_IN_SUB_BY_ID.get(sub_id)
    return found.parent if found is not None else None


def group_of(category: Category, kind: TransactionType | None = None) -> str:
    """The group a row belongs to: by its category, except that a row whose type is a loan received or repaid, a card
    settled or money collected or settled with a person is a debt flow."""
    if kind in DEBT_TYPES:
        return "debt"
    return GROUP_OF.get(category, "other")


def is_essential(category: Category, sub_id: str | None = None) -> bool:
    found = BUILT_IN_SUB_BY_ID.get(sub_id or "")
    return found.essential if found is not None else category in ESSENTIAL_CATEGORIES


__all__ += ["BUILT_IN_SUBCATEGORIES", "BUILT_IN_SUB_BY_ID", "BUILT_IN_SUB_PREFIX", "BuiltInSub", "CATEGORY_NAMES", "DEBT_TYPES",
            "ESSENTIAL_CATEGORIES", "GROUPS", "GROUP_OF", "Group", "built_in_subcategory", "group_of", "is_built_in_sub", "is_essential",
            "subcategory_parent"]
