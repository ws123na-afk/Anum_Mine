"""The owner's classification rules, merchant names and what may be automated (docs/finance.md#rules-and-learning).

Pure: no store, clock or network. ``categories.classify_text`` runs the rules; ``rules_store`` keeps them;
``rules_api`` exposes them. The engine's order is fixed:

1. the owner's approved rules (``FinanceRule``, first match by priority wins; they replace the plain
   ``FinanceSettings.category_rules`` phrase list, which is still read until the first rule is saved and is then
   copied in once as ``imported`` rules),
2. the built-in keyword rules (``categories.BUILT_IN``),
3. historical reuse (:func:`history_suggestion`): the category the owner chose before for the same merchant, offered
   as a suggestion with a confidence, never applied unless an automation policy the owner turned on says so,
4. an optional AI suggestion, only on request, only when a model is set up: shown, never applied by itself and
   never learned from.

A rule matches on the merchant (after the owner's aliases), on description words with word boundaries, an amount
range, the direction, the account, the bank or sender and a reference written as plain text (never a regular
expression from the owner). It gives a category, an optional subcategory and an optional transaction type. It never
changes an amount, a currency or a date, and nothing a rule or a policy does touches a row the owner set by hand
(``txn_types.owner_locked``).

Every change is a new version (``rule_id`` plus ``version``); an approved version is what runs. A version is proposed
first and approved with the payload hash the owner was shown (the Guardian's binding rule). An agent may propose,
never approve. Reverting is a new version with an older version's content.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .merchant_match import STOP_WORDS, _fold
from .models import Category, CategoryRule, CategorySource, Transaction, TransactionType, _Owned

MAX_RULES = 300  # rule lineages per person
MAX_VERSIONS = 50  # versions kept per rule
MAX_ALIASES = 500
MAX_SUBCATEGORIES = 200
MAX_WORDS = 8
# Categories a rule may give only to money in (the sign guard, as for the built-in rules).
MONEY_IN_ONLY = frozenset({Category.INCOME, Category.CASHBACK, Category.REFUNDS})
# Phrases too broad to learn from on their own (a word rule with only these would file unrelated rows).
TOO_BROAD = frozenset({"rent", "food", "transfer", "bank", "payment", "card", "cash", "salary", "room", "debit", "credit", "purchase", "pos",
                       "شراء", "تحويل", "حوالة", "دفع", "سداد"})

Provenance = Literal["owner", "learned", "imported", "agent"]
RuleStatus = Literal["proposed", "approved", "rejected"]
Direction = Literal["any", "in", "out"]

# -- text ------------------------------------------------------------------------------------------

_DIACRITICS = re.compile("[ً-ٰٟۖ-ۭ]")
_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"})
_NOISE = re.compile(r"\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{1,2}:\d{2}(?::\d{2})?|[x*#•]{2,}\d{2,4}|\d{3,}", re.IGNORECASE)
_BOUNDARY_START, _BOUNDARY_END = r"(?<![\w&])(?:", r")(?![\w&])"


def fold(text: str | None) -> str:
    """Lower case, Arabic letter forms and diacritics folded, invisible marks removed, spaces collapsed.

    "إيجار" and "ايجار" fold to one word, so a rule or alias typed either way matches both."""
    cleaned = _fold(text or "").lower()
    cleaned = _DIACRITICS.sub("", unicodedata.normalize("NFKC", cleaned)).translate(_ARABIC_FOLD)
    return " ".join(cleaned.split())


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """A whole-word pattern for a plain phrase (escaped: the owner never writes a regular expression)."""
    return re.compile(_BOUNDARY_START + re.escape(fold(phrase)) + _BOUNDARY_END)


def has_phrase(text_folded: str, phrase: str) -> bool:
    words = fold(phrase)
    return bool(words) and bool(phrase_pattern(words).search(text_folded))


def merchant_key(text: str | None) -> str:
    """The merchant a description names, without card numbers, dates, channel words, cities or legal forms.

    The first three meaningful words, in order: "POS 1234 CARREFOUR CITY CENTRE DUBAI AE" gives
    "carrefour city centre". Empty when the description names no merchant."""
    words = re.findall(r"[^\W_]+", _NOISE.sub(" ", fold(text)))
    kept: list[str] = []
    for word in words:
        if len(word) < 2 or any(ch.isdigit() for ch in word) or word in STOP_WORDS or fold(word) in _FOLDED_STOP:
            continue
        if word.startswith("ال") and len(word) > 4 and word[2:] not in _FOLDED_STOP:
            word = word[2:]
        kept.append(word)
        if len(kept) == 3:
            break
    return " ".join(kept)


_FOLDED_STOP = frozenset(fold(w) for w in STOP_WORDS)


class MerchantAlias(_Owned):
    """One spelling of a merchant the owner named: every description containing ``alias`` is ``merchant``."""

    id: str
    alias: str = Field(min_length=2, max_length=80)
    merchant: str = Field(min_length=2, max_length=80)
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AliasBook:
    """The owner's aliases, folded once. ``name`` resolves a description to its merchant."""

    entries: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, aliases: list[MerchantAlias]) -> AliasBook:
        # Longer spellings first, so "carrefour market" wins over "carrefour".
        pairs = sorted(((fold(a.alias), a.merchant) for a in aliases if fold(a.alias)), key=lambda p: -len(p[0]))
        return cls(tuple(pairs))

    def name(self, description: str | None) -> str | None:
        """The merchant the owner named for this description, else ``None``."""
        text = fold(description)
        for alias, merchant in self.entries:
            if has_phrase(text, alias):
                return merchant
        return None

    def key(self, description: str | None) -> str:
        """The merchant key: the owner's merchant name when an alias matches, else :func:`merchant_key`."""
        named = self.name(description)
        return fold(named) if named else merchant_key(description)


