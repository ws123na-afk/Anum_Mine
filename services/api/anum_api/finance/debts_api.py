"""The personal debt overview, credit cards, promises to pay, financial alerts and the Money Advisor's answers: the routes
(docs/finance.md#debt-overview, #credit-cards, #financial-alerts, #advisor-answers).

* ``GET /debt-overview``: every obligation (bank and finance loans, other debts, credit cards, buy now pay later, employer
  loans and salary advances, money lent and borrowed with people) with its figures and their sources, the calendar, the
  repayment order and the totals per currency.
* ``PUT /debts/{id}/profile``: the debt's type, the lender's own balance and its date, a disputed amount, fees, and a card's
  statement terms (limit, closing day, statement balance, minimum and date).
* ``GET /cards``: every credit card account with its statement, cycle, payments and what paying the minimum or the full
  amount leaves.
* ``POST /people-loans/{id}/promises``, ``DELETE /people-loans/{id}/promises/{promise_id}``: what a person promised to pay
  and by when; ``PUT /people-loans/{id}/agreed-balance``: the balance both sides last agreed (empty clears it).
* ``GET /alerts/settings``, ``PUT /alerts/settings``: which financial alerts the person wants; ``GET /alerts/preview``: what
  would be told today.
* ``GET /advisor/answer``: one Money Advisor question answered from the records, with the records and the calculation.

Reading needs ``finance:read``; changing needs ``finance:manage``. Everything is the caller's own: another person's record
is not found. Nothing here moves money or links a payment.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import personal as P
from .api import Amount, _audit, _clean, _invalid, _manage, _money, _not_found, _read
from .models import AccountKind, Debt
from .store import open_finance_store
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


def _today(context: TenantContext) -> date:
    with open_finance_store(context) as store:
        return today_in(settings_for(store, context))


# -- the overview -------------------------------------------------------------------------------------------


@router.get("/debt-overview")
def debt_overview(context: TenantContext = Depends(tenant_context)) -> Any:
    """Everything you owe and are owed, each figure with its source, the calendar and the repayment order."""
    from . import debt_overview as D

    _read(context)
    return D.overview(context, _today(context)).model_dump(mode="json")


class DebtProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debt_type: Literal["personal_loan", "vehicle_finance", "mortgage", "credit_card", "employer_loan", "salary_advance", "bnpl", "informal", "other"] | None = None
    verified_balance: Amount | None = None  # the lender's own figure; empty clears it
    verified_on: date | None = None
    disputed_amount: Amount | None = None
    dispute_note: str | None = Field(default=None, max_length=300)
    fees: Amount | None = None
    card_limit: Amount | None = None
    card_statement_day: int | None = Field(default=None, ge=1, le=31)
    card_statement_balance: Amount | None = None
    card_statement_minimum: Amount | None = None
    card_statement_on: date | None = None


class DebtProfileView(BaseModel):
    debt_id: str
    debt_type: str | None
    verified_balance: Decimal | None
    verified_on: date | None
    disputed_amount: Decimal | None
    dispute_note: str | None
    fees: Decimal | None
    card_limit: Decimal | None
    card_statement_day: int | None
    card_statement_balance: Decimal | None
    card_statement_minimum: Decimal | None
    card_statement_on: date | None


def _optional(value: Amount | None, currency: str, field: str, *, allow_zero: bool = True) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return _money(value, currency, field=field, positive=True, allow_zero=allow_zero)


def _profile_view(debt: Debt) -> DebtProfileView:
    return DebtProfileView(debt_id=debt.id, **{name: getattr(debt, name) for name in DebtProfileView.model_fields if name != "debt_id"})


@router.put("/debts/{debt_id}/profile", response_model=DebtProfileView)
def put_debt_profile(debt_id: str, body: DebtProfileInput, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> DebtProfileView:
    """Set the debt's type, the lender's balance with its date, a disputed amount, fees and a card's statement terms. The whole
    profile is replaced: a field left empty is cleared."""
    _manage(context)
    with open_finance_store(context) as store:
        debt = store.get_item(context, "debt", debt_id)
        if not isinstance(debt, Debt):
            raise _not_found("Debt")
        today = today_in(settings_for(store, context))
        c = debt.currency
        verified = _optional(body.verified_balance, c, "Lender's balance")
        if (verified is None) != (body.verified_on is None):
            raise _invalid("Enter the lender's balance together with the date it was read from their statement or app.")
        if body.verified_on is not None and body.verified_on > today:
            raise _invalid("The lender's balance date cannot be in the future.")
        statement = _optional(body.card_statement_balance, c, "Statement balance")
        if (statement is None) != (body.card_statement_on is None):
            raise _invalid("Enter the statement balance together with the statement date.")
        if body.card_statement_on is not None and body.card_statement_on > today:
            raise _invalid("The statement date cannot be in the future.")
        disputed = _optional(body.disputed_amount, c, "Disputed amount", allow_zero=False)
        limit = _optional(body.card_limit, c, "Credit limit", allow_zero=False)
        if any(v is not None for v in (limit, statement, body.card_statement_day, body.card_statement_minimum)) and debt.account_id:
            account = store.get_account(context, debt.account_id)
            if account is None or account.kind != AccountKind.CARD:
                raise _invalid("Card terms belong on a debt kept on a card account.")
        updated = debt.model_copy(update={
            "debt_type": body.debt_type, "verified_balance": verified, "verified_on": body.verified_on, "disputed_amount": disputed,
            "dispute_note": _clean(body.dispute_note, 300) if disputed is not None else None, "fees": _optional(body.fees, c, "Fees"),
            "card_limit": limit, "card_statement_day": body.card_statement_day, "card_statement_balance": statement,
            "card_statement_minimum": _optional(body.card_statement_minimum, c, "Minimum payment"), "card_statement_on": body.card_statement_on,
        })
        store.save_item("debt", updated)
    _audit(repository, context, "finance.debt.profile", debt_id, {"type": body.debt_type or "", "verified": verified is not None,
                                                                  "statement": statement is not None})
    return _profile_view(updated)


# -- credit cards -------------------------------------------------------------------------------------------


@router.get("/cards")
def list_cards(context: TenantContext = Depends(tenant_context)) -> Any:
    """Every credit card account: what it owes on the ledger, its statement, cycle, payments and the minimum and full payment."""
    from . import credit_cards as CC

    _read(context)
    today = _today(context)
    cards = CC.build(CC.gather(context, today))
    return {"today": today.isoformat(), "items": [c.model_dump(mode="json") for c in cards],
            "note": "A payment from your bank to a card is one transfer: not spending. A rate is never guessed: enter the card's terms."}


# -- promises and the agreed balance on a loan with a person -------------------------------------------------


class PromiseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Amount
    due_by: date
    made_on: date | None = None  # default: today
    note: str | None = Field(default=None, max_length=300)


class AgreedBalanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    balance: Amount | None = None  # empty clears it
    on: date | None = None


def _loan(context: TenantContext, loan_id: str) -> P.PersonLoan:
    from .personal_store import open_personal_store

    with open_personal_store(context) as store:
        found = store.get(context, "person_loan", loan_id)
    if not isinstance(found, P.PersonLoan):
        raise _not_found("Loan")
    return found


def _save_loan(context: TenantContext, loan: P.PersonLoan) -> None:
    from .personal_store import open_personal_store

    with open_personal_store(context) as store:
        store.save("person_loan", loan)


@router.post("/people-loans/{loan_id}/promises", status_code=201)
def add_promise(loan_id: str, body: PromiseInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Any:
    """Record what the person promised to pay and by when. Whether it was kept is read from the repayments linked later."""
    _manage(context)
    today = _today(context)
    loan = _loan(context, loan_id)
    made_on = body.made_on or today
    if made_on > today:
        raise _invalid("A promise is recorded on or after the day it was made.")
    if body.due_by < made_on:
        raise _invalid("The promised date is on or after the day the promise was made.")
    if len(loan.promises) >= 50:
        raise _invalid("A loan keeps up to 50 promises. Delete old ones first.")
    amount = _money(body.amount, loan.currency, field="Amount", positive=True, allow_zero=False)
    promise = P.LoanPromise(id=new_id("fin_prom"), made_on=made_on, due_by=body.due_by, amount=amount, note=_clean(body.note, 300))
    _save_loan(context, loan.model_copy(update={"promises": [*loan.promises, promise], "updated_at": utc_now()}))
    _audit(repository, context, "finance.person_loan.promise", loan_id)
    return promise.model_dump(mode="json")


@router.delete("/people-loans/{loan_id}/promises/{promise_id}", status_code=204, response_model=None)
def delete_promise(loan_id: str, promise_id: str, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    loan = _loan(context, loan_id)
    kept = [p for p in loan.promises if p.id != promise_id]
    if len(kept) == len(loan.promises):
        raise _not_found("Promise")
    _save_loan(context, loan.model_copy(update={"promises": kept, "updated_at": utc_now()}))
    _audit(repository, context, "finance.person_loan.promise_removed", loan_id)
    return Response(status_code=204)


@router.put("/people-loans/{loan_id}/agreed-balance")
def put_agreed_balance(loan_id: str, body: AgreedBalanceInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    """The balance both of you last agreed, with its date: compared with what the linked repayments leave."""
    _manage(context)
    today = _today(context)
    loan = _loan(context, loan_id)
    balance = None if body.balance is None or str(body.balance).strip() == "" else _money(body.balance, loan.currency, field="Balance", positive=True)
    on = body.on or (today if balance is not None else None)
    if balance is None:
        on = None
    elif on is not None and on > today:
        raise _invalid("The agreed balance date cannot be in the future.")
    _save_loan(context, loan.model_copy(update={"verified_balance": balance, "verified_on": on, "updated_at": utc_now()}))
    _audit(repository, context, "finance.person_loan.agreed_balance", loan_id, {"set": balance is not None})
    return {"loan_id": loan_id, "verified_balance": str(balance) if balance is not None else None, "verified_on": on.isoformat() if on else None}


# -- alerts -------------------------------------------------------------------------------------------------


class AlertSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: dict[str, bool] = Field(max_length=40)


@router.get("/alerts/settings")
def get_alert_settings(context: TenantContext = Depends(tenant_context)) -> Any:
    from . import alerts as A

    _read(context)
    chosen = A.settings(context)
    return {"kinds": [{"kind": k, "on": chosen[k], "title": A.TITLES[k]} for k in A.KINDS],
            "note": "In-app only. Each item is told once; standing ones at most once a week. Never 'paid' without a matched payment."}


@router.put("/alerts/settings")
def put_alert_settings(body: AlertSettingsInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    from . import alerts as A

    _manage(context)
    unknown = sorted(set(body.kinds) - set(A.KINDS))
    if unknown:
        raise _invalid("Unknown alert kind: " + ", ".join(unknown[:5]) + ".")
    chosen = A.save_settings(context, body.kinds)
    _audit(repository, context, "finance.alerts.settings", context.user_id, {"off": sum(1 for v in chosen.values() if not v)})
    return {"kinds": [{"kind": k, "on": chosen[k], "title": A.TITLES[k]} for k in A.KINDS]}


@router.get("/alerts/preview")
def preview_alerts(context: TenantContext = Depends(tenant_context)) -> Any:
    """What the alerts would tell today (each item still once only when the daily run sends it)."""
    from . import alerts as A

    _read(context)
    today, found, chosen = A.preview(context)
    return {"today": today.isoformat(), "items": [{"kind": a.kind, "title": A.TITLES[a.kind], "body": a.body, "on": a.on.isoformat(), "evidence": a.evidence,
                                                    "on_for_you": chosen.get(a.kind, True)} for a in found]}


# -- the Money Advisor's answers ----------------------------------------------------------------------------


@router.get("/advisor/answer")
def advisor_answer(question: str = Query(min_length=2, max_length=40, pattern=r"^[a-z_]+$"), category: str | None = Query(default=None, max_length=40),
                   words: str | None = Query(default=None, max_length=60), country: str | None = Query(default=None, max_length=40),
                   currency: str | None = Query(default=None, pattern=r"^[A-Z]{3}$"), month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                   amount: str | None = Query(default=None, pattern=r"^\d{1,12}(\.\d{1,3})?$"), context: TenantContext = Depends(tenant_context)) -> Any:
    """One question answered from your records, with the records, the calculation and what is missing."""
    from . import advisor_answers as AA

    _read(context)
    if question not in AA.QUESTIONS:
        raise _invalid("Ask one of: " + ", ".join(AA.QUESTIONS) + ".")
    return AA.answer(context, question, category=category, words=words, country=country, currency=currency, month=month, amount=amount).model_dump(mode="json")


__all__ = ["router"]
