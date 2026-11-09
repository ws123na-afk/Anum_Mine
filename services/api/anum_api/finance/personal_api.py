"""Routes for money lent and borrowed with people, payslips, remittances, the fixed commitments view and the
Plan view's planning answers (docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home,
#fixed-commitments, #planning-and-advice). Under ``/api/v1/finance``.

Reading needs ``finance:read``; writing ``finance:manage``. Every record is the caller's own (the store repeats
the tenant, workspace and owner; PostgreSQL enforces it with forced RLS). Linking a record to a ledger
transaction writes a finance link (``links.create``: the owner's lock, idempotency, never more than the
payment); nothing here posts money, pays, buys or transfers. ANUM only advises.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import links as K
from . import personal as P
from . import planner as PL
from . import planning_service as S
from .api import Amount, _audit, _clean, _currency, _invalid, _manage, _money, _not_found, _read
from .commitments import is_money_out
from .links_api import LinkResult, LinkView, _view, refusals
from .links_store import LinkStatus, open_link_stores
from .money import MoneyError, normalize_currency, quantize
from .personal_store import MAX_ROWS, Kind, open_personal_store
from .store import open_finance_store
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

MAX_PAGE = 100
ADVICE_NOTE = "Advice only: ANUM never moves money. Figures are estimates from your own records unless a source is named."


def _settings(context: TenantContext) -> tuple[str, date]:
    with open_finance_store(context) as store:
        found = settings_for(store, context)
        return found.base_currency, today_in(found)


def _page(rows: list, limit: int, offset: int) -> list:
    return rows[offset:offset + limit]


def _links(context: TenantContext, dimension: str, target_id: str | None = None) -> tuple[list, dict[str, Any]]:
    """The live links of a dimension (or one target) and each one's live ledger entry."""
    with open_link_stores(context) as (_finance, store, links):
        rows, _ = links.list_links(context, canonical_id=None, dimension=dimension, target_type=None, target_id=target_id, status=None,
                                   limit=10_000, offset=0)
        rows = [l for l in rows if l.status != LinkStatus.VOID]
        entries = {cid: K.live_entry(store.chain(context, cid)) for cid in {l.canonical_id for l in rows}}
    return rows, entries


class Movement(BaseModel):
    """One ledger transaction linked to a record."""

    link: LinkView
    posted_on: date | None
    description: str | None
    account_id: str | None


def _movements(links: list, entries: dict[str, Any], target_id: str) -> list[Movement]:
    out = []
    for link in links:
        if link.target_id != target_id:
            continue
        entry = entries.get(link.canonical_id)
        out.append(Movement(link=_view(link), posted_on=entry.posted_on if entry else None,
                            description=(entry.merchant or entry.description)[:80] if entry else None,
                            account_id=next((p.account_id for p in entry.postings if p.account_id), None) if entry else None))
    out.sort(key=lambda m: (m.posted_on or date.min, m.link.id), reverse=True)
    return out


def _with_currency(value: Amount | None, currency: str, field: str, *, positive: bool = True, allow_zero: bool = True) -> Decimal:
    return _money(value, currency, field=field, positive=positive, allow_zero=allow_zero)


def _own(context: TenantContext, kind: Kind, item_id: str, label: str) -> Any:
    with open_personal_store(context) as store:
        found = store.get(context, kind, item_id)
    if found is None:
        raise _not_found(label)
    return found


def _save(context: TenantContext, kind: Kind, item: Any, *, new: bool) -> None:
    with open_personal_store(context) as store:
        if new and store.count(context, kind) >= MAX_ROWS:
            raise _invalid(f"You can keep up to {MAX_ROWS} of these. Delete old ones first.")
        store.save(kind, item)


def _delete(context: TenantContext, kind: Kind, item_id: str, label: str, dimension: str) -> None:
    rows, _ = _links(context, dimension, item_id)
    for link in rows:  # the payments stay; only their links go, so they count by their category again
        K.void(context, link.id, expected_version=None)
    with open_personal_store(context) as store:
        if not store.delete(context, kind, item_id):
            raise _not_found(label)


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=80)
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("1e15"))


def _left(context: TenantContext, canonical_id: str, dimension: str, currency: str, wanted: Decimal | None, expected: Decimal) -> Decimal:
    """What to link: what was asked, else what is still expected, never more than is left of the payment."""
    with open_link_stores(context) as (_finance, store, links):
        chain = store.chain(context, canonical_id)
        live = links.live_links(context, canonical_id)
    if not chain:
        raise K.LinkError(404, "Transaction not found. It may have been deleted.", "transaction_not_found")
    caps = K.capacity(chain)
    if currency not in caps:
        raise K.LinkError(422, f"The transaction is not in {currency}. Link one in the same currency.", "currency_mismatch", {"currencies": sorted(caps)})
    if wanted is not None:
        return quantize(wanted, currency)
    left = caps[currency] - K.used(live, dimension, currency)
    amount = min(expected, left) if expected > 0 else left
    if amount <= 0:
        raise K.LinkError(409, "This transaction is already linked up to its amount.", "over_allocated", {"remaining": "0", "currency": currency})
    return amount


