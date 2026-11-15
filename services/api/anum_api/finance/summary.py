"""The Financial Twin's read-outs (docs/finance.md#summary).

All figures are per currency: nothing is converted or added across currencies. Dates are
the person's own (``FinanceSettings.timezone``, Asia/Dubai by default): "this month" and
"today" are in that time zone.

* Balances: an account's opening balance plus every transaction in it.
* Spend, income, refunds, cashback and transfers: counted by the one shared rule
  (``flows.py``, docs/finance.md#how-figures-are-counted). Spend by category is net of the
  refunds filed in that category; transfers and card payments are neither spend nor income.
* Uncategorized: rows no rule recognised are in none of those figures; the summary counts them
  (``uncategorized_count`` and the amounts in and out) so the page says what it left out.
* Upcoming bills: every active bill's due dates in the next ``days`` days (default 30) that
  have no matched payment, and this month's passed ones that still have none (overdue).
* Debts: what is owed (following matched payments and linked accounts, ``obligations.py``)
  and the minimum payments, per currency. Net worth is account balances less only the debts
  that have no account of their own.
* Bill suggestions: money out with the same description at a steady interval and a
  steady amount (three times or more in 180 days), not yet saved as a bill.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from . import commitment_math as M
from . import obligations as O
from .flows import EXCLUDED_KINDS, NOT_SPEND, Flow, figures, flow_of, kinds_of
from .models import (
    AccountKind,
    Bill,
    BillSource,
    Cadence,
    Category,
    Debt,
    FinanceSettings,
    Goal,
    Holding,
    Transaction,
    TransactionType,
)
from .money import quantize, totals
from .store import FinanceStore, TransactionFilter

UPCOMING_DAYS = 30
MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class CurrencyAmount(BaseModel):
    currency: str
    amount: Decimal


class AccountBalance(BaseModel):
    id: str
    name: str
    kind: AccountKind
    currency: str
    institution: str | None
    last4: str | None
    balance: Decimal
    archived: bool


class CategorySpend(BaseModel):
    category: Category
    currency: str
    amount: Decimal  # money out, as a positive figure
    count: int


class UpcomingBill(BaseModel):
    id: str
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    due_on: date
    days_until: int  # negative: overdue
    category: Category
    overdue: bool = False  # passed this month with no matched payment


class GoalProgress(BaseModel):
    id: str
    name: str
    currency: str
    target_amount: Decimal
    saved_amount: Decimal
    percent: int
    target_on: date | None


class NetWorthPart(BaseModel):
    """One currency's net worth, line by line (docs/finance.md#how-figures-are-counted)."""

    currency: str
    accounts: Decimal  # account balances (cards and loans by their sign); an investment account with holdings counts them at cost
    receivables: Decimal  # money lent to people, still owed back
    holdings_at_cost: Decimal  # holdings with a cost basis, instead of their account's ledger balance
    debts: Decimal  # debts with no account of their own, money borrowed from people included
    installments: Decimal  # what is still due on installment plans (Tamara, Tabby)
    net: Decimal


class NetWorthExcluded(BaseModel):
    """Something left out of net worth, with the reason (a holding without a cost basis has no value ANUM can use)."""

    kind: Literal["holding"]
    name: str
    currency: str
    reason: Literal["no_cost_basis", "currency_differs"]


class ExcludedFlow(BaseModel):
    """Money that moved this month but is neither income nor spend, by why (docs/finance.md#dashboard)."""

    kind: Literal["own_transfer", "card_payment", "loan_principal", "lending", "other_transfer"]
    currency: str
    money_in: Decimal
    money_out: Decimal  # positive
    count: int


class PositionPart(BaseModel):
    """One currency's cash against card credit (docs/finance.md#dashboard). Nothing converted."""

    currency: str
    cash: Decimal  # bank and cash accounts
    investments: Decimal  # investment accounts at their ledger balance
    card_owed: Decimal  # what cards owe, as a positive figure
    card_in_credit: Decimal  # cards paid above what they owe
    card_limit: Decimal | None = None  # the known limits (iPhone SMS card mapping); None when no card has one
    card_available: Decimal | None = None  # limit less owed, for the cards whose limit is known
    loans_owed: Decimal  # loan accounts, as a positive figure


class UpcomingPayment(BaseModel):
    """A bill due date or an installment due in the next 30 days (or overdue this month), not yet matched to a payment."""

    kind: Literal["bill", "installment"]
    id: str
    name: str
    provider: str | None = None
    amount: Decimal
    currency: str
    due_on: date
    days_until: int
    overdue: bool = False
    # pending: no payment matched yet; needs_review: the provider's statement row waits for review.
    status: Literal["pending", "needs_review"] = "pending"


