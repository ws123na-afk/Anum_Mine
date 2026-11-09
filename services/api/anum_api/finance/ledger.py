"""The double-entry journal and the ledger dashboard (docs/finance-sms.md#ledger).

Every transaction an SMS (or a correction of one) records is a **journal entry** with two or
more **postings** that balance to zero per currency (a debit is positive, a credit negative).
The database enforces the balance at commit (``finance_postings_balanced``, migration
``0131_finance_sms_ledger``), and postings are never updated: a correction is a reversing
entry plus a new one, never an edit or a delete.

Ledger accounts are codes, not rows:

* ``asset:<finance account id>`` for bank, cash and investment accounts;
* ``liability:<finance account id>`` for cards and loans (a debt is a credit balance, which
  is the negative balance the stage 2 ledger already shows for a card);
* ``income:<kind>`` (salary, deposit, transfer_in, other), ``expense:<category>`` (the stage 2
  categories plus ``interest``), ``equity:opening`` for opening balances and
  ``suspense:own_transfer`` for the side of a transfer between the person's own accounts that
  no message has named yet.

Each posting on a finance account is also a stage 2 ``Transaction`` on that account (same
amount and sign), so balances, safe-to-spend, the Guardian and exports keep working.
Transactions from statements and manual entry have no stored entry: :func:`derived_legs`
builds the same postings for them on the fly, so the dashboard covers every source without
counting anything twice.

Pure: no store, clock or network here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from .flows import Flow, classify, ledger_flow
from .models import Account, AccountKind, Category, Transaction
from .money import quantize
from .sms_parser import Kind

ASSET_KINDS = frozenset({AccountKind.BANK, AccountKind.CASH, AccountKind.INVESTMENT})
SUSPENSE = "suspense:own_transfer"
OPENING = "equity:opening"


class EntryStatus(StrEnum):
    POSTED = "posted"
    REVERSED = "reversed"  # a later entry undid it; both stay in the journal


class EntrySource(StrEnum):
    SMS = "sms"  # an iPhone message, or one pasted by hand
    CORRECTION = "correction"  # the person corrected a posted message
    REVERSAL = "reversal"  # undoes another entry
    CLEARING = "clearing"  # a later message named the other side of an own transfer
    MANUAL = "manual"  # typed by the person (stage 2 transaction)
    STATEMENT = "statement"  # a CSV statement row


class Posting(BaseModel):
    id: str
    entry_id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    ledger_account: str = Field(max_length=120)
    account_id: str | None = None
    transaction_id: str | None = None
    amount: Decimal  # debit positive, credit negative
    currency: str
    created_at: datetime


class JournalEntry(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    # The canonical transaction id: the first entry's id, kept by every correction, reversal and
    # clearing entry of the same payment. Links from other modules point here, never at one entry.
    canonical_id: str | None = None
    posted_on: date
    occurred_at: datetime | None = None
    kind: str
    category: str | None = None
    merchant: str | None = None
    description: str
    source: EntrySource
    source_ref: str | None = None  # the SMS message id
    status: EntryStatus = EntryStatus.POSTED
    reverses_id: str | None = None
    reversed_by_id: str | None = None
    original_amount: Decimal | None = None  # FX: what the message said, in its own currency
    original_currency: str | None = None
    fx_rate: Decimal | None = None  # account currency per unit of the original currency
    fx_fee: Decimal | None = None
    fingerprint: str | None = None
    reference: str | None = None  # the bank's reference, when the message gave one
    reconciled_at: datetime | None = None
    reconciled_ref: str | None = None  # the statement transaction that confirmed it
    created_at: datetime
    postings: list[Posting] = Field(default_factory=list)


@dataclass(frozen=True)
class Leg:
    ledger_account: str
    amount: Decimal
    currency: str
    account_id: str | None = None


class PlanError(ValueError):
    """The entry cannot be posted as read: the message goes to review with this code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def ledger_account(account: Account) -> str:
    return f"{'asset' if account.kind in ASSET_KINDS else 'liability'}:{account.id}"


def _leg(account: Account, amount: Decimal) -> Leg:
    return Leg(ledger_account(account), amount, account.currency, account.id)


