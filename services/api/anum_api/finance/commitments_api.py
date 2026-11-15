"""Bills and budgets, debts and goals: the routes (docs/finance.md#bills-and-budgets, #debts-and-goals).

* ``GET /bill-schedule``: every active bill's due dates in a window, each with its status worked out
  from matched payments (``paid`` only with an active match).
* ``GET /payment-candidates``: recorded payments a bill or a debt could be matched to.
* ``POST /bills/{id}/matches``, ``DELETE /bills/{id}/matches/{link_id}``: match a recorded payment to one
  due date, or remove the match (the link is voided, never deleted).
* ``GET /debt-payments``, ``POST /debts/{id}/payments``, ``DELETE /debts/{id}/payments/{link_id}``.
* ``GET, PUT /debt-plan``: the payoff strategy and planned monthly payment per currency.
* ``GET /budgets``, ``PUT /budgets/alerts``: the Guardian category caps as budgets, with spend, pace and
  the alert threshold per budget. Caps themselves change only through Guardian (propose, then confirm).
* ``GET, POST /budgets/plans``, ``PATCH, DELETE /budgets/plans/{id}``: the owner's own budgets (spending, savings,
  reserve, sinking funds, remittances, debts; monthly, weekly or custom) with spent, left, coming bills, forecast and
  risk; ``GET /budgets/allocation``: the month's income split across them (docs/finance.md#budgets-and-planning).

Matching writes a finance link through ``links.create`` (the owner's lock, idempotency, never more than
the payment); nothing here posts money or changes the journal.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import links as K
from .api import _audit, _currency, _invalid, _manage, _money, _not_found, _read
from .commitments import (
    BILL_DIMENSION,
    BILL_TARGET,
    DEBT_DIMENSION,
    DEBT_TARGET,
    AllocationView,
    BillOccurrence,
    BudgetReport,
    PlanBudgetLine,
    PlanReport,
    bill_schedule,
    bill_target_id,
    budget_report,
    is_money_out,
    occurrences,
    plan_budget_report,
    salary_allocation,
)
from .links_api import LinkResult, _view, refusals
from .links_store import LinkStatus, open_link_stores
from .money import quantize
from .models import (
    Bill,
    Budget,
    BudgetAlert,
    BudgetKind,
    BudgetPeriod,
    BudgetTerms,
    Category,
    Debt,
    DebtPlan,
    DebtStrategy,
    InstallmentRefundNotice,
    InstallmentStatementEntry,
    OverspendMode,
    Planning,
    RolloverMode,
)
from .policy import cap_carry, current_rules
from .store import open_finance_store
from .summary import month_range, settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
logger = logging.getLogger("anum_api.finance.commitments")

MAX_PAGE = 100


# -- complete provider installment statements --------------------------------------------------------------


class InstallmentStatementView(BaseModel):
    items: list[InstallmentStatementEntry]
    total: int


class InstallmentStatementSyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty when a Gmail read found only refund emails: those are still saved as evidence for orders already imported.
    entries: list[InstallmentStatementEntry] = Field(default_factory=list, max_length=1000)
    # Refund and order-updated emails read with this import, kept as refund evidence (docs/finance.md#tamara-refunds).
    refund_notices: list[InstallmentRefundNotice] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _something(self) -> "InstallmentStatementSyncInput":
        if not self.entries and not self.refund_notices:
            raise ValueError("Send statement entries, refund notices or both.")
        return self


class InstallmentStatementSyncResult(BaseModel):
    created: int
    updated: int
    unchanged: int
    conflicts: list[str]
    total: int
    # Installments a later statement shows with a lower amount: refund evidence, reviewed in Refunds.
    refund_signals: list[str] = Field(default_factory=list)
    notices_saved: int = 0


_ANUM_FIELDS = {"refunded_amount", "reported_amount"}


def _source_terms(row: InstallmentStatementEntry) -> dict:
    """What the statement itself says about a row, without what ANUM applied to it."""
    return row.model_dump(exclude=_ANUM_FIELDS)


def _installment_key(row: InstallmentStatementEntry) -> tuple[str, str, int]:
    return (row.provider.casefold(), row.order_id.casefold(), row.installment)


@router.get("/installment-statement", response_model=InstallmentStatementView)
def get_installment_statement(context: TenantContext = Depends(tenant_context)) -> InstallmentStatementView:
    """The owner's complete imported statement, including paid and refund-review source rows."""
    _read(context)
    with open_finance_store(context) as store:
        rows = store.get_planning(context).installment_entries
    return InstallmentStatementView(items=sorted(rows, key=lambda r: (r.due_on, r.order_id, r.installment)), total=len(rows))


