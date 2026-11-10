"""Bank and finance loans on a Debt: the routes (docs/finance.md#bank-loans).

* ``GET /loans``: every debt with loan terms, with what is paid, owed and due, open and paid off alike.
* ``GET /debts/{id}/loan``: the terms, every instalment from the start to the end with its status (paid with its
  payments, part paid, missed, upcoming, ambiguous with the debits that fit, settled), the totals and the findings.
* ``PUT /debts/{id}/loan``: set or change the terms; ``DELETE /debts/{id}/loan`` removes them (the debt's typed balance
  counts again; matched payments stay).
* ``GET /loan-batches?debt_id``, ``POST /loan-batches/{id}/undo``: the payments accepted at once from the suggestion
  ``loan_payments`` and their undo (every link the batch made is voided).

Reading needs ``finance:read``; changing needs ``finance:manage``. Everything is the caller's own: another person's debt is
not found. Nothing here links a payment: only accepting the suggestion (``POST /suggestions/accept`` with its payload hash)
or a pick (``POST /debts/{id}/payments``) does.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext
from . import bank_loan_service as S
from . import bank_loans as BL
from .api import Amount, _audit, _clean, _invalid, _manage, _money, _not_found, _read
from .models import AccountKind, Debt
from .store import open_finance_store
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


class LoanTermsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lender: str | None = Field(default=None, max_length=120)
    start_on: date
    amount: Amount
    rate_type: Literal["reducing", "flat"] = "reducing"
    rate: Amount | None = None  # a yearly percentage; empty: not known (never guessed)
    term_months: int | None = Field(default=None, ge=1, le=600)
    installment: Amount | None = None  # the lender's instalment, when you know it
    payment_day: int | None = Field(default=None, ge=1, le=31)
    repayment: Literal["bank_debit", "salary_deduction", "manual"] = "manual"
    account_id: str | None = Field(default=None, max_length=80)
    status: Literal["open", "paid_off", "closed"] = "open"
    closed_on: date | None = None


class LoanTerms(BaseModel):
    lender: str | None
    start_on: date
    amount: Decimal
    rate_type: str
    rate: Decimal | None
    term_months: int | None
    installment: Decimal | None
    payment_day: int | None
    repayment: str
    account_id: str | None
    status: str
    closed_on: date | None


class LoanPayment(BaseModel):
    kind: Literal["link", "salary"]
    ref: str
    on: date
    amount: Decimal
    link_id: str | None
    batch_id: str | None


class LoanCandidate(BaseModel):
    canonical_id: str
    posted_on: date
    amount: Decimal
    currency: str
    merchant: str
    account_id: str


class LoanRow(BaseModel):
    number: int
    due_on: date
    payment: Decimal
    principal: Decimal | None
    interest: Decimal | None
    balance_after: Decimal
    status: str  # paid, part_paid, missed, upcoming, ambiguous, settled
    paid: Decimal
    payments: list[LoanPayment]
    candidates: list[LoanCandidate] = Field(default_factory=list)
    found: LoanPayment | None = None  # a payment found for it, waiting for the suggestion's accept


class LoanTotals(BaseModel):
    status: str
    basis: str  # reducing, flat or plain (no rate: the split is not known)
    installment: Decimal
    installment_from: str  # lender or computed
    split_known: bool
    never_ends: bool
    effective_rate: Decimal | None
    total_payable: Decimal
    total_interest: Decimal | None
    paid: Decimal
    interest_paid: Decimal | None
    principal_paid: Decimal | None
    balance: Decimal
    payoff_on: date | None
    due_count: int
    paid_count: int
    months_ahead: int
    next_due_on: date | None
    next_amount: Decimal
    from_salary: bool
    found: int
    missing: int
    ambiguous: int


class LoanBatchView(BaseModel):
    id: str
    debt_id: str
    status: str
    created_at: Any
    undone_at: Any
    count: int
    amount: Decimal
    currency: str | None


class LoanView(BaseModel):
    debt_id: str
    name: str
    kind: str
    currency: str
    terms: LoanTerms
    totals: LoanTotals
    rows: list[LoanRow]
    batches: list[LoanBatchView]


class LoanSummary(BaseModel):
    debt_id: str
    name: str
    lender: str | None
    currency: str
    repayment: str
    totals: LoanTotals


class LoanList(BaseModel):
    items: list[LoanSummary]


def _today(context: TenantContext) -> date:
    with open_finance_store(context) as store:
        return today_in(settings_for(store, context))


def _terms(debt: Debt) -> LoanTerms:
    return LoanTerms(lender=debt.lender, start_on=debt.loan_start_on or date.min, amount=debt.loan_amount or Decimal(0), rate_type=debt.loan_rate_type or "reducing",
                     rate=debt.loan_rate, term_months=debt.loan_term_months, installment=debt.loan_installment, payment_day=debt.loan_payment_day,
                     repayment=debt.loan_repayment or "manual", account_id=debt.loan_account_id, status=debt.loan_status or "open",
                     closed_on=debt.loan_closed_on)


def _totals(state: S.LoanState) -> LoanTotals:
    v, s = state.view, state.schedule
    count = {k: sum(1 for f in state.findings if f.state == k) for k in ("found", "missing", "ambiguous")}
    return LoanTotals(status=v.status, basis=s.basis, installment=s.installment, installment_from=s.installment_from, split_known=s.split_known,
                      never_ends=s.never_ends, effective_rate=s.effective_rate, total_payable=s.total_payable, total_interest=s.total_interest, paid=v.paid,
                      interest_paid=v.interest_paid, principal_paid=v.principal_paid, balance=v.balance, payoff_on=v.payoff_on, due_count=v.due_count,
                      paid_count=v.paid_count, months_ahead=v.months_ahead, next_due_on=v.next_due_on, next_amount=v.next_amount,
                      from_salary=bool(getattr(state.as_debt, "from_salary", False)), **count)


def _batch(batch: Any) -> LoanBatchView:
    currencies = {i.currency for i in batch.items}
    return LoanBatchView(id=batch.id, debt_id=batch.debt_id, status=batch.status, created_at=batch.created_at, undone_at=batch.undone_at, count=len(batch.items),
                         amount=sum((i.amount for i in batch.items), Decimal(0)), currency=next(iter(currencies)) if len(currencies) == 1 else None)


def _payment(p: BL.Paid) -> LoanPayment:
    return LoanPayment(kind=p.kind, ref=p.ref, on=p.on, amount=p.amount, link_id=p.link_id, batch_id=p.batch_id)


def _view(state: S.LoanState) -> LoanView:
    debt = state.debt
    findings = {f.number: f for f in state.findings}
    rows: list[LoanRow] = []
    for r in state.view.rows:
        f = findings.get(r.number)
        candidates = [LoanCandidate(canonical_id=e.canonical_id, posted_on=e.posted_on, amount=e.amount, currency=e.currency, merchant=e.text[:80],
                                    account_id=e.account_id) for e in (f.candidates if f is not None and f.state == "ambiguous" else [])]
        found = None
        if f is not None and f.state == "found":
            found = LoanPayment(kind="link" if f.canonical_id else "salary", ref=f.canonical_id or f.source_id or "", on=f.candidates[0].posted_on if f.candidates else f.due_on,
                                amount=f.amount or f.expected, link_id=None, batch_id=None)
        rows.append(LoanRow(number=r.number, due_on=r.due_on, payment=r.payment, principal=r.principal, interest=r.interest, balance_after=r.balance_after,
                            status="ambiguous" if candidates else r.status, paid=r.paid, payments=[_payment(p) for p in r.payments], candidates=candidates,
                            found=found))
    return LoanView(debt_id=debt.id, name=debt.name, kind=debt.kind.value, currency=debt.currency, terms=_terms(debt), totals=_totals(state), rows=rows,
                    batches=[_batch(b) for b in sorted(state.batches, key=lambda b: b.created_at, reverse=True)])


def _own_debt(context: TenantContext, debt_id: str) -> Debt:
    with open_finance_store(context) as store:
        found = store.get_item(context, "debt", debt_id)
    if not isinstance(found, Debt):
        raise _not_found("Debt")
    return found


def _state(context: TenantContext, debt_id: str) -> S.LoanState:
    debt = _own_debt(context, debt_id)
    if not BL.has_terms(debt):
        raise _not_found("Loan terms for this debt")
    found = S.states(context, _today(context), debt_id=debt_id)
    if not found:
        raise _not_found("Loan terms for this debt")
    return found[0]


@router.get("/loans", response_model=LoanList)
def list_loans(context: TenantContext = Depends(tenant_context)) -> LoanList:
    """Every debt with loan terms: open ones first, then paid off and closed (they stay with their whole record)."""
    _read(context)
    items = [LoanSummary(debt_id=s.debt.id, name=s.debt.name, lender=s.debt.lender, currency=s.debt.currency, repayment=s.debt.loan_repayment or "manual",
                         totals=_totals(s)) for s in S.states(context, _today(context))]
    items.sort(key=lambda i: (i.totals.status != "open", i.name.casefold(), i.debt_id))
    return LoanList(items=items)


@router.get("/debts/{debt_id}/loan", response_model=LoanView)
def get_loan(debt_id: str, context: TenantContext = Depends(tenant_context)) -> LoanView:
    _read(context)
    return _view(_state(context, debt_id))


def _rate(value: Amount | None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        rate = Decimal(str(value).strip()).quantize(Decimal("0.0001"))
    except Exception:  # noqa: BLE001 - any unreadable value is the same mistake
        raise _invalid("Rate: enter a yearly rate in percent, for example 4.25, or leave it empty if you do not know it.") from None
    if not rate.is_finite() or rate <= 0 or rate > 100:
        raise _invalid("Rate: enter a yearly rate more than 0 and at most 100 percent, or leave it empty if you do not know it.")
    return rate


@router.put("/debts/{debt_id}/loan", response_model=LoanView)
def put_loan(debt_id: str, body: LoanTermsInput, context: TenantContext = Depends(tenant_context),
             repository: AnumRepository = Depends(repository_context)) -> LoanView:
    """Set the loan's terms. The schedule and the matched payments then decide what it owes, everywhere."""
    _manage(context)
    with open_finance_store(context) as store:
        debt = store.get_item(context, "debt", debt_id)
        if not isinstance(debt, Debt):
            raise _not_found("Debt")
        today = today_in(settings_for(store, context))
        c = debt.currency
        amount = _money(body.amount, c, field="Amount borrowed", positive=True, allow_zero=False)
        installment = None if body.installment in (None, "") else _money(body.installment, c, field="Instalment", positive=True, allow_zero=False)
        if body.term_months is None and installment is None:
            raise _invalid("Enter the term in months or the instalment, so the schedule can be worked out.")
        if body.start_on > today + timedelta(days=366):
            raise _invalid("The start date is more than a year away. Enter the date the loan was paid out.")
        rate = _rate(body.rate)
        account_id = None
        if body.account_id:
            account = store.get_account(context, body.account_id)
            if account is None:
                raise _not_found("Account")
            if account.kind not in (AccountKind.BANK, AccountKind.CASH) or account.currency != c:
                raise _invalid(f"Pick a bank account in {c}: the instalments are debited from it.")
            account_id = account.id
        if body.repayment == "bank_debit" and account_id is None:
            raise _invalid("Pick the account the bank debits the instalments from.")
        closed_on = body.closed_on if body.status != "open" else None
        if body.status != "open" and closed_on is None:
            raise _invalid("Enter the date the loan was paid off or closed.")
        if closed_on is not None and closed_on < body.start_on:
            raise _invalid("The loan cannot be paid off before it started.")
        updated = debt.model_copy(update={
            "lender": _clean(body.lender, 120), "loan_start_on": body.start_on, "loan_amount": amount, "loan_rate_type": body.rate_type, "loan_rate": rate,
            "loan_term_months": body.term_months, "loan_installment": installment, "loan_payment_day": body.payment_day, "loan_repayment": body.repayment,
            "loan_account_id": account_id, "loan_status": body.status, "loan_closed_on": closed_on,
            "original_balance": debt.original_balance if debt.original_balance is not None else amount,
        })
        schedule = BL.build(updated)
        if schedule is None or not schedule.rows:
            raise _invalid("The instalment does not cover the interest, so this loan never ends. Check the rate and the instalment.")
        store.save_item("debt", updated)
    _audit(repository, context, "finance.debt.loan_terms", debt_id, {"repayment": body.repayment, "status": body.status})
    return _view(_state(context, debt_id))


