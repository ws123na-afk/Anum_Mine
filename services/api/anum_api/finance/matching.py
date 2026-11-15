"""The matching engine: which recorded payment pays which obligation (docs/finance-matching.md).

Pure and deterministic: it reads payments (canonical transactions, money out) and obligations (a bill's due date, a
Tamara installment's one-time bill, a card, a bank loan's month) that the service loaded, scores every pair on the
evidence, and decides for each payment: link it automatically, put it in review with the reason, or leave it. It
never writes; ``matching_service`` makes the links through ``links.create`` (one linking system: the owner's lock,
idempotency, never more than the payment) and records each decision.

Evidence, each with a fixed weight (``WEIGHTS``): the exact amount (or a part of it), the currency (a gate: another
currency is never matched), the direction (money out only), the posting date against the due date, the provider's name
(Tamara, in English or Arabic), the provider's order reference, the merchant words of a bill, the payments the owner
linked to the same bill before, the paying account the bill names, the receiving card or loan account, the bank
reference. A gate rules a pair out whatever else agrees: another currency, money in, an own-account transfer for
anything but a card or loan, a payment already undone, another provider's name, a date outside the window.

A link is automatic only when every one of these holds: the owner's approved policy allows the kind
(``MatchingPolicy.automatic``), the score reaches ``min_confidence``, the amount is exact, the payment is on one of
the source accounts the owner named, the match is **unique both ways** (no other obligation for this payment and no
other payment for this obligation at review level), and nothing flags it (a possible second report of another
payment, a refund or reversal after it, a changed source). Two installments of the same amount on one day from two
orders, with nothing to tell them apart, always wait for the owner. Within one order the oldest unpaid installment is
the one paid (providers collect in order), so a debit that fits several installments of the same order picks that one.

**Across currencies** (docs/finance-matching.md#across-currencies): a debit in the account's currency pays an obligation
in another currency only on the bank's own figures (``FxEvidence``, read by ``card_fx.details``): the original amount
the bank printed is compared with what is due, the obligation is settled in its own currency, the bank side stays the
purchase in the account's currency, and an FX fee or markup stays apart (never part of what the obligation was paid).
No rate is looked up or guessed: a debit whose original amount is not known, but whose provider, order, merchant or
loan account points at the obligation, waits for the owner (``fx_evidence_missing``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import combinations

from .match_policy import MatchingPolicy
from .match_store import EvidenceItem
from .merchant_match import SAME_PAYMENT_DAYS, merchant_words, merchants_agree

# -- installment providers: the adapter seam ----------------------------------------------------------------------


@dataclass(frozen=True)
class InstallmentProvider:
    """An installment provider ANUM recognises. ``reads`` names what ANUM actually imports from it; a provider with no
    reads is recognised in bank text only (to tell its debits apart), never presented as connected."""

    key: str
    name: str
    words: tuple[str, ...]  # how a bank names it (folded, lower case)
    reads: tuple[str, ...] = ()  # "gmail", "statement_csv"


_PROVIDERS: dict[str, InstallmentProvider] = {}


def register_provider(provider: InstallmentProvider) -> None:
    _PROVIDERS[provider.key] = provider


def providers() -> list[InstallmentProvider]:
    return sorted(_PROVIDERS.values(), key=lambda p: p.key)


register_provider(InstallmentProvider("tamara", "Tamara", ("tamara", "تمارا"), ("gmail", "statement_csv")))
# Recognised so a debit to them is never taken for a Tamara installment; ANUM reads nothing from them.
register_provider(InstallmentProvider("tabby", "Tabby", ("tabby", "تابي")))
register_provider(InstallmentProvider("postpay", "Postpay", ("postpay",)))
register_provider(InstallmentProvider("spotii", "Spotii", ("spotii",)))


def _fold(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[ً-ٟـ]", "", value)


def provider_in(text: str | None) -> str | None:
    """The provider a bank text names, if exactly one."""
    folded = _fold(text)
    found = {p.key for p in _PROVIDERS.values() if any(w in folded for w in p.words)}
    return next(iter(found)) if len(found) == 1 else None


def provider_key(name: str | None) -> str | None:
    folded = _fold(name)
    return next((p.key for p in _PROVIDERS.values() if folded == p.key or folded == _fold(p.name) or any(w == folded for w in p.words)), None)


def _alnum(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", _fold(text))


def names_order(text: str | None, order_ref: str | None) -> bool:
    """The bank text quotes the provider's order reference (letters and digits only, at least 5)."""
    ref = _alnum(order_ref)
    return len(ref) >= 5 and ref in _alnum(text)