@router.post("/installment-statement/sync", response_model=InstallmentStatementSyncResult)
def sync_installment_statement(
    body: InstallmentStatementSyncInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> InstallmentStatementSyncResult:
    """Idempotent owner-scoped upsert. No bank transactions, payment matches or real money actions."""
    _manage(context)
    incoming: dict[tuple[str, str, int], InstallmentStatementEntry] = {}
    for entry in body.entries:
        if entry.installment > entry.total_installments:
            raise _invalid("Installment number cannot exceed the plan length.")
        # The refunded and reported amounts are ANUM's own, never taken from an upload.
        normalized = entry.model_copy(update={"amount": quantize(entry.amount, entry.currency), "refunded_amount": Decimal(0),
                                              "reported_amount": None})
        key = _installment_key(normalized)
        if key in incoming:
            raise _invalid(f"Duplicate provider order installment in upload: {entry.order_id} #{entry.installment}")
        incoming[key] = normalized
    created = updated = unchanged = notices_saved = 0
    conflicts: list[str] = []
    signals: list[str] = []
    with open_finance_store(context) as store:
        planning = store.get_planning(context)
        current = {_installment_key(entry): entry for entry in planning.installment_entries}
        changed_reported = False
        for key, row in incoming.items():
            old = current.get(key)
            if old is None:
                if len(current) >= 3000:
                    raise _invalid("Maximum 3,000 saved statement entries reached.")
                current[key] = row
                created += 1
                continue
            label = f"{row.provider} / {row.order_id} / {row.installment}"
            if old.currency == row.currency and old.due_on == row.due_on and row.amount < old.amount and old.source_status != "paid":
                # Same installment and due date, lower amount: Tamara's own new schedule after a refund. The original
                # terms stay; the lower figure is kept as refund evidence for the owner to apply.
                if old.reported_amount != row.amount:
                    current[key] = old.model_copy(update={"reported_amount": row.amount})
                    changed_reported = True
                signals.append(f"{label}: lower amount reported")
                continue
            # A different amount or date may mean a reschedule, refund or incorrect source file.
            # Keep the prior financial terms until the person reconciles the difference.
            if old.amount != row.amount or old.due_on != row.due_on or old.currency != row.currency:
                conflicts.append(f"{label}: due date or amount changed")
                continue
            if _source_terms(old) == _source_terms(row):
                unchanged += 1
            else:
                # The applied refund and any reported amount survive a reimport.
                current[key] = row.model_copy(update={"refunded_amount": old.refunded_amount, "reported_amount": old.reported_amount})
                updated += 1
        known = {n.message_ref for n in planning.installment_refund_notices}
        for notice in body.refund_notices:
            if notice.message_ref not in known and len(planning.installment_refund_notices) < 3000:
                planning.installment_refund_notices.append(notice)
                known.add(notice.message_ref)
                notices_saved += 1
        if created or updated or changed_reported or notices_saved:
            planning.installment_entries = sorted(current.values(), key=lambda r: (r.due_on, r.order_id, r.installment))
            store.save_planning(context, planning)
    _audit(repository, context, "finance.installment_statement.sync", context.user_id,
           {"created": created, "updated": updated, "unchanged": unchanged, "conflicts": len(conflicts),
            "refund_signals": len(signals), "notices_saved": notices_saved})
    return InstallmentStatementSyncResult(created=created, updated=updated, unchanged=unchanged, conflicts=conflicts,
                                          total=len(current), refund_signals=signals, notices_saved=notices_saved)


# -- bills ----------------------------------------------------------------------------------------------


class BillSchedule(BaseModel):
    today: date
    start: date
    end: date
    items: list[BillOccurrence]


@router.get("/bill-schedule", response_model=BillSchedule)
def get_bill_schedule(
    start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
    context: TenantContext = Depends(tenant_context),
) -> BillSchedule:
    """Due dates from ``from`` to ``to`` (default: this month), with matched payments and status."""
    _read(context)
    with open_link_stores(context) as (finance, _sms, links):
        today = today_in(settings_for(finance, context))
        _label, month_start, month_end = month_range(None, today)
        first, last = start or month_start, end or month_end
        if last < first:
            raise _invalid("The end date is before the start date.")
        if (last - first).days > 400:
            raise _invalid("Ask for at most 400 days at a time.")
        bills = [b for b in finance.list_items(context, "bill") if isinstance(b, Bill)]
        rows, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None, status=None,
                                   limit=10_000, offset=0)
    return BillSchedule(today=today, start=first, end=last, items=bill_schedule(bills, rows, today, first, last))


class Candidate(BaseModel):
    canonical_id: str
    posted_on: date
    merchant: str
    category: str | None
    account_id: str | None
    amount: Decimal
    currency: str
    remaining: Decimal  # what is left of the payment to match in this dimension


class CandidatePage(BaseModel):
    items: list[Candidate]


@router.get("/payment-candidates", response_model=CandidatePage)
def payment_candidates(
    for_: Literal["bill", "debt", "person_loan", "payslip", "remittance", "trip", "project"] = Query(alias="for"),
    direction: Literal["in", "out"] | None = Query(default=None),
    around: date | None = Query(default=None),
    amount: Decimal | None = Query(default=None, ge=0, le=Decimal("1e15")),
    currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
    q: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    context: TenantContext = Depends(tenant_context),
) -> CandidatePage:
    """Your recorded payments near a date (60 days before to 15 after), closest in amount first. A suggestion only.

    Bills, debts and remittances take money out; a payslip takes the salary credit (money in); a loan with a person
    takes either (``direction``: lending and repaying a loan are money out, borrowing and being repaid money in), and so does a
    personal trip or project (a payment, or a refund that came back)."""
    _read(context)
    return CandidatePage(items=find_candidates(context, for_, direction=direction, around=around, amount=amount, currency=currency, q=q, limit=limit))