class IncomeLine(BaseModel):
    """The month's money in, by kind: the salary the salary check found, interest received, other income,
    cashback and refunds without a purchase. Income is ``salary`` + ``interest`` + ``other_income``."""

    kind: Literal["salary", "interest", "other_income", "cashback", "refunds"]
    currency: str
    amount: Decimal
    count: int


class SalaryStatus(BaseModel):
    """The shown month in the salary check (``salary.salary_months``, the same as ``GET /salary/months``)."""

    month: str
    currency: str
    pay_on: date
    expected_net: Decimal
    received: Decimal
    difference: Decimal
    status: Literal["received", "late", "partial", "missing", "upcoming"]
    basis: Literal["confirmed", "payslip", "suggested"] | None
    transaction_id: str | None
    waiting: int  # months whose salary waits for a confirm or reject
    missing: int


class ReviewCounts(BaseModel):
    """What waits for the person, with where each is reviewed."""

    uncategorized: int = 0  # this month's rows no rule recognised
    sms_review: int = 0  # iPhone SMS and Apple Pay messages in the review queue
    links_need_review: int = 0  # links to bills, loans and other records waiting after the payment changed
    installments_review: int = 0  # installment statement rows marked for review
    total: int = 0


class AccountFreshness(BaseModel):
    """How current one account's figures are: what feeds it, when each source last delivered, and the last
    balance a bank stated (verified) against the ledger's own (estimated)."""

    account_id: str
    sources: list[Literal["manual", "sms", "apple_wallet", "statement"]]
    last_activity_on: date | None = None
    last_import_at: datetime | None = None
    last_sms_at: datetime | None = None
    last_apple_pay_at: datetime | None = None
    verified_balance: Decimal | None = None
    verified_at: datetime | None = None
    verified_by: Literal["sms", "statement"] | None = None
    difference: Decimal | None = None  # verified (SMS) less the ledger balance now; None when no SMS stated one
    balance_kind: Literal["verified", "estimated"] = "estimated"
    state: Literal["current", "stale", "manual"] = "manual"
    credit_limit: Decimal | None = None


class FinanceSummary(BaseModel):
    month: str
    start: date
    end: date
    today: date
    base_currency: str
    timezone: str
    accounts: list[AccountBalance]
    balances: list[CurrencyAmount]
    spend_by_category: list[CategorySpend]
    income: list[CurrencyAmount]
    cashback: list[CurrencyAmount] = Field(default_factory=list)
    refunds: list[CurrencyAmount] = Field(default_factory=list)
    spend: list[CurrencyAmount]
    net: list[CurrencyAmount]
    transaction_count: int
    upcoming_bills: list[UpcomingBill]
    bills_due: list[CurrencyAmount]
    debts: list[CurrencyAmount]
    # Debts no account counts yet (no card or loan account of their own): the only ones net worth subtracts.
    unlinked_debts: list[CurrencyAmount] = Field(default_factory=list)
    net_worth: list[CurrencyAmount] = Field(default_factory=list)
    net_worth_parts: list[NetWorthPart] = Field(default_factory=list)
    net_worth_excluded: list[NetWorthExcluded] = Field(default_factory=list)
    minimum_payments: list[CurrencyAmount]
    debt_count: int
    goals: list[GoalProgress]
    month_rows_capped: bool = False  # the month had more rows than one read takes: figures are partial
    # Rows no rule recognised (type ``uncategorized``): in none of the figures above, held for review. Shown as
    # "N uncategorized, not counted" so a missing income or spend is never a silent zero.
    uncategorized_count: int = 0
    uncategorized_in: list[CurrencyAmount] = Field(default_factory=list)
    uncategorized_out: list[CurrencyAmount] = Field(default_factory=list)
    # The dashboard contract (docs/finance.md#dashboard): web and phone read these instead of working anything out.
    detail: Literal["full", "figures"] = "full"
    excluded: list[ExcludedFlow] = Field(default_factory=list)
    positions: list[PositionPart] = Field(default_factory=list)
    upcoming_payments: list[UpcomingPayment] = Field(default_factory=list)
    payments_due: list[CurrencyAmount] = Field(default_factory=list)  # the upcoming payments' total per currency
    # ``full`` only (``figures`` leaves them empty): what needs the person's attention and how current each account is.
    income_breakdown: list[IncomeLine] = Field(default_factory=list)
    salary: SalaryStatus | None = None
    review: ReviewCounts = Field(default_factory=ReviewCounts)
    freshness: list[AccountFreshness] = Field(default_factory=list)


class BillSuggestion(BaseModel):
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    next_due_on: date
    category: Category
    account_id: str
    occurrences: int
    # docs/finance.md#recurring-payments: what kind of payment it is, the merchant key it was found for, the newest
    # amount and how far it moved from the usual one (5 percent or more), and the payments it rests on (newest first).
    kind: str = "other"
    merchant: str | None = None
    last_amount: Decimal | None = None
    price_change: Decimal | None = None
    transaction_ids: list[str] = Field(default_factory=list)