class Subcategory(_Owned):
    """A subcategory the owner made under one of the built-in categories (its ``parent``).

    Optional on a row: summaries keep grouping by the parent category, and a row without one is unchanged."""

    id: str
    parent: Category
    name: str = Field(min_length=1, max_length=60)
    archived: bool = False
    created_at: datetime
    updated_at: datetime


# -- rules -----------------------------------------------------------------------------------------


class RuleMatch(BaseModel):
    """What a rule matches. Every condition given must hold; at least one of merchant, words or reference is needed."""

    model_config = ConfigDict(extra="forbid")

    merchant: str | None = Field(default=None, max_length=80)
    words: list[str] = Field(default_factory=list, max_length=MAX_WORDS)
    reference: str | None = Field(default=None, max_length=60)  # plain text, never a regular expression
    direction: Direction = "any"
    min_amount: Decimal | None = Field(default=None, ge=0)  # on the absolute amount, in ``currency``
    max_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    account_ids: list[str] = Field(default_factory=list, max_length=20)  # empty: every account
    senders: list[str] = Field(default_factory=list, max_length=10)  # the bank or SMS sender; empty: any

    @field_validator("words", "senders")
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            words = " ".join(str(item).split())
            if not words:
                continue
            if len(words) > 80:
                raise ValueError("A word or phrase is at most 80 characters.")
            if words.casefold() not in {o.casefold() for o in out}:
                out.append(words)
        return out

    @field_validator("merchant", "reference")
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        words = " ".join((value or "").split())
        return words or None

    @model_validator(mode="after")
    def _specific(self) -> RuleMatch:
        if not (self.merchant or self.words or self.reference):
            raise ValueError("A rule needs a merchant, words from the description or a reference.")
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValueError("The lowest amount is above the highest amount.")
        if (self.min_amount is not None or self.max_amount is not None) and not self.currency:
            raise ValueError("An amount range needs its currency.")
        return self

    def too_broad(self) -> bool:
        """Only very common words and nothing else: such a rule would file unrelated rows."""
        if self.merchant or self.reference or self.account_ids or self.min_amount is not None or self.max_amount is not None:
            return False
        return all(fold(w) in {fold(b) for b in TOO_BROAD} or len(fold(w)) < 3 for w in self.words)


class RuleContent(BaseModel):
    """What one version of a rule says (the part the payload hash covers)."""

    model_config = ConfigDict(extra="forbid")

    match: RuleMatch
    category: Category
    subcategory_id: str | None = Field(default=None, max_length=80)
    transaction_type: TransactionType | None = None
    priority: int = Field(default=100, ge=0, le=1000)  # lower runs first; ties by the newest rule
    enabled: bool = True
    auto_apply: bool = False  # the owner's automation policy for this rule (docs/finance.md#automation-policies)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _sign(self) -> RuleContent:
        if self.category in MONEY_IN_ONLY and self.match.direction == "out":
            raise ValueError("Income, cashback and refunds are money in: such a rule cannot match money out.")
        if self.transaction_type == TransactionType.UNCATEGORIZED:
            raise ValueError("A rule files a row: choose a type other than uncategorized.")
        return self


class FinanceRule(_Owned):
    """One version of one rule. Versions are kept; the newest approved version of a rule is the one that runs."""

    id: str  # this version's id
    rule_id: str  # the rule (every version of it)
    version: int = Field(ge=1)
    status: RuleStatus
    content: RuleContent
    provenance: Provenance
    source_transaction_id: str | None = None  # the correction a learned rule came from
    confidence: Decimal = Field(default=Decimal("0.9"), ge=0, le=1)
    payload_hash: str
    created_by: str
    decided_by: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    reverted_from: int | None = None  # the version this one restores


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")