def find_candidates(context: TenantContext, for_: str, *, direction: str | None = None, around: date | None = None, amount: Decimal | None = None,
                    currency: str | None = None, q: str | None = None, limit: int = 20) -> list[Candidate]:
    """The recorded payments a bill, debt, loan, payslip or remittance could be matched to, closest in amount first (a
    suggestion only). The one matcher behind ``GET /payment-candidates`` and the suggestion queue's payment items."""
    wanted_direction = direction or ("in" if for_ == "payslip" else "out")
    if for_ in ("bill", "debt", "remittance") and wanted_direction != "out":
        raise _invalid("Bills, debts and remittances are paid with money out.")
    if for_ == "payslip" and wanted_direction != "in":
        raise _invalid("A payslip is matched to the salary credit (money in).")
    needle = (q or "").casefold().strip()
    wanted = currency.upper() if currency else None
    with open_link_stores(context) as (finance, store, links):
        centre = around or today_in(settings_for(finance, context))  # the owner's local day, like every due date
        entries = store.list_entries(context, start=centre - timedelta(days=60), end=centre + timedelta(days=15), limit=5_000)
        taken, _ = links.list_links(context, canonical_id=None, dimension=for_, target_type=None, target_id=None, status=None, limit=10_000, offset=0)
    chains: dict[str, list[Any]] = {}
    for entry in entries:
        chains.setdefault(entry.canonical_id or entry.id, []).append(entry)
    live: dict[str, list[Any]] = {}
    for link in taken:
        if link.status != LinkStatus.VOID:
            live.setdefault(link.canonical_id, []).append(link)
    out: list[tuple[tuple[Any, ...], Candidate]] = []
    for canonical, chain in chains.items():
        current = K.live_entry(chain)
        if current is None or is_money_out(current) != (wanted_direction == "out") or not any(p.account_id for p in current.postings):
            continue
        if needle and needle not in f"{current.merchant or ''} {current.description}".casefold():
            continue
        account = next((p.account_id for p in current.postings if p.account_id and (p.amount < 0) == (wanted_direction == "out")), None)
        for cur, cap in K.capacity(chain).items():
            if wanted and cur != wanted:
                continue
            remaining = max(cap - K.used(live.get(canonical, []), for_, cur), Decimal(0))
            if remaining <= 0:
                continue
            gap = abs(cap - amount) if amount is not None else Decimal(0)
            out.append(((gap, abs((current.posted_on - centre).days), canonical), Candidate(
                canonical_id=canonical, posted_on=current.posted_on, merchant=(current.merchant or current.description)[:80],
                category=current.category, account_id=account, amount=cap, currency=cur, remaining=remaining,
            )))
    out.sort(key=lambda pair: pair[0])
    return [c for _, c in out[:limit]]


class MatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=80)
    due_on: date | None = None  # bills only
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("1e15"))


def _match_amount(context: TenantContext, canonical_id: str, dimension: str, target_id: str, currency: str, wanted: Decimal | None,
                  fallback: Decimal) -> Decimal:
    """The amount to match: what was asked, else the expected amount, never more than is left of the payment.

    Links to the same target are left out of "what is left", so a retried request works out the same amount
    and ``links.create`` returns the first link; a second match of the same target is refused there."""
    with open_link_stores(context) as (_finance, store, links):
        chain = store.chain(context, canonical_id)
        live = links.live_links(context, canonical_id)
    caps = K.capacity(chain)
    if not chain:
        raise K.LinkError(404, "Transaction not found. It may have been deleted.", "transaction_not_found")
    if currency not in caps:
        raise K.LinkError(422, f"The payment is not in {currency}. Match a payment in the same currency.", "currency_mismatch",
                          {"currencies": sorted(caps)})
    left = caps[currency] - K.used([l for l in live if l.target_id != target_id], dimension, currency)
    if wanted is not None:
        return wanted
    amount = min(fallback, left) if fallback > 0 else left
    if amount <= 0:
        raise K.LinkError(409, "This payment is already matched up to its amount.", "over_allocated", {"remaining": "0", "currency": currency})
    return amount


def _own_bill(context: TenantContext, bill_id: str) -> Bill:
    with open_finance_store(context) as store:
        found = store.get_item(context, "bill", bill_id)
    if not isinstance(found, Bill):
        raise _not_found("Bill")
    return found


def _own_debt(context: TenantContext, debt_id: str) -> Debt:
    with open_finance_store(context) as store:
        found = store.get_item(context, "debt", debt_id)
    if not isinstance(found, Debt):
        raise _not_found("Debt")
    return found