def settings_for(store: FinanceStore, context: TenantContext) -> FinanceSettings:
    return store.get_settings(context) or FinanceSettings()


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Dubai")


def today_in(settings: FinanceSettings, now: datetime | None = None) -> date:
    moment = now or datetime.now(tz=ZoneInfo("UTC"))
    return moment.astimezone(zone(settings.timezone)).date()


def month_range(month: str | None, today: date) -> tuple[str, date, date]:
    """``("2026-10", 1 Oct, 31 Oct)``; ``None`` is the month of ``today``."""
    if month:
        found = MONTH.match(month)
        if not found:
            raise ValueError("A month is written YYYY-MM, for example 2026-10.")
        year, number = int(found.group(1)), int(found.group(2))
    else:
        year, number = today.year, today.month
    last = calendar.monthrange(year, number)[1]
    return f"{year:04d}-{number:02d}", date(year, number, 1), date(year, number, last)


def add_months(day: date, months: int, anchor_day: int | None = None) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    wanted = anchor_day or day.day
    return date(year, month, min(wanted, calendar.monthrange(year, month)[1]))


def next_after(due: date, cadence: Cadence, anchor_day: int) -> date | None:
    if cadence == Cadence.WEEKLY:
        return due + timedelta(days=7)
    if cadence == Cadence.MONTHLY:
        return add_months(due, 1, anchor_day)
    if cadence == Cadence.QUARTERLY:
        return add_months(due, 3, anchor_day)
    if cadence == Cadence.YEARLY:
        return add_months(due, 12, anchor_day)
    return None


def due_dates(bill: Bill, start: date, end: date) -> list[date]:
    """The bill's due dates from ``start`` to ``end`` (inclusive); a past one rolls forward."""
    found: list[date] = []
    due: date | None = bill.next_due_on
    anchor = bill.next_due_on.day
    guard = 0
    while due is not None and due <= end and guard < 600:
        guard += 1
        if due >= start:
            found.append(due)
        due = next_after(due, bill.cadence, anchor)
    return found


def account_balances(store: FinanceStore, context: TenantContext) -> list[AccountBalance]:
    sums = store.account_sums(context)
    return [
        AccountBalance(
            id=a.id, name=a.name, kind=a.kind, currency=a.currency, institution=a.institution, last4=a.last4,
            balance=quantize(a.opening_balance + sums.get(a.id, Decimal(0)), a.currency), archived=a.archived,
        )
        for a in store.list_accounts(context)
    ]


def _amounts(values: dict[str, Decimal]) -> list[CurrencyAmount]:
    return [CurrencyAmount(currency=c, amount=quantize(v, c)) for c, v in values.items()]


def net_worth(accounts: list[AccountBalance], loose: dict[str, Decimal], holdings: list[Holding], links: O.Links | None,
              installments: list, today: date) -> tuple[list[NetWorthPart], list[NetWorthExcluded]]:
    """Net worth per currency: account balances, plus money lent to people and holdings at cost, less debts with no
    account of their own (money borrowed from people included) and what is still due on installment plans.

    An investment account with holdings counts them at their cost basis (ANUM keeps no market prices) instead of its
    ledger balance, so a purchase is never counted twice. A holding without a cost basis, or in another currency
    than its account, is left out and listed in ``excluded``."""
    from .personal import _still_due

    live = {a.id: a for a in accounts if not a.archived}
    at_cost: dict[str, Decimal] = {}
    replaced: set[str] = set()
    excluded: list[NetWorthExcluded] = []
    for h in holdings:
        account = live.get(h.account_id)
        if account is None:
            continue
        if h.cost_basis <= 0:
            excluded.append(NetWorthExcluded(kind="holding", name=h.symbol, currency=h.currency, reason="no_cost_basis"))
        elif h.currency != account.currency:
            excluded.append(NetWorthExcluded(kind="holding", name=h.symbol, currency=h.currency, reason="currency_differs"))
        else:
            at_cost[h.currency] = at_cost.get(h.currency, Decimal(0)) + h.cost_basis
            replaced.add(account.id)
    own = totals((a.currency, a.balance) for a in live.values() if a.id not in replaced)
    lent = O.lent_receivables(links.person_loans, links.loan_links, today) if links is not None else {}
    plans = totals((e.currency, _still_due(e)) for e in installments if e.source_status not in ("paid", "refunded") and _still_due(e) > 0)
    parts: list[NetWorthPart] = []
    for c in sorted(set(own) | set(lent) | set(at_cost) | set(loose) | set(plans)):
        values = [own.get(c, Decimal(0)), lent.get(c, Decimal(0)), at_cost.get(c, Decimal(0)), loose.get(c, Decimal(0)), plans.get(c, Decimal(0))]
        a, r, h, d, i = (quantize(v, c) for v in values)
        parts.append(NetWorthPart(currency=c, accounts=a, receivables=r, holdings_at_cost=h, debts=d, installments=i, net=a + r + h - d - i))
    return parts, sorted(excluded, key=lambda e: (e.currency, e.name))


