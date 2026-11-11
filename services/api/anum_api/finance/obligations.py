"""What is still owed and still to pay, read with the person's matched payments
(docs/finance.md#how-figures-are-counted).

Every reader of bills and debts (the summary, Safe to spend, the advisor, the ledger dashboard and
the assistant's tools) goes through here, so a bill paid early is not taken off again and a debt's
balance follows the payments matched to it:

* **Bills**: a due date with an active matched payment (a ``bill`` link, ``commitments.py``) is
  paid and drops out. This month's due dates that have passed without a matched payment stay in as
  *overdue* until a payment is matched or the person moves the bill's next due date.
* **Debts**: a debt kept on a card or loan account (``account_id``) owes what that account's
  ledger says (its negative balance). A debt without one owes the balance the person saved, less
  the principal of the payments matched to it since then: with a rate, each payment's interest
  (``balance x APR / 12``) is taken out first.
* **Net worth** is account balances (cards and loans count by their sign, so what they owe is
  already in) less only the debts that have no account of their own.
* **Minimums**: a debt whose minimum is also saved as a bill (same currency, and the same name or
  the same account) is counted once, as the bill.
* **Money borrowed from a person** (``PersonLoan``, direction borrowed, still open) is a debt too
  (``borrowed_debts``): what is left to repay, no interest, and a minimum only when its due date falls in
  this month or has passed (then the whole amount left is due). A loan whose person is also saved as a
  Debt (same name and currency) is that Debt, never counted twice. Money lent to a person is a
  receivable (``lent_receivables``): net worth counts it, nothing else spends it.

``load`` reads the links in one short owner-scoped unit of its own; the rest is pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..schemas import TenantContext
from . import commitment_math as M
from . import personal as P
from .models import Account, AccountKind, Bill, Debt, DebtKind, Goal

LIABILITY_KINDS = frozenset({AccountKind.CARD, AccountKind.LOAN})
ASSET_KINDS = frozenset({AccountKind.BANK, AccountKind.CASH, AccountKind.INVESTMENT})


@dataclass(frozen=True)
class DebtPayment:
    debt_id: str
    amount: Decimal
    currency: str
    posted_on: date
    matched_at: object  # the link's created_at (a datetime)
    canonical_id: str = ""
    link_id: str = ""


@dataclass
class Links:
    """The person's bill and debt links, as the readers need them."""

    bill_links: list = field(default_factory=list)  # FinanceLink rows in the ``bill`` dimension
    debt_payments: list[DebtPayment] = field(default_factory=list)
    # Transactions linked to money lent or borrowed with a person: transfers by the shared rule (``flows.py``).
    lending_ids: frozenset[str] = frozenset()
    # Money lent and borrowed with people, and their live links (what changed hands and every repayment).
    person_loans: list = field(default_factory=list)
    loan_links: list = field(default_factory=list)
    # The salary profile (or None), the payslips and, per payslip month, the transactions linked to that payslip.
    salary_profile: object | None = None
    payslips: list = field(default_factory=list)
    payslip_credits: dict[str, set[str]] = field(default_factory=dict)
    # Payments accepted at once for bank loans (``LoanBatch``): their salary months count as paid instalments.
    loan_batches: list = field(default_factory=list)