def plan(
    kind: Kind,
    amount: Decimal,
    *,
    account: Account | None,
    counter: Account | None = None,
    category: str = Category.OTHER.value,
    cash_account: Account | None = None,
    interest: Decimal | None = None,
    fee: Decimal | None = None,
) -> list[Leg]:
    """The postings for one transaction of ``amount`` (positive, in ``account``'s currency).

    ``account`` is the account the message is about; ``counter`` the other own account a
    transfer, repayment or loan names (the card for a repayment, the loan for a loan). A side
    no message named yet goes to ``suspense:own_transfer`` (own transfers and repayments only).
    """
    if amount <= 0:
        raise PlanError("missing_amount", "The amount must be more than zero.")
    if kind in (Kind.CARD_REPAYMENT, Kind.OWN_TRANSFER) and account is None and counter is not None:
        # Only the receiving side is known (a card's "payment received"): the paying side waits in suspense.
        cur = counter.currency
        if kind == Kind.OWN_TRANSFER or counter.kind == AccountKind.CARD:
            return [_leg(counter, amount), Leg(SUSPENSE, -amount, cur)]
        return [Leg(SUSPENSE, amount, cur), _leg(counter, -amount)]
    if account is None:
        raise PlanError("account_unknown", "Pick the account this message is about.")
    cur = account.currency
    fee = fee or Decimal(0)
    interest = interest or Decimal(0)
    if kind in (Kind.SALARY, Kind.DEPOSIT, Kind.TRANSFER_IN):
        source = "income:" + kind.value
        return [_leg(account, amount), Leg(source, -amount, cur)]
    if kind in (Kind.CARD_PURCHASE, Kind.TRANSFER_OUT):
        expense = "expense:" + (Category.TRANSFER.value if kind == Kind.TRANSFER_OUT else category)
        legs = [Leg(expense, amount, cur)]
        if fee:
            legs.append(Leg("expense:fees", fee, cur))
        legs.append(_leg(account, -(amount + fee)))
        return legs
    if kind == Kind.BANK_FEE:
        return [Leg("expense:fees", amount, cur), _leg(account, -amount)]
    if kind == Kind.ATM_WITHDRAWAL:
        target = _leg(cash_account, amount) if cash_account is not None and cash_account.currency == cur else Leg("expense:cash", amount, cur)
        legs = [target]
        if fee:
            legs.append(Leg("expense:fees", fee, cur))
        legs.append(_leg(account, -(amount + fee)))
        return legs
    if kind in (Kind.REFUND, Kind.REVERSAL):
        # A refund that found its purchase lowers that category's spending; one that found none is its own line.
        other = {Category.REFUNDS.value: "income:refunds", Category.CASHBACK.value: "income:cashback"}.get(category, "expense:" + category)
        return [_leg(account, amount), Leg(other, -amount, cur)]
    if kind == Kind.CARD_REPAYMENT:
        # Never an expense: money moves from the paying account to the card.
        card, payer = (account, counter) if account.kind == AccountKind.CARD else (counter, account)
        if card is not None and payer is not None and card.currency != payer.currency:
            raise PlanError("currency_mismatch", "The card and the paying account have different currencies.")
        card_leg = _leg(card, amount) if card is not None else Leg(SUSPENSE, amount, cur)
        payer_leg = _leg(payer, -amount) if payer is not None else Leg(SUSPENSE, -amount, cur)
        if card is None and payer is None:
            raise PlanError("account_unknown", "Pick the card this payment was for.")
        return [card_leg, payer_leg]
    if kind == Kind.OWN_TRANSFER:
        # ``account`` is the side the money left, ``counter`` the side it reached.
        if counter is not None and counter.currency != cur:
            raise PlanError("currency_mismatch", "Both accounts of a transfer must have the same currency.")
        to_leg = _leg(counter, amount) if counter is not None else Leg(SUSPENSE, amount, cur)
        return [to_leg, _leg(account, -amount)]
    if kind == Kind.LOAN_DISBURSEMENT:
        if counter is None or counter.kind != AccountKind.LOAN:
            raise PlanError("loan_account_unknown", "Pick the loan account this money came from.")
        return [_leg(account, amount), _leg(counter, -amount)]
    if kind == Kind.LOAN_REPAYMENT:
        if counter is None or counter.kind != AccountKind.LOAN:
            raise PlanError("loan_account_unknown", "Pick the loan account this payment was for.")
        if interest >= amount:
            raise PlanError("interest_too_large", "The interest must be less than the payment.")
        # The whole payment moves from the bank to the loan (a transfer); the interest is charged on the
        # loan as its own pair, so only the interest is spending and the loan falls by the principal.
        legs = [_leg(counter, amount), _leg(account, -amount)]
        if interest:
            legs += [Leg("expense:interest", interest, cur), _leg(counter, -interest)]
        return legs
    raise PlanError("unknown_kind", "Pick what kind of transaction this is.")


