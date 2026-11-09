"""Routes for the salary profile and the month by month salary check (docs/finance.md#salary). Under
``/api/v1/finance``.

Reading needs ``finance:read``; writing ``finance:manage``. The profile is the caller's own (one per person,
stored next to the payslips, forced RLS). Confirming or rejecting a month's salary records the person's
decision on the profile; nothing here posts, links or moves money. Audit records carry no amounts or names.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import obligations
from . import personal as P
from . import salary as SAL
from .api import Amount, _audit, _clean, _currency, _invalid, _manage, _money, _not_found, _read
from .models import Account, Transaction
from .personal_store import open_personal_store
from .store import open_finance_store
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


class SalaryRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=80)
    kind: P.SalaryRuleKind
    reason: P.SalaryDeductionReason = P.SalaryDeductionReason.OTHER
    label: str | None = Field(default=None, max_length=80)
    amount: Amount
    starts: str = Field(pattern=MONTH)
    ends: str | None = Field(default=None, pattern=MONTH)
    total: Amount | None = None
    received_on: date | None = None


class SalaryProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str = Field(min_length=1, max_length=120)
    joined_on: date
    currency: str | None = Field(default=None, max_length=3)
    expected_gross: Amount
    pay_day: int = Field(ge=1, le=31)
    account_id: str | None = Field(default=None, max_length=80)
    # None keeps the saved rules; a list replaces them.
    rules: list[SalaryRuleInput] | None = Field(default=None, max_length=P.MAX_SALARY_RULES)


class AdvanceView(BaseModel):
    rule_id: str
    label: str | None
    total: Decimal
    left: Decimal  # still to be repaid after this month


class SalaryProfileView(BaseModel):
    id: str
    employer: str
    joined_on: date
    currency: str
    expected_gross: Decimal
    pay_day: int
    account_id: str | None
    rules: list[P.SalaryRule]
    created_at: object
    updated_at: object
    # The one income figure for this month and the next usual pay day.
    expected_net: Decimal
    next_pay_on: date
    advances: list[AdvanceView]


class SalaryProfilePage(BaseModel):
    profile: SalaryProfileView | None


class DeductedView(BaseModel):
    reason: str
    label: str | None
    amount: Decimal


class ExpectedView(BaseModel):
    gross: Decimal
    deductions: list[DeductedView]
    advance_repayment: Decimal
    net: Decimal
    payslip: bool


class FoundView(BaseModel):
    transaction_id: str
    posted_on: date
    amount: Decimal
    description: str
    account_id: str
    confidence: SAL.Confidence
    score: int


class SalaryMonthView(BaseModel):
    month: str
    currency: str
    pay_on: date
    status: SAL.MonthStatus
    flags: list[str]
    expected: ExpectedView
    received: Decimal
    difference: Decimal  # received less the expected net
    found: FoundView | None
    basis: SAL.Basis | None  # confirmed by you, linked to the payslip, or suggested (waiting for you)
    rejected: int  # transactions you said are not this month's salary


class SalaryMonthsPage(BaseModel):
    currency: str | None
    months: list[SalaryMonthView]  # newest first
    total: int
    missing: int
    waiting: int  # suggested months waiting for a confirm or reject
    limit: int
    offset: int


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1, max_length=80)
    decision: Literal["confirm", "reject"]


def _today(context: TenantContext) -> tuple[str, date, list[Account]]:
    with open_finance_store(context) as store:
        found = settings_for(store, context)
        return found.base_currency, today_in(found), store.list_accounts(context)


def _profile(context: TenantContext) -> P.SalaryProfile | None:
    with open_personal_store(context) as store:
        found = store.list(context, "salary_profile")
    return found[0] if found else None


def _payslips(context: TenantContext) -> list[P.Payslip]:
    with open_personal_store(context) as store:
        return store.list(context, "payslip")


def _view(profile: P.SalaryProfile, today: date, payslips: list[P.Payslip]) -> SalaryProfileView:
    this = SAL.month_key(today)
    advances = [AdvanceView(rule_id=r.id, label=r.label, total=r.total if r.total is not None else r.amount, left=SAL.advance_left(r, this))
                for r in profile.rules if r.kind == P.SalaryRuleKind.ADVANCE]
    return SalaryProfileView(**profile.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "decisions"}),
                             expected_net=SAL.expected_for(profile, this, payslips).net, next_pay_on=SAL.next_payday(profile, today), advances=advances)


def _rules(body: list[SalaryRuleInput], currency: str) -> list[P.SalaryRule]:
    out: list[P.SalaryRule] = []
    for rule in body:
        amount = _money(rule.amount, currency, field="Monthly amount", positive=True, allow_zero=False)
        if rule.ends is not None and rule.ends < rule.starts:
            raise _invalid("A deduction cannot end before it starts.")
        total: Decimal | None = None
        if rule.kind == P.SalaryRuleKind.ADVANCE:
            total = _money(rule.total if rule.total is not None else rule.amount, currency, field="Advance received", positive=True, allow_zero=False)
            if amount > total:
                raise _invalid("The monthly repayment is more than the advance.")
        out.append(P.SalaryRule(id=rule.id or new_id("fin_salary_rule"), kind=rule.kind,
                                reason=P.SalaryDeductionReason.ADVANCE if rule.kind == P.SalaryRuleKind.ADVANCE else rule.reason,
                                label=_clean(rule.label, 80), amount=amount, starts=rule.starts,
                                ends=rule.ends if rule.kind == P.SalaryRuleKind.DEDUCTION else None, total=total,
                                received_on=rule.received_on if rule.kind == P.SalaryRuleKind.ADVANCE else None))
    return out


@router.get("/salary/profile", response_model=SalaryProfilePage)
def get_salary_profile(context: TenantContext = Depends(tenant_context)) -> SalaryProfilePage:
    """Your salary profile (none until you add it), with this month's expected net and the next pay day."""
    _read(context)
    _base, today, _accounts = _today(context)
    profile = _profile(context)
    return SalaryProfilePage(profile=_view(profile, today, _payslips(context)) if profile else None)