def upcoming_bills(bills: list[Bill], today: date, days: int = UPCOMING_DAYS, links: list | None = None) -> list[UpcomingBill]:
    """Due dates in the next ``days`` days without a matched payment, and this month's overdue ones.

    ``links`` are the person's ``bill`` links (``obligations.load``); a due date with an active one is paid."""
    end = today + timedelta(days=days)
    rows = [
        UpcomingBill(
            id=d.bill.id, name=d.bill.name, amount=d.bill.amount, currency=d.bill.currency, cadence=d.bill.cadence, due_on=d.due_on,
            days_until=(d.due_on - today).days, category=d.bill.category, overdue=d.overdue,
        )
        for d in O.unpaid(bills, links or [], today, end)
    ]
    return sorted(rows, key=lambda r: (r.due_on, r.name.lower()))


MONTH_ROW_LIMIT = 50_000  # what one ``transactions_between`` read returns at most (store.py)
STALE_CAPTURE_DAYS = 7  # an account fed by iPhone SMS or Apple Pay with nothing newer is stale
STALE_STATEMENT_DAYS = 35  # an account fed only by statements: about one statement a month
SOURCE_WINDOW_DAYS = 120  # how far back the summary looks for the last SMS and Apple Pay capture per account


@dataclass
class SourceFacts:
    """What the iPhone SMS, Apple Pay and link stores say, read in their own short unit of work (``source_facts``)."""

    limits: dict[str, Decimal] = field(default_factory=dict)
    observed: dict[str, tuple[Decimal, datetime]] = field(default_factory=dict)
    mapped_sms: set[str] = field(default_factory=set)
    mapped_wallet: set[str] = field(default_factory=set)
    last_sms: dict[str, datetime] = field(default_factory=dict)
    last_wallet: dict[str, datetime] = field(default_factory=dict)
    sms_review: int = 0
    links_review: int = 0


def source_facts(context: TenantContext, today: date) -> SourceFacts:
    """Card limits, the banks' stated balances, which accounts SMS and Apple Pay feed and when each last delivered,
    and the review queues. Read only, the caller's own rows."""
    from .links_store import LinkStatus, open_link_stores
    from .sms_service import settings_for as sms_settings_for
    from .sms_store import MessageStatus, source_kind

    with open_link_stores(context) as (_finance, sms, links):
        settings = sms_settings_for(sms, context)
        observed = sms.latest_observations(context)
        entries = sms.list_entries(context, start=today - timedelta(days=SOURCE_WINDOW_DAYS))
        accounts_of = {e.id: {p.account_id for p in e.postings if p.account_id} for e in entries}
        sources = sms.entry_sources(context, sorted(accounts_of)) if accounts_of else {}
        _messages, sms_review = sms.list_messages(context, status=MessageStatus.REVIEW.value, limit=1, offset=0)
        _links, links_review = links.list_links(context, canonical_id=None, dimension=None, target_type=None, target_id=None,
                                                status=LinkStatus.NEEDS_REVIEW.value, limit=1, offset=0)
    facts = SourceFacts(limits={m.account_id: m.credit_limit for m in settings.account_map if m.credit_limit},
                        observed=dict(observed), mapped_sms={m.account_id for m in settings.account_map},
                        mapped_wallet={c.account_id for c in settings.wallet_cards}, sms_review=sms_review, links_review=links_review)
    if settings.default_account_id:
        facts.mapped_sms.add(settings.default_account_id)
    for entry_id, messages in sources.items():
        for message in messages:
            kind = source_kind(message.source)
            target = facts.last_sms if kind == "sms" else facts.last_wallet if kind == "wallet" else None
            if target is None:
                continue
            for account_id in accounts_of.get(entry_id, set()):
                if account_id not in target or message.received_at > target[account_id]:
                    target[account_id] = message.received_at
    return facts