# -- what the engine reads ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FxEvidence:
    """What the bank's figures say about a foreign purchase (``card_fx.Details``): never a rate ANUM chose."""

    original_currency: str
    original_amount: Decimal | None  # None: the bank named the currency but not the amount
    billed_amount: Decimal  # what the bank charged, in the account's currency
    purchase_amount: Decimal  # the billed amount less an FX fee the bank included in it
    rate: Decimal | None = None
    rate_kind: str = "not_stated"  # printed, column, implied, not_stated
    rate_source: str | None = None  # statement, sms, manual
    rate_date: date | None = None
    fee_amount: Decimal | None = None
    fee_source: str | None = None  # fee_row, markup_stated, fee_stated
    fee_transaction_id: str | None = None

    @property
    def sufficient(self) -> bool:
        return self.original_amount is not None and self.original_amount > 0 and self.purchase_amount > 0

    def as_dict(self, bank_currency: str, bank_amount: Decimal | None) -> dict[str, str | None]:
        """The evidence stored with the decision and the link (strings only)."""

        def s(v: object) -> str | None:
            return None if v is None else str(v)

        return {"original_amount": s(self.original_amount), "original_currency": self.original_currency, "billed_amount": s(self.billed_amount),
                "purchase_amount": s(self.purchase_amount), "bank_amount": s(bank_amount), "bank_currency": bank_currency, "rate": s(self.rate),
                "rate_kind": self.rate_kind, "rate_source": self.rate_source, "rate_date": s(self.rate_date), "fee_amount": s(self.fee_amount),
                "fee_currency": bank_currency if self.fee_amount is not None else None, "fee_source": self.fee_source,
                "fee_transaction_id": self.fee_transaction_id}


@dataclass(frozen=True)
class Payment:
    """One canonical transaction as the matcher sees it, on the account that paid."""

    canonical_id: str
    posted_on: date
    account_id: str | None
    amount: Decimal  # positive
    currency: str
    direction: str  # "out" or "in"
    text: str
    sources: frozenset[str] = frozenset()  # statement, sms, wallet, manual
    reference: str | None = None
    kind: str | None = None  # the message kind (card_purchase, card_repayment, own_transfer, loan_repayment, refund, ...)
    txn_type: str | None = None  # the stage 2 row's type (internal_transfer, card_payment, refund, ...)
    counter_account_id: str | None = None  # the other own account it went to (a card or a loan), if any
    own_transfer: bool = False  # a move between the owner's own accounts (paired or typed so)
    original_currency: str | None = None
    original_amount: Decimal | None = None
    left: dict[str, Decimal] = field(default_factory=dict)  # what is still unlinked per dimension (bill, debt)
    fx: FxEvidence | None = None  # the bank's conversion figures, for a purchase made in another currency

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.canonical_id, self.posted_on.isoformat(), str(self.amount), self.currency, self.account_id or "")


@dataclass(frozen=True)
class Obligation:
    """One thing a payment can pay: a due date of a bill (an installment is a one-time bill), a card or a bank loan month."""

    kind: str  # installment, bill, card_repayment, bank_loan
    dimension: str  # bill or debt
    target_type: str
    target_id: str
    label: str
    due_on: date
    expected: Decimal  # still due on this target (positive)
    currency: str
    account_id: str | None = None  # the paying account the bill or debt names
    provider: str | None = None
    order_ref: str | None = None
    installment_no: int | None = None
    group: str | None = None  # the provider order: one installment of a group is paid at a time, the oldest first
    liability_account_id: str | None = None  # the card or loan account
    history_words: frozenset[str] = frozenset()  # merchant words of payments the owner linked to this bill before

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.target_id, self.due_on.isoformat(), str(self.expected), self.currency)


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


# -- scoring ------------------------------------------------------------------------------------------------------