def payload_hash(*, rule_id: str, version: int, owner_user_id: str, content: RuleContent) -> str:
    """SHA-256 of exactly what the owner approves: this rule, this version, this content."""
    document = {"owner": owner_user_id, "rule_id": rule_id, "version": version, "content": json.loads(content.model_dump_json())}
    return hashlib.sha256(canonical_json(document)).hexdigest()


def from_legacy(rule: CategoryRule) -> RuleContent:
    """A plain phrase rule from ``FinanceSettings.category_rules`` as a rule: the phrase is its words."""
    return RuleContent(match=RuleMatch(words=[rule.match]), category=rule.category, priority=100)


@dataclass(frozen=True)
class Row:
    """What a rule reads from a row."""

    description: str
    amount: Decimal
    currency: str | None = None
    account_id: str | None = None
    sender: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class ActiveRule:
    """An approved rule ready to run: its content, folded once."""

    rule_id: str
    version: int
    content: RuleContent
    provenance: Provenance
    confidence: Decimal
    merchant: str = ""
    words: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    book: AliasBook | None = None  # the owner's aliases, attached by ``rules_store.owner_rules``

    @classmethod
    def of(cls, rule: FinanceRule) -> ActiveRule:
        return cls.from_content(rule.rule_id, rule.version, rule.content, rule.provenance, rule.confidence)

    @classmethod
    def from_content(cls, rule_id: str, version: int, content: RuleContent, provenance: Provenance = "owner",
                     confidence: Decimal = Decimal("0.9")) -> ActiveRule:
        return cls(rule_id, version, content, provenance, confidence, fold(content.match.merchant),
                   tuple(phrase_pattern(w) for w in content.match.words if fold(w)))

    def matches(self, row: Row, aliases: AliasBook | None = None) -> bool:
        m, content = self.content.match, self.content
        if not content.enabled:
            return False
        if content.category in MONEY_IN_ONLY and row.amount < 0:
            return False  # the sign guard
        if m.direction == "in" and row.amount <= 0 or m.direction == "out" and row.amount >= 0:
            return False
        if m.account_ids and row.account_id not in m.account_ids:
            return False  # a scoped rule never matches a row whose account it does not name (or an unknown account)
        if m.senders and not (row.sender and fold(row.sender) in {fold(s) for s in m.senders}):
            return False
        if m.currency and (m.min_amount is not None or m.max_amount is not None):
            if row.currency != m.currency:
                return False
            size = abs(row.amount)
            if m.min_amount is not None and size < m.min_amount or m.max_amount is not None and size > m.max_amount:
                return False
        text = fold(row.description)
        if self.merchant:
            book = aliases or self.book or AliasBook()
            named = book.name(row.description)
            if named is not None:
                if fold(named) != self.merchant:
                    return False
            elif not has_phrase(text, self.merchant):
                return False
        if any(not p.search(text) for p in self.words):
            return False
        if m.reference and fold(m.reference) not in fold(f"{row.description} {row.reference or ''}"):
            return False
        return True


def ordered(rules: list[ActiveRule]) -> list[ActiveRule]:
    """The order rules run in: priority (lower first), then the more specific, then the newest."""
    def specific(r: ActiveRule) -> int:
        m = r.content.match
        return (bool(m.account_ids) + bool(m.senders) + (m.min_amount is not None or m.max_amount is not None) + bool(m.reference)
                + bool(m.merchant) + len(m.words))
    return sorted(rules, key=lambda r: (r.content.priority, -specific(r), r.rule_id))


def first_match(rules: list[ActiveRule], row: Row, aliases: AliasBook | None = None) -> ActiveRule | None:
    for rule in rules:
        if rule.matches(row, aliases):
            return rule
    return None


def current_versions(versions: list[FinanceRule]) -> dict[str, FinanceRule]:
    """The newest approved version of each rule (the one that runs)."""
    out: dict[str, FinanceRule] = {}
    for v in versions:
        if v.status != "approved":
            continue
        if v.rule_id not in out or v.version > out[v.rule_id].version:
            out[v.rule_id] = v
    return out


def active_rules(versions: list[FinanceRule]) -> list[ActiveRule]:
    return ordered([ActiveRule.of(v) for v in current_versions(versions).values() if v.content.enabled])


# -- historical reuse --------------------------------------------------------------------------------

HISTORY_MIN = 2  # the owner chose the category for the same merchant at least this many times


@dataclass(frozen=True)
class HistoryHint:
    category: Category
    subcategory_id: str | None
    transaction_type: TransactionType | None
    confidence: Decimal
    merchant: str
    evidence: tuple[str, ...]  # the owner's earlier rows it learned from (newest first, at most 5)
    agree: int
    seen: int