def positions(accounts: list[AccountBalance], limits: dict[str, Decimal]) -> list[PositionPart]:
    """Cash (bank and cash accounts) against card credit, per currency; a card's available credit only where its
    limit is known."""
    zero = Decimal(0)
    out: dict[str, dict[str, Decimal | None]] = {}
    for a in accounts:
        if a.archived:
            continue
        row = out.setdefault(a.currency, {"cash": zero, "investments": zero, "card_owed": zero, "card_in_credit": zero, "loans_owed": zero,
                                          "card_limit": None, "card_available": None})

        def bump(key: str, value: Decimal, row: dict[str, Decimal | None] = row) -> None:
            row[key] = (row[key] or zero) + value

        if a.kind in (AccountKind.BANK, AccountKind.CASH):
            bump("cash", a.balance)
        elif a.kind == AccountKind.INVESTMENT:
            bump("investments", a.balance)
        elif a.kind == AccountKind.LOAN:
            bump("loans_owed", max(zero, -a.balance))
        elif a.kind == AccountKind.CARD:
            owed = max(zero, -a.balance)
            bump("card_owed", owed)
            bump("card_in_credit", max(zero, a.balance))
            limit = limits.get(a.id)
            if limit:
                bump("card_limit", limit)
                bump("card_available", limit - owed)
    return [PositionPart(currency=c, **{k: (quantize(v, c) if v is not None else None) for k, v in row.items()})  # type: ignore[arg-type]
            for c, row in sorted(out.items())]


def upcoming_payments(bills: list[UpcomingBill], installments: list, today: date, days: int = UPCOMING_DAYS) -> list[UpcomingPayment]:
    """Bills without a matched payment (``upcoming_bills``) and installment plan rows still due, in one list by date.

    An installment the provider's statement marks paid or refunded is not due; a schedule row is never proof of payment,
    so a ``scheduled`` one stays pending until the provider or a matched payment says otherwise (docs/finance-links.md)."""
    from .personal import _still_due

    end = today + timedelta(days=days)
    start = today.replace(day=1)
    rows = [UpcomingPayment(kind="bill", id=b.id, name=b.name, amount=b.amount, currency=b.currency, due_on=b.due_on, days_until=b.days_until,
                            overdue=b.overdue) for b in bills]
    for e in installments:
        left = _still_due(e)
        if e.source_status in ("paid", "refunded") or left <= 0 or not (start <= e.due_on <= end):
            continue
        rows.append(UpcomingPayment(
            kind="installment", id=f"{e.provider}:{e.order_id}:{e.installment}", name=f"{e.merchant} {e.installment}/{e.total_installments}",
            provider=e.provider, amount=quantize(left, e.currency), currency=e.currency, due_on=e.due_on, days_until=(e.due_on - today).days,
            overdue=e.due_on < today, status="needs_review" if e.source_status == "review" else "pending"))
    return sorted(rows, key=lambda r: (r.due_on, r.name.lower(), r.id))


def freshness(accounts: list[AccountBalance], facts: SourceFacts, last_activity: dict[str, date], imports: list,
              now: datetime) -> list[AccountFreshness]:
    """Per live account: its sources, when each last delivered, and its verified balance (the bank's last stated one,
    from an SMS, or the closing balance of a statement that tied) against the ledger's estimate."""
    last_import: dict[str, datetime] = {}
    tied: dict[str, tuple[Decimal, datetime]] = {}
    for record in imports:
        last_import[record.account_id] = max(last_import.get(record.account_id, record.created_at), record.created_at)
        check = record.reconciliation or {}
        if check.get("status") == "tied" and check.get("closing"):
            try:
                closing = Decimal(str(check["closing"]))
            except ArithmeticError:
                continue
            if record.account_id not in tied or record.created_at > tied[record.account_id][1]:
                tied[record.account_id] = (closing, record.created_at)
    out: list[AccountFreshness] = []
    for a in accounts:
        if a.archived:
            continue
        sources: set[str] = set()
        if a.id in facts.mapped_sms or a.id in facts.last_sms:
            sources.add("sms")
        if a.id in facts.mapped_wallet or a.id in facts.last_wallet:
            sources.add("apple_wallet")
        if a.id in last_import:
            sources.add("statement")
        automated = bool(sources)
        sources.add("manual")
        sms_seen, statement_seen = facts.observed.get(a.id), tied.get(a.id)
        verified_by: Literal["sms", "statement"] | None = None
        verified: tuple[Decimal, datetime] | None = None
        if sms_seen and (statement_seen is None or sms_seen[1] >= statement_seen[1]):
            verified_by, verified = "sms", sms_seen
        elif statement_seen:
            verified_by, verified = "statement", statement_seen
        captures = [t for t in (facts.last_sms.get(a.id), facts.last_wallet.get(a.id)) if t is not None]
        state: Literal["current", "stale", "manual"]
        if not automated:
            state = "manual"
        elif captures or a.id in facts.mapped_sms or a.id in facts.mapped_wallet:
            newest = max([*captures, *([last_import[a.id]] if a.id in last_import else [])], default=None)
            state = "current" if newest is not None and now - newest <= timedelta(days=STALE_CAPTURE_DAYS) else "stale"
        else:
            state = "current" if now - last_import[a.id] <= timedelta(days=STALE_STATEMENT_DAYS) else "stale"
        difference = quantize(sms_seen[0] - a.balance, a.currency) if verified_by == "sms" and sms_seen else None
        out.append(AccountFreshness(
            account_id=a.id, sources=sorted(sources), last_activity_on=last_activity.get(a.id),  # type: ignore[arg-type]
            last_import_at=last_import.get(a.id), last_sms_at=facts.last_sms.get(a.id), last_apple_pay_at=facts.last_wallet.get(a.id),
            verified_balance=quantize(verified[0], a.currency) if verified else None, verified_at=verified[1] if verified else None,
            verified_by=verified_by, difference=difference,
            balance_kind="verified" if verified_by == "sms" and difference == 0 else "estimated", state=state,
            credit_limit=facts.limits.get(a.id)))
    return out