@router.delete("/debts/{debt_id}/loan", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_loan(debt_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Remove the loan terms: the balance you typed counts again. Matched payments and accepted batches stay."""
    _manage(context)
    from .api import LOAN_FIELDS

    with open_finance_store(context) as store:
        debt = store.get_item(context, "debt", debt_id)
        if not isinstance(debt, Debt):
            raise _not_found("Debt")
        store.save_item("debt", debt.model_copy(update={name: None for name in LOAN_FIELDS}))
    _audit(repository, context, "finance.debt.loan_terms_removed", debt_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class LoanBatchList(BaseModel):
    items: list[LoanBatchView]


@router.get("/loan-batches", response_model=LoanBatchList)
def list_batches(debt_id: str | None = Query(default=None, max_length=80), context: TenantContext = Depends(tenant_context)) -> LoanBatchList:
    _read(context)
    from .personal_store import open_personal_store

    with open_personal_store(context) as people:
        rows = [b for b in people.list(context, "loan_batch") if debt_id is None or b.debt_id == debt_id]
    return LoanBatchList(items=[_batch(b) for b in rows])


@router.post("/loan-batches/{batch_id}/undo", response_model=LoanBatchView)
def undo_batch(batch_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LoanBatchView:
    """Undo an accepted batch: every link it made is voided and its salary months are forgotten; the suggestion comes back."""
    _manage(context)
    try:
        batch = S.undo_batch(context, batch_id)
    except S.BatchMissing:
        raise _not_found("Accepted payments") from None
    _audit(repository, context, "finance.loan_batch.undo", batch_id, {"debt": batch.debt_id, "items": len(batch.items)})
    return _batch(batch)


__all__ = ["router"]