WEIGHTS: dict[str, Decimal] = {
    "amount_exact": Decimal("0.45"),
    "amount_partial": Decimal("0.15"),
    "date_close": Decimal("0.15"),  # a day either side of the due date (an installment: up to 3 days after)
    "date_window": Decimal("0.08"),
    "provider": Decimal("0.30"),
    "provider_missing": Decimal("-0.25"),
    "order_reference": Decimal("0.35"),
    "merchant_words": Decimal("0.25"),
    "merchant_conflict": Decimal("-0.30"),
    "history": Decimal("0.15"),
    "account": Decimal("0.10"),
    "account_conflict": Decimal("-0.20"),
    "liability_account": Decimal("0.45"),
    "repayment_kind": Decimal("0.10"),
    "bank_reference": Decimal("0.05"),
    "fx_original": Decimal(0),  # the bank's conversion (a fact shown with the match, not a weight: the amount carries it)
    "fx_fee": Decimal(0),  # an FX fee kept apart from what the obligation was paid
    "fx_evidence_missing": Decimal(0),
}
MAX_SCORE = Decimal("0.99")
PARTIAL_MIN = Decimal("0.1")  # a part below a tenth of what is due is not a part payment of it
# Days from the due date a payment may post: (before, after).
WINDOWS: dict[str, tuple[int, int]] = {"installment": (5, 7), "installment_ref": (60, 30), "bill": (7, 10), "bank_loan": (7, 10),
                                       "card_repayment": (400, 400)}
REFUND_DAYS = 45
# A provider collects an installment on its due date and retries for a few days after a failed attempt.
CLOSE_AFTER_INSTALLMENT = 3


@dataclass
class Candidate:
    payment: Payment
    obligation: Obligation
    score: Decimal
    evidence: list[EvidenceItem]
    exact: bool
    amount: Decimal  # what this pair would link, in the obligation's currency
    bank_amount: Decimal | None = None  # across currencies: the bank side, in the payment's currency (the FX fee left out)
    fx: FxEvidence | None = None
    fx_missing: bool = False  # another currency and no original amount from the bank: review only, never linked on its own


def _ev(code: str, value: str | None = None) -> EvidenceItem:
    return EvidenceItem(code=code, value=value[:120] if value else None, weight=WEIGHTS.get(code, Decimal(0)))


def _repayment_out(p: Payment) -> bool:
    return p.kind in ("card_repayment", "loan_repayment") or p.txn_type in ("card_payment", "loan_principal")