def build_summary(store: FinanceStore, context: TenantContext, month: str | None = None, *, now: datetime | None = None,
                  links: O.Links | None = None, sources: SourceFacts | None = None,
                  detail: Literal["full", "figures"] = "full") -> FinanceSummary:
    """``links`` are the person's bill and debt links (``obligations.load``, read in its own unit of work first);
    ``sources`` what iPhone SMS, Apple Pay and the link store say (``source_facts``, also read first). ``figures``
    leaves out the salary, the income breakdown, the review counts and the account freshness (the six-month charts)."""
    settings = settings_for(store, context)
    today = today_in(settings, now)
    label, start, end = month_range(month, today)
    raw_accounts = store.list_accounts(context)
    sums = store.account_sums(context)
    accounts = account_balances(store, context)
    month_rows: list[Transaction] = store.transactions_between(context, start, end)
    found = figures(month_rows, kinds_of(raw_accounts), links.lending_ids if links else None)
    category_rows = sorted(
        (CategorySpend(category=cat, currency=cur, amount=quantize(v, cur), count=n) for (cat, cur), (v, n) in found.by_category.items() if v != 0),
        key=lambda r: (r.currency, -r.amount, r.category.value),
    )
    bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    upcoming = upcoming_bills(bills, today, links=links.bill_links if links else None)
    debts = O.current_debts([d for d in store.list_items(context, "debt") if isinstance(d, Debt)], raw_accounts, sums, links, today)
    goals = O.current_goals([g for g in store.list_items(context, "goal") if isinstance(g, Goal)], raw_accounts, sums)
    balances = totals((a.currency, a.balance) for a in accounts if not a.archived)
    loose = totals((d.currency, d.balance) for d in O.unlinked(debts, raw_accounts) if d.balance > 0)
    holdings = [h for h in store.list_items(context, "holding") if isinstance(h, Holding)]
    parts, excluded = net_worth(accounts, loose, holdings, links, store.get_planning(context).installment_entries, today)
    worth = {p.currency: p.net for p in parts}
    installments = store.get_planning(context).installment_entries
    payments = upcoming_payments(upcoming, installments, today)
    facts = sources or SourceFacts()
    extra = _details(store, context, label, today, now, raw_accounts, accounts, month_rows, found, links, facts, installments) if detail == "full" else {}
    return FinanceSummary(
        detail=detail,
        excluded=[ExcludedFlow(kind=k, currency=c, money_in=quantize(v[0], c), money_out=quantize(v[1], c), count=v[2])  # type: ignore[arg-type]
                  for (k, c), v in sorted(found.excluded.items(), key=lambda kv: (kv[0][1], EXCLUDED_KINDS.index(kv[0][0])))],
        positions=positions(accounts, facts.limits),
        upcoming_payments=payments, payments_due=_amounts(totals((p.currency, p.amount) for p in payments)),
        **extra,
        month=label, start=start, end=end, today=today, base_currency=settings.base_currency, timezone=settings.timezone,
        accounts=accounts,
        balances=_amounts(balances),
        spend_by_category=category_rows,
        income=_amounts(found.income), cashback=_amounts(found.cashback), refunds=_amounts(found.refunds), spend=_amounts(found.spend),
        net=_amounts(found.net()),
        uncategorized_count=found.uncategorized_count, uncategorized_in=_amounts(found.uncategorized_in),
        uncategorized_out=_amounts(found.uncategorized_out),
        transaction_count=len(month_rows), month_rows_capped=len(month_rows) >= MONTH_ROW_LIMIT,
        upcoming_bills=upcoming,
        bills_due=_amounts(totals((b.currency, b.amount) for b in upcoming)),
        debts=_amounts(totals((d.currency, d.balance) for d in debts)),
        unlinked_debts=_amounts(loose), net_worth=_amounts(worth), net_worth_parts=parts, net_worth_excluded=excluded,
        minimum_payments=_amounts(totals((d.currency, M.debt_minimum(d)) for d in debts if d.balance > 0)),
        debt_count=len(debts),
        goals=[
            GoalProgress(
                id=g.id, name=g.name, currency=g.currency, target_amount=g.target_amount, saved_amount=g.saved_amount,
                percent=max(0, min(100, int(g.saved_amount * 100 / g.target_amount))) if g.target_amount > 0 else 0,
                target_on=g.target_on,
            )
            for g in goals
        ],
    )