def load(context: TenantContext) -> Links:
    """Bill links, matched debt payments, the transactions lent or borrowed with a person and the salary credits
    linked to payslips, in one short unit of work; then the loans, payslips and salary profile in another (never
    nested)."""
    from .commitments import BILL_DIMENSION, BILL_TARGET, DEBT_DIMENSION, DEBT_TARGET
    from .links import live_entry
    from .links_store import LinkStatus, open_link_stores
    from .personal import PAYSLIP_DIMENSION, PERSON_LOAN_DIMENSION
    from .personal_store import open_personal_store

    with open_link_stores(context) as (_finance, sms, links):
        bill_rows, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None,
                                        status=None, limit=10_000, offset=0)
        debt_rows, _ = links.list_links(context, canonical_id=None, dimension=DEBT_DIMENSION, target_type=DEBT_TARGET, target_id=None,
                                        status=LinkStatus.ACTIVE.value, limit=10_000, offset=0)
        payments: list[DebtPayment] = []
        for link in debt_rows:
            if link.status != LinkStatus.ACTIVE or link.amount is None or not link.currency:
                continue
            entry = live_entry(sms.chain(context, link.canonical_id))
            if entry is None:
                continue
            payments.append(DebtPayment(debt_id=link.target_id, amount=link.amount, currency=link.currency, posted_on=entry.posted_on,
                                        matched_at=link.created_at, canonical_id=link.canonical_id, link_id=link.id))
        loan_rows, lending = _lending(sms, links, context)
        slip_rows, _ = links.list_links(context, canonical_id=None, dimension=PAYSLIP_DIMENSION, target_type=None, target_id=None,
                                        status=LinkStatus.ACTIVE.value, limit=10_000, offset=0)
        by_slip: dict[str, set[str]] = {}
        for link in slip_rows:
            if link.status != LinkStatus.ACTIVE:
                continue
            for entry in sms.chain(context, link.canonical_id):
                by_slip.setdefault(link.target_id, set()).update(p.transaction_id for p in entry.postings if p.transaction_id)
    with open_personal_store(context) as people:  # its own short unit, after the links one
        loans = people.list(context, "person_loan")
        slips = people.list(context, "payslip")
        profiles = people.list(context, "salary_profile")
        batches = [b for b in people.list(context, "loan_batch") if b.status == "active"]
    credits: dict[str, set[str]] = {}
    for slip in slips:
        if slip.id in by_slip:
            credits.setdefault(slip.month, set()).update(by_slip[slip.id])
    return Links(bill_links=[l for l in bill_rows if l.status != LinkStatus.VOID], debt_payments=payments, lending_ids=lending,
                 person_loans=loans, loan_links=[l for l in loan_rows if l.status != LinkStatus.VOID],
                 salary_profile=profiles[0] if profiles else None, payslips=slips, payslip_credits=credits, loan_batches=batches)


def _lending(sms: object, links: object, context: TenantContext) -> tuple[list, frozenset[str]]:
    """The live ``person_loan`` links and the ids of every transaction in their canonical chains."""
    from .links_store import LinkStatus
    from .personal import PERSON_LOAN_DIMENSION

    loan_rows, _ = links.list_links(context, canonical_id=None, dimension=PERSON_LOAN_DIMENSION, target_type=None,  # type: ignore[attr-defined]
                                    target_id=None, status=None, limit=10_000, offset=0)
    found: set[str] = set()
    for canonical in {l.canonical_id for l in loan_rows if l.status != LinkStatus.VOID}:
        for entry in sms.chain(context, canonical):  # type: ignore[attr-defined]
            found.update(p.transaction_id for p in entry.postings if p.transaction_id)
    return loan_rows, frozenset(found)


def lending_ids_on(finance: object, context: TenantContext) -> frozenset[str]:
    """``Links.lending_ids`` read on the caller's open finance unit of work (the same SQL session, never a nested one),
    for readers that already hold a finance store: the Guardian's caps and alerts (``policy.py``, ``guardian_review.py``)."""
    from .links_store import links_memory_store
    from .sms_store import sms_memory_store

    session = getattr(finance, "session", None)
    if session is None:
        return _lending(sms_memory_store, links_memory_store, context)[1]
    from ..db.finance_links_repository import SqlAlchemyLinkStore
    from ..db.sms_repository import SqlAlchemySmsStore

    return _lending(SqlAlchemySmsStore(session), SqlAlchemyLinkStore(session), context)[1]


# -- bills ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Due:
    bill: Bill
    due_on: date
    overdue: bool


def unpaid(bills: list[Bill], links: list, today: date, end: date) -> list[Due]:
    """Every active bill's due dates from the start of this month to ``end`` without an active matched
    payment: the passed ones are overdue, the rest still to come."""
    from .commitments import bill_schedule

    start = today.replace(day=1)
    by_id = {b.id: b for b in bills}
    out: list[Due] = []
    for row in bill_schedule([b for b in bills if b.active], links, today, start, max(end, start)):
        if row.status == "paid" or row.due_on > end:
            continue
        out.append(Due(bill=by_id[row.bill_id], due_on=row.due_on, overdue=row.due_on < today))
    return out


_name = M.name_key
bill_for_debt = M.bill_for_debt  # one rule for every reader (commitment_math.py)


# -- debts and goals ------------------------------------------------------------------------------------


def _account(accounts: dict[str, Account], account_id: str | None) -> Account | None:
    account = accounts.get(account_id or "")
    return account if account is not None and not account.archived else None


def has_account(debt: Debt, accounts: dict[str, Account]) -> bool:
    """The debt is kept on one of the person's card or loan accounts in its currency (so the account already counts it)."""
    account = _account(accounts, debt.account_id)
    return account is not None and account.kind in LIABILITY_KINDS and account.currency == debt.currency