def score(p: Payment, o: Obligation) -> Candidate | None:
    """The pair's score and evidence, or None when a gate rules it out."""
    if p.direction != "out" or o.expected <= 0:
        return None
    left = p.left.get(o.dimension, p.amount)
    if left <= 0:
        return None
    fx: FxEvidence | None = None
    fx_missing = False
    if p.currency != o.currency:
        # Across currencies only on the bank's own figures; a card is repaid in its own currency (an own transfer).
        if o.kind == "card_repayment":
            return None
        if p.fx is not None and p.fx.original_currency != o.currency:
            return None  # the bank names another original currency
        if p.fx is not None and p.fx.sufficient:
            if left < p.amount:
                return None  # part of this foreign purchase is already linked: it is never split on a guessed rate
            fx = p.fx
        else:
            fx_missing = True
    is_debt = o.kind in ("card_repayment", "bank_loan")
    if (p.own_transfer or p.txn_type == "internal_transfer" or p.kind == "own_transfer") and not is_debt:
        return None  # moving money between own accounts pays no bill
    if o.kind == "card_repayment" and not (_repayment_out(p) or p.own_transfer) :
        return None  # a card is repaid by a repayment, never by a purchase on it
    if o.kind != "card_repayment" and (p.kind == "card_repayment" or p.txn_type == "card_payment"):
        return None  # a card repayment is not a second expense, and pays no bill
    days = (p.posted_on - o.due_on).days
    evidence: list[EvidenceItem] = []
    total = Decimal(0)

    def add(code: str, value: str | None = None) -> None:
        nonlocal total
        evidence.append(_ev(code, value))
        total += WEIGHTS[code]

    # The provider and its order reference (installments).
    named = provider_in(p.text)
    if o.kind == "installment":
        ref = names_order(p.text, o.order_ref)
        if named is not None and named != o.provider:
            return None  # a debit to another provider never pays this one
        before, after = WINDOWS["installment_ref" if ref else "installment"]
        if not -before <= days <= after:
            return None
        if named == o.provider:
            add("provider", named)
        elif not ref:
            add("provider_missing")
        if ref:
            add("order_reference", o.order_ref)
    elif o.kind == "card_repayment":
        if o.liability_account_id and p.counter_account_id == o.liability_account_id:
            add("liability_account")
        elif merchants_agree(o.label, p.text):
            add("merchant_words", o.label)
        else:
            return None
        if _repayment_out(p):
            add("repayment_kind")
    else:
        before, after = WINDOWS[o.kind]
        if not -before <= days <= after:
            return None
        if o.kind == "bank_loan":
            if o.liability_account_id and p.counter_account_id == o.liability_account_id:
                add("liability_account")
            elif merchants_agree(o.label, p.text):
                add("merchant_words", o.label)
            if _repayment_out(p):
                add("repayment_kind")
        else:
            agree = merchants_agree(o.label, p.text)
            if agree:
                add("merchant_words", o.label)
            elif agree is False:
                add("merchant_conflict")
            if o.history_words and merchant_words(p.text) & o.history_words:
                add("history")
    bank_amount: Decimal | None = None
    if fx_missing:
        # No original amount: only what names this obligation (provider, order, merchant, loan account) makes it a candidate.
        if not {e.code for e in evidence} & {"provider", "order_reference", "merchant_words", "liability_account"}:
            return None
        add("fx_evidence_missing", f"{p.currency} {p.amount} for {o.currency} {o.expected}")
        exact, amount = False, o.expected
    else:
        if fx is not None and fx.original_amount is not None:
            # The bank's original amount is what was paid of the obligation, in its currency; the bank side is the purchase.
            left = fx.original_amount
            bank_amount = fx.purchase_amount
            add("fx_original", f"{o.currency} {fx.original_amount} = {p.currency} {fx.billed_amount}"
                + (f" at {fx.rate} ({fx.rate_kind})" if fx.rate is not None else ""))
            if fx.fee_amount is not None and fx.fee_amount > 0:
                add("fx_fee", f"{p.currency} {fx.fee_amount} ({fx.fee_source})")
        # The amount: exact, or a part of what is due (never more: a combined payment is found separately).
        exact = left == o.expected if o.kind != "card_repayment" else left <= o.expected
        amount = min(left, o.expected)
        if exact:
            add("amount_exact", f"{o.currency} {amount}")
        elif left < o.expected and left >= o.expected * PARTIAL_MIN and o.kind != "card_repayment":
            add("amount_partial", f"{o.currency} {left} of {o.expected}")
        else:
            return None
    if o.kind != "card_repayment":
        close = -1 <= days <= (CLOSE_AFTER_INSTALLMENT if o.kind == "installment" else 1)
        add("date_close" if close else "date_window", f"{days:+d}d")
    if o.account_id:
        if p.account_id == o.account_id:
            add("account")
        elif p.account_id is not None:
            add("account_conflict")
    if p.reference:
        add("bank_reference")
    return Candidate(p, o, min(max(total, Decimal(0)), MAX_SCORE).quantize(Decimal("0.001")), evidence, exact, amount, bank_amount, fx, fx_missing)


# -- signals over the whole set ----------------------------------------------------------------------------------


def possible_duplicates(payments: Iterable[Payment], linked: Iterable[Payment] = ()) -> dict[str, str]:
    """A payment that looks like a second report of another (the same account, direction, amount and currency within
    ``SAME_PAYMENT_DAYS``, merchant words or the provider agreeing, a different canonical transaction, no conflicting
    bank reference): canonical id to the one it may repeat. Rows already linked are the originals."""
    pool = sorted([*linked, *payments], key=lambda p: (p.posted_on, p.canonical_id))
    linked_ids = {p.canonical_id for p in linked}
    out: dict[str, str] = {}
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if (b.posted_on - a.posted_on).days > SAME_PAYMENT_DAYS:
                break
            if a.canonical_id == b.canonical_id or (a.account_id, a.direction, a.amount, a.currency) != (b.account_id, b.direction, b.amount, b.currency):
                continue
            if a.reference and b.reference and a.reference != b.reference:
                continue
            if not (merchants_agree(a.text, b.text) or (provider_in(a.text) is not None and provider_in(a.text) == provider_in(b.text))):
                continue
            later, earlier = (b, a) if b.canonical_id not in linked_ids else (a, b)
            out.setdefault(later.canonical_id, earlier.canonical_id)
    return out