INCOME_ORDER = {"salary": 0, "interest": 1, "other_income": 2, "cashback": 3, "refunds": 4}


def _salary(store: FinanceStore, context: TenantContext, label: str, today: date, raw_accounts: list, links: O.Links | None
            ) -> tuple[set[str], SalaryStatus | None]:
    """The salary check's own answer (``salary.salary_months`` over the same rows ``GET /salary/months`` reads): every
    month's salary transaction id, and the shown month."""
    from . import personal as P
    from . import salary as SAL

    profile = links.salary_profile if links is not None else None
    if not isinstance(profile, P.SalaryProfile) or links is None:
        return set(), None
    first, last = SAL.rows_window(profile, today)
    months = SAL.salary_months(profile, SAL.salary_rows(store.transactions_between(context, first, last), profile), raw_accounts, today,
                               links.payslips, lending_ids=links.lending_ids, payslip_credits=links.payslip_credits)
    ids = {m.transaction.transaction_id for m in months if m.transaction is not None}
    shown = next((m for m in months if m.month == label), None)
    if shown is None:
        return ids, None
    return ids, SalaryStatus(
        month=shown.month, currency=profile.currency, pay_on=shown.pay_on, expected_net=shown.expected.net, received=shown.received,
        difference=shown.received - shown.expected.net, status=shown.status, basis=shown.basis,
        transaction_id=shown.transaction.transaction_id if shown.transaction else None,
        waiting=sum(1 for m in months if m.basis == "suggested"), missing=sum(1 for m in months if m.status == "missing"))


def _details(store: FinanceStore, context: TenantContext, label: str, today: date, now: datetime | None, raw_accounts: list,
             accounts: list[AccountBalance], month_rows: list[Transaction], found, links: O.Links | None, facts: SourceFacts,
             installments: list) -> dict:
    """The dashboard's ``full`` part: income by kind, the salary month, what waits for review and how current each account is."""
    salary_ids, salary_view = _salary(store, context, label, today, raw_accounts, links)
    kinds = kinds_of(raw_accounts)
    lending = links.lending_ids if links else None
    lines: dict[tuple[str, str], list] = {}
    for t in month_rows:
        if t.amount == 0:
            continue
        flow = flow_of(t, kinds, lending)
        if flow == Flow.INCOME:
            kind = "salary" if t.id in salary_ids else "interest" if t.category == Category.INTEREST else "other_income"
        elif flow == Flow.CASHBACK:
            kind = "cashback"
        elif flow == Flow.REFUND:
            kind = "refunds"
        else:
            continue
        slot = lines.setdefault((kind, t.currency), [Decimal(0), 0])
        slot[0] += t.amount
        slot[1] += 1
    imports, _ = store.list_imports(context, limit=5_000, offset=0)
    last_activity: dict[str, date] = {}
    for a in raw_accounts:
        if a.archived:
            continue
        newest, _count = store.list_transactions(context, TransactionFilter(account_id=a.id), limit=1, offset=0)
        if newest:
            last_activity[a.id] = newest[0].posted_on
    review = ReviewCounts(uncategorized=found.uncategorized_count, sms_review=facts.sms_review, links_need_review=facts.links_review,
                          installments_review=sum(1 for e in installments if e.source_status == "review"))
    review.total = review.uncategorized + review.sms_review + review.links_need_review + review.installments_review
    return {
        "income_breakdown": [IncomeLine(kind=k, currency=c, amount=quantize(v[0], c), count=v[1])  # type: ignore[arg-type]
                             for (k, c), v in sorted(lines.items(), key=lambda kv: (kv[0][1], INCOME_ORDER[kv[0][0]]))],
        "salary": salary_view, "review": review,
        "freshness": freshness(accounts, facts, last_activity, imports, now or datetime.now(tz=ZoneInfo("UTC"))),
    }


# -- recurring bill detection -------------------------------------------------------------------

_CADENCE_DAYS = ((Cadence.WEEKLY, 6, 8), (Cadence.MONTHLY, 26, 35), (Cadence.QUARTERLY, 84, 98), (Cadence.YEARLY, 350, 380))


def _payee(description: str) -> str:
    text = re.sub(r"\d+", " ", description.lower())
    text = re.sub(r"[^\w\s&.]", " ", text)
    return " ".join(text.split())[:60]


def suggest_bills(store: FinanceStore, context: TenantContext, *, now: datetime | None = None) -> list[BillSuggestion]:
    settings = settings_for(store, context)
    today = today_in(settings, now)
    rows = store.transactions_between(context, today - timedelta(days=400), today)
    bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    return detect_recurring(rows, today, [b.name for b in bills], bills)