def _link(context: TenantContext, repository: AnumRepository, *, dimension: str, target: str, target_id: str, role: str, currency: str,
          body: LinkInput, expected: Decimal, key: str | None, action: str) -> LinkResult:
    # A retry of a request that already succeeded returns its link before anything is worked out again: the first
    # link used the capacity, so a fresh "what is left" would refuse the replay.
    with open_link_stores(context) as (_finance, _store, links):
        existing = links.link_by_idem(context, K.idem_key(context, body.canonical_id, dimension, target, target_id, role, key))
    if existing is not None and (existing.canonical_id, existing.dimension, existing.target_type, existing.target_id, existing.role) == (
            body.canonical_id, dimension, target, target_id, role) and (body.amount is None or existing.amount == quantize(body.amount, currency)):
        return LinkResult(link=_view(existing), replayed=True)
    amount = _left(context, body.canonical_id, dimension, currency, body.amount, expected)
    made = K.create(context, K.LinkRequest(canonical_id=body.canonical_id, dimension=dimension, role=role, target_type=target,
                                           target_id=target_id, amount=amount, currency=currency, idempotency_key=key))
    if not made.replayed:
        _audit(repository, context, action, made.link.id, {"target": target_id})
    return LinkResult(link=_view(made.link), replayed=made.replayed)


def _unlink(context: TenantContext, dimension: str, target_id: str, link_id: str) -> None:
    with open_link_stores(context) as (_finance, _store, links):
        found = links.get_link(context, link_id)
    if found is None or found.dimension != dimension or found.target_id != target_id:
        raise K.LinkError(404, "Link not found. It may have been removed.", "link_not_found")
    K.void(context, link_id, expected_version=None)


# -- lent and borrowed -------------------------------------------------------------------------------


class LoanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: P.LoanDirection
    person: str = Field(min_length=1, max_length=120)
    amount: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    lent_on: date
    due_on: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    remind: bool = True
    remind_days_before: int = Field(default=3, ge=0, le=30)
    closed: bool = False


class LoanView(BaseModel):
    id: str
    direction: P.LoanDirection
    person: str
    amount: Decimal
    currency: str
    lent_on: date
    due_on: date | None
    notes: str | None
    remind: bool
    remind_days_before: int
    closed_on: date | None
    repaid: Decimal
    outstanding: Decimal
    handed: Decimal
    status: P.LoanStatus
    days_until_due: int | None
    links: int
    created_at: Any
    updated_at: Any


class LoanDetail(LoanView):
    movements: list[Movement]


class Owed(BaseModel):
    currency: str
    they_owe_me: Decimal
    i_owe: Decimal


class LoanPage(BaseModel):
    items: list[LoanView]
    total: int
    limit: int
    offset: int
    owed: list[Owed]  # every open loan, not just the page
    overdue: int
    note: str = "Money lent or borrowed with a person is not spending or income: its linked transactions count as transfers."


def _loan_view(loan: P.PersonLoan, links: list, today: date) -> LoanView:
    f = P.loan_figures(loan, links, today)
    return LoanView(**loan.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "reminded_for", "reminded_overdue_for"}),
                    repaid=f.repaid, outstanding=f.outstanding, handed=f.handed, status=f.status, days_until_due=f.days_until_due,
                    links=sum(1 for l in links if l.target_id == loan.id))