def learnable(t: Transaction) -> bool:
    """A row the owner categorised themselves, not from an AI suggestion: the only rows history learns from."""
    return t.category_source == CategorySource.MANUAL and (t.type_reason or "") != "owner_ai"


def history_index(past: list[Transaction], aliases: AliasBook | None = None) -> dict[str, list[Transaction]]:
    """The owner's hand-categorised rows grouped by merchant key, built once for many lookups."""
    book = aliases or AliasBook()
    out: dict[str, list[Transaction]] = {}
    for t in past:
        if learnable(t):
            key = book.key(t.description)
            if key:
                out.setdefault(key, []).append(t)
    return out


def history_suggestion(row: Transaction, past: list[Transaction], aliases: AliasBook | None = None,
                       index: dict[str, list[Transaction]] | None = None) -> HistoryHint | None:
    """The category the owner gave the same merchant before, on the same side (money in or out), with a confidence.

    Reads only rows the owner categorised by hand (never a rule's guess, an AI suggestion or a match). The
    confidence grows with how often and how consistently the owner chose it: 2 of 2 is 0.7, 5 of 5 is 0.95,
    a split history is lower and under 0.5 gives nothing."""
    book = aliases or AliasBook()
    key = book.key(row.description)
    if not key:
        return None
    pool = index.get(key, []) if index is not None else [t for t in past if learnable(t) and book.key(t.description) == key]
    same = [t for t in pool if t.id != row.id and (t.amount < 0) == (row.amount < 0)]
    if len(same) < HISTORY_MIN:
        return None
    tally: dict[tuple[Category, str | None, TransactionType | None], list[Transaction]] = {}
    for t in sorted(same, key=lambda t: (t.posted_on, t.updated_at), reverse=True):
        tally.setdefault((t.category, t.subcategory_id, t.transaction_type), []).append(t)
    (category, sub, kind), rows = max(tally.items(), key=lambda kv: (len(kv[1]), kv[1][0].posted_on))
    share = Decimal(len(rows)) / Decimal(len(same))
    weight = min(Decimal(1), Decimal("0.75") + Decimal("0.05") * Decimal(len(rows)))
    confidence = (share * weight).quantize(Decimal("0.01"))
    if confidence < Decimal("0.5"):
        return None
    return HistoryHint(category, sub, kind, min(confidence, Decimal("0.95")), key, tuple(t.id for t in rows[:5]), len(rows), len(same))


# -- automation ------------------------------------------------------------------------------------------

# What an owner policy may automate, and what is never automated whatever a policy says.
AUTOMATABLE = frozenset({"classify", "notify"})
NEVER_AUTOMATED = frozenset({"post_adjustment", "delete_record", "change_bank_mapping", "approve_payment", "change_period"})


class AutomationRefused(PermissionError):
    """An action ANUM never runs on its own (docs/finance.md#automation-policies)."""


def require_automatable(action: str) -> None:
    """Refuse anything a policy may not do. Only classification and notifications are ever automated."""
    if action in NEVER_AUTOMATED or action not in AUTOMATABLE:
        raise AutomationRefused(f"'{action}' is never automated: the owner does it by hand.")


class AutomationPolicy(BaseModel):
    """The owner's automation switches. Off by default; every automatic change is audited and can be undone."""

    model_config = ConfigDict(extra="forbid")

    auto_classify: bool = False  # apply suggestions at or above ``min_confidence`` (history, and rules marked auto)
    min_confidence: Decimal = Field(default=Decimal("0.9"), ge=Decimal("0.8"), le=1)
    sources: list[Literal["rule", "history"]] = Field(default_factory=lambda: ["rule"], max_length=2)
    notify_unresolved: bool = False  # a daily in-app notice when suggestions wait
    notify_min_items: int = Field(default=1, ge=1, le=500)


class AutomationSettings(_Owned):
    policy: AutomationPolicy = Field(default_factory=AutomationPolicy)
    notified_on: str | None = None  # the local date the last unresolved notice was claimed for
    updated_at: datetime | None = None
    updated_by: str | None = None


__all__ = [
    "AUTOMATABLE", "ActiveRule", "AliasBook", "AutomationPolicy", "AutomationRefused", "AutomationSettings", "FinanceRule", "HistoryHint",
    "MAX_ALIASES", "MAX_RULES", "MAX_SUBCATEGORIES", "MAX_VERSIONS", "MerchantAlias", "NEVER_AUTOMATED", "Row", "RuleContent", "RuleMatch",
    "active_rules", "current_versions", "first_match", "fold", "from_legacy", "has_phrase", "history_index", "history_suggestion", "learnable", "merchant_key",
    "ordered", "payload_hash", "require_automatable", "Subcategory",
]