def _regular(gap: int, typical: float) -> bool:
    return typical * 0.6 <= gap <= typical * 1.4


def _skipped(gap: int, typical: float) -> bool:
    """One period missed (a month without the payment) keeps the pattern."""
    return typical * 1.6 <= gap <= typical * 2.4


def _covered(bills: list[Bill], currency: str, cadence: Cadence, middle: Decimal, account_ids: set[str]) -> bool:
    """A saved bill already stands for this payment, whatever the bank calls it ("Electricity" and "DEWA BILL")."""
    for bill in bills:
        if not bill.active or bill.currency != currency or bill.cadence != cadence:
            continue
        if bill.account_id and bill.account_id not in account_ids:
            continue
        if abs(bill.amount - middle) <= max(bill.amount, middle) * Decimal("0.15"):
            return True
    return False


def detect_recurring(rows: list[Transaction], today: date, known_names: list[str], known_bills: list[Bill] | None = None) -> list[BillSuggestion]:
    """Pure: money out with the same payee at a steady interval and amount, not yet a bill.

    One payee is one group across accounts (a bill paid from two accounts is still one bill), one missed
    period does not break the pattern, and a saved bill with the same currency, cadence and amount (within
    15 percent) counts as already saved even when its name differs from the bank's wording.

    Repeated ordinary purchases (groceries, eating out, fuel, taxis, shopping) are never suggested unless something names
    them as recurring (a gym membership, an installment plan): ``recurring.ordinary_purchase``. A loan repayment (a
    transfer of principal) and an uncategorized transfer out to the same person can recur and are looked at too."""
    from .recurring import counts_for_recurring, kind_of, ordinary_purchase
    from .rules import merchant_key

    known = {_payee(name) for name in known_names}
    groups: dict[tuple[str, str], list[Transaction]] = {}
    for t in rows:
        if not counts_for_recurring(t) or (t.category in NOT_SPEND and t.transaction_type != TransactionType.LOAN_PRINCIPAL):
            continue
        key = _payee(t.description)
        if key:
            groups.setdefault((t.currency, key), []).append(t)
    found: list[BillSuggestion] = []
    for (currency, key), items in groups.items():
        if len(items) < 3 or key in known:
            continue
        items.sort(key=lambda t: t.posted_on)
        kind = kind_of(items[-1])
        if ordinary_purchase(items[-1], kind):
            continue
        gaps = [(b.posted_on - a.posted_on).days for a, b in pairwise(items)]
        base = min(gaps) if gaps else 0
        # A gap of about two periods is one missed payment: halve it to find the usual interval.
        typical = median([g / 2 if base > 0 and g >= base * 1.6 else g for g in gaps]) if gaps else 0
        cadence = next((c for c, low, high in _CADENCE_DAYS if low <= typical <= high), None)
        if cadence is None or not all(_regular(g, typical) or _skipped(g, typical) for g in gaps):
            continue
        if sum(1 for g in gaps if _regular(g, typical)) * 2 < len(gaps):
            continue
        amounts = [-t.amount for t in items]
        middle = Decimal(median(amounts))
        # The earlier payments are steady (within 15 percent); the newest may have moved (a price change, within half).
        steady = Decimal(median(amounts[:-1]))
        if any(abs(a - steady) > steady * Decimal("0.15") for a in amounts[:-1]) or abs(amounts[-1] - steady) > steady * Decimal("0.5"):
            continue
        if _covered(known_bills or [], currency, cadence, middle, {t.account_id for t in items}):
            continue
        last = items[-1]
        account_id = last.account_id
        due = next_after(last.posted_on, cadence, last.posted_on.day) or last.posted_on
        while due < today:
            due = next_after(due, cadence, last.posted_on.day) or today
        earlier = Decimal(median(amounts[:-1]))
        moved = amounts[-1] - earlier
        found.append(
            BillSuggestion(
                name=last.description[:120], amount=quantize(middle, currency), currency=currency, cadence=cadence,
                next_due_on=due, category=last.category if last.category not in (Category.OTHER, Category.TRANSFER) else Category.UTILITIES,
                account_id=account_id, occurrences=len(items), kind=kind, merchant=merchant_key(last.description) or None,
                last_amount=quantize(amounts[-1], currency),
                price_change=quantize(moved, currency) if earlier > 0 and abs(moved) >= earlier * Decimal("0.05") else None,
                transaction_ids=[t.id for t in reversed(items[-6:])],
            )
        )
    return sorted(found, key=lambda s: (s.next_due_on, s.name.lower()))[:50]


__all__ = ["BillSource", "SourceFacts", "build_summary", "detect_recurring", "month_range", "settings_for", "source_facts", "suggest_bills", "today_in", "upcoming_bills"]
