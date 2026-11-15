"""Credit cards read from the records: what each card owes, its statement, its cycle and what paying the minimum or the
full amount means (docs/finance.md#credit-cards).

A card is one of the person's ``card`` accounts. Its figures come from three places, each named on the view:

* **The ledger** (``ledger``): what the card owes now is the account's own balance (opening balance plus every
  transaction on it), the same figure net worth and the summary use. Purchases, refunds, cashback, fees, interest, cash
  advances, instalment conversions and payments are the card's own rows, sorted by their transaction type and words.
* **The bank's statement** as the person entered it on the card's debt record (``statement``: migration 0186): the
  statement date and balance, the minimum, the closing day and the limit. The bank's message settings may carry the
  limit too (``account_map.credit_limit``); that one wins.
* **The person's terms** (the card's yearly rate on its debt record). **A rate is never invented**: without one the
  view lists ``rate`` under ``missing`` and says what to enter, and no interest figure is shown.

**A payment to a card is not spending.** Money into a card from one of the person's bank accounts is one own transfer:
the bank debit and the card credit are linked (``transfer`` links, ``statement_ledger.make_links``), both are filed as a
card payment, so it is neither spend nor income, the card owes less and the bank holds less. The view says for every
payment whether its bank side is linked (``linked``); one that is not is still a payment on the card, shown as
``provisional`` until its bank debit is paired.

**Posted, pending and unverified.** A row confirmed by a bank statement (imported from one, or an SMS or Apple Pay record a
statement later confirmed) is ``posted``; a row whose words say it is an authorisation or on hold is ``pending``; any other
row (an SMS, Apple Pay or hand entry no statement has confirmed yet) is ``unverified``. The statement check compares the
ledger's balance on the statement date with the statement's balance.

Pure: ``build`` takes what ``gather`` read in one short owner-scoped unit of work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from . import commitment_math as M
from . import txn_types as TT
from .models import Account, AccountKind, Debt, DebtKind, Transaction, TransactionType
from .money import minor_digits, quantize

LOOKBACK_DAYS = 120
DUE_SOON_DAYS = 7
RecordKind = Literal["purchase", "refund", "cashback", "fee", "interest", "cash_advance", "installment_conversion", "payment", "other"]
Evidence = Literal["posted", "pending", "unverified"]
PaymentState = Literal["paid_full", "paid_minimum", "part_paid", "not_paid", "due", "no_statement"]
CardMissing = Literal["debt_record", "rate", "limit", "statement", "minimum", "due_day"]

_CASH_ADVANCE = re.compile(r"\b(?:cash advance|cash withdrawal|atm)\b|سحب نقدي", re.IGNORECASE)
_INSTALLMENT_PLAN = re.compile(r"\b(?:ipp|epp|installment plan|instalment plan|easy payment plan|balance transfer|flexi ?pay)\b|تقسيط", re.IGNORECASE)
_PENDING = re.compile(r"\b(?:pending|authori[sz]ation|authori[sz]ed|on hold|hold)\b|معلق|قيد المعالجة", re.IGNORECASE)
_PAYMENT_WORDS = re.compile(r"\b(?:payment|repayment|thank you|transfer|trf)\b|سداد|دفعة", re.IGNORECASE)


@dataclass(frozen=True)
class EntryInfo:
    """What the journal says about one transaction: its canonical id and whether a statement confirmed it."""

    canonical_id: str
    confirmed: bool


@dataclass
class CardInputs:
    today: date
    accounts: list[Account]
    balances: dict[str, Decimal]
    transactions: list[Transaction]  # card rows of the last LOOKBACK_DAYS (or since the oldest statement date)
    debts: list[Debt]
    limits: dict[str, Decimal] = field(default_factory=dict)  # card account id: limit from the bank message settings
    observations: dict[str, tuple[Decimal, Any]] = field(default_factory=dict)
    entries: dict[str, EntryInfo] = field(default_factory=dict)  # transaction id: journal entry
    transfer_partner: dict[str, str] = field(default_factory=dict)  # canonical id: the other side's canonical id
    canonical_account: dict[str, str] = field(default_factory=dict)  # canonical id: the account it posts to (for the other side)


class CardRecord(BaseModel):
    transaction_id: str
    posted_on: date
    amount: Decimal  # signed, as on the card
    description: str
    kind: RecordKind
    evidence: Evidence
    linked: bool = False  # a payment whose bank debit is paired with it as one transfer
    from_account_id: str | None = None
    from_account_name: str | None = None


class CycleTotals(BaseModel):
    start: date | None
    end: date | None
    purchases: Decimal
    refunds: Decimal
    cashback: Decimal
    fees: Decimal
    interest: Decimal
    cash_advances: Decimal
    installment_conversions: Decimal
    payments: Decimal


class StatementCheck(BaseModel):
    status: Literal["tied", "differs", "no_statement"]
    statement_on: date | None = None
    statement_balance: Decimal | None = None
    ledger_balance: Decimal | None = None  # what the ledger says the card owed at the end of the statement date
    difference: Decimal | None = None  # statement less ledger
    unverified_rows: int = 0  # rows on or before the statement date that no statement confirmed


class PayOption(BaseModel):
    code: Literal["full", "minimum"]
    amount: Decimal | None
    leaves: Decimal | None  # what stays owed on this statement after paying it
    interest_estimate: Decimal | None = None  # about one month's interest on what is left, at the rate the person entered
    months_to_clear: int | None = None
    total_interest: Decimal | None = None
    never_clears: bool = False


class CardView(BaseModel):
    account_id: str
    debt_id: str | None
    name: str
    last4: str | None
    currency: str
    outstanding: Decimal  # what the card owes now (ledger)
    outstanding_source: Literal["ledger"] = "ledger"
    limit: Decimal | None
    limit_source: Literal["bank_messages", "entered"] | None
    available: Decimal | None
    utilization_percent: int | None
    observed_balance: Decimal | None  # the last balance a bank message stated
    observed_at: Any = None
    apr_percent: Decimal | None  # None: not entered (never guessed)
    statement_day: int | None
    statement_on: date | None
    statement_balance: Decimal | None
    statement_minimum: Decimal | None
    next_closing_on: date | None
    due_on: date | None  # the due date of the last statement (or the next one without a statement)
    days_until_due: int | None
    paid_since_statement: Decimal  # verified: linked or statement-posted payments after the statement date
    paid_provisional: Decimal  # payments on the card whose bank side is not paired yet
    payment_state: PaymentState
    cycle: CycleTotals
    check: StatementCheck
    pay_options: list[PayOption]
    payments: list[CardRecord]
    records: list[CardRecord]  # the latest rows (at most 30)
    evidence: dict[str, int]  # posted, pending, unverified counts in the window
    unverified_amount: Decimal
    missing: list[CardMissing]


def _sum(rows: list[CardRecord], kind: str) -> Decimal:
    return sum((abs(r.amount) for r in rows if r.kind == kind), Decimal(0))


def classify(t: Transaction, account_kind: AccountKind | None = AccountKind.CARD) -> RecordKind:
    """What a card row is, from its type and its words."""
    kind = TT.typed(t, account_kind).type
    text = t.description or ""
    if t.amount > 0:
        if kind == TransactionType.CASHBACK:
            return "cashback"
        if kind == TransactionType.REFUND or kind == TransactionType.REVERSAL:
            return "refund"
        if kind in (TransactionType.CARD_PAYMENT, TransactionType.INTERNAL_TRANSFER) or _PAYMENT_WORDS.search(text):
            return "payment"
        if kind == TransactionType.EXPENSE:
            return "refund"  # money in filed under a spending category gives that spend back
        return "other"
    if kind == TransactionType.INTEREST:
        return "interest"
    if kind == TransactionType.BANK_FEE:
        return "fee"
    if _INSTALLMENT_PLAN.search(text):
        return "installment_conversion"
    if _CASH_ADVANCE.search(text):
        return "cash_advance"
    if kind in (TransactionType.CARD_PAYMENT, TransactionType.INTERNAL_TRANSFER):
        return "other"
    return "purchase"


def evidence_of(t: Transaction, entry: EntryInfo | None) -> Evidence:
    if t.import_id or (entry is not None and entry.confirmed):
        return "posted"
    if _PENDING.search(t.description or ""):
        return "pending"
    return "unverified"


def _closing_after(day: int, after: date) -> date:
    """The first closing date strictly after ``after`` on ``day`` (the month's last day when shorter)."""
    candidate = after.replace(day=min(day, M.month_end(after).day))
    if candidate <= after:
        nxt = M.month_end(after) + timedelta(days=1)
        candidate = nxt.replace(day=min(day, M.month_end(nxt).day))
    return candidate


def _due_after(due_day: int, after: date) -> date:
    return _closing_after(due_day, after)


def _unit(currency: str) -> Decimal:
    return Decimal(1).scaleb(-minor_digits(currency))


def card_debt(account: Account, debts: list[Debt]) -> Debt | None:
    """The debt record that carries a card's terms: kept on that card account (one of the card kind first)."""
    mine = [d for d in debts if d.account_id == account.id]
    mine.sort(key=lambda d: (d.kind != DebtKind.CARD, d.created_at))
    return mine[0] if mine else None


def view(account: Account, inputs: CardInputs) -> CardView:
    from .advisor import interest_at_minimum

    c = account.currency
    today = inputs.today
    debt = card_debt(account, inputs.debts)
    balance = account.opening_balance + inputs.balances.get(account.id, Decimal(0))
    outstanding = quantize(max(-balance, Decimal(0)), c)
    limit, limit_source = None, None
    if inputs.limits.get(account.id):
        limit, limit_source = inputs.limits[account.id], "bank_messages"
    elif debt is not None and debt.card_limit:
        limit, limit_source = debt.card_limit, "entered"
    apr = debt.apr_percent if debt is not None and debt.apr_percent > 0 else None
    statement_on = debt.card_statement_on if debt is not None else None
    statement_balance = debt.card_statement_balance if debt is not None else None
    minimum = debt.card_statement_minimum if debt is not None and debt.card_statement_minimum is not None else (
        debt.minimum_payment if debt is not None and debt.minimum_payment > 0 else None)
    statement_day = debt.card_statement_day if debt is not None else None
    due_day = debt.due_day if debt is not None else None

    rows = sorted((t for t in inputs.transactions if t.account_id == account.id), key=lambda t: (t.posted_on, t.id))
    names = {a.id: a.name for a in inputs.accounts}
    records: list[CardRecord] = []
    for t in rows:
        entry = inputs.entries.get(t.id)
        kind = classify(t)
        partner = inputs.transfer_partner.get(entry.canonical_id) if entry is not None else None
        other_account = inputs.canonical_account.get(partner) if partner else None
        records.append(CardRecord(transaction_id=t.id, posted_on=t.posted_on, amount=t.amount, description=(t.description or "")[:200], kind=kind,
                                  evidence=evidence_of(t, entry), linked=partner is not None, from_account_id=other_account,
                                  from_account_name=names.get(other_account or "")))
    # The cycle: from the day after the last closing date to the next closing date.
    next_closing = _closing_after(statement_day, today - timedelta(days=1)) if statement_day else None
    cycle_start = (statement_on + timedelta(days=1)) if statement_on else (
        (_closing_after(statement_day, today - timedelta(days=40)) + timedelta(days=1)) if statement_day else None)
    if cycle_start is not None and next_closing is not None and cycle_start > next_closing:
        cycle_start = None
    in_cycle = [r for r in records if cycle_start is None or r.posted_on >= cycle_start]
    cycle = CycleTotals(start=cycle_start, end=next_closing, purchases=quantize(_sum(in_cycle, "purchase"), c), refunds=quantize(_sum(in_cycle, "refund"), c),
                        cashback=quantize(_sum(in_cycle, "cashback"), c), fees=quantize(_sum(in_cycle, "fee"), c), interest=quantize(_sum(in_cycle, "interest"), c),
                        cash_advances=quantize(_sum(in_cycle, "cash_advance"), c),
                        installment_conversions=quantize(_sum(in_cycle, "installment_conversion"), c), payments=quantize(_sum(in_cycle, "payment"), c))
    payments = [r for r in records if r.kind == "payment"]
    since = [p for p in payments if statement_on is not None and p.posted_on > statement_on]
    verified = sum((p.amount for p in since if p.linked or p.evidence == "posted"), Decimal(0))
    provisional = sum((p.amount for p in since if not (p.linked or p.evidence == "posted")), Decimal(0))
    # The due date of the last statement: the first due day after the statement date.
    due_on: date | None = None
    if due_day and statement_on:
        due_on = _due_after(due_day, statement_on)
    elif due_day:
        due_on = M.next_due_day(due_day, today)
    state: PaymentState
    if statement_balance is None or statement_on is None:
        state = "no_statement"
    elif statement_balance <= 0 or verified >= statement_balance:
        state = "paid_full"
    elif minimum is not None and verified >= minimum:
        state = "paid_minimum"
    elif due_on is not None and today > due_on:
        state = "part_paid" if verified > 0 else "not_paid"
    else:
        state = "part_paid" if verified > 0 else "due"
    # The statement check: the ledger balance at the end of the statement date.
    if statement_on is not None and statement_balance is not None:
        at = account.opening_balance + inputs.balances.get(account.id, Decimal(0)) - sum((t.amount for t in rows if t.posted_on > statement_on), Decimal(0))
        ledger_owed = quantize(max(-at, Decimal(0)), c)
        difference = quantize(statement_balance - ledger_owed, c)
        tolerance = _unit(c)
        check = StatementCheck(status="tied" if abs(difference) <= tolerance else "differs", statement_on=statement_on, statement_balance=statement_balance,
                               ledger_balance=ledger_owed, difference=difference,
                               unverified_rows=sum(1 for r in records if r.posted_on <= statement_on and r.evidence != "posted"))
    else:
        check = StatementCheck(status="no_statement")
    # What paying the full statement balance or only the minimum leaves, and what the minimum costs at the person's own rate.
    options: list[PayOption] = []
    owed_now = max((statement_balance if statement_balance is not None else outstanding) - max(verified, Decimal(0)), Decimal(0))
    options.append(PayOption(code="full", amount=quantize(owed_now, c), leaves=Decimal(0)))
    if minimum is not None:
        left_min = max(min(minimum, owed_now), Decimal(0))
        option = PayOption(code="minimum", amount=quantize(left_min, c), leaves=quantize(owed_now - left_min, c))
        if apr is not None and owed_now > 0 and minimum > 0:
            first, months, total, never = interest_at_minimum(owed_now, apr, minimum, c)
            option = option.model_copy(update={"interest_estimate": first, "months_to_clear": months, "total_interest": total, "never_clears": never})
        options.append(option)
    missing: list[CardMissing] = []
    if debt is None:
        missing.append("debt_record")
    else:
        if apr is None:
            missing.append("rate")
        if statement_on is None:
            missing.append("statement")
        if minimum is None:
            missing.append("minimum")
        if due_day is None:
            missing.append("due_day")
    if limit is None:
        missing.append("limit")
    seen = inputs.observations.get(account.id)
    counts = {"posted": 0, "pending": 0, "unverified": 0}
    for r in records:
        counts[r.evidence] += 1
    return CardView(
        account_id=account.id, debt_id=debt.id if debt is not None else None, name=account.name, last4=account.last4, currency=c, outstanding=outstanding,
        limit=limit, limit_source=limit_source, available=quantize(max(limit - outstanding, Decimal(0)), c) if limit else None,
        utilization_percent=min(999, int((outstanding * 100 / limit).to_integral_value())) if limit else None,
        observed_balance=seen[0] if seen else None, observed_at=seen[1] if seen else None, apr_percent=apr, statement_day=statement_day,
        statement_on=statement_on, statement_balance=statement_balance, statement_minimum=minimum, next_closing_on=next_closing, due_on=due_on,
        days_until_due=(due_on - today).days if due_on else None, paid_since_statement=quantize(verified, c), paid_provisional=quantize(provisional, c),
        payment_state=state, cycle=cycle, check=check, pay_options=options, payments=list(reversed(payments))[:20], records=list(reversed(records))[:30],
        evidence=counts, unverified_amount=quantize(sum((abs(r.amount) for r in records if r.evidence != "posted"), Decimal(0)), c), missing=missing,
    )


def build(inputs: CardInputs) -> list[CardView]:
    cards = [a for a in inputs.accounts if a.kind == AccountKind.CARD and not a.archived]
    out = [view(a, inputs) for a in cards]
    return sorted(out, key=lambda v: (v.due_on is None, v.due_on or inputs.today, -v.outstanding, v.name.casefold()))


def gather(context: Any, today: date) -> CardInputs:
    """Everything the card views need, in one short owner-scoped unit of work."""
    from .links_store import open_link_stores
    from .statement_ledger import TRANSFER_DIMENSION

    with open_link_stores(context) as (finance, sms, links):
        accounts = finance.list_accounts(context)
        cards = {a.id for a in accounts if a.kind == AccountKind.CARD and not a.archived}
        debts = [d for d in finance.list_items(context, "debt") if isinstance(d, Debt)]
        if not cards:
            return CardInputs(today=today, accounts=accounts, balances={}, transactions=[], debts=debts)
        oldest = min([today - timedelta(days=LOOKBACK_DAYS)] + [d.card_statement_on - timedelta(days=40) for d in debts
                                                                  if d.account_id in cards and d.card_statement_on])
        sums = finance.account_sums(context)
        rows = [t for t in finance.transactions_between(context, oldest, today) if t.account_id in cards]
        entries = sms.list_entries(context, start=oldest - timedelta(days=10), end=today + timedelta(days=1))
        settings = sms.get_settings(context)
        observations = sms.latest_observations(context)
        transfer_rows, _ = links.list_links(context, canonical_id=None, dimension=TRANSFER_DIMENSION, target_type=None, target_id=None, status=None,
                                            limit=10_000, offset=0)
    by_txn: dict[str, EntryInfo] = {}
    canonical_account: dict[str, str] = {}
    for entry in entries:
        canonical = entry.canonical_id or entry.id
        confirmed = entry.reconciled_at is not None or getattr(entry.source, "value", entry.source) == "statement"
        for posting in entry.postings:
            if posting.transaction_id:
                current = by_txn.get(posting.transaction_id)
                by_txn[posting.transaction_id] = EntryInfo(canonical, confirmed or (current.confirmed if current else False))
            if posting.account_id and canonical not in canonical_account:
                canonical_account[canonical] = posting.account_id
    partner: dict[str, str] = {}
    for link in transfer_rows:
        if getattr(link.status, "value", link.status) == "void":
            continue
        partner[link.canonical_id] = link.target_id
        partner[link.target_id] = link.canonical_id
    limits = {m.account_id: Decimal(m.credit_limit) for m in (settings.account_map if settings else []) if m.credit_limit and m.credit_limit > 0}
    return CardInputs(today=today, accounts=accounts, balances=sums, transactions=rows, debts=debts, limits=limits, observations=observations,
                      entries=by_txn, transfer_partner=partner, canonical_account=canonical_account)


__all__ = ["CardInputs", "CardRecord", "CardView", "EntryInfo", "build", "card_debt", "classify", "evidence_of", "gather", "view"]