def refund_signals(payments: Iterable[Payment], credits: Iterable[Payment]) -> dict[str, str]:
    """A payment followed (0 to ``REFUND_DAYS`` days) by money back of the same amount and currency that names the same
    merchant or provider, or by a reversal of it: canonical id to the credit's."""
    ins = sorted((c for c in credits if c.direction == "in"), key=lambda c: (c.posted_on, c.canonical_id))
    out: dict[str, str] = {}
    for p in payments:
        for c in ins:
            if not 0 <= (c.posted_on - p.posted_on).days <= REFUND_DAYS or c.currency != p.currency or c.amount != p.amount:
                continue
            same = merchants_agree(p.text, c.text) or (provider_in(p.text) is not None and provider_in(p.text) == provider_in(c.text))
            if same or c.kind == "reversal":
                out[p.canonical_id] = c.canonical_id
                break
    return out


# -- decisions ----------------------------------------------------------------------------------------------------


@dataclass
class Decision:
    payment: Payment
    parts: list[Candidate]  # one, or several for a combined payment
    outcome: str  # "auto", "review"
    reason: str
    confidence: Decimal
    state: str  # "verified" or "provisional": what the payment's sources prove
    competitors: int = 0
    flags: list[str] = field(default_factory=list)
    alternatives: list[Candidate] = field(default_factory=list)  # the other obligations it could pay, each shown for review

    @property
    def kind(self) -> str:
        return self.parts[0].obligation.kind


@dataclass
class Result:
    decisions: list[Decision]
    duplicates: dict[str, str]
    refunds: dict[str, str]
    unmatched: list[str]  # payments with no candidate at all


def evidence_state(p: Payment, policy: MatchingPolicy) -> str:
    return "verified" if p.sources & set(policy.settled_sources) else "provisional"


def _collapse_groups(cands: list[Candidate]) -> tuple[list[Candidate], bool]:
    """Within one provider order, only the oldest open installment can be the one paid."""
    out: list[Candidate] = []
    groups: dict[str, list[Candidate]] = {}
    for c in cands:
        if c.obligation.group:
            groups.setdefault(c.obligation.group, []).append(c)
        else:
            out.append(c)
    collapsed = False
    for items in groups.values():
        items.sort(key=lambda c: (c.obligation.due_on, c.obligation.installment_no or 0))
        if len(items) > 1:
            collapsed = True
        out.append(items[0])
    return out, collapsed


def _combined(p: Payment, obligations: list[Obligation], policy: MatchingPolicy) -> list[Candidate] | None:
    """One payment that pays exactly two or three open obligations of the same currency together (two installments of one
    order, two bills on one day). Only a unique combination counts."""
    pool: list[Candidate] = []
    for o in obligations:
        if o.currency != p.currency or o.expected >= p.amount:
            continue
        trial = Payment(**{**p.__dict__, "amount": o.expected, "left": {o.dimension: o.expected}})
        c = score(trial, o)
        if c is not None and c.score >= policy.review_confidence:
            pool.append(Candidate(p, o, c.score, c.evidence, True, o.expected))
    found: list[tuple[Candidate, ...]] = []
    for size in (2, 3):
        for combo in combinations(pool, size):
            if len({c.obligation.dimension for c in combo}) == 1 and sum((c.amount for c in combo), Decimal(0)) == p.left.get(combo[0].obligation.dimension, p.amount):
                found.append(combo)
        if len(found) > 1:
            return None
    return list(found[0]) if len(found) == 1 else None