def balanced(legs: list[Leg] | list[Posting]) -> bool:
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for leg in legs:
        sums[leg.currency] += leg.amount
    return all(value == 0 for value in sums.values()) and len(legs) >= 2


def reversal_legs(entry: JournalEntry) -> list[Leg]:
    return [Leg(p.ledger_account, -p.amount, p.currency, p.account_id) for p in entry.postings]


def _category_for(ledger_account: str) -> Category | None:
    flow = ledger_flow(ledger_account)
    if flow is None:
        return None
    if flow == Flow.TRANSFER:
        return Category.TRANSFER
    if flow == Flow.CASHBACK:
        return Category.CASHBACK
    if flow == Flow.REFUND:
        return Category.REFUNDS
    if flow == Flow.INCOME:
        return Category.INCOME
    name = ledger_account.split(":", 1)[1]
    if name == "interest":
        return Category.FEES
    try:
        return Category(name)
    except ValueError:
        return Category.OTHER


def transaction_category(legs: list[Leg], leg: Leg) -> Category:
    """The stage 2 category for the transaction a finance-account leg becomes.

    The leg that balances it exactly decides: another own account (or suspense) makes it a transfer, an
    income or expense account its category. A loan payment's bank leg is balanced by the loan, so it is a
    transfer, and the interest charged on the loan is the only spending (docs/finance.md#how-figures-are-counted)."""
    partner = next((o for o in legs if o is not leg and o.currency == leg.currency and o.amount == -leg.amount), None)
    if partner is not None:
        if partner.account_id is not None or partner.ledger_account.startswith("suspense:"):
            return Category.TRANSFER
        found = _category_for(partner.ledger_account)
        if found is not None:
            return found
    for other in (o for o in legs if o is not leg and o.account_id != leg.account_id):
        found = _category_for(other.ledger_account)
        if found is not None:
            return found
    return Category.TRANSFER


# -- the dashboard ------------------------------------------------------------------------------


def derived_legs(transaction: Transaction, account: Account) -> list[Leg]:
    """The postings a statement or hand-entered transaction stands for, by the shared rules (``flows.py``)."""
    amount = transaction.amount
    own = _leg(account, amount)
    flow = classify(transaction.category, amount, account.kind, transaction.category_source)
    other = {
        Flow.INCOME: "income:other", Flow.TRANSFER: "suspense:transfer", Flow.CASHBACK: "income:cashback", Flow.REFUND: "income:refunds",
    }.get(flow, "expense:" + transaction.category.value)
    return [own, Leg(other, -amount, transaction.currency)]


@dataclass
class Row:
    """One posting with what the dashboard filters on."""

    day: date
    ledger_account: str
    account_id: str | None
    amount: Decimal
    currency: str
    kind: str
    category: str | None
    merchant: str | None
    entry_id: str | None


class Money(BaseModel):
    currency: str
    amount: Decimal


class AccountLine(BaseModel):
    id: str
    name: str
    kind: AccountKind
    currency: str
    last4: str | None
    balance: Decimal  # the ledger's figure: opening balance plus every posting
    owed: Decimal | None = None  # cards and loans: what is owed, positive
    credit_limit: Decimal | None = None
    utilization_percent: int | None = None
    observed_balance: Decimal | None = None  # the last balance a message stated (an observation)
    observed_at: datetime | None = None
    difference: Decimal | None = None  # observed minus ledger


class CategoryLine(BaseModel):
    category: str
    currency: str
    amount: Decimal
    count: int


class MerchantLine(BaseModel):
    merchant: str
    currency: str
    amount: Decimal
    count: int


class MonthLine(BaseModel):
    month: str
    currency: str
    income: Decimal
    expenses: Decimal
    net: Decimal


class Dashboard(BaseModel):
    month: str
    income: list[Money]
    expenses: list[Money]
    surplus: list[Money]  # income, cashback and unmatched refunds less expenses (the summary's net)
    cashback: list[Money] = Field(default_factory=list)
    refunds: list[Money] = Field(default_factory=list)
    assets: list[Money]
    liabilities: list[Money]
    net_worth: list[Money]
    in_transit: list[Money]  # own transfers whose other side no message named yet
    accounts: list[AccountLine]
    cards: list[AccountLine]
    loans: list[AccountLine]
    categories: list[CategoryLine]
    merchants: list[MerchantLine]
    trend: list[MonthLine]  # the last 12 months, oldest first
    year: list[MonthLine]  # this calendar year, one line per currency (``month`` is the year)
    currencies: list[str]
    entry_count: int


