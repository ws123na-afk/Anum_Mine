"""Routes for bank and card reconciliation and connection health (docs/finance.md#reconciliation-sessions). Under
``/api/v1/finance``.

Reading needs ``finance:read``; every change ``finance:manage``. Sessions and their resolutions are the caller's own
(forced RLS, owner only). Nothing here happens on its own: the owner matches, attaches, marks pending, accepts a
reversal pair, approves a correcting entry (with the payload hash of the legs they were shown), completes and reopens.
There is no balancing entry: a difference nobody explained keeps the session unresolved. Audit records carry ids and
codes only, never amounts, descriptions or names.

``GET /reconciliations/exceptions`` (:func:`list_open_exceptions`) is the one list the unified review queue reads.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import reconciliation as R
from .api import Amount, _audit, _invalid, _manage, _money, _not_found, _read
from .categories import classify_text
from .csv_import import safe_cell
from .ledger import derived_legs
from .models import Account, AccountKind, Category, Transaction
from .reconciliation_store import MAX_SESSIONS, PeriodTaken, ReconItem, Reconciliation, open_recon_stores
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
RECONCILABLE = frozenset({AccountKind.BANK, AccountKind.CARD, AccountKind.CASH, AccountKind.LOAN})
MAX_EXCEPTIONS_PAGE = 200


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


# -- views --------------------------------------------------------------------------------------------------


class FiguresView(BaseModel):
    currency: str
    book_opening: Decimal
    deposits: Decimal
    payments: Decimal
    book_closing: Decimal
    statement_opening: Decimal | None
    statement_closing: Decimal
    difference: Decimal
    pending_total: Decimal
    adjusted_book_closing: Decimal
    unexplained: Decimal
    opening_difference: Decimal | None


class CardView(BaseModel):
    amount_due: Decimal | None
    minimum_due: Decimal | None
    due_on: date | None
    owed_in_ledger: Decimal
    due_difference: Decimal | None
    paid_since: Decimal
    minimum_covered: bool | None


class ExceptionView(BaseModel):
    id: str
    kind: R.ExceptionKind
    severity: R.Severity
    actions: list[str]
    posted_on: date | None = None
    amount: Decimal | None = None
    description: str | None = None
    transaction_id: str | None = None
    other_transaction_id: str | None = None
    statement_ref: str | None = None
    reason: str | None = None
    month: str | None = None
    age_days: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ResolutionView(BaseModel):
    id: str
    kind: str
    statement_ref: str | None
    transaction_id: str | None
    other_transaction_id: str | None
    note: str | None
    created_at: datetime


class HistoryView(BaseModel):
    action: Literal["completed", "reopened"]
    at: datetime
    by: str
    reason: str | None = None
    unexplained: str | None = None
    statement_closing: str | None = None
    book_closing: str | None = None


class ReconciliationSummary(BaseModel):
    id: str
    account_id: str
    account_name: str
    account_kind: str
    currency: str
    period_start: date
    period_end: date
    source: Literal["import", "typed"]
    import_id: str | None
    status: Literal["open", "completed"]
    state: Literal["balanced", "unresolved"]
    changed: bool
    can_complete: bool
    blockers: list[str]
    statement_closing: Decimal
    book_closing: Decimal
    unexplained: Decimal
    exceptions: int
    blocking: int
    completed_at: datetime | None
    completed_by: str | None
    version: int


class ReconciliationDetail(ReconciliationSummary):
    figures: FiguresView
    card: CardView | None
    counts: dict[str, int]
    items: list[ExceptionView]
    total: int
    limit: int
    offset: int
    resolutions: list[ResolutionView]
    history: list[HistoryView]
    snapshot: dict[str, Any]


class ReconciliationPage(BaseModel):
    items: list[ReconciliationSummary]
    total: int
    limit: int
    offset: int


class HealthView(BaseModel):
    account_id: str
    account_name: str
    kind: str
    currency: str
    last_statement_end: date | None
    last_import_at: datetime | None
    last_sms_on: date | None
    last_apple_pay_on: date | None
    missing_months: list[str]
    gaps: int
    reconciled_through: date | None
    open_reconciliation_id: str | None
    changed_reconciliation_id: str | None
    next_step: Literal["import_statement", "reconcile", "finish_reconciliation", "review_changes", "up_to_date", "none"]
    next_month: str | None
    bank_feed: Literal["not_connected"]


class HealthPage(BaseModel):
    accounts: list[HealthView]
    today: date


class QueueItem(BaseModel):
    """One open reconciliation exception, in the shape the unified review queue lists."""

    source: Literal["reconciliation"] = "reconciliation"
    reconciliation_id: str | None
    account_id: str
    account_name: str = ""
    currency: str = ""
    period_start: date | None
    period_end: date | None
    exception: ExceptionView


class QueuePage(BaseModel):
    items: list[QueueItem]
    total: int
    limit: int
    offset: int


# -- inputs -------------------------------------------------------------------------------------------------


class ReconciliationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    import_id: str | None = Field(default=None, max_length=80, description="Start from an imported statement: its period and balances")
    period_start: date | None = None
    period_end: date | None = None
    statement_opening: Amount | None = None
    statement_closing: Amount | None = Field(default=None, description="In the ledger's sign: a card or loan that owes money is negative")
    amount_due: Amount | None = None
    minimum_due: Amount | None = None
    due_on: date | None = None


class ReconciliationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    statement_opening: Amount | None = None
    statement_closing: Amount | None = None
    amount_due: Amount | None = None
    minimum_due: Amount | None = None
    due_on: date | None = None


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["match", "attach", "pending", "reversal"]
    statement_ref: str | None = Field(default=None, max_length=120)
    transaction_id: str | None = Field(default=None, max_length=80)
    other_transaction_id: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=200)


class CorrectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement_ref: str = Field(min_length=1, max_length=120)
    category: Category | None = None
    payload_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LegView(BaseModel):
    ledger_account: str
    amount: Decimal
    currency: str


class CorrectionPreview(BaseModel):
    statement_ref: str
    account_id: str
    posted_on: date
    amount: Decimal
    currency: str
    description: str
    category: Category
    legs: list[LegView]
    payload_hash: str


class CorrectionResult(BaseModel):
    transaction_id: str
    resolution_id: str


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)


class ReopenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=200)


# -- helpers ------------------------------------------------------------------------------------------------


def _session_or_404(recon: Any, context: TenantContext, reconciliation_id: str) -> Reconciliation:
    found = recon.get(context, reconciliation_id)
    if found is None:
        raise _not_found("Reconciliation")
    return found


def _evaluate(finance: Any, sms: Any, links: Any, recon: Any, context: TenantContext, rec: Reconciliation) -> tuple[Account, R.Facts, R.Outcome]:
    account = finance.get_account(context, rec.account_id)
    if account is None:
        raise _not_found("Account")
    items = recon.items(context, rec.id)
    today = today_in(settings_for(finance, context))
    facts = R.gather(finance, sms, links, context, account, rec, items, today)
    return account, facts, R.compute(rec, facts)


def _figures(outcome: R.Outcome) -> FiguresView:
    return FiguresView(**asdict(outcome.figures))


def _summary(rec: Reconciliation, account: Account, outcome: R.Outcome) -> ReconciliationSummary:
    return ReconciliationSummary(
        id=rec.id, account_id=rec.account_id, account_name=account.name, account_kind=account.kind.value, currency=rec.currency,
        period_start=rec.period_start, period_end=rec.period_end, source=rec.source, import_id=rec.import_id, status=rec.status,
        state=outcome.state, changed=outcome.changed, can_complete=outcome.can_complete, blockers=outcome.blockers,
        statement_closing=rec.statement_closing, book_closing=outcome.figures.book_closing, unexplained=outcome.figures.unexplained,
        exceptions=sum(1 for i in outcome.issues if i.severity != "info"), blocking=sum(1 for i in outcome.issues if i.severity == "blocking"),
        completed_at=rec.completed_at, completed_by=rec.completed_by, version=rec.version,
    )


def _issue(issue: R.Issue) -> ExceptionView:
    return ExceptionView(**{k: v for k, v in asdict(issue).items()})


def _history(rec: Reconciliation) -> list[HistoryView]:
    out = []
    for item in rec.history:
        try:
            out.append(HistoryView.model_validate(item))
        except ValueError:
            continue
    return out


def _detail(rec: Reconciliation, account: Account, outcome: R.Outcome, items: list[ReconItem], *, kind: str | None, severity: str | None,
            limit: int, offset: int) -> ReconciliationDetail:
    issues = [i for i in outcome.issues if (kind is None or i.kind == kind) and (severity is None or i.severity == severity)]
    card = CardView(**asdict(outcome.card)) if outcome.card is not None else None
    return ReconciliationDetail(
        **_summary(rec, account, outcome).model_dump(), figures=_figures(outcome), card=card, counts=outcome.counts,
        items=[_issue(i) for i in issues[offset:offset + limit]], total=len(issues), limit=limit, offset=offset,
        resolutions=[ResolutionView(id=i.id, kind=i.kind, statement_ref=i.statement_ref, transaction_id=i.transaction_id,
                                    other_transaction_id=i.other_transaction_id, note=i.note, created_at=i.created_at)
                     for i in items if i.status == "active"],
        history=_history(rec), snapshot=rec.snapshot,
    )


def _amount(value: Amount | None, currency: str, field: str) -> Decimal | None:
    return None if value is None else _money(value, currency, field=field)


def _locked(context: TenantContext) -> Any:
    from .sms_service import owner_lock

    return owner_lock(context)


# -- sessions -----------------------------------------------------------------------------------------------


@router.get("/reconciliations", response_model=ReconciliationPage)
def list_reconciliations(
    account_id: str | None = Query(default=None, max_length=80),
    state: Literal["open", "completed", "changed", "unresolved"] | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> ReconciliationPage:
    """The person's reconciliation sessions, newest period first, with each one's state worked out on the live ledger."""
    _read(context)
    with open_recon_stores(context) as (finance, sms, links, recon):
        sessions = recon.list(context, account_id=account_id)
        accounts = {a.id: a for a in finance.list_accounts(context)}
        cache: dict[str, Any] = {}
        out: list[ReconciliationSummary] = []
        today = today_in(settings_for(finance, context))
        messages = R.statement_message_states(sms, context)
        for rec in sessions:
            account = accounts.get(rec.account_id)
            if account is None:
                continue
            if rec.account_id not in cache:
                cache[rec.account_id] = (R.account_rows(finance, context, account.id), R.account_imports(finance, context, account.id))
            rows, imports = cache[rec.account_id]
            facts = R.gather(finance, sms, links, context, account, rec, recon.items(context, rec.id), today, all_rows=rows, imports=imports,
                             messages=messages)
            outcome = R.compute(rec, facts)
            summary = _summary(rec, account, outcome)
            if state == "open" and rec.status != "open":
                continue
            if state == "completed" and (rec.status != "completed" or summary.changed):
                continue
            if state == "changed" and not summary.changed:
                continue
            if state == "unresolved" and (rec.status != "open" or outcome.state != "unresolved"):
                continue
            out.append(summary)
    return ReconciliationPage(items=out[offset:offset + limit], total=len(out), limit=limit, offset=offset)


@router.post("/reconciliations", response_model=ReconciliationDetail, status_code=status.HTTP_201_CREATED)
def create_reconciliation(
    body: ReconciliationInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ReconciliationDetail:
    """Start reconciling one account for one statement period: from an imported statement (its period and balances) or
    from the balances the owner types. Nothing in the ledger changes."""
    _manage(context)
    with _locked(context), open_recon_stores(context) as (finance, sms, links, recon):
        sms.lock_owner(context)
        account = finance.get_account(context, body.account_id)
        if account is None:
            raise _not_found("Account")
        if account.kind not in RECONCILABLE:
            raise _invalid("Reconcile a bank, card, cash or loan account. Investment accounts are checked under Holdings.")
        if len(recon.list(context)) >= MAX_SESSIONS:
            raise _invalid(f"You have {MAX_SESSIONS} reconciliations. Delete an open one you no longer need.")
        start, end, opening, closing = body.period_start, body.period_end, body.statement_opening, body.statement_closing
        source: Literal["import", "typed"] = "typed"
        if body.import_id:
            record = finance.get_import(context, body.import_id)
            if record is None or record.account_id != account.id:
                raise _not_found("Import")
            source = "import"
            check = record.reconciliation or {}
            covered = R.coverage_of(record, {})
            if covered is None:
                rows = [t.posted_on for t in R.account_rows(finance, context, account.id) if t.import_id == record.id]
                covered = R.Coverage(record.id, min(rows), max(rows)) if rows else None
            start = start or (covered.start if covered else None)
            end = end or (covered.end if covered else None)
            opening = opening if opening is not None else check.get("opening")
            closing = closing if closing is not None else check.get("closing")
        if start is None or end is None:
            raise _invalid("Enter the statement period: its first and last day.")
        if end < start or (end - start).days > 400:
            raise _invalid("The period must end after it starts and cover at most 400 days.")
        today = today_in(settings_for(finance, context))
        if end > today:
            raise _invalid("The period cannot end after today: reconcile a statement the bank has issued.")
        if closing is None:
            raise _invalid("Enter the statement's closing balance, as printed on the statement.")
        card = account.kind == AccountKind.CARD
        if not card and any(v is not None for v in (body.amount_due, body.minimum_due, body.due_on)):
            raise _invalid("The amount due and minimum payment are for card statements only.")
        now = utc_now()
        rec = Reconciliation(
            id=new_id("fin_rec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, period_start=start, period_end=end, currency=account.currency,
            statement_opening=_amount(opening, account.currency, "Opening balance"), statement_closing=_money(closing, account.currency, field="Closing balance"),
            source=source, import_id=body.import_id, amount_due=_amount(body.amount_due, account.currency, "Amount due"),
            minimum_due=_amount(body.minimum_due, account.currency, "Minimum payment"), due_on=body.due_on,
            created_by=context.user_id, created_at=now, updated_at=now,
        )
        if (rec.amount_due is not None and rec.amount_due < 0) or (rec.minimum_due is not None and rec.minimum_due < 0):
            raise _invalid("Enter the amount due and the minimum payment as positive amounts.")
        try:
            recon.add(rec)
        except PeriodTaken:
            raise _conflict("period_taken", "This account already has a reconciliation for exactly this period. Open it instead.") from None
        account, _facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
        detail = _detail(rec, account, outcome, [], kind=None, severity=None, limit=50, offset=0)
    _audit(repository, context, "finance.reconciliation.create", rec.id, {"source": source})
    return detail


@router.get("/reconciliations/health", response_model=HealthPage)
def reconciliation_health(context: TenantContext = Depends(tenant_context)) -> HealthPage:
    """Connection health per account (Phase 5): the last statement period covered, the last SMS and Apple Pay record,
    the last import, months with activity no statement covers, and the plain next step. No live bank feed is claimed."""
    _read(context)
    return HealthPage(**_health(context))


def _health(context: TenantContext) -> dict[str, Any]:
    from . import statement_ledger as SL

    with open_recon_stores(context) as (finance, sms, links, recon):
        today = today_in(settings_for(finance, context))
        accounts = [a for a in finance.list_accounts(context) if not a.archived]
        sessions = recon.list(context)
        recorded = SL.records(finance, sms, context, today - timedelta(days=400), today, account_ids={a.id for a in accounts}) if accounts else []
        out = []
        for account in accounts:
            rows = R.account_rows(finance, context, account.id)
            imports = R.account_imports(finance, context, account.id)
            mine = [s for s in sessions if s.account_id == account.id]
            changed = {s.id for s in mine if s.status == "completed" and s.fingerprint and s.fingerprint != R.fingerprint(account, rows, s.period_end)}
            found = R.health_for(account, rows, imports, [r for r in recorded if r.account_id == account.id], mine, changed, today)
            out.append(HealthView(**asdict(found), account_name=account.name))
    return {"accounts": out, "today": today}


def list_open_exceptions(context: TenantContext, *, account_id: str | None = None, limit: int = 50, offset: int = 0,
                         sort: str | None = None, desc: bool = False) -> QueuePage:
    """Every open reconciliation exception that needs the owner (blocking and warnings), for the unified review queue:
    the exceptions of each account's open or changed session, then the months with activity no statement covers on
    accounts without an open session. Read only; resolving one happens on its session."""
    queue: list[QueueItem] = []
    with open_recon_stores(context) as (finance, sms, links, recon):
        accounts = {a.id: a for a in finance.list_accounts(context) if not a.archived and (account_id is None or a.id == account_id)}
        today = today_in(settings_for(finance, context))
        messages = R.statement_message_states(sms, context)
        with_session: set[str] = set()
        for rec in recon.list(context):
            account = accounts.get(rec.account_id)
            if account is None:
                continue
            rows = R.account_rows(finance, context, account.id)
            if rec.status == "completed" and not (rec.fingerprint and rec.fingerprint != R.fingerprint(account, rows, rec.period_end)):
                continue
            facts = R.gather(finance, sms, links, context, account, rec, recon.items(context, rec.id), today, all_rows=rows, messages=messages)
            outcome = R.compute(rec, facts)
            with_session.add(account.id)
            queue.extend(QueueItem(reconciliation_id=rec.id, account_id=account.id, account_name=account.name, currency=account.currency, period_start=rec.period_start, period_end=rec.period_end,
                                   exception=_issue(i)) for i in outcome.issues if i.severity != "info")
        this_month = date(today.year, today.month, 1)
        for account in accounts.values():
            if account.id in with_session or account.kind not in R.HEALTH_KINDS:
                continue
            rows = R.account_rows(finance, context, account.id)
            coverage = R.coverage_for(R.account_imports(finance, context, account.id), rows)
            for month in R.missing_months(sorted({t.posted_on for t in rows}), coverage, before=this_month):
                queue.append(QueueItem(reconciliation_id=None, account_id=account.id, account_name=account.name, currency=account.currency, period_start=None, period_end=None,
                                       exception=ExceptionView(id=f"missing_period:{month}", kind="missing_period", severity="warning", actions=["import"],
                                                               month=month)))
    if sort is not None:
        queue = sort_queue(queue, sort, desc)
    return QueuePage(items=queue[offset:offset + limit], total=len(queue), limit=limit, offset=offset)


# The exceptions list's sortable columns (the web table). Severity puts what blocks first; the order is stable, so
# rows of equal rank keep the queue's own order (each session's items, then the missing months).
QUEUE_SORTS: frozenset[str] = frozenset({"severity", "account", "period", "kind", "posted", "amount"})
_SEVERITY_RANK = {"blocking": 0, "warning": 1, "info": 2}


def sort_queue(queue: list[QueueItem], sort: str, desc: bool) -> list[QueueItem]:
    if sort not in QUEUE_SORTS:
        raise _invalid(f"Sort the exceptions by one of: {', '.join(sorted(QUEUE_SORTS))}.")

    def key(item: QueueItem) -> tuple[Any, ...]:
        e = item.exception
        if sort == "severity":
            return (_SEVERITY_RANK.get(e.severity, 9),)
        if sort == "account":
            return (item.account_name.casefold(), item.account_id)
        if sort == "period":
            return (item.period_end or date.min, e.month or "")
        if sort == "kind":
            return (e.kind,)
        if sort == "posted":
            return (e.posted_on or (date.fromisoformat(f"{e.month}-01") if e.month else date.min),)
        return (e.amount if e.amount is not None else Decimal("-Infinity"),)

    return sorted(queue, key=key, reverse=desc)


@router.get("/reconciliations/exceptions", response_model=QueuePage)
def reconciliation_exceptions(
    account_id: str | None = Query(default=None, max_length=80),
    sort: str | None = Query(default=None, max_length=20),
    desc: bool = False,
    limit: int = Query(default=50, ge=1, le=MAX_EXCEPTIONS_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> QueuePage:
    """Open reconciliation exceptions across accounts (for the unified review queue and the Reconciliation page's
    exceptions table). ``sort`` orders by severity, account, period, kind, posted or amount; without it the queue's
    own order (each session's items, then the missing months)."""
    _read(context)
    return list_open_exceptions(context, account_id=account_id, limit=limit, offset=offset, sort=sort, desc=desc)


EXPORT_ROWS = 20_000
QUEUE_CSV_HEAD = ["account", "period_start", "period_end", "severity", "kind", "posted_on", "month", "amount", "currency", "description", "reason", "actions", "reconciliation_id"]


@router.get("/reconciliations/exceptions/export")
def export_reconciliation_exceptions(
    account_id: str | None = Query(default=None, max_length=80),
    sort: str | None = Query(default=None, max_length=20),
    desc: bool = False,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """The open exceptions as a spreadsheet file, in the list's order, up to EXPORT_ROWS rows. Formula-safe cells;
    descriptions are the statement's own words, no account numbers."""
    _read(context)
    page = list_open_exceptions(context, account_id=account_id, limit=EXPORT_ROWS, offset=0, sort=sort, desc=desc)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(QUEUE_CSV_HEAD)
    for item in page.items:
        e = item.exception
        cells = [item.account_name, item.period_start, item.period_end, e.severity, e.kind, e.posted_on, e.month, e.amount, item.currency,
                 e.description, e.reason, " ".join(e.actions), item.reconciliation_id]
        writer.writerow([safe_cell(str(cell)) if cell is not None else "" for cell in cells])
    _audit(repository, context, "finance.reconciliation.exceptions_export", context.workspace_id, {"rows": len(page.items)})
    return Response(content=out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="anum-reconciliation-exceptions.csv"', "Cache-Control": "no-store"})


@router.get("/reconciliations/{reconciliation_id}", response_model=ReconciliationDetail)
def get_reconciliation(
    reconciliation_id: str,
    kind: R.ExceptionKind | None = None,
    severity: R.Severity | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_EXCEPTIONS_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> ReconciliationDetail:
    """One session: the equation, the card check, the exceptions (filtered and paged), the owner's resolutions and the
    history of completions and reopenings."""
    _read(context)
    with open_recon_stores(context) as (finance, sms, links, recon):
        rec = _session_or_404(recon, context, reconciliation_id)
        account, _facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
        items = recon.items(context, rec.id)
    return _detail(rec, account, outcome, items, kind=kind, severity=severity, limit=limit, offset=offset)


@router.patch("/reconciliations/{reconciliation_id}", response_model=ReconciliationDetail)
def update_reconciliation(
    reconciliation_id: str,
    body: ReconciliationPatch,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ReconciliationDetail:
    """Fix the statement's figures as printed while the session is open."""
    _manage(context)
    changes = body.model_dump(exclude_unset=True)
    with _locked(context), open_recon_stores(context) as (finance, sms, links, recon):
        sms.lock_owner(context)
        rec = _session_or_404(recon, context, reconciliation_id)
        if rec.status != "open":
            raise _conflict("completed", "This reconciliation is completed. Reopen it with a reason to change it.")
        account = finance.get_account(context, rec.account_id)
        if account is None:
            raise _not_found("Account")
        if "statement_closing" in changes:
            if body.statement_closing is None:
                raise _invalid("Enter the statement's closing balance.")
            rec.statement_closing = _money(body.statement_closing, rec.currency, field="Closing balance")
        if "statement_opening" in changes:
            rec.statement_opening = _amount(body.statement_opening, rec.currency, "Opening balance")
        card_fields = {"amount_due", "minimum_due", "due_on"} & set(changes)
        if card_fields and account.kind != AccountKind.CARD:
            raise _invalid("The amount due and minimum payment are for card statements only.")
        if "amount_due" in changes:
            rec.amount_due = _amount(body.amount_due, rec.currency, "Amount due")
        if "minimum_due" in changes:
            rec.minimum_due = _amount(body.minimum_due, rec.currency, "Minimum payment")
        if "due_on" in changes:
            rec.due_on = body.due_on
        rec.updated_at = utc_now()
        if not recon.save(rec, expected_version=body.version):
            raise _conflict("stale", "This reconciliation changed in another tab. Reload it and try again.")
        account, _facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
        detail = _detail(rec, account, outcome, recon.items(context, rec.id), kind=None, severity=None, limit=50, offset=0)
    _audit(repository, context, "finance.reconciliation.update", rec.id)
    return detail


@router.delete("/reconciliations/{reconciliation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reconciliation(
    reconciliation_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Delete a session that was never completed. A completed one keeps its history: reopen it instead."""
    _manage(context)
    with _locked(context), open_recon_stores(context) as (_finance, sms, _links, recon):
        sms.lock_owner(context)
        rec = _session_or_404(recon, context, reconciliation_id)
        if rec.status == "completed" or rec.history:
            raise _conflict("has_history", "A reconciliation that was completed keeps its history. Reopen it to work on it again.")
        recon.delete(context, rec.id)
    _audit(repository, context, "finance.reconciliation.delete", reconciliation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- resolutions --------------------------------------------------------------------------------------------


def _open_or_conflict(rec: Reconciliation) -> None:
    if rec.status != "open":
        raise _conflict("completed", "This reconciliation is completed. Reopen it with a reason to change it.")


@router.post("/reconciliations/{reconciliation_id}/resolutions", response_model=ResolutionView, status_code=status.HTTP_201_CREATED)
def resolve_exception(
    reconciliation_id: str,
    body: ResolutionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ResolutionView:
    """The owner settles one exception: ``match`` a statement row to a ledger record (equal amounts), ``attach`` a ledger
    record as confirmed by this statement, mark one ``pending`` (not on this statement: it leaves the adjusted book
    balance) or accept a debit and an equal credit as a ``reversal`` pair. A row already resolved in this session
    cannot be resolved twice (409), also when two requests arrive at the same moment."""
    _manage(context)
    with _locked(context), open_recon_stores(context) as (finance, sms, links, recon):
        sms.lock_owner(context)
        rec = _session_or_404(recon, context, reconciliation_id)
        _open_or_conflict(rec)
        account, facts, _outcome = _evaluate(finance, sms, links, recon, context, rec)
        rows = {r.transaction_id: r for r in facts.rows}
        in_period = {k: r for k, r in rows.items() if rec.period_start <= r.posted_on <= rec.period_end}
        statement = {s.ref: s for s in facts.statement_rows}
        if body.kind == "match":
            if not body.statement_ref or not body.transaction_id:
                raise _invalid("Choose the statement row and the ledger record it is.")
            row = statement.get(body.statement_ref)
            if row is None or row.disposition in ("imported", "attached_to_existing"):
                raise _not_found("Statement row")
            txn = rows.get(body.transaction_id)
            if txn is None:
                raise _not_found("Ledger record")
            if row.amount is not None and row.amount != txn.amount:
                raise _invalid("The statement row and the ledger record have different amounts. Choose the record with the same amount, "
                               "or post a correcting entry.")
        elif body.kind in ("attach", "pending"):
            if not body.transaction_id or body.statement_ref or body.other_transaction_id:
                raise _invalid("Choose one ledger record of this period.")
            if body.transaction_id not in in_period:
                raise _not_found("Ledger record")
        else:
            if not body.transaction_id or not body.other_transaction_id or body.transaction_id == body.other_transaction_id:
                raise _invalid("Choose the payment and the credit that reverses it.")
            first, second = rows.get(body.transaction_id), rows.get(body.other_transaction_id)
            if first is None or second is None:
                raise _not_found("Ledger record")
            if first.amount != -second.amount:
                raise _invalid("A reversal pair is a payment and a credit of the same amount.")
            if first.amount > 0:
                body.transaction_id, body.other_transaction_id = body.other_transaction_id, body.transaction_id
        item = ReconItem(id=new_id("fin_rci"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                         reconciliation_id=rec.id, kind=body.kind, statement_ref=body.statement_ref if body.kind == "match" else None,
                         transaction_id=body.transaction_id, other_transaction_id=body.other_transaction_id if body.kind == "reversal" else None,
                         note=(body.note or "").strip() or None, created_by=context.user_id, created_at=utc_now())
        if not recon.add_item(item):
            raise _conflict("already_resolved", "This row is already resolved in this reconciliation. Undo that first.")
        _ = account
    _audit(repository, context, "finance.reconciliation.resolve", rec.id, {"kind": body.kind, "resolution": item.id})
    return ResolutionView(id=item.id, kind=item.kind, statement_ref=item.statement_ref, transaction_id=item.transaction_id,
                          other_transaction_id=item.other_transaction_id, note=item.note, created_at=item.created_at)


@router.delete("/reconciliations/{reconciliation_id}/resolutions/{resolution_id}", status_code=status.HTTP_204_NO_CONTENT)
def undo_resolution(
    reconciliation_id: str,
    resolution_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Undo a match, attach, pending mark or reversal pair. A posted correcting entry is a transaction: change or delete
    it under Transactions, the reconciliation then shows the statement row again."""
    _manage(context)
    with _locked(context), open_recon_stores(context) as (_finance, sms, _links, recon):
        sms.lock_owner(context)
        rec = _session_or_404(recon, context, reconciliation_id)
        _open_or_conflict(rec)
        found = next((i for i in recon.items(context, rec.id) if i.id == resolution_id and i.status == "active"), None)
        if found is None:
            raise _not_found("Resolution")
        if found.kind == "correction":
            raise _conflict("posted", "A correcting entry is posted. Change or delete the transaction under Transactions.")
        recon.undo_item(context, found.id, by=context.user_id, at=utc_now())
    _audit(repository, context, "finance.reconciliation.undo", reconciliation_id, {"resolution": resolution_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _correction(finance: Any, sms: Any, links: Any, recon: Any, context: TenantContext, rec: Reconciliation, body: CorrectionInput) -> CorrectionPreview:
    account, facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
    issue = next((i for i in outcome.issues if i.statement_ref == body.statement_ref and i.kind == "not_in_ledger"), None)
    if issue is None or "correct" not in issue.actions or issue.amount is None or issue.posted_on is None:
        raise _invalid("Only a statement row the ledger does not hold, with its date and amount, can be posted as a correcting entry.")
    _ = facts
    description = (issue.description or "Statement row").strip()[:500] or "Statement row"
    found = classify_text(description, issue.amount, settings_for(finance, context).category_rules, account.kind)
    category = body.category or found.category
    draft = Transaction(id="draft", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                        account_id=account.id, posted_on=issue.posted_on, amount=issue.amount, currency=account.currency, description=description,
                        category=category, category_source=found.source, dedupe_hash="draft", created_at=utc_now(), updated_at=utc_now())
    legs = [LegView(ledger_account=leg.ledger_account, amount=leg.amount, currency=leg.currency) for leg in derived_legs(draft, account)]
    payload = {"reconciliation_id": rec.id, "statement_ref": body.statement_ref, "account_id": account.id, "posted_on": issue.posted_on.isoformat(),
               "amount": str(issue.amount), "currency": account.currency, "description": description, "category": category.value,
               "legs": [[leg.ledger_account, str(leg.amount), leg.currency] for leg in legs]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CorrectionPreview(statement_ref=body.statement_ref, account_id=account.id, posted_on=issue.posted_on, amount=issue.amount,
                             currency=account.currency, description=description, category=category, legs=legs, payload_hash=digest)


@router.post("/reconciliations/{reconciliation_id}/corrections/preview", response_model=CorrectionPreview)
def preview_correction(reconciliation_id: str, body: CorrectionInput, context: TenantContext = Depends(tenant_context)) -> CorrectionPreview:
    """The correcting entry for a statement row the ledger does not hold, with its journal legs and the payload hash the
    owner approves. Nothing is posted. The amount is always the statement row's own: never a balancing figure."""
    _manage(context)
    with open_recon_stores(context) as (finance, sms, links, recon):
        rec = _session_or_404(recon, context, reconciliation_id)
        _open_or_conflict(rec)
        return _correction(finance, sms, links, recon, context, rec, body)


@router.post("/reconciliations/{reconciliation_id}/corrections", response_model=CorrectionResult, status_code=status.HTTP_201_CREATED)
def post_correction(
    reconciliation_id: str,
    body: CorrectionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CorrectionResult:
    """Post the correcting entry the owner approved: the payload hash must be the one the preview showed (409
    ``payload_changed`` otherwise). Recorded through the hand-entry path (one transaction, one balanced journal entry)."""
    _manage(context)
    if body.payload_hash is None:
        raise _invalid("Approve the correcting entry you were shown: its payload hash is missing.")
    from .api import TransactionInput, record_manual_transaction

    with open_recon_stores(context) as (finance, sms, links, recon):
        rec = _session_or_404(recon, context, reconciliation_id)
        _open_or_conflict(rec)
        preview = _correction(finance, sms, links, recon, context, rec, body)
    if preview.payload_hash != body.payload_hash:
        raise _conflict("payload_changed", "The correcting entry changed since you saw it. Review it again before you post it.")
    # A deterministic id makes a retried approval post the entry once.
    txn_id = "fin_txn_rc_" + hashlib.sha256(f"{rec.id}|{body.statement_ref}".encode()).hexdigest()[:24]
    item, _decision, _canonical = record_manual_transaction(context, TransactionInput(
        account_id=preview.account_id, posted_on=preview.posted_on, amount=preview.amount, description=preview.description,
        category=preview.category, notes="Correcting entry from a reconciliation"), transaction_id=txn_id)
    resolution = ReconItem(id=new_id("fin_rci"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                           reconciliation_id=rec.id, kind="correction", statement_ref=body.statement_ref, transaction_id=item.id,
                           payload_hash=body.payload_hash, created_by=context.user_id, created_at=utc_now())
    with _locked(context), open_recon_stores(context) as (_finance, sms, _links, recon):
        sms.lock_owner(context)
        if not recon.add_item(resolution):
            raise _conflict("already_resolved", "This statement row is already resolved in this reconciliation.")
    _audit(repository, context, "finance.reconciliation.correct", rec.id, {"transaction": item.id, "resolution": resolution.id})
    return CorrectionResult(transaction_id=item.id, resolution_id=resolution.id)


# -- completing and reopening ------------------------------------------------------------------------------


@router.post("/reconciliations/{reconciliation_id}/complete", response_model=ReconciliationDetail)
def complete_reconciliation(
    reconciliation_id: str,
    body: VersionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ReconciliationDetail:
    """Complete a balanced session: records who, when, the figures and what was matched. Refused (409 ``unresolved``)
    while a difference is unexplained or statement rows are not in the ledger: a balance is never forced."""
    _manage(context)
    with _locked(context), open_recon_stores(context) as (finance, sms, links, recon):
        sms.lock_owner(context)
        rec = _session_or_404(recon, context, reconciliation_id)
        _open_or_conflict(rec)
        account, facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
        if not outcome.can_complete:
            raise _conflict("unresolved", "This reconciliation does not balance yet: " + ", ".join(outcome.blockers) + ".")
        now = utc_now()
        items = [i for i in recon.items(context, rec.id) if i.status == "active"]
        figures = _figures(outcome).model_dump(mode="json")
        confirmed = sorted(r.transaction_id for r in facts.rows if rec.period_start <= r.posted_on <= rec.period_end and r.confirmed)
        rec.snapshot = {"figures": figures, "counts": outcome.counts, "confirmed": confirmed[:5_000],
                        "resolutions": [{"kind": i.kind, "statement_ref": i.statement_ref, "transaction_id": i.transaction_id,
                                         "other_transaction_id": i.other_transaction_id} for i in items][:2_000]}
        rec.status, rec.completed_at, rec.completed_by, rec.fingerprint = "completed", now, context.user_id, facts.fingerprint
        rec.history = [*rec.history, {"action": "completed", "at": now.isoformat(), "by": context.user_id, "unexplained": figures["unexplained"],
                                      "statement_closing": figures["statement_closing"], "book_closing": figures["book_closing"]}][-100:]
        rec.updated_at = now
        if not recon.save(rec, expected_version=body.version):
            raise _conflict("stale", "This reconciliation changed in another tab. Reload it and try again.")
        account, _facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
        detail = _detail(rec, account, outcome, items, kind=None, severity=None, limit=50, offset=0)
    _audit(repository, context, "finance.reconciliation.complete", rec.id)
    return detail


@router.post("/reconciliations/{reconciliation_id}/reopen", response_model=ReconciliationDetail)
def reopen_reconciliation(
    reconciliation_id: str,
    body: ReopenInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ReconciliationDetail:
    """Reopen a completed session with a reason (kept in its history with the figures it was completed with)."""
    _manage(context)
    reason = " ".join(body.reason.split())
    if len(reason) < 3:
        raise _invalid("Say why you reopen it.")
    with _locked(context), open_recon_stores(context) as (finance, sms, links, recon):
        sms.lock_owner(context)
        rec = _session_or_404(recon, context, reconciliation_id)
        if rec.status != "completed":
            raise _conflict("not_completed", "Only a completed reconciliation can be reopened.")
        now = utc_now()
        rec.history = [*rec.history, {"action": "reopened", "at": now.isoformat(), "by": context.user_id, "reason": reason}][-100:]
        rec.status, rec.completed_at, rec.completed_by, rec.fingerprint, rec.snapshot = "open", None, None, None, {}
        rec.updated_at = now
        if not recon.save(rec, expected_version=body.version):
            raise _conflict("stale", "This reconciliation changed in another tab. Reload it and try again.")
        account, _facts, outcome = _evaluate(finance, sms, links, recon, context, rec)
        detail = _detail(rec, account, outcome, recon.items(context, rec.id), kind=None, severity=None, limit=50, offset=0)
    _audit(repository, context, "finance.reconciliation.reopen", rec.id)
    return detail


# -- for the register and the account overview -------------------------------------------------------------


def completed_coverage(finance: Any, recon: Any, context: TenantContext, accounts: dict[str, Account]) -> dict[str, list[tuple[Reconciliation, set[str]]]]:
    """Per account, its completed sessions whose ledger has not changed since, with the rows marked pending in each:
    the source of the register's ``reconciled`` and the account card's last reconciliation."""
    out: dict[str, list[tuple[Reconciliation, set[str]]]] = defaultdict(list)
    sessions = [s for s in recon.list(context) if s.status == "completed" and s.account_id in accounts]
    rows_cache: dict[str, list[Transaction]] = {}
    for rec in sessions:
        if rec.account_id not in rows_cache:
            rows_cache[rec.account_id] = R.account_rows(finance, context, rec.account_id)
        if rec.fingerprint != R.fingerprint(accounts[rec.account_id], rows_cache[rec.account_id], rec.period_end):
            continue
        pending = {i.transaction_id for i in recon.items(context, rec.id) if i.status == "active" and i.kind == "pending" and i.transaction_id}
        out[rec.account_id].append((rec, pending))
    return out


def account_sessions(recon: Any, context: TenantContext) -> dict[str, list[Reconciliation]]:
    out: dict[str, list[Reconciliation]] = defaultdict(list)
    for rec in recon.list(context):
        out[rec.account_id].append(rec)
    return out


__all__ = ["account_sessions", "completed_coverage", "list_open_exceptions", "router"]