def decide(payments: list[Payment], obligations: list[Obligation], policy: MatchingPolicy, *, credits: Iterable[Payment] = (),
           linked: Iterable[Payment] = ()) -> Result:
    """Every decision for these payments. ``linked``: payments already linked to an obligation (they are the originals a
    possible duplicate repeats); ``credits``: money in that may refund or reverse a payment."""
    duplicates = possible_duplicates(payments, linked) if policy.prevent_duplicates else {}
    refunds = refund_signals(payments, credits)
    scored: dict[str, list[Candidate]] = {}
    by_target: dict[str, list[Candidate]] = {}
    for p in payments:
        cands = [c for o in obligations if (c := score(p, o)) is not None and (c.score >= policy.review_confidence or c.fx_missing)]
        if any(not c.fx_missing for c in cands):
            cands = [c for c in cands if not c.fx_missing]  # an obligation the bank's figures fit beats one that only names it
        named = [c for c in cands if any(e.code == "order_reference" for e in c.evidence)]
        if named:
            cands = named  # the bank text quotes the order: other orders are not candidates
        cands, _ = _collapse_groups(cands)
        scored[p.canonical_id] = cands
        for c in cands:
            by_target.setdefault(_slot(c), []).append(c)
    decisions: list[Decision] = []
    unmatched: list[str] = []
    for p in payments:
        cands = scored[p.canonical_id]
        flags = []
        if p.canonical_id in duplicates:
            flags.append("duplicate_event")
        if p.canonical_id in refunds:
            flags.append("refund_signal")
        state = evidence_state(p, policy)
        if not cands:
            combo = _combined(p, obligations, policy)
            if combo is None:
                unmatched.append(p.canonical_id)
                continue
            confidence = min(c.score for c in combo)
            same_order = len({c.obligation.group for c in combo}) == 1 and combo[0].obligation.group is not None
            auto = same_order and not flags and _auto_ok(p, combo[0].obligation, confidence, policy)
            decisions.append(Decision(p, combo, "auto" if auto else "review", "combined_payment" if auto else "combined_needs_review",
                                      confidence, state, 0, flags))
            continue
        cands.sort(key=lambda c: (-c.score, c.obligation.due_on, c.obligation.target_id))
        best = cands[0]
        rivals_here = len(cands) - 1
        rivals_there = [c for c in by_target.get(_slot(best), []) if c.payment.canonical_id != p.canonical_id]
        competitors = rivals_here + len(rivals_there)
        if best.fx_missing:
            reason, outcome = "fx_evidence_missing", "review"  # another currency without the bank's original amount
        elif rivals_here:
            reason, outcome = "ambiguous_obligation", "review"
        elif rivals_there:
            twins = [c for c in rivals_there if duplicates.get(c.payment.canonical_id) == p.canonical_id
                     or duplicates.get(p.canonical_id) == c.payment.canonical_id]
            reason, outcome = ("duplicate_event" if twins else "ambiguous_payment"), "review"
            if twins and "duplicate_event" not in flags:
                flags.append("duplicate_event")
        elif flags:
            reason, outcome = flags[0], "review"
        elif not best.exact:
            reason, outcome = "partial_amount", "review"
        elif _auto_ok(p, best.obligation, best.score, policy):
            reason, outcome = _auto_reason(best), "auto"
        else:
            reason, outcome = _review_reason(p, best, policy), "review"
        decisions.append(Decision(p, [best], outcome, reason, best.score, state, competitors, flags,
                                  alternatives=cands[1:] if outcome == "review" else []))
    return Result(decisions, duplicates, refunds, unmatched)


def _slot(c: Candidate) -> str:
    """What two payments compete for: one due date of a bill, one month of a loan; a card takes every repayment."""
    o = c.obligation
    if o.kind == "card_repayment":
        return f"{o.target_id}:{c.payment.canonical_id}"
    if o.kind == "bank_loan":
        return f"{o.target_id}:{o.due_on.isoformat()}"
    return o.target_id


def _auto_ok(p: Payment, o: Obligation, confidence: Decimal, policy: MatchingPolicy) -> bool:
    return policy.automatic(o.kind) and confidence >= policy.min_confidence and p.account_id in policy.source_account_ids


def _auto_reason(c: Candidate) -> str:
    codes = {e.code for e in c.evidence}
    if c.fx is not None:
        return "fx_original_amount"
    if "order_reference" in codes:
        return "order_reference_match"
    if "liability_account" in codes:
        return "repayment_to_account"
    if c.obligation.kind == "installment":
        return "provider_amount_date"
    return "merchant_amount_date"


def _review_reason(p: Payment, c: Candidate, policy: MatchingPolicy) -> str:
    if not policy.automatic(c.obligation.kind):
        return "policy_off"
    if p.account_id not in policy.source_account_ids:
        return "account_not_source"
    return "low_confidence"


__all__ = [
    "Candidate",
    "Decision",
    "FxEvidence",
    "InstallmentProvider",
    "Obligation",
    "Payment",
    "Result",
    "WEIGHTS",
    "WINDOWS",
    "decide",
    "evidence_state",
    "fingerprint",
    "names_order",
    "possible_duplicates",
    "provider_in",
    "provider_key",
    "providers",
    "refund_signals",
    "register_provider",
    "score",
]