@router.post("/bills/{bill_id}/matches", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def match_bill(
    bill_id: str, body: MatchInput,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> Any:
    """Match one recorded payment to one due date of the bill. Only this makes the due date paid."""
    _manage(context)
    bill = _own_bill(context, bill_id)
    if body.due_on is None:
        raise _invalid("Pick the due date this payment paid.")
    if body.due_on not in occurrences(bill, body.due_on, body.due_on):
        raise _invalid("That date is not one of this bill's due dates.")
    amount = _match_amount(context, body.canonical_id, BILL_DIMENSION, bill_target_id(bill.id, body.due_on), bill.currency, body.amount,
                           bill.amount)
    made = K.create(context, K.LinkRequest(
        canonical_id=body.canonical_id, dimension=BILL_DIMENSION, role="payment", target_type=BILL_TARGET,
        target_id=bill_target_id(bill.id, body.due_on), amount=amount, currency=bill.currency, idempotency_key=idempotency_key,
    ))
    if not made.replayed:
        _audit(repository, context, "finance.bill.match", made.link.id, {"bill": bill.id})
    return LinkResult(link=_view(made.link), replayed=made.replayed)


def _void_own(context: TenantContext, link_id: str, dimension: str, target_ok: Any) -> Any:
    with open_link_stores(context) as (_finance, _store, links):
        found = links.get_link(context, link_id)
    if found is None or found.dimension != dimension or not target_ok(found.target_id):
        raise K.LinkError(404, "Match not found. It may have been removed.", "link_not_found")
    return K.void(context, link_id, expected_version=None)


@router.delete("/bills/{bill_id}/matches/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@refusals
def unmatch_bill(bill_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    """Remove a match: the due date is a forecast again. The link is voided, the payment is untouched."""
    _manage(context)
    _void_own(context, link_id, BILL_DIMENSION, lambda target: target.startswith(f"{bill_id}:"))
    _audit(repository, context, "finance.bill.unmatch", link_id, {"bill": bill_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- debts ------------------------------------------------------------------------------------------------


class DebtPayment(BaseModel):
    link_id: str
    debt_id: str
    debt_name: str | None
    canonical_id: str
    posted_on: date | None
    merchant: str | None
    account_id: str | None
    amount: Decimal
    currency: str
    status: LinkStatus


class DebtPaymentPage(BaseModel):
    items: list[DebtPayment]
    total: int
    limit: int
    offset: int
    paid: list[dict[str, str]]  # active matched payments per currency (and per debt with ``debt_id``)


@router.get("/debt-payments", response_model=DebtPaymentPage)
def debt_payments(
    debt_id: str | None = Query(default=None, max_length=80), limit: int = Query(default=20, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000), context: TenantContext = Depends(tenant_context),
) -> DebtPaymentPage:
    """Recorded payments matched to your debts, newest first."""
    _read(context)
    with open_link_stores(context) as (finance, store, links):
        debts = {d.id: d for d in finance.list_items(context, "debt") if isinstance(d, Debt)}
        rows, _ = links.list_links(context, canonical_id=None, dimension=DEBT_DIMENSION, target_type=DEBT_TARGET, target_id=debt_id, status=None,
                                   limit=10_000, offset=0)
        rows = [l for l in rows if l.status != LinkStatus.VOID]
        entries = {cid: K.live_entry(store.chain(context, cid)) for cid in {l.canonical_id for l in rows}}
    paid: dict[tuple[str, str], Decimal] = {}
    items: list[DebtPayment] = []
    for link in rows:
        entry = entries.get(link.canonical_id)
        ccy = K.settled_currency(link)
        if link.status == LinkStatus.ACTIVE and link.amount is not None and ccy:
            paid[(link.target_id, ccy)] = paid.get((link.target_id, ccy), Decimal(0)) + K.settled(link, ccy)
        debt = debts.get(link.target_id)
        items.append(DebtPayment(
            link_id=link.id, debt_id=link.target_id, debt_name=debt.name if debt else None, canonical_id=link.canonical_id,
            posted_on=entry.posted_on if entry else None, merchant=(entry.merchant or entry.description)[:80] if entry else None,
            account_id=next((p.account_id for p in entry.postings if p.account_id and p.amount < 0), None) if entry else None,
            amount=quantize(link.amount or Decimal(0), link.currency) if link.currency else link.amount or Decimal(0),
            currency=link.currency or (debt.currency if debt else ""), status=link.status,
        ))
    items.sort(key=lambda p: (p.posted_on or date.min, p.link_id), reverse=True)
    return DebtPaymentPage(
        items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset,
        paid=[{"debt_id": d, "currency": c, "amount": str(quantize(a, c))} for (d, c), a in sorted(paid.items())],
    )


def _not_more_than_owed(context: TenantContext, debt: Debt, canonical_id: str, amount: Decimal, *, asked: bool) -> Decimal:
    """A payment matched to a debt is never more than the debt owes now (docs/finance.md#bills-and-budgets): the excess would
    vanish from every figure (a balance never goes below zero). An amount asked for above what is owed is refused; without
    one, the match takes what is owed and the rest of the payment keeps its category. A retry of a match already made, and an
    amount above the payment itself, are left to ``links.create`` (``over_allocated``)."""
    from . import obligations as O

    links = O.load(context)  # its own short unit of work first
    if any(p.debt_id == debt.id and p.canonical_id == canonical_id for p in links.debt_payments):
        return amount
    with open_link_stores(context) as (_finance, store, link_store):
        caps = K.capacity(store.chain(context, canonical_id))
        live = link_store.live_links(context, canonical_id)
    if amount > caps.get(debt.currency, Decimal(0)) - K.used(live, DEBT_DIMENSION, debt.currency):
        return amount
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        accounts = store.list_accounts(context)
        sums = store.account_sums(context)
    owed = O.current_debts([debt], accounts, sums, links, today)[0].balance
    if not asked and 0 < owed < amount:
        return owed
    if amount > owed:
        raise K.LinkError(409, f"This debt owes {debt.currency} {owed:,} now, less than the {debt.currency} {amount:,} matched. Match only "
                          "what paid it; the rest of the payment keeps its own category.", "over_debt", {"owed": str(owed), "currency": debt.currency})
    return amount


@router.post("/debts/{debt_id}/payments", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def match_debt_payment(
    debt_id: str, body: MatchInput,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> Any:
    """Match a recorded payment to a debt (its history). The balance stays what you entered."""
    _manage(context)
    debt = _own_debt(context, debt_id)
    amount = _match_amount(context, body.canonical_id, DEBT_DIMENSION, debt.id, debt.currency, body.amount, Decimal(0))
    amount = _not_more_than_owed(context, debt, body.canonical_id, amount, asked=body.amount is not None)
    made = K.create(context, K.LinkRequest(
        canonical_id=body.canonical_id, dimension=DEBT_DIMENSION, role="payment", target_type=DEBT_TARGET, target_id=debt.id,
        amount=amount, currency=debt.currency, idempotency_key=idempotency_key,
    ))
    if not made.replayed:
        _audit(repository, context, "finance.debt.payment_match", made.link.id, {"debt": debt.id})
        # A card repayment linked by hand is compared with the card's statement like an engine link (docs/finance-matching.md).
        from .matching_service import record_hand_link

        try:
            record_hand_link(context, debt.id, made.link)
        except Exception as exc:  # noqa: BLE001 - the link stands; the comparison is shown the next time it is worked out
            logger.warning("finance_card_hand_link_check_failed error_class=%s", type(exc).__name__)
    return LinkResult(link=_view(made.link), replayed=made.replayed)


@router.delete("/debts/{debt_id}/payments/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@refusals
def unmatch_debt_payment(debt_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                         repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    voided = _void_own(context, link_id, DEBT_DIMENSION, lambda target: target == debt_id)
    from .matching_service import hand_link_removed

    hand_link_removed(context, link_id, voided.canonical_id, debt_id)
    _audit(repository, context, "finance.debt.payment_unmatch", link_id, {"debt": debt_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- the debt plan ------------------------------------------------------------------------------------------


class DebtPlanView(BaseModel):
    plans: list[DebtPlan]


class DebtPlanRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(min_length=3, max_length=3)
    strategy: DebtStrategy = DebtStrategy.AVALANCHE
    monthly_payment: Decimal | str | None = None


class DebtPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[DebtPlanRow] = Field(max_length=20)


@router.get("/debt-plan", response_model=DebtPlanView)
def get_debt_plan(context: TenantContext = Depends(tenant_context)) -> DebtPlanView:
    _read(context)
    with open_finance_store(context) as store:
        return DebtPlanView(plans=store.get_planning(context).debt_plans)


@router.put("/debt-plan", response_model=DebtPlanView)
def put_debt_plan(body: DebtPlanInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> DebtPlanView:
    """Replace the plan: per currency, the strategy and the monthly payment (empty: the minimums only)."""
    _manage(context)
    plans: dict[str, DebtPlan] = {}
    for row in body.plans:
        currency = _currency(row.currency, row.currency)
        if currency in plans:
            raise _invalid(f"Plan: {currency} is listed twice.")
        monthly = None if row.monthly_payment in (None, "") else _money(row.monthly_payment, currency, field="Monthly payment", positive=True)
        plans[currency] = DebtPlan(currency=currency, strategy=row.strategy, monthly_payment=monthly)
    with open_finance_store(context) as store:
        planning = store.get_planning(context)
        planning.debt_plans = [plans[c] for c in sorted(plans)]
        store.save_planning(context, planning)
    _audit(repository, context, "finance.debt_plan.update", context.user_id, {"currencies": len(plans)})
    return DebtPlanView(plans=planning.debt_plans)


# -- budgets ---------------------------------------------------------------------------------------------------


@router.get("/budgets", response_model=BudgetReport)
def get_budgets(month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), context: TenantContext = Depends(tenant_context)) -> BudgetReport:
    """Your Guardian category caps as budgets for a month: spent, left, state, pace and the daily spend line."""
    _read(context)
    from . import obligations

    lending = obligations.load(context).lending_ids  # its own short unit of work first
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        label, start, end = month_range(month, today)
        rules, version = current_rules(store, context)
        alerts = store.get_planning(context).budget_alerts
        rows = store.transactions_between(context, start, end) if rules.category_caps else []
        carried = cap_carry(store, context, rules, start, today=today, lending=lending)
    return budget_report(rules, version, alerts, rows, month=label, start=start, end=end, today=today, lending=lending, carried=carried)


class AlertRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    currency: str = Field(min_length=3, max_length=3)
    alert_percent: int = Field(ge=50, le=99)
    alert_near: bool = True
    alert_over: bool = True


class AlertsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: list[AlertRow] = Field(max_length=100)


class AlertsView(BaseModel):
    alerts: list[BudgetAlert]


@router.put("/budgets/alerts", response_model=AlertsView)
def put_budget_alerts(body: AlertsInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> AlertsView:
    """When each budget alerts: at its threshold (50 to 99 percent) and at 100 percent. The caps stay Guardian's."""
    _manage(context)
    seen: dict[tuple[Category, str], BudgetAlert] = {}
    for row in body.alerts:
        currency = _currency(row.currency, row.currency)
        key = (row.category, currency)
        if key in seen:
            raise _invalid(f"Alerts: {row.category.value} in {currency} is listed twice.")
        seen[key] = BudgetAlert(category=row.category, currency=currency, alert_percent=row.alert_percent, alert_near=row.alert_near,
                                alert_over=row.alert_over)
    with open_finance_store(context) as store:
        planning: Planning = store.get_planning(context)
        planning.budget_alerts = [seen[k] for k in sorted(seen, key=lambda k: (k[0].value, k[1]))]
        store.save_planning(context, planning)
    _audit(repository, context, "finance.budget_alerts.update", context.user_id, {"budgets": len(seen)})
    return AlertsView(alerts=planning.budget_alerts)


# -- planned budgets (docs/finance.md#budgets-and-planning) ---------------------------------------------------


class PlanBudgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    kind: BudgetKind = BudgetKind.SPENDING
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    currency: str = Field(min_length=3, max_length=3)
    amount: Any
    categories: list[Category] = Field(default_factory=list, max_length=30)
    subcategory_ids: list[str] = Field(default_factory=list, max_length=60)
    starts_on: date | None = None
    ends_on: date | None = None
    fixed: bool = False
    essential: bool | None = None  # None: from the categories (``categories.is_essential``)
    allocation: str | None = Field(default=None, pattern=r"^(housing|food|transport|family|debts|cards|savings|remittances|other)$")
    goal_id: str | None = Field(default=None, max_length=80)
    debt_ids: list[str] = Field(default_factory=list, max_length=20)
    alert_percent: int = Field(default=80, ge=50, le=99)
    reserve_in_safe_to_spend: bool = False
    active: bool = True
    notes: str | None = Field(default=None, max_length=500)
    # Rollover (docs/finance.md#budget-rollover), spending budgets only. Left out: kept as it is (none and absorb at first).
    # A change applies from the current period on; earlier periods keep the terms they had.
    rollover: RolloverMode | None = None
    rollover_cap: Any = None  # with ``capped``: the most that carries, in the budget's currency
    overspend: OverspendMode | None = None


MAX_PLAN_BUDGETS = 200
CUSTOM_MAX_DAYS = 400


def _owned_sub_ids(context: TenantContext) -> dict[str, Category]:
    from .rules_store import open_rule_stores

    with open_rule_stores(context) as (_finance, store):
        return {s.id: s.parent for s in store.list_subcategories(context) if not s.archived}


def _check_plan(store: Any, context: TenantContext, body: PlanBudgetInput, owned_subs: dict[str, Category]) -> tuple[Decimal, str, bool]:
    """Every reference is the owner's own and the budget says what it covers. Returns the amount, currency and essential."""
    from .categories import is_essential, subcategory_parent

    currency = _currency(body.currency, body.currency)
    amount = _money(body.amount, currency, field="Amount", positive=True, allow_zero=False)
    kind = body.kind
    if body.period == BudgetPeriod.CUSTOM:
        if body.starts_on is None or body.ends_on is None:
            raise _invalid("A custom period needs its first and last day.")
        if body.ends_on < body.starts_on:
            raise _invalid("The last day is before the first day.")
        if (body.ends_on - body.starts_on).days > CUSTOM_MAX_DAYS:
            raise _invalid(f"A custom period is at most {CUSTOM_MAX_DAYS} days.")
    for sub in body.subcategory_ids:
        if subcategory_parent(sub) is None and sub not in owned_subs:
            raise _invalid("One of the subcategories does not exist. Choose another.")
    if kind == BudgetKind.SPENDING:
        if not body.categories and not body.subcategory_ids:
            raise _invalid("Choose at least one category or subcategory for a spending budget.")
        from .flows import NOT_SPEND

        if any(c in NOT_SPEND for c in body.categories):
            raise _invalid("Transfers, income, cashback and refunds are not spending: choose spending categories.")
    elif kind in (BudgetKind.SAVINGS, BudgetKind.EMERGENCY, BudgetKind.SINKING_FUND):
        if not body.goal_id:
            raise _invalid("Choose the goal this budget saves into (the emergency reserve or a sinking fund is a goal).")
        goal = store.get_item(context, "goal", body.goal_id)
        if goal is None:
            raise _invalid("That goal does not exist. Choose another.")
        if goal.currency != currency:
            raise _invalid(f"The goal is in {goal.currency}: a budget for it is in the same currency.")
    elif kind == BudgetKind.DEBT:
        for debt_id in body.debt_ids:
            debt = store.get_item(context, "debt", debt_id)
            if debt is None:
                raise _invalid("One of the debts does not exist. Choose another.")
            if debt.currency != currency:
                raise _invalid(f"{debt.name} is in {debt.currency}: a budget for it is in the same currency.")
    essential = body.essential
    if essential is None:
        essential = bool(body.categories) and all(is_essential(c) for c in body.categories) if kind == BudgetKind.SPENDING else True
    return amount, currency, bool(essential)


def _period_key(budget: Any) -> tuple[Any, ...]:
    """What decides a budget's period boundaries: fixed once money carries between its periods."""
    period = getattr(budget.period, "value", budget.period)
    if period == "weekly":
        return (period, budget.starts_on.weekday() if budget.starts_on is not None else 0)
    if period == "custom":
        return (period, budget.starts_on, budget.ends_on)
    return (period,)


def _record_terms(planning: Planning, item: Budget, existing: Budget | None, body: PlanBudgetInput, today: date, now: Any) -> None:
    """Keep the budget's terms for the current period when it has rollover history or now carries money. An entry for an
    earlier period is never changed: a change applies from the current period on (docs/finance.md#budget-rollover)."""
    from . import budget_rollover as RO

    history = RO.history_for(item.id, planning.budget_rollovers)
    latest = history[-1] if history else None
    spending = item.kind == BudgetKind.SPENDING
    if not spending and ((body.rollover not in (None, RolloverMode.NONE)) or body.overspend == OverspendMode.CARRY):
        raise _invalid("Only a spending budget carries money between periods.")
    mode = body.rollover or (latest.rollover if latest else RolloverMode.NONE)
    overspend = body.overspend or (latest.overspend if latest else OverspendMode.ABSORB)
    if not spending:
        mode, overspend = RolloverMode.NONE, OverspendMode.ABSORB
    cap = None
    if mode == RolloverMode.CAPPED:
        if body.rollover_cap not in (None, ""):
            cap = _money(body.rollover_cap, item.currency, field="Carry at most", positive=True, allow_zero=False)
        elif latest is not None and latest.cap is not None and latest.currency == item.currency:
            cap = latest.cap
        else:
            raise _invalid("Enter the most that may carry into the next period.")
    if latest is None and mode == RolloverMode.NONE and overspend == OverspendMode.ABSORB:
        return  # never carried and still does not: nothing to keep
    if history and existing is not None and _period_key(existing) != _period_key(item):
        raise _invalid("This budget carries money between periods, so its period cannot change. Make a new budget for the new period.")
    start = RO.period_for(item, today, item.period == BudgetPeriod.CUSTOM)[0]
    terms = BudgetTerms(budget_id=item.id, effective_from=start, currency=item.currency, amount=item.amount, categories=list(item.categories),
                        subcategory_ids=list(item.subcategory_ids), rollover=mode, cap=cap, overspend=overspend, set_at=now)
    if latest is not None and latest.model_dump(exclude={"effective_from", "set_at"}) == terms.model_dump(exclude={"effective_from", "set_at"}):
        return  # nothing changed
    kept = [t for t in planning.budget_rollovers if not (t.budget_id == item.id and t.effective_from >= start)]
    if sum(1 for t in kept if t.budget_id == item.id) >= RO.MAX_TERMS_PER_BUDGET:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This budget has changed too many times. Make a new budget.")
    planning.budget_rollovers = [*kept, terms]


def _save_plan(context: TenantContext, body: PlanBudgetInput, budget_id: str | None) -> Budget:
    owned = _owned_sub_ids(context)
    now = utc_now()
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context), now)
        planning: Planning = store.get_planning(context)
        existing = next((b for b in planning.budgets if b.id == budget_id), None) if budget_id else None
        if budget_id and existing is None:
            raise _not_found("Budget")
        if not budget_id and len(planning.budgets) >= MAX_PLAN_BUDGETS:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"You have {MAX_PLAN_BUDGETS} budgets. Remove one first.")
        amount, currency, essential = _check_plan(store, context, body, owned)
        item = Budget(
            id=budget_id or new_id("fin_bud"), name=" ".join(body.name.split()), kind=body.kind, period=body.period, currency=currency,
            amount=amount, categories=list(dict.fromkeys(body.categories)), subcategory_ids=list(dict.fromkeys(body.subcategory_ids)),
            starts_on=body.starts_on, ends_on=body.ends_on if body.period == BudgetPeriod.CUSTOM else None, fixed=body.fixed, essential=essential,
            allocation=body.allocation, goal_id=body.goal_id if body.kind in (BudgetKind.SAVINGS, BudgetKind.EMERGENCY, BudgetKind.SINKING_FUND) else None,
            debt_ids=list(dict.fromkeys(body.debt_ids)) if body.kind == BudgetKind.DEBT else [], alert_percent=body.alert_percent,
            reserve_in_safe_to_spend=body.reserve_in_safe_to_spend and body.kind == BudgetKind.SPENDING, active=body.active,
            notes=" ".join((body.notes or "").split()) or None, created_at=existing.created_at if existing else now, updated_at=now,
        )
        _record_terms(planning, item, existing, body, today, now)
        planning.budgets = [item if b.id == item.id else b for b in planning.budgets] if existing else [*planning.budgets, item]
        store.save_planning(context, planning)
    return item


def _plan_inputs(context: TenantContext, on: date | None) -> tuple[list[Budget], dict[str, Any]]:
    from . import budget_rollover as RO
    from . import obligations
    from .personal_store import open_personal_store

    links = obligations.load(context)  # its own short unit of work first
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        day = on or today
        planning = store.get_planning(context)
        budgets, rollovers = planning.budgets, planning.budget_rollovers
        accounts = store.list_accounts(context)
        balances = store.account_sums(context)
        start, end = RO.window(budgets, rollovers, day) or (day, day)  # each period and every rollover chain back to its start
        rows = store.transactions_between(context, start, end) if budgets else []
        bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
        goals = obligations.current_goals([g for g in store.list_items(context, "goal")], accounts, balances)
    with open_personal_store(context) as personal:
        remittances = personal.list(context, "remittance") if any(b.kind == BudgetKind.REMITTANCE for b in budgets) else []
    return budgets, {"rows": rows, "on": day, "today": today, "bills": bills, "bill_links": links.bill_links, "lending": links.lending_ids,
                     "goals": goals, "remittances": remittances, "debt_payments": links.debt_payments, "rollovers": rollovers}


@router.get("/budgets/plans", response_model=PlanReport)
def list_plan_budgets(on: date | None = Query(default=None), include_inactive: bool = Query(default=False),
                      context: TenantContext = Depends(tenant_context)) -> PlanReport:
    """Your own budgets for the period holding ``on`` (default today): spent, left, coming bills, the forecast to the
    period end and the risk, from linked transactions, each counted once."""
    _read(context)
    budgets, inputs = _plan_inputs(context, on)
    return plan_budget_report(budgets, include_inactive=include_inactive, **inputs)


@router.post("/budgets/plans", response_model=PlanBudgetLine, status_code=status.HTTP_201_CREATED)
def create_plan_budget(body: PlanBudgetInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> PlanBudgetLine:
    _manage(context)
    item = _save_plan(context, body, None)
    _audit(repository, context, "finance.budget.create", item.id, {"kind": item.kind.value, "period": item.period.value})
    return _line_for(context, item.id)


@router.patch("/budgets/plans/{budget_id}", response_model=PlanBudgetLine)
def update_plan_budget(budget_id: str, body: PlanBudgetInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> PlanBudgetLine:
    """Change a budget: send the whole budget (the form's fields)."""
    _manage(context)
    item = _save_plan(context, body, budget_id)
    _audit(repository, context, "finance.budget.update", item.id, {"kind": item.kind.value, "active": item.active})
    return _line_for(context, item.id)


@router.delete("/budgets/plans/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan_budget(budget_id: str, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_finance_store(context) as store:
        planning: Planning = store.get_planning(context)
        if not any(b.id == budget_id for b in planning.budgets):
            raise _not_found("Budget")
        planning.budgets = [b for b in planning.budgets if b.id != budget_id]
        planning.budget_rollovers = [t for t in planning.budget_rollovers if t.budget_id != budget_id]
        store.save_planning(context, planning)
    _audit(repository, context, "finance.budget.delete", budget_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _line_for(context: TenantContext, budget_id: str) -> PlanBudgetLine:
    budgets, inputs = _plan_inputs(context, None)
    report = plan_budget_report([b for b in budgets if b.id == budget_id], include_inactive=True, **inputs)
    return report.budgets[0]


class AllocationReport(BaseModel):
    month: str
    currencies: list[AllocationView]


@router.get("/budgets/allocation", response_model=AllocationReport)
def budget_allocation(context: TenantContext = Depends(tenant_context)) -> AllocationReport:
    """How this month's income (the salary profile's expected net, else the average of the last three full months) is
    split across your budgets: housing, food, transport, family, debts, cards, savings, remittances and other."""
    _read(context)
    from . import planning_service as S

    ctx = S.load(context)
    with open_finance_store(context) as store:
        budgets = store.get_planning(context).budgets
    currencies = sorted({b.currency for b in budgets if b.active} | ({ctx.salary_profile.currency} if ctx.salary_profile else set()))
    views = []
    for currency in currencies:
        income = S.income(ctx, currency)
        views.append(salary_allocation(budgets, currency, income.amount, income.source))
    return AllocationReport(month=ctx.today.strftime("%Y-%m"), currencies=views)


__all__ = ["router"]
