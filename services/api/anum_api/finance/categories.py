"""Rule-based transaction categories (docs/finance.md#categories). No AI, no network.

Order of precedence: the owner's own rules (``FinanceSettings.category_rules``, first
match wins), then the built-in keyword rules below (common UAE merchants and statement
wording), then ``income`` for money in or ``other`` for money out. A category the owner
picks by hand (``manual``) is never changed by a rule afterwards.

Matching is on the cleaned, lower-case description: a keyword matches as a whole word
or phrase, so "du" matches "DU TELECOM" but not "dubai".
"""

from __future__ import annotations

import re
from decimal import Decimal

from .models import Category, CategoryRule, CategorySource

# (category, keywords). Earlier entries win, so the specific ones come first.
BUILT_IN: tuple[tuple[Category, tuple[str, ...]], ...] = (
    (Category.CASHBACK, ("cashback", "cash back", "cash-back", "cashback earned", "cashback credit", "cash back reward", "مكافأة نقدية", "استرداد نقدي")),
    (Category.REFUNDS, ("refund", "refunded", "merchant credit", "purchase reversal", "reversal of purchase", "رد المبلغ", "استرجاع")),
    (Category.FEES, ("foreign transaction fee", "international transaction fee", "cross border fee", "currency conversion charge", "fx markup", "fx fee", "atm fee", "annual fee", "monthly fee", "late fee", "finance charge", "interest", "vat charge", "ضريبة رسوم")),
    # Explicit personal expenses must win over generic transfer/service-charge keywords.
    (Category.ROOM_RENT, ("room rent", "rent room", "room rental", "monthly room", "room payment",
                          "bedspace", "bed space", "accommodation rent", "room sharing",
                          "ايجار غرفة", "إيجار غرفة", "إيجار السكن", "سكن مشترك", "kiraya room")),
    (Category.FOOD, ("food", "food order", "food delivery", "meal", "meals", "lunch", "dinner", "breakfast",
                     "canteen", "mess bill", "mess food", "وجبات", "طعام", "غداء", "عشاء", "فطور", "کھانا")),
    (Category.TRANSFER, ("transfer", "trf", "own account", "ipp", "aani", "remittance", "تحويل")),
    (Category.FEES, ("fee", "fees", "charges", "commission", "interest charge", "late payment", "vat on", "رسوم")),
    (Category.CASH, ("atm", "cash withdrawal", "cash wdl", "سحب نقدي")),
    (Category.INCOME, ("salary", "payroll", "wps", "dividend", "راتب")),
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
)

_COMPILED = tuple(
    (category, re.compile(r"(?<![\w&])(?:" + "|".join(re.escape(word) for word in words) + r")(?![\w&])", re.IGNORECASE))
    for category, words in BUILT_IN
)


def categorize(description: str, amount: Decimal, rules: list[CategoryRule] | None = None) -> tuple[Category, CategorySource]:
    """The category for a transaction, and what decided it."""
    text = " ".join(description.lower().split())
    for rule in rules or []:
        if rule.match.strip().lower() in text:
            return rule.category, CategorySource.OWNER_RULE
    for category, pattern in _COMPILED:
        if pattern.search(text):
            # "refund" or "cashback" on money out is a fee reversal at most: keep it other.
            if category in {Category.INCOME, Category.CASHBACK, Category.REFUNDS} and amount < 0:
                continue
            return category, CategorySource.RULE
    if amount > 0:
        return Category.INCOME, CategorySource.NONE
    return Category.OTHER, CategorySource.NONE


# Categories that are not spending: excluded from "spend by category" and "spend".
NOT_SPEND = frozenset({Category.TRANSFER, Category.INCOME, Category.CASHBACK, Category.REFUNDS})

# Do not mutate account money based only on a merchant label. Incoming rent or
# outgoing cashback should be reviewed against the actual debit/credit column.
_PAYMENT_WORDS = re.compile(
    r"(?i)(?:room[\\s-]*rent|room[\\s-]*rental|bed[\\s-]*space|rent[\\s-]*payment|"
    r"monthly[\\s-]*rent|accommodation[\\s-]*rent|rent[\\s-]*room|"
    r"إيجار[\\s-]*غرفة|ايجار[\\s-]*غرفة)"
)


def suspected_direction(description: str, amount: Decimal) -> str | None:
    """A review hint only. Never flip a ledger entry without human confirmation."""
    if amount > 0 and _PAYMENT_WORDS.search(description):
        return "Room rent is shown as incoming. Check the original bank debit/credit and correct to money out if paid."
    return None