def _months_back(month: str, count: int) -> list[str]:
    year, mon = (int(part) for part in month.split("-"))
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{mon:02d}")
        mon -= 1
        if mon == 0:
            year, mon = year - 1, 12
    return list(reversed(out))


def _money(sums: dict[str, Decimal]) -> list[Money]:
    return [Money(currency=c, amount=quantize(v, c)) for c, v in sorted(sums.items())]


@dataclass
class Filters:
    account_id: str | None = None
    currency: str | None = None
    category: str | None = None
    kind: str | None = None


def dashboard(
    *,
    month: str,
    today: date,
    accounts: list[Account],
    rows: list[Row],
    filters: Filters,
    limits: dict[str, Decimal] | None = None,
    observed: dict[str, tuple[Decimal, datetime]] | None = None,
    balances: dict[str, Decimal] | None = None,
    suspense: dict[str, Decimal] | None = None,
) -> Dashboard:
    """Every figure per currency, never added across currencies.

    Income is every credit to an ``income:`` account and expenses every debit to an
    ``expense:`` account (a refund's credit lowers them) in the month, by the shared rules
    (``flows.ledger_flow``): transfers (``expense:transfer``, ``income:transfer_in``) are neither,
    cashback and unmatched refunds are their own lines. ``balances`` are the
    accounts' balances (opening balance plus every transaction, which includes every posting on
    a finance account), ``suspense`` the open own-transfer sides per currency.
    """
    balances = balances or {}
    limits = limits or {}
    observed = observed or {}

    def keep(row: Row) -> bool:
        if filters.currency and row.currency != filters.currency:
            return False
        if filters.kind and row.kind != filters.kind:
            return False
        if filters.category and (row.category or "") != filters.category:
            return False
        return True

    entry_accounts: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.entry_id and row.account_id:
            entry_accounts[row.entry_id].add(row.account_id)

    def touches(row: Row) -> bool:
        if not filters.account_id:
            return True
        return row.account_id == filters.account_id or (row.entry_id is not None and filters.account_id in entry_accounts[row.entry_id])

    flow = [row for row in rows if keep(row) and touches(row)]
    income: dict[str, Decimal] = defaultdict(Decimal)
    expenses: dict[str, Decimal] = defaultdict(Decimal)
    cashback: dict[str, Decimal] = defaultdict(Decimal)
    refunds: dict[str, Decimal] = defaultdict(Decimal)
    categories: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    merchants: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    by_month: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    year = today.strftime("%Y")
    by_year: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for row in flow:
        row_month = row.day.strftime("%Y-%m")
        kind = ledger_flow(row.ledger_account)
        if kind in (Flow.CASHBACK, Flow.REFUND):
            if row_month == month:
                (cashback if kind == Flow.CASHBACK else refunds)[row.currency] += -row.amount
        elif kind == Flow.INCOME:
            by_month[(row_month, row.currency)][0] += -row.amount
            if row_month[:4] == year:
                by_year[row.currency][0] += -row.amount
            if row_month == month:
                income[row.currency] += -row.amount
        elif kind == Flow.SPEND:
            by_month[(row_month, row.currency)][1] += row.amount
            if row_month[:4] == year:
                by_year[row.currency][1] += row.amount
            if row_month == month:
                expenses[row.currency] += row.amount
                name = row.ledger_account.split(":", 1)[1]
                categories[(name, row.currency)].append(row.amount)
                if row.merchant:
                    merchants[(row.merchant.strip().upper()[:60], row.currency)].append(row.amount)

    suspense = {c: v for c, v in (suspense or {}).items() if v != 0}

    lines: list[AccountLine] = []
    assets: dict[str, Decimal] = defaultdict(Decimal)
    liabilities: dict[str, Decimal] = defaultdict(Decimal)
    for account in accounts:
        if account.archived:
            continue
        if filters.account_id and account.id != filters.account_id:
            continue
        if filters.currency and account.currency != filters.currency:
            continue
        balance = quantize(balances.get(account.id, account.opening_balance), account.currency)
        line = AccountLine(id=account.id, name=account.name, kind=account.kind, currency=account.currency, last4=account.last4, balance=balance)
        if account.kind in ASSET_KINDS:
            assets[account.currency] += balance
        else:
            owed = max(-balance, Decimal(0))
            line.owed = quantize(owed, account.currency)
            liabilities[account.currency] += owed
            # A credit balance on a card (overpaid) is an asset, not a negative debt.
            if balance > 0:
                assets[account.currency] += balance
            limit = limits.get(account.id)
            if limit and limit > 0 and account.kind == AccountKind.CARD:
                line.credit_limit = limit
                line.utilization_percent = min(999, int((owed * 100 / limit).to_integral_value()))
        seen = observed.get(account.id)
        if seen is not None:
            line.observed_balance, line.observed_at = seen
            line.difference = quantize(seen[0] - balance, account.currency) if account.kind in ASSET_KINDS else None
        lines.append(line)
    # A month whose entries were all undone nets to zero: no figure rather than "0.00".
    income = defaultdict(Decimal, {c: v for c, v in income.items() if v != 0})
    expenses = defaultdict(Decimal, {c: v for c, v in expenses.items() if v != 0})
    cashback = defaultdict(Decimal, {c: v for c, v in cashback.items() if v != 0})
    refunds = defaultdict(Decimal, {c: v for c, v in refunds.items() if v != 0})
    currencies = sorted({*assets, *liabilities, *income, *expenses})
    worth = {c: assets.get(c, Decimal(0)) - liabilities.get(c, Decimal(0)) for c in currencies if c in assets or c in liabilities}
    surplus = {c: income.get(c, Decimal(0)) + cashback.get(c, Decimal(0)) + refunds.get(c, Decimal(0)) - expenses.get(c, Decimal(0))
               for c in sorted({*income, *expenses, *cashback, *refunds})}
    trend = []
    for m in _months_back(month, 12):
        for cur in sorted({c for (mm, c) in by_month if mm == m}):
            inc, exp = by_month[(m, cur)]
            trend.append(MonthLine(month=m, currency=cur, income=quantize(inc, cur), expenses=quantize(exp, cur), net=quantize(inc - exp, cur)))
    return Dashboard(
        month=month,
        income=_money(income),
        expenses=_money(expenses),
        surplus=_money(surplus),
        cashback=_money(cashback),
        refunds=_money(refunds),
        assets=_money(assets),
        liabilities=_money(liabilities),
        net_worth=_money(worth),
        in_transit=_money(suspense),
        accounts=[line for line in lines if line.kind in ASSET_KINDS],
        cards=[line for line in lines if line.kind == AccountKind.CARD],
        loans=[line for line in lines if line.kind == AccountKind.LOAN],
        categories=sorted(
            (CategoryLine(category=name, currency=cur, amount=quantize(sum(v, Decimal(0)), cur), count=len(v)) for (name, cur), v in categories.items() if sum(v, Decimal(0)) != 0),
            key=lambda line: (line.currency, -line.amount),
        ),
        merchants=sorted(
            (MerchantLine(merchant=name, currency=cur, amount=quantize(sum(v, Decimal(0)), cur), count=len(v)) for (name, cur), v in merchants.items() if sum(v, Decimal(0)) != 0),
            key=lambda line: (line.currency, -line.amount),
        )[:20],
        trend=trend,
        year=[MonthLine(month=year, currency=cur, income=quantize(v[0], cur), expenses=quantize(v[1], cur), net=quantize(v[0] - v[1], cur))
              for cur, v in sorted(by_year.items())],
        currencies=currencies,
        entry_count=len({row.entry_id for row in flow if row.entry_id}),
    )


def unusual(amount: Decimal, history: list[Decimal]) -> bool:
    """An amount at least three times the median of at least five earlier ones (an alert, never a block)."""
    if len(history) < 5:
        return False
    ordered = sorted(history)
    middle = ordered[len(ordered) // 2] if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    return middle > 0 and amount >= middle * 3


__all__ = [
    "ASSET_KINDS",
    "AccountLine",
    "Dashboard",
    "EntrySource",
    "EntryStatus",
    "Filters",
    "JournalEntry",
    "Leg",
    "PlanError",
    "Posting",
    "Row",
    "balanced",
    "dashboard",
    "derived_legs",
    "ledger_account",
    "plan",
    "reversal_legs",
    "transaction_category",
    "unusual",
]