def debt_balance(debt: Debt, accounts: dict[str, Account], balances: dict[str, Decimal], payments: list[DebtPayment]) -> Decimal:
    """What the debt owes now (positive)."""
    if has_account(debt, accounts):
        account = accounts[debt.account_id or ""]
        return max(-(account.opening_balance + balances.get(account.id, Decimal(0))), Decimal(0))
    remaining = debt.balance
    rate = debt.apr_percent / Decimal(1200) if debt.apr_percent > 0 else Decimal(0)
    saved_on = debt.created_at.date()
    mine = sorted((p for p in payments if p.debt_id == debt.id and p.currency == debt.currency and p.posted_on >= saved_on
                   and _after(p.matched_at, debt.updated_at)), key=lambda p: (p.posted_on, str(p.matched_at)))
    for payment in mine:
        interest = min(payment.amount, remaining * rate) if remaining > 0 else Decimal(0)
        remaining -= payment.amount - interest
    return max(remaining, Decimal(0))


def _after(matched_at: object, saved_at: object) -> bool:
    try:
        return matched_at >= saved_at  # type: ignore[operator]
    except TypeError:
        return True


class LoanDebt(Debt):
    """A debt with bank or finance loan terms, read from its schedule and the payments matched to it (never stored as such):
    the balance is what the schedule and the payments leave, the minimum the instalment while it is open, the rate the
    reducing rate its instalments give, and ``from_salary`` when the salary's expected net already takes the instalment
    (so it is not a commitment of its own: ``commitment_math.debt_minimum``)."""

    source: str = "bank_loan"
    from_salary: bool = False


def loan_payments(debt: Debt, links: Links | None) -> list:
    """The payments toward a loan: active debt links in its currency and the salary months of its accepted batches."""
    from . import bank_loans as BL

    if links is None:
        return []
    out = [BL.Paid(on=p.posted_on, amount=p.amount, kind="link", ref=p.canonical_id, link_id=p.link_id or None)
           for p in links.debt_payments if p.debt_id == debt.id and p.currency == debt.currency]
    for batch in links.loan_batches:
        if getattr(batch, "debt_id", None) != debt.id:
            continue
        for item in batch.items:
            if item.source != "bank_debit" and item.currency == debt.currency:
                out.append(BL.Paid(on=item.due_on, amount=item.amount, kind="salary", ref=item.source_id or "", batch_id=batch.id, due_on=item.due_on))
    return out


def loan_debt(debt: Debt, links: Links | None, today: date) -> Debt:
    """The debt as every reader counts it when it has loan terms (else unchanged)."""
    from . import bank_loans as BL

    schedule = BL.build(debt)
    if schedule is None:
        return debt
    view = BL.figures(debt, schedule, loan_payments(debt, links), today)
    open_ = view.status == "open" and view.balance > 0
    day = debt.loan_payment_day or (schedule.rows[0].due_on.day if schedule.rows else debt.due_day)
    from_salary = open_ and links is not None and BL.salary_already_net(debt, links.salary_profile, links.payslips, today)
    values = debt.model_dump()
    values.update(balance=view.balance, minimum_payment=schedule.installment if open_ else Decimal(0), due_day=day if open_ else None,
                  apr_percent=schedule.effective_rate or Decimal(0), from_salary=from_salary)
    return LoanDebt(**values)


def current_debts(debts: list[Debt], accounts: list[Account], balances: dict[str, Decimal], links: Links | None,
                  today: date | None = None) -> list[Debt]:
    """The debts with today's balance (copies; nothing stored changes). A debt with bank loan terms owes what its schedule
    and matched payments leave (``loan_debt``) unless a card or loan account already counts it. With ``today``, money
    borrowed from a person that is still open is added as a debt (``borrowed_debts``)."""
    from ..schemas import utc_now
    from . import bank_loans as BL

    by_id = {a.id: a for a in accounts}
    payments = links.debt_payments if links is not None else []
    day = today or utc_now().date()
    out = [loan_debt(d, links, day) if BL.has_terms(d) and not has_account(d, by_id)
           else d.model_copy(update={"balance": debt_balance(d, by_id, balances, payments)}) for d in debts]
    if links is not None and today is not None:
        out.extend(borrowed_debts(links.person_loans, links.loan_links, debts, today))
    return out


# -- money lent and borrowed with people ----------------------------------------------------------------


class PersonDebt(Debt):
    """Money borrowed from a person, read as a debt (never stored as one). ``id`` is the loan's id."""

    source: str = "person_loan"
    person_loan_id: str
    due_on: date | None = None
    overdue: bool = False
    # The repayments of a schedule not repaid yet, as (date, what is left of it), in date order (migration 0158). Empty for a
    # loan repaid once on its due date.
    installments_left: list[tuple[date, Decimal]] = []