def _build_loan(context: TenantContext, body: LoanInput, existing: P.PersonLoan | None, base: str, today: date) -> P.PersonLoan:
    currency = _currency(body.currency, base)
    person = _clean(body.person, 120)
    if not person:
        raise _invalid("Write the person's name.")
    if body.due_on is not None and body.due_on < body.lent_on:
        raise _invalid("The due date is before the date the money changed hands.")
    now = utc_now()
    closed_on = (existing.closed_on if existing and existing.closed_on else today) if body.closed else None
    changed_due = existing is not None and existing.due_on != body.due_on
    return P.PersonLoan(
        id=existing.id if existing else new_id("fin_ploan"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, direction=body.direction, person=person,
        amount=_with_currency(body.amount, currency, "Amount", allow_zero=False), currency=currency, lent_on=body.lent_on, due_on=body.due_on,
        notes=_clean(body.notes, 1000), remind=body.remind, remind_days_before=body.remind_days_before,
        reminded_for=None if changed_due or existing is None else existing.reminded_for,
        reminded_overdue_for=None if changed_due or existing is None else existing.reminded_overdue_for,
        closed_on=closed_on, created_at=existing.created_at if existing else now, updated_at=now,
    )


@router.get("/people-loans", response_model=LoanPage)
def list_people_loans(
    q: str | None = Query(default=None, max_length=80), direction: P.LoanDirection | None = Query(default=None),
    state: Literal["open", "overdue", "settled", "all"] = Query(default="all", alias="status"),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> LoanPage:
    """Money you lent (they owe you) and borrowed (you owe them), newest first, with what is repaid and left."""
    _read(context)
    _base, today = _settings(context)
    with open_personal_store(context) as store:
        loans = store.list(context, "person_loan")
    links, _ = _links(context, P.PERSON_LOAN_DIMENSION)
    views = [_loan_view(l, links, today) for l in loans]
    owed: dict[str, list[Decimal]] = {}
    for v in views:
        if v.status in ("repaid", "closed"):
            continue
        pair = owed.setdefault(v.currency, [Decimal(0), Decimal(0)])
        pair[0 if v.direction == P.LoanDirection.LENT else 1] += v.outstanding
    needle = (q or "").casefold().strip()
    shown = [v for v in views if (not needle or needle in f"{v.person} {v.notes or ''}".casefold())
             and (direction is None or v.direction == direction)
             and (state == "all" or (state == "open" and v.status in ("open", "due_soon", "overdue")) or (state == "overdue" and v.status == "overdue")
                  or (state == "settled" and v.status in ("repaid", "closed")))]
    return LoanPage(items=_page(shown, limit, offset), total=len(shown), limit=limit, offset=offset,
                    owed=[Owed(currency=c, they_owe_me=quantize(a, c), i_owe=quantize(b, c)) for c, (a, b) in sorted(owed.items())],
                    overdue=sum(1 for v in views if v.status == "overdue"))


@router.post("/people-loans", response_model=LoanView, status_code=status.HTTP_201_CREATED)
def create_people_loan(body: LoanInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> LoanView:
    _manage(context)
    base, today = _settings(context)
    loan = _build_loan(context, body, None, base, today)
    _save(context, "person_loan", loan, new=True)
    _audit(repository, context, "finance.person_loan.create", loan.id)
    return _loan_view(loan, [], today)


@router.get("/people-loans/{loan_id}", response_model=LoanDetail)
def get_people_loan(loan_id: str, context: TenantContext = Depends(tenant_context)) -> LoanDetail:
    _read(context)
    _base, today = _settings(context)
    loan = _own(context, "person_loan", loan_id, "Loan")
    links, entries = _links(context, P.PERSON_LOAN_DIMENSION, loan_id)
    return LoanDetail(**_loan_view(loan, links, today).model_dump(), movements=_movements(links, entries, loan_id))


@router.patch("/people-loans/{loan_id}", response_model=LoanView)
def update_people_loan(loan_id: str, body: LoanInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> LoanView:
    """Replace the loan with the full body sent. A different currency is refused while transactions are linked."""
    _manage(context)
    base, today = _settings(context)
    existing = _own(context, "person_loan", loan_id, "Loan")
    links, _ = _links(context, P.PERSON_LOAN_DIMENSION, loan_id)
    loan = _build_loan(context, body, existing, base, today)
    if links and (loan.currency != existing.currency or loan.direction != existing.direction):
        raise _invalid("Remove the linked transactions before changing the currency or who owes whom.")
    _save(context, "person_loan", loan, new=False)
    _audit(repository, context, "finance.person_loan.update", loan_id)
    return _loan_view(loan, links, today)


@router.delete("/people-loans/{loan_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_people_loan(loan_id: str, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete the record and remove its links; the transactions themselves stay in the ledger."""
    _manage(context)
    _delete(context, "person_loan", loan_id, "Loan", P.PERSON_LOAN_DIMENSION)
    _audit(repository, context, "finance.person_loan.delete", loan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class LoanLinkInput(LinkInput):
    kind: Literal["handed", "repayment"] = "repayment"


@router.post("/people-loans/{loan_id}/links", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def link_people_loan(loan_id: str, body: LoanLinkInput, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
                     context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Link the transaction that handed the money over (``handed``) or a repayment. From then on it counts as a transfer."""
    _manage(context)
    _base, today = _settings(context)
    loan = _own(context, "person_loan", loan_id, "Loan")
    links, _ = _links(context, P.PERSON_LOAN_DIMENSION, loan_id)
    figures = P.loan_figures(loan, links, today)
    role = P.handed_role(loan.direction) if body.kind == "handed" else P.repaid_role(loan.direction)
    expected = max(loan.amount - figures.handed, Decimal(0)) if body.kind == "handed" else figures.outstanding
    return _link(context, repository, dimension=P.PERSON_LOAN_DIMENSION, target=P.PERSON_LOAN_TARGET, target_id=loan.id, role=role,
                 currency=loan.currency, body=LinkInput(canonical_id=body.canonical_id, amount=body.amount), expected=expected, key=idempotency_key,
                 action="finance.person_loan.link")


@router.delete("/people-loans/{loan_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@refusals
def unlink_people_loan(loan_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    _unlink(context, P.PERSON_LOAN_DIMENSION, loan_id, link_id)
    _audit(repository, context, "finance.person_loan.unlink", link_id, {"target": loan_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- payslips ----------------------------------------------------------------------------------------


class DeductionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: P.DeductionKind
    amount: Amount
    label: str | None = Field(default=None, max_length=80)


class PayslipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str = Field(min_length=1, max_length=120)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    paid_on: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    gross: Amount
    deductions: list[DeductionInput] = Field(default_factory=list, max_length=P.MAX_DEDUCTIONS)
    notes: str | None = Field(default=None, max_length=1000)


class PayslipView(BaseModel):
    id: str
    employer: str
    month: str
    paid_on: date | None
    currency: str
    gross: Decimal
    deductions: list[P.Deduction]
    notes: str | None
    total_deductions: Decimal
    net: Decimal
    received: Decimal
    difference: Decimal
    status: P.ReconcileStatus
    by_kind: dict[str, Decimal]
    created_at: Any
    updated_at: Any


class PayslipDetail(PayslipView):
    movements: list[Movement]


class PayMonthView(BaseModel):
    month: str
    currency: str
    gross: Decimal
    deductions: Decimal
    net: Decimal
    received: Decimal
    by_kind: dict[str, Decimal]
    payslips: int


class PayslipPage(BaseModel):
    items: list[PayslipView]
    total: int
    limit: int
    offset: int
    months: list[PayMonthView]  # every payslip of the year asked (or all), not just the page


def _payslip_view(slip: P.Payslip, links: list) -> PayslipView:
    f = P.payslip_figures(slip, links)
    return PayslipView(**slip.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"}), total_deductions=f.deductions, net=f.net,
                       received=f.received, difference=f.difference, status=f.status, by_kind=f.by_kind)


def _build_payslip(context: TenantContext, body: PayslipInput, existing: P.Payslip | None, base: str) -> P.Payslip:
    currency = _currency(body.currency, base)
    employer = _clean(body.employer, 120)
    if not employer:
        raise _invalid("Write the employer's name.")
    gross = _with_currency(body.gross, currency, "Gross pay")
    deductions = [P.Deduction(kind=d.kind, amount=_with_currency(d.amount, currency, "Deduction", allow_zero=False), label=_clean(d.label, 80))
                  for d in body.deductions]
    if sum((d.amount for d in deductions), Decimal(0)) > gross:
        raise _invalid("The deductions add up to more than the gross pay.")
    now = utc_now()
    return P.Payslip(id=existing.id if existing else new_id("fin_payslip"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                     owner_user_id=context.user_id, employer=employer, month=body.month, paid_on=body.paid_on, currency=currency, gross=gross,
                     deductions=deductions, notes=_clean(body.notes, 1000), created_at=existing.created_at if existing else now, updated_at=now)


@router.get("/payslips", response_model=PayslipPage)
def list_payslips(year: int | None = Query(default=None, ge=2000, le=2100), q: str | None = Query(default=None, max_length=80),
                  limit: int = Query(default=24, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
                  context: TenantContext = Depends(tenant_context)) -> PayslipPage:
    """Your payslips, newest month first, with gross against net, the deductions by kind and the salary credit check."""
    _read(context)
    with open_personal_store(context) as store:
        slips = store.list(context, "payslip")
    links, _ = _links(context, P.PAYSLIP_DIMENSION)
    needle = (q or "").casefold().strip()
    shown = [s for s in slips if (year is None or s.month.startswith(f"{year:04d}-")) and (not needle or needle in s.employer.casefold())]
    months = [PayMonthView(month=m.month, currency=m.currency, gross=quantize(m.gross, m.currency), deductions=quantize(m.deductions, m.currency),
                           net=quantize(m.net, m.currency), received=quantize(m.received, m.currency),
                           by_kind={k: quantize(v, m.currency) for k, v in sorted(m.by_kind.items())}, payslips=m.payslips)
              for m in P.pay_months(shown, links)]
    return PayslipPage(items=[_payslip_view(s, links) for s in _page(shown, limit, offset)], total=len(shown), limit=limit, offset=offset, months=months)


@router.post("/payslips", response_model=PayslipView, status_code=status.HTTP_201_CREATED)
def create_payslip(body: PayslipInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> PayslipView:
    _manage(context)
    base, _today = _settings(context)
    slip = _build_payslip(context, body, None, base)
    _save(context, "payslip", slip, new=True)
    _audit(repository, context, "finance.payslip.create", slip.id, {"deductions": len(slip.deductions)})
    return _payslip_view(slip, [])


@router.get("/payslips/{payslip_id}", response_model=PayslipDetail)
def get_payslip(payslip_id: str, context: TenantContext = Depends(tenant_context)) -> PayslipDetail:
    _read(context)
    slip = _own(context, "payslip", payslip_id, "Payslip")
    links, entries = _links(context, P.PAYSLIP_DIMENSION, payslip_id)
    return PayslipDetail(**_payslip_view(slip, links).model_dump(), movements=_movements(links, entries, payslip_id))


@router.patch("/payslips/{payslip_id}", response_model=PayslipView)
def update_payslip(payslip_id: str, body: PayslipInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> PayslipView:
    _manage(context)
    base, _today = _settings(context)
    existing = _own(context, "payslip", payslip_id, "Payslip")
    links, _ = _links(context, P.PAYSLIP_DIMENSION, payslip_id)
    slip = _build_payslip(context, body, existing, base)
    if links and slip.currency != existing.currency:
        raise _invalid("Remove the linked salary credit before changing the currency.")
    _save(context, "payslip", slip, new=False)
    _audit(repository, context, "finance.payslip.update", payslip_id, {"deductions": len(slip.deductions)})
    return _payslip_view(slip, links)


@router.delete("/payslips/{payslip_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_payslip(payslip_id: str, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    _delete(context, "payslip", payslip_id, "Payslip", P.PAYSLIP_DIMENSION)
    _audit(repository, context, "finance.payslip.delete", payslip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/payslips/{payslip_id}/links", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def link_payslip(payslip_id: str, body: LinkInput, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
                 context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Link the salary credit (money in) this payslip paid. Net is then checked against it."""
    _manage(context)
    slip = _own(context, "payslip", payslip_id, "Payslip")
    links, _ = _links(context, P.PAYSLIP_DIMENSION, payslip_id)
    f = P.payslip_figures(slip, links)
    return _link(context, repository, dimension=P.PAYSLIP_DIMENSION, target=P.PAYSLIP_TARGET, target_id=slip.id, role="deposit",
                 currency=slip.currency, body=body, expected=max(f.net - f.received, Decimal(0)), key=idempotency_key, action="finance.payslip.link")


@router.delete("/payslips/{payslip_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@refusals
def unlink_payslip(payslip_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    _unlink(context, P.PAYSLIP_DIMENSION, payslip_id, link_id)
    _audit(repository, context, "finance.payslip.unlink", link_id, {"target": payslip_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- remittances -------------------------------------------------------------------------------------


class RemittanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sent_on: date
    amount: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    fee: Amount = "0"
    fx_rate: Amount | None = None
    received_amount: Amount | None = None
    received_currency: str | None = Field(default=None, min_length=3, max_length=3)
    recipient: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    regular: bool = False
    notes: str | None = Field(default=None, max_length=1000)


class RemittanceView(BaseModel):
    id: str
    sent_on: date
    amount: Decimal
    currency: str
    fee: Decimal
    fx_rate: Decimal | None
    received_amount: Decimal | None
    received_currency: str | None
    recipient: str
    provider: str
    regular: bool
    notes: str | None
    total_paid: Decimal
    fee_percent: Decimal | None
    effective_rate: Decimal | None
    linked: Decimal
    created_at: Any
    updated_at: Any


class RemittanceDetail(RemittanceView):
    movements: list[Movement]


class RemitMonthView(BaseModel):
    month: str
    currency: str
    sent: Decimal
    fees: Decimal
    count: int
    received: dict[str, Decimal]


class ProviderView(BaseModel):
    provider: str
    corridor: str
    transfers: int
    average_fee_percent: Decimal | None
    best_effective_rate: Decimal | None
    latest_effective_rate: Decimal | None
    latest_on: date


class RemittancePage(BaseModel):
    items: list[RemittanceView]
    total: int
    limit: int
    offset: int
    months: list[RemitMonthView]
    providers: list[ProviderView]


def _rate(value: Amount | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        rate = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - any unreadable value is the same answer
        raise _invalid("The rate is not a number.") from None
    if not rate.is_finite() or rate <= 0 or rate > Decimal("1e6"):
        raise _invalid("The rate must be above zero.")
    return rate.quantize(Decimal("0.00000001"))


def _remittance_view(r: P.Remittance, links: list) -> RemittanceView:
    linked = sum((l.amount or Decimal(0) for l in links if l.target_id == r.id and l.status == LinkStatus.ACTIVE and l.currency == r.currency), Decimal(0))
    return RemittanceView(**r.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"}), total_paid=quantize(r.amount + r.fee, r.currency),
                          fee_percent=P.fee_percent(r), effective_rate=P.effective_rate(r), linked=quantize(linked, r.currency))


def _build_remittance(context: TenantContext, body: RemittanceInput, existing: P.Remittance | None, base: str) -> P.Remittance:
    currency = _currency(body.currency, base)
    recipient, provider = _clean(body.recipient, 120), _clean(body.provider, 120)
    if not recipient or not provider:
        raise _invalid("Write who received it and the provider you used.")
    amount = _with_currency(body.amount, currency, "Amount sent", allow_zero=False)
    fee = _with_currency(body.fee, currency, "Fee")
    rate = _rate(body.fx_rate)
    received_currency: str | None = None
    received: Decimal | None = None
    if body.received_currency or body.received_amount not in (None, "") or rate is not None:
        try:
            received_currency = normalize_currency(body.received_currency or "")
        except MoneyError:
            raise _invalid("Choose the currency the money arrived in.") from None
        if body.received_amount not in (None, ""):
            received = _with_currency(body.received_amount, received_currency, "Amount received", allow_zero=False)
        elif rate is not None:
            received = quantize(amount * rate, received_currency)  # what the rate gives, before any fee at the other end
        if rate is None and received is not None and received_currency != currency:
            rate = (received / amount).quantize(Decimal("0.00000001"))
        if received_currency == currency and rate is None:
            rate = Decimal(1)
    now = utc_now()
    return P.Remittance(id=existing.id if existing else new_id("fin_remit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        owner_user_id=context.user_id, sent_on=body.sent_on, amount=amount, currency=currency, fee=fee, fx_rate=rate,
                        received_amount=received, received_currency=received_currency, recipient=recipient, provider=provider, regular=body.regular,
                        notes=_clean(body.notes, 1000), created_at=existing.created_at if existing else now, updated_at=now)


@router.get("/remittances", response_model=RemittancePage)
def list_remittances(q: str | None = Query(default=None, max_length=80), month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                     limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
                     context: TenantContext = Depends(tenant_context)) -> RemittancePage:
    """Money you sent home, newest first, with the monthly totals and fees and each provider's cost from your own transfers."""
    _read(context)
    with open_personal_store(context) as store:
        rows = store.list(context, "remittance")
    links, _ = _links(context, P.REMITTANCE_DIMENSION)
    needle = (q or "").casefold().strip()
    shown = [r for r in rows if (not month or r.sent_on.strftime("%Y-%m") == month)
             and (not needle or needle in f"{r.recipient} {r.provider} {r.notes or ''}".casefold())]
    months = [RemitMonthView(month=m.month, currency=m.currency, sent=quantize(m.sent, m.currency), fees=quantize(m.fees, m.currency), count=m.count,
                             received={c: quantize(v, c) for c, v in sorted(m.received.items())}) for m in P.remit_months(rows)]
    providers = [ProviderView(**p.__dict__) for p in P.provider_costs(rows)]
    return RemittancePage(items=[_remittance_view(r, links) for r in _page(shown, limit, offset)], total=len(shown), limit=limit, offset=offset,
                          months=months, providers=providers)


@router.post("/remittances", response_model=RemittanceView, status_code=status.HTTP_201_CREATED)
def create_remittance(body: RemittanceInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> RemittanceView:
    _manage(context)
    base, _today = _settings(context)
    r = _build_remittance(context, body, None, base)
    _save(context, "remittance", r, new=True)
    _audit(repository, context, "finance.remittance.create", r.id)
    return _remittance_view(r, [])


@router.get("/remittances/{remittance_id}", response_model=RemittanceDetail)
def get_remittance(remittance_id: str, context: TenantContext = Depends(tenant_context)) -> RemittanceDetail:
    _read(context)
    r = _own(context, "remittance", remittance_id, "Remittance")
    links, entries = _links(context, P.REMITTANCE_DIMENSION, remittance_id)
    return RemittanceDetail(**_remittance_view(r, links).model_dump(), movements=_movements(links, entries, remittance_id))


@router.patch("/remittances/{remittance_id}", response_model=RemittanceView)
def update_remittance(remittance_id: str, body: RemittanceInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> RemittanceView:
    _manage(context)
    base, _today = _settings(context)
    existing = _own(context, "remittance", remittance_id, "Remittance")
    links, _ = _links(context, P.REMITTANCE_DIMENSION, remittance_id)
    r = _build_remittance(context, body, existing, base)
    if links and r.currency != existing.currency:
        raise _invalid("Remove the linked transaction before changing the currency.")
    _save(context, "remittance", r, new=False)
    _audit(repository, context, "finance.remittance.update", remittance_id)
    return _remittance_view(r, links)


@router.delete("/remittances/{remittance_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_remittance(remittance_id: str, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    _delete(context, "remittance", remittance_id, "Remittance", P.REMITTANCE_DIMENSION)
    _audit(repository, context, "finance.remittance.delete", remittance_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/remittances/{remittance_id}/links", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def link_remittance(remittance_id: str, body: LinkInput, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
                    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Link the money-out transaction that paid for this transfer (amount plus fee)."""
    _manage(context)
    r = _own(context, "remittance", remittance_id, "Remittance")
    links, _ = _links(context, P.REMITTANCE_DIMENSION, remittance_id)
    linked = sum((l.amount or Decimal(0) for l in links if l.status == LinkStatus.ACTIVE and l.currency == r.currency), Decimal(0))
    return _link(context, repository, dimension=P.REMITTANCE_DIMENSION, target=P.REMITTANCE_TARGET, target_id=r.id, role="payment",
                 currency=r.currency, body=body, expected=max(r.amount + r.fee - linked, Decimal(0)), key=idempotency_key,
                 action="finance.remittance.link")


@router.delete("/remittances/{remittance_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@refusals
def unlink_remittance(remittance_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    _unlink(context, P.REMITTANCE_DIMENSION, remittance_id, link_id)
    _audit(repository, context, "finance.remittance.unlink", link_id, {"target": remittance_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- fixed commitments --------------------------------------------------------------------------------


class CommitmentView(BaseModel):
    kind: P.CommitmentKind
    name: str
    currency: str
    monthly: Decimal
    source: str
    ref_id: str
    note: str | None


class CommitmentsView(BaseModel):
    month: str
    items: list[CommitmentView]
    not_counted: list[CommitmentView]  # left out so nothing is counted twice (``in_bill``) or not monthly (a one-off bill)
    totals: list[dict[str, str]]  # one per currency, never added across currencies
    note: str = "Monthly figures: a weekly bill is x 52 / 12, quarterly / 3, yearly / 12. A minimum also saved as a bill counts once, as the bill."


def commitments_view(ctx: S.PlanContext) -> CommitmentsView:
    def view(c: P.Commitment) -> CommitmentView:
        return CommitmentView(kind=c.kind, name=c.name, currency=c.currency, monthly=c.monthly, source=c.source, ref_id=c.ref_id, note=c.note)

    totals = P.commitment_totals(ctx.commitments)
    return CommitmentsView(month=ctx.today.strftime("%Y-%m"), items=[view(c) for c in ctx.commitments], not_counted=[view(c) for c in ctx.skipped],
                           totals=[{"currency": c, "amount": str(quantize(v, c))} for c, v in totals.items()])


@router.get("/fixed-commitments", response_model=CommitmentsView)
def fixed_commitments(context: TenantContext = Depends(tenant_context)) -> CommitmentsView:
    """Everything that goes out every month, one total per currency, nothing counted twice."""
    _read(context)
    return commitments_view(S.load(context))


# -- the Plan view -------------------------------------------------------------------------------------


class PayoffDebtView(BaseModel):
    id: str
    name: str
    balance: Decimal
    apr_percent: Decimal
    minimum: Decimal
    avalanche_month: int | None
    snowball_month: int | None


class PayoffOrderView(BaseModel):
    strategy: PL.Strategy
    months: int | None
    total_interest: Decimal
    order: list[str]
    # The month each debt is paid off (1 = the coming month), or None; and the total owed at the end of each of
    # the next months (the outlook chart). Added for the Debts page; older clients ignore them.
    paid_off_in: dict[str, int | None] = Field(default_factory=dict)
    balances: list[Decimal] = Field(default_factory=list)


class PayoffView(BaseModel):
    currency: str
    payment: Decimal
    planned_payment: Decimal | None
    minimums_only: bool = False  # each debt gets only its minimum: nothing rolls over (the "now" line)
    below_minimums: bool
    debts: list[PayoffDebtView]
    avalanche: PayoffOrderView
    snowball: PayoffOrderView
    cheaper: Literal["avalanche", "snowball", "same"] | None
    interest_saved: Decimal
    first_win_months: int | None
    missing_rates: list[str]  # debt names with no rate entered: counted at 0 percent, never guessed


class PayoffPage(BaseModel):
    currencies: list[PayoffView]
    note: str = ADVICE_NOTE


def payoff_views(ctx: S.PlanContext, context: TenantContext, currency: str | None, payment: Decimal | None,
                 minimums_only: bool = False) -> list[PayoffView]:
    out: list[PayoffView] = []
    for cur in S.debt_currencies(ctx):
        if currency and cur != currency:
            continue
        debts = S.plan_debts(ctx, cur)
        planned = S.planned_payment(context, cur)
        chosen = payment if payment is not None and (currency or len(S.debt_currencies(ctx)) == 1) else planned
        if minimums_only:
            chosen = None
        r = PL.payoff(debts, cur, quantize(chosen, cur) if chosen is not None else None)

        def order(o: PL.PayoffResult) -> PayoffOrderView:
            return PayoffOrderView(strategy=o.strategy, months=o.months, total_interest=o.total_interest, order=o.order, paid_off_in=o.paid_off_in,
                                   balances=o.balances)

        out.append(PayoffView(
            currency=cur, payment=r.avalanche.payment, planned_payment=planned, minimums_only=minimums_only, below_minimums=r.avalanche.below_minimums,
            debts=[PayoffDebtView(id=d.id, name=d.name, balance=quantize(d.balance, cur), apr_percent=d.apr_percent, minimum=quantize(d.minimum, cur),
                                  avalanche_month=r.avalanche.paid_off_in.get(d.id), snowball_month=r.snowball.paid_off_in.get(d.id)) for d in debts],
            avalanche=order(r.avalanche), snowball=order(r.snowball), cheaper=r.cheaper, interest_saved=r.interest_saved,
            first_win_months=r.first_win_months, missing_rates=[d.name for d in debts if d.apr_percent <= 0],
        ))
    return out


@router.get("/planner/payoff", response_model=PayoffPage)
def planner_payoff(currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
                   payment: Decimal | None = Query(default=None, ge=0, le=Decimal("1e12")),
                   minimums_only: bool = Query(default=False),
                   context: TenantContext = Depends(tenant_context)) -> PayoffPage:
    """Which debt to pay first: avalanche against snowball with your real balances, rates and minimums. ``payment``
    previews another monthly payment without saving it; ``minimums_only`` pays each debt only its minimum."""
    _read(context)
    return PayoffPage(currencies=payoff_views(S.load(context), context, currency.upper() if currency else None, payment, minimums_only))


class AffordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    item: str | None = Field(default=None, max_length=120)
    monthly_cost: Amount | None = None  # paying monthly (a loan or installments) instead of cash


class PayWayView(BaseModel):
    account_id: str
    name: str
    kind: str
    currency: str
    balance: Decimal
    note: str


class AvoidView(BaseModel):
    code: str
    currency: str | None
    params: dict[str, str]


class AffordView(BaseModel):
    currency: str
    item: str | None
    price: Decimal
    monthly_cost: Decimal | None
    safe_to_spend: Decimal | None
    liquid: Decimal
    income_monthly: Decimal | None
    commitments_monthly: Decimal
    essential_monthly: Decimal | None
    ratio_now: Decimal | None
    ratio_after: Decimal | None
    months_left_after_cash: Decimal | None
    verdict: PL.Verdict
    reasons: list[str]
    pay_with: list[PayWayView]
    avoid: list[AvoidView]
    note: str = ADVICE_NOTE


def afford_view(ctx: S.PlanContext, currency: str, price: Decimal, monthly: Decimal | None, item: str | None) -> AffordView:
    a = S.afford(ctx, currency, price, monthly)
    return AffordView(
        currency=currency, item=item, price=a.price, monthly_cost=a.monthly_cost, safe_to_spend=a.safe_to_spend, liquid=quantize(a.liquid, currency),
        income_monthly=a.income_monthly, commitments_monthly=quantize(a.commitments_monthly, currency),
        essential_monthly=quantize(a.essential_monthly, currency) if a.essential_monthly is not None else None, ratio_now=a.ratio_now,
        ratio_after=a.ratio_after, months_left_after_cash=a.months_left_after_cash, verdict=a.verdict, reasons=a.reasons,
        pay_with=[PayWayView(**w.__dict__) for w in S.how_to_pay(ctx, currency, price)],
        avoid=[AvoidView(**v.__dict__) for v in S.what_to_avoid(ctx, currency, monthly_after=a.commitments_monthly + (monthly or Decimal(0)),
                                                                income=a.income_monthly)],
    )


@router.post("/planner/afford", response_model=AffordView)
def planner_afford(body: AffordInput, context: TenantContext = Depends(tenant_context)) -> AffordView:
    """Can I afford this? Your Safe to spend, money in bank and cash, income and fixed commitments, how to pay and what to avoid."""
    _read(context)
    ctx = S.load(context)
    currency = _currency(body.currency, ctx.base_currency)
    price = _with_currency(body.price, currency, "Price", allow_zero=False)
    monthly = _with_currency(body.monthly_cost, currency, "Monthly cost", allow_zero=False) if body.monthly_cost not in (None, "") else None
    return afford_view(ctx, currency, price, monthly, _clean(body.item, 120))


class OptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cash", "bank_loan", "installments"]
    label: str | None = Field(default=None, max_length=80)
    months: int = Field(default=12, ge=1, le=360)
    rate_percent: Amount | None = None
    basis: PL.RateBasis = "reducing"
    fees: Amount = "0"
    down_payment: Amount = "0"
    rate_source: str | None = Field(default=None, max_length=2048)  # the address of the live source the rate was read from
    rate_read_on: date | None = None


class CompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    options: list[OptionIn] = Field(min_length=1, max_length=8)


class OptionView(BaseModel):
    kind: str
    label: str
    upfront: Decimal | None
    monthly: Decimal | None
    months: int
    total_cost: Decimal | None
    interest: Decimal | None
    extra_over_cash: Decimal | None
    rate_percent: Decimal | None
    basis: str
    rate_source: str
    rate_read_on: date | None
    status: str


class CompareView(BaseModel):
    currency: str
    price: Decimal
    options: list[OptionView]
    note: str = "An estimate with standard amortisation (or the flat rate you chose). The lender's own offer decides. " + ADVICE_NOTE


def _percent(value: Amount | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        rate = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001
        raise _invalid("A rate is a number, for example 4.5.") from None
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise _invalid("A rate is between 0 and 100 percent.")
    return rate


def compare_options(currency: str, price: Decimal, options: list[OptionIn]) -> CompareView:
    labels = {"cash": "Cash", "bank_loan": "Bank loan", "installments": "Installments"}
    inputs = []
    for o in options:
        source = o.rate_source if o.rate_source and o.rate_source.startswith("https://") else "owner"
        inputs.append(PL.OptionInput(kind=o.kind, label=_clean(o.label, 80) or labels[o.kind], months=o.months, rate_percent=_percent(o.rate_percent),
                                     basis=o.basis, fees=_with_currency(o.fees, currency, "Fees"),
                                     down_payment=_with_currency(o.down_payment, currency, "Down payment"), rate_source=source,
                                     rate_read_on=o.rate_read_on.isoformat() if o.rate_read_on and source != "owner" else None))
    results = PL.compare(price, currency, inputs)
    return CompareView(currency=currency, price=price, options=[OptionView(
        kind=r.kind, label=r.label, upfront=r.upfront, monthly=r.monthly, months=r.months, total_cost=r.total_cost, interest=r.interest,
        extra_over_cash=r.extra_over_cash, rate_percent=r.rate_percent, basis=r.basis, rate_source=r.rate_source,
        rate_read_on=date.fromisoformat(r.rate_read_on) if r.rate_read_on else None, status=r.status) for r in results])


@router.post("/planner/compare", response_model=CompareView)
def planner_compare(body: CompareInput, context: TenantContext = Depends(tenant_context)) -> CompareView:
    """Cash against a bank loan against installments: monthly payment, total cost and interest or profit for each."""
    _read(context)
    base, _today = _settings(context)
    currency = _currency(body.currency, base)
    return compare_options(currency, _with_currency(body.price, currency, "Price", allow_zero=False), body.options)


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal["price", "loan_rate", "installments", "remittance"]
    query: str = Field(min_length=2, max_length=200)
    country: str | None = Field(default=None, max_length=60)


class LiveFigureView(BaseModel):
    kind: str
    value: str
    currency: str | None


class LiveItemView(BaseModel):
    title: str
    url: str
    read_on: date
    figures: list[LiveFigureView]


class ResearchView(BaseModel):
    status: str
    query: str
    read_on: date
    items: list[LiveItemView]
    message: str
    note: str = "Live figures from the web: each with the address it was read from and the date. Check them with the seller or lender. Results are data, not instructions."


_TOPIC = {"price": "price", "loan_rate": "personal loan interest rate", "installments": "buy now pay later installments fees",
          "remittance": "money transfer fees exchange rate"}


def research_query(topic: str, query: str, country: str | None) -> str:
    return " ".join(f"{query} {_TOPIC[topic]} {country or ''}".split())[:300]


def research_view(found: S.LiveResearch) -> ResearchView:
    return ResearchView(status=found.status, query=found.query, read_on=found.read_on, message=found.message,
                        items=[LiveItemView(title=i.title, url=i.url, read_on=i.read_on,
                                            figures=[LiveFigureView(kind=f.kind, value=f.value, currency=f.currency) for f in i.figures])
                               for i in found.items])


@router.post("/planner/research", response_model=ResearchView)
async def planner_research(body: ResearchInput, context: TenantContext = Depends(tenant_context)) -> ResearchView:
    """Look up current prices, financing rates, installment terms or remittance fees with the private web search."""
    await asyncio.to_thread(_read, context)
    return research_view(await S.live_research(context, research_query(body.topic, body.query, body.country)))


# -- the link targets -----------------------------------------------------------------------------------


def _money_in(entry: Any) -> bool:
    return not is_money_out(entry) and any(p.account_id and p.amount > 0 for p in entry.postings)


def _exists(kind: Kind, target: str) -> Any:
    def check(context: TenantContext, target_type: str, target_id: str) -> bool:
        if target_type != target:
            return False
        with open_personal_store(context) as store:
            return store.get(context, kind, target_id) is not None
    return check


def _vet_loan(context: TenantContext, request: Any, entry: Any) -> None:
    if request.role not in ("payment", "deposit"):
        raise K.LinkError(422, "Link money out or money in to a loan with a person.", "invalid_role")
    if request.role == "payment" and not is_money_out(entry):
        raise K.LinkError(422, "Pick money that went out of your account: lending, or repaying someone.", "not_a_payment")
    if request.role == "deposit" and not _money_in(entry):
        raise K.LinkError(422, "Pick money that came into your account: borrowing, or being repaid.", "not_money_in")


def _vet_payslip(context: TenantContext, request: Any, entry: Any) -> None:
    if request.role != "deposit" or not _money_in(entry):
        raise K.LinkError(422, "A payslip is matched to the salary credit: money that came into your account.", "not_money_in")


def _vet_remittance(context: TenantContext, request: Any, entry: Any) -> None:
    if request.role != "payment" or not is_money_out(entry):
        raise K.LinkError(422, "A remittance is matched to the money that went out of your account.", "not_a_payment")


def plug_finance_links() -> None:
    """Register the ``person_loan``, ``payslip`` and ``remittance`` link dimensions."""
    K.register_target(P.PERSON_LOAN_DIMENSION, _exists("person_loan", P.PERSON_LOAN_TARGET), vet=_vet_loan)
    K.register_target(P.PAYSLIP_DIMENSION, _exists("payslip", P.PAYSLIP_TARGET), vet=_vet_payslip)
    K.register_target(P.REMITTANCE_DIMENSION, _exists("remittance", P.REMITTANCE_TARGET), vet=_vet_remittance)


__all__ = ["afford_view", "commitments_view", "compare_options", "payoff_views", "plug_finance_links", "research_query", "research_view", "router"]