@router.put("/salary/profile", response_model=SalaryProfileView)
def put_salary_profile(body: SalaryProfileInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> SalaryProfileView:
    """Add or change your salary profile: the job, the expected monthly salary, the pay day and the rules."""
    _manage(context)
    base, today, accounts = _today(context)
    existing = _profile(context)
    currency = _currency(body.currency, existing.currency if existing else base)
    employer = _clean(body.employer, 120)
    if not employer:
        raise _invalid("Write the employer's name.")
    if body.joined_on > today:
        raise _invalid("The joining date is in the future. Add the profile once you have joined.")
    if body.account_id is not None:
        account = next((a for a in accounts if a.id == body.account_id and not a.archived), None)
        if account is None:
            raise _not_found("Account")
        if account.currency != currency:
            raise _invalid(f"The account is in {account.currency}; the salary is in {currency}. Choose an account in {currency}.")
    gross = _money(body.expected_gross, currency, field="Expected monthly salary", positive=True, allow_zero=False)
    rules = _rules(body.rules, currency) if body.rules is not None else (existing.rules if existing else [])
    if existing is not None and existing.currency != currency and body.rules is None and existing.rules:
        raise _invalid("Send the rules again in the new currency, or remove them, before changing the currency.")
    now = utc_now()
    profile = P.SalaryProfile(
        id=existing.id if existing else new_id("fin_salary"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, employer=employer, joined_on=body.joined_on, currency=currency, expected_gross=gross, pay_day=body.pay_day,
        account_id=body.account_id, rules=rules, decisions=existing.decisions if existing and existing.currency == currency else [],
        created_at=existing.created_at if existing else now, updated_at=now,
    )
    with open_personal_store(context) as store:
        store.save("salary_profile", profile)
    _audit(repository, context, "finance.salary_profile.update" if existing else "finance.salary_profile.create", profile.id, {"rules": len(rules)})
    return _view(profile, today, _payslips(context))


@router.delete("/salary/profile", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_salary_profile(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Remove the salary profile and its decisions. Payslips and transactions stay."""
    _manage(context)
    profile = _profile(context)
    if profile is None:
        raise _not_found("Salary profile")
    with open_personal_store(context) as store:
        store.delete(context, "salary_profile", profile.id)
    _audit(repository, context, "finance.salary_profile.delete", profile.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _rows(context: TenantContext, profile: P.SalaryProfile, today: date) -> list[Transaction]:
    start = max(profile.joined_on, today.replace(day=1) - timedelta(days=31 * SAL.MAX_MONTHS)) - timedelta(days=SAL.WINDOW_BEFORE)
    with open_finance_store(context) as store:
        return [t for t in store.transactions_between(context, start, today + timedelta(days=1)) if t.amount > 0 and t.currency == profile.currency]


@router.get("/salary/months", response_model=SalaryMonthsPage)
def salary_months(limit: int = Query(default=12, ge=1, le=SAL.MAX_MONTHS), offset: int = Query(default=0, ge=0, le=SAL.MAX_MONTHS),
                  context: TenantContext = Depends(tenant_context)) -> SalaryMonthsPage:
    """Each month from your joining date: expected against received, with missing, late and partial flags, and the
    salary found in your records (statement, SMS or typed) waiting for you to confirm or reject."""
    _read(context)
    _base, today, accounts = _today(context)
    links = obligations.load(context)
    profile = links.salary_profile if isinstance(links.salary_profile, P.SalaryProfile) else None
    if profile is None:
        return SalaryMonthsPage(currency=None, months=[], total=0, missing=0, waiting=0, limit=limit, offset=offset)
    months = SAL.salary_months(profile, _rows(context, profile, today), accounts, today, links.payslips, lending_ids=links.lending_ids,
                               payslip_credits=links.payslip_credits)
    c = profile.currency
    views = [SalaryMonthView(
        month=m.month, currency=c, pay_on=m.pay_on, status=m.status, flags=m.flags,
        expected=ExpectedView(gross=m.expected.gross, deductions=[DeductedView(reason=d.reason, label=d.label, amount=d.amount) for d in m.expected.deductions],
                              advance_repayment=m.expected.advance_repayment, net=m.expected.net, payslip=m.expected.payslip),
        received=m.received, difference=m.received - m.expected.net,
        found=FoundView(transaction_id=m.transaction.transaction_id, posted_on=m.transaction.posted_on, amount=m.transaction.amount,
                        description=m.transaction.description, account_id=m.transaction.account_id, confidence=m.transaction.confidence,
                        score=m.transaction.score) if m.transaction else None,
        basis=m.basis, rejected=len(m.rejected)) for m in reversed(months)]
    return SalaryMonthsPage(currency=c, months=views[offset:offset + limit], total=len(views), missing=sum(1 for m in months if m.status == "missing"),
                            waiting=sum(1 for m in months if m.basis == "suggested"), limit=limit, offset=offset)


@router.post("/salary/months/{month}/decision", response_model=SalaryProfileView)
def decide_salary_month(body: DecisionInput, month: str = Path(pattern=MONTH), context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> SalaryProfileView:
    """Confirm a transaction as this month's salary, or say it is not (it is never suggested for the month again)."""
    _manage(context)
    _base, today, _accounts = _today(context)
    profile = _profile(context)
    if profile is None:
        raise _not_found("Salary profile")
    with open_finance_store(context) as store:
        found = store.get_transaction(context, body.transaction_id)
    if found is None:
        raise _not_found("Transaction")
    if found.amount <= 0 or found.currency != profile.currency:
        raise _invalid(f"Choose money in, in {profile.currency}.")
    kept = [d for d in profile.decisions if not (d.month == month and (d.transaction_id == body.transaction_id
                                                                        or (body.decision == "confirm" and d.decision == "confirmed")))]
    if body.decision == "confirm" and any(d.decision == "confirmed" and d.transaction_id == body.transaction_id for d in kept):
        raise _invalid("This transaction is already confirmed as another month's salary.")
    kept.append(P.SalaryDecision(month=month, transaction_id=body.transaction_id, decision="confirmed" if body.decision == "confirm" else "rejected",
                                 decided_at=utc_now()))
    if len(kept) > P.MAX_SALARY_DECISIONS:
        kept = kept[-P.MAX_SALARY_DECISIONS:]
    updated = profile.model_copy(update={"decisions": kept, "updated_at": utc_now()})
    with open_personal_store(context) as store:
        store.save("salary_profile", updated)
    _audit(repository, context, f"finance.salary_month.{body.decision}", profile.id, {"month": month})
    return _view(updated, today, _payslips(context))


@router.delete("/salary/months/{month}/decision", response_model=SalaryProfileView)
def undo_salary_month(month: str = Path(pattern=MONTH), transaction_id: str = Query(min_length=1, max_length=80),
                      context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> SalaryProfileView:
    """Undo a confirm or reject: the month is worked out again from your records."""
    _manage(context)
    _base, today, _accounts = _today(context)
    profile = _profile(context)
    if profile is None:
        raise _not_found("Salary profile")
    kept = [d for d in profile.decisions if not (d.month == month and d.transaction_id == transaction_id)]
    if len(kept) == len(profile.decisions):
        raise _not_found("Decision")
    updated = profile.model_copy(update={"decisions": kept, "updated_at": utc_now()})
    with open_personal_store(context) as store:
        store.save("salary_profile", updated)
    _audit(repository, context, "finance.salary_month.undo", profile.id, {"month": month})
    return _view(updated, today, _payslips(context))


__all__ = ["router"]