def _open(loan: P.PersonLoan, links: list, today: date) -> Decimal:
    figures = P.loan_figures(loan, links, today)
    return figures.outstanding if figures.status not in ("repaid", "closed") else Decimal(0)


def _left_by_installment(figures: P.LoanFigures) -> list[tuple[date, Decimal]]:
    left = figures.repaid
    out: list[tuple[date, Decimal]] = []
    for item in figures.schedule:
        covered = min(left, item.amount)
        left -= covered
        if item.amount - covered > 0:
            out.append((item.due_on, item.amount - covered))
    return out


def borrowed_debts(loans: list, links: list, debts: list[Debt], today: date) -> list[PersonDebt]:
    """Each open loan borrowed from a person as a debt: what is left to repay (scheduled interest included), at the
    loan's interest rate (0 when interest free). Its minimum is what the schedule asks for to the end of this month (a
    single due date: the whole amount left once it is in this month or has passed), ``overdue`` when a repayment date
    passed unpaid, else nothing (it is paid off from what is left over, in the plan's order). A loan whose person is
    also a saved Debt in its currency is skipped."""
    saved = {(M.name_key(d.name), d.currency) for d in debts}
    end = M.month_end(today)
    out: list[PersonDebt] = []
    for loan in loans:
        if not isinstance(loan, P.PersonLoan) or loan.direction != P.LoanDirection.BORROWED:
            continue
        figures = P.loan_figures(loan, links, today)
        left = figures.outstanding if figures.status not in ("repaid", "closed") else Decimal(0)
        if left <= 0 or (M.name_key(loan.person), loan.currency) in saved:
            continue
        next_on = figures.next_due_on or loan.due_on
        minimum = min(P.due_through(figures, end), left) if figures.schedule else Decimal(0)
        due = minimum > 0
        out.append(PersonDebt(
            tenant_id=loan.tenant_id, workspace_id=loan.workspace_id, owner_user_id=loan.owner_user_id, id=loan.id, person_loan_id=loan.id,
            name=loan.person, kind=DebtKind.OTHER, balance=left, currency=loan.currency, apr_percent=loan.interest_rate or Decimal(0),
            minimum_payment=minimum,
            due_day=next_on.day if due and next_on and next_on >= M.month_start(today) else None, due_on=next_on,
            overdue=figures.overdue_amount > 0, created_at=loan.created_at, updated_at=loan.updated_at,
            installments_left=_left_by_installment(figures) if loan.installments else [],
        ))
    return out


def lent_receivables(loans: list, links: list, today: date) -> dict[str, Decimal]:
    """What people still owe the person, per currency (open loans lent)."""
    out: dict[str, Decimal] = {}
    for loan in loans:
        if isinstance(loan, P.PersonLoan) and loan.direction == P.LoanDirection.LENT:
            left = _open(loan, links, today)
            if left > 0:
                out[loan.currency] = out.get(loan.currency, Decimal(0)) + left
    return out


def is_person_debt(debt: Debt) -> bool:
    return isinstance(debt, PersonDebt)


def unlinked(debts: list[Debt], accounts: list[Account]) -> list[Debt]:
    """The debts no account counts yet: the only ones net worth subtracts on top of account balances."""
    by_id = {a.id: a for a in accounts}
    return [d for d in debts if not has_account(d, by_id)]


def current_goals(goals: list[Goal], accounts: list[Account], balances: dict[str, Decimal]) -> list[Goal]:
    """A goal kept in one of the person's own bank, cash or investment accounts in its currency has saved what
    that account holds; any other goal keeps what the person entered."""
    by_id = {a.id: a for a in accounts}
    out = []
    for goal in goals:
        account = _account(by_id, goal.account_id)
        if account is not None and account.kind in ASSET_KINDS and account.currency == goal.currency:
            held = max(account.opening_balance + balances.get(account.id, Decimal(0)), Decimal(0))
            out.append(goal.model_copy(update={"saved_amount": held}))
        else:
            out.append(goal)
    return out


__all__ = [
    "DebtPayment",
    "Due",
    "Links",
    "LoanDebt",
    "PersonDebt",
    "bill_for_debt",
    "borrowed_debts",
    "current_debts",
    "current_goals",
    "debt_balance",
    "has_account",
    "is_person_debt",
    "lent_receivables",
    "load",
    "loan_debt",
    "loan_payments",
    "unlinked",
    "unpaid",
]
