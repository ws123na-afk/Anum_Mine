"""HTTP routes of the business journal under ``/api/v1/business/books`` (docs/business-books.md#the-journal).

Who can do what:

* ``business:read`` (owners and members): the chart, the journal, the general ledger, the statements,
  the posting status, the period close status and the policy notes.
* ``business:write``: Post now (run the posting run).
* Owners and accountants (members an owner named accountant, ``/accountants``): add and edit accounts,
  manual journal entries and their reversal, closing periods. Adjustments and approving draft bills
  follow the same rule (``api_books``).
* Owners only (``business:manage``): reopening a closed period (with a reason) and naming accountants.

Refusals answer ``{"detail": "<plain words>", "code": "<code>"}``. Audit rows hold ids, codes, dates and
before/after summaries of figures, never a description, a note or a reason. Every route is a plain ``def``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, lookup_membership, repository_context, tenant_context
from ..finance.money import MoneyError, to_money
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import books_ledger as L
from . import books_report as R
from . import books_service as S
from .api_books import MAX_SPAN_DAYS, _audit, _csv, _manage, _page, _read, _stale, _today, _write, refusals
from .books_chart import ChartError, accounts, category_allowed, check_code, ensure_chart
from .books_models import SUBTYPES, Accountant, LedgerAccount
from .books_store import MAX_PAGE, DuplicateError, open_books_store

router = APIRouter(prefix="/api/v1/business/books", tags=["business"])

ACCOUNTING_DENIED = "Only an owner or an accountant of this business can do this. An owner can name accountants in Books, Settings."
REOPEN_DENIED = "Only an owner can reopen a closed period."


def is_accountant(context: TenantContext) -> bool:
    if not has_permission(context, Permission.BUSINESS_WRITE):
        return False
    with open_books_store(context) as store:
        return store.first("accountants", user_id=context.user_id) is not None


def can_account(context: TenantContext) -> bool:
    """Owners, and members an owner named accountant."""
    return has_permission(context, Permission.BUSINESS_MANAGE) or is_accountant(context)


def require_accounting(context: TenantContext) -> None:
    _read(context)
    if not can_account(context):
        raise S.BooksError(403, ACCOUNTING_DENIED, "forbidden")


def _ledger(route: Any) -> Any:
    """``LedgerError`` and ``ChartError`` as the books' plain refusals."""
    import functools

    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return route(*args, **kwargs)
        except L.LedgerError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})
        except ChartError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.message, "code": exc.code})

    return wrapper


def _period(context: TenantContext, start: date | None, end: date | None) -> tuple[date, date]:
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
    first, last = R.default_period(settings, _today())
    start, end = start or first, end or last
    if start > end:
        raise S.BooksError(422, "The period starts after it ends.", "invalid_period")
    if (end - start).days > MAX_SPAN_DAYS:
        raise S.BooksError(422, "A report covers at most three years.", "period_too_long")
    return start, end


# -- chart of accounts ----------------------------------------------------------------------------------------------


class AccountInput(BaseModel):
    code: str = Field(min_length=4, max_length=6, pattern=r"^[0-9]+$")
    name: str = Field(min_length=2, max_length=120)
    subtype: str = Field(max_length=30)
    category: str | None = Field(default=None, max_length=40)


class AccountUpdate(BaseModel):
    code: str = Field(min_length=4, max_length=6, pattern=r"^[0-9]+$")
    name: str = Field(min_length=2, max_length=120)
    category: str | None = Field(default=None, max_length=40)
    archived: bool = False
    version: int


def _category(subtype: str, value: str | None) -> str | None:
    category = S.category_of(value, required=False)
    if category and not category_allowed(subtype):
        raise S.BooksError(422, "Only a cost of sales or an operating expense account takes a category.", "category_not_allowed")
    return category


@router.get("/ledger/accounts")
@refusals
@_ledger
def list_accounts(include_archived: bool = False, context: TenantContext = Depends(tenant_context)) -> Any:
    """The chart, by code. Empty until the first posting run made the standard accounts (Post now)."""
    _read(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        rows = [row for row in accounts(store) if include_archived or not row.archived]
    return {"items": [L.account_view(row) for row in rows], "subtypes": {key: value[0].value for key, value in SUBTYPES.items()},
            "can_edit": can_account(context)}


@router.post("/ledger/accounts", status_code=status.HTTP_201_CREATED)
@refusals
@_ledger
def create_account(payload: AccountInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    require_accounting(context)
    now = utc_now()
    code = check_code(payload.code, payload.subtype)
    name = S.clean(payload.name, 120)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        ensure_chart(store, context, now, context.user_id)
        item = LedgerAccount(id=new_id("bacc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, code=code, name=name,
                             subtype=payload.subtype, category=_category(payload.subtype, payload.category), created_by=context.user_id, updated_at=now)
        try:
            store.add("ledger_accounts", item)
        except DuplicateError as exc:
            raise S.BooksError(409, "An account with this code (or bound to this category) already exists.", "duplicate") from exc
    _audit(repository, context, "account_created", item.id, {"code": item.code, "subtype": item.subtype})
    return L.account_view(item)


@router.patch("/ledger/accounts/{account_id}")
@refusals
@_ledger
def update_account(account_id: str, payload: AccountUpdate, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    require_accounting(context)
    with open_books_store(context) as store:
        current = store.get("ledger_accounts", account_id, for_update=True)
        if current is None:
            raise S.BooksError(404, "Account not found.", "account_not_found")
        if current.version != payload.version:
            raise _stale()
        if payload.archived and current.system_key:
            raise S.BooksError(409, "The posting rules use this account: it can be renamed or renumbered, never archived.", "system_account")
        item = current.model_copy(update={"code": check_code(payload.code, current.subtype), "name": S.clean(payload.name, 120),
                                          "category": _category(current.subtype, payload.category), "archived": payload.archived,
                                          "version": current.version + 1, "updated_at": utc_now()})
        try:
            saved = store.save("ledger_accounts", item, expected_version=current.version)
        except DuplicateError as exc:
            raise S.BooksError(409, "An account with this code (or bound to this category) already exists.", "duplicate") from exc
        if not saved:
            raise _stale()
    before = {"code": current.code, "category": current.category or "", "archived": current.archived}
    after = {"code": item.code, "category": item.category or "", "archived": item.archived}
    _audit(repository, context, "account_updated", account_id, {"before": before, "after": after})
    if (current.category, current.archived) != (item.category, item.archived):
        L.post_quietly(context)  # a category now posts to another account: move its entries
    return L.account_view(item)


# -- posting -----------------------------------------------------------------------------------------------------------


@router.get("/ledger/status")
@refusals
@_ledger
def ledger_status(context: TenantContext = Depends(tenant_context)) -> Any:
    """What is not in the journal yet: posting issues with their reasons, and changes a posting run would make."""
    _read(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        locked = L.locked_through(store)
    return {**L.status(context, _today()), "closed_through": locked.isoformat() if locked else None, "can_post": has_permission(context, Permission.BUSINESS_WRITE),
            "can_account": can_account(context), "can_reopen": has_permission(context, Permission.BUSINESS_MANAGE)}


@router.post("/ledger/post")
@refusals
@_ledger
def run_posting(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Post now: bring the journal in line with the records (idempotent; the daily job does the same)."""
    _write(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
    result = L.post(context, actor=context.user_id)
    _audit(repository, context, "ledger_posted", context.workspace_id, {"posted": result.posted, "reversed": result.reversed, "issues": result.issues})
    return {"posted": result.posted, "reversed": result.reversed, "late": result.late, "issues": result.issues, "issue_rows": result.issue_rows,
            "scheduled": result.scheduled, "unchanged": result.unchanged, "truncated": result.truncated}


@router.get("/ledger/issues")
@refusals
def list_issues(limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                context: TenantContext = Depends(tenant_context)) -> Any:
    """Events the posting run could not post, with the reason (for example no exchange rate for that currency and day)."""
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("posting_issues", order_by="event_date", limit=limit, offset=offset)
    return _page([{"id": row.id, "event_key": row.event_key, "source_kind": row.source_kind, "source_id": row.source_id,
                   "event_date": row.event_date.isoformat(), "reason": row.reason, "currency": row.currency,
                   "rate_date": row.rate_date.isoformat() if row.rate_date else None, "description": row.description} for row in rows], total, limit, offset)


# -- the journal ---------------------------------------------------------------------------------------------------------


SOURCE_KINDS = ("invoice", "credit_note", "invoice_payment", "invoice_refund", "bill", "bill_payment", "allocation", "adjustment", "intercompany", "manual", "closing",
                "asset", "loan", "loan_repayment", "revaluation")


@router.get("/ledger/journal")
@refusals
@_ledger
def list_journal(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                 source_kind: str | None = Query(default=None, pattern=r"^[a-z_]{3,20}$"), limit: int = Query(default=25, ge=1, le=100),
                 offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    if source_kind is not None and source_kind not in SOURCE_KINDS:
        raise S.BooksError(422, "Unknown source.", "invalid_filter", allowed=list(SOURCE_KINDS))
    page = L.journal(context, start=start, end=end, source_kind=source_kind, limit=limit, offset=offset)
    if has_permission(context, Permission.DOCUMENT_READ):
        # The drill-down shows each entry's supporting documents (docs/business-books.md#supporting-documents-and-evidence).
        from .books_evidence import with_evidence

        page["items"] = with_evidence(context, page["items"])
    return {**page, "can_account": can_account(context)}


class ManualLine(BaseModel):
    account_id: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    debit: str = Field(default="0", max_length=30)
    credit: str = Field(default="0", max_length=30)


class ManualInput(BaseModel):
    entry_date: date
    description: str = Field(min_length=3, max_length=300)
    lines: list[ManualLine] = Field(min_length=2, max_length=50)


class ReasonInput(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


def _money(value: str, currency: str, line: int) -> Decimal:
    try:
        amount = to_money(value or "0", currency)
    except MoneyError as exc:
        raise S.BooksError(422, f"Line {line}: {exc}", "invalid_amount", line=line) from None
    if amount < 0:
        raise S.BooksError(422, f"Line {line}: amounts are positive; use the other column.", "invalid_amount", line=line)
    return amount


@router.post("/ledger/journal", status_code=status.HTTP_201_CREATED)
@refusals
@_ledger
def create_manual(payload: ManualInput, response: Response, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    """A manual journal entry in the base currency (opening balances, a correction): owners and accountants."""
    require_accounting(context)
    key = S.idem_hash(idempotency_key, "manual_entry")
    now = utc_now()
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        if key and (earlier := store.first("journal_entries", event_key=f"manual:{key}")) is not None:
            response.status_code = status.HTTP_200_OK
            return {**L.entry_view(earlier), "replayed": True}
        lines = [{"account_id": line.account_id, "debit": _money(line.debit, settings.base_currency, index + 1),
                  "credit": _money(line.credit, settings.base_currency, index + 1)} for index, line in enumerate(payload.lines)]
        description = S.clean(payload.description, 300)
        if len(description) < 3:
            raise S.BooksError(422, "Say what the entry is for (it stays in the journal).", "invalid_description")
        entry = L.manual_entry(store, context, entry_date=payload.entry_date, description=description, lines=lines, today=_today(), now=now,
                               event_key=f"manual:{key}" if key else None)
        total = sum((line["debit"] for line in lines), Decimal(0))
    _audit(repository, context, "journal_entry_created", entry.id, {"entry_date": entry.entry_date.isoformat(), "lines": len(lines),
                                                                     "total": str(total), "currency": settings.base_currency})
    return L.entry_view(entry)


@router.post("/ledger/journal/{entry_id}/reverse")
@refusals
@_ledger
def reverse_entry(entry_id: str, payload: ReasonInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    """Reverse a manual entry (its mirror on its own day, or the first open day). Entries from a source document follow it."""
    require_accounting(context)
    with open_books_store(context) as store:
        reversal = L.reverse_manual(store, context, entry_id, reason=S.clean(payload.reason, 300), now=utc_now())
    _audit(repository, context, "journal_entry_reversed", entry_id, {"reversal_id": reversal.id, "entry_date": reversal.entry_date.isoformat()})
    return L.entry_view(reversal)


@router.get("/ledger/accounts/{account_id}/lines")
@refusals
@_ledger
def account_lines(account_id: str, start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                  limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=5000),
                  context: TenantContext = Depends(tenant_context)) -> Any:
    """The general ledger of one account: opening balance, lines with the running balance and their source, closing balance."""
    _read(context)
    first, last = _period(context, start, end)
    return L.general_ledger(context, account_id, first, last, limit=limit, offset=offset)


# -- statements -----------------------------------------------------------------------------------------------------------


@router.get("/ledger/statements")
@refusals
@_ledger
def statements(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"), compare: bool = False,
               context: TenantContext = Depends(tenant_context)) -> Any:
    """Trial balance, balance sheet, P&L with its reconciliation to the derived P&L, changes in equity and the direct cash flow for
    one period, from the journal; ``compare`` adds the previous period and the same period last year."""
    _read(context)
    first, last = _period(context, start, end)
    from .api_books import _with_evidence_warning

    data = _with_evidence_warning(context, L.statements(context, first, last, today=_today(), compare=compare), first, last)
    return {**data, "policies": policies_for(context)}


@router.get("/ledger/statements.csv")
@refusals
@_ledger
def export_statements(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                      context: TenantContext = Depends(tenant_context)) -> Response:
    _read(context)
    first, last = _period(context, start, end)
    data = L.statements(context, first, last, today=_today())
    base = data["base_currency"]
    rows: list[list[Any]] = [["statement", "line", "code", "currency", "amount"],
                             ["period", f"{data['period']['from']} to {data['period']['to']}", "", base, ""],
                             ["complete", "yes" if data["complete"] else "no", "", "", ""]]
    for note in data["notes"]:
        rows.append(["note", note, "", "", ""])
    pending = data["pending"]
    rows.append(["not_posted", "to_post", "", "", pending["to_post"] + pending["to_reverse"]])
    rows.append(["not_posted", "issues", "", "", pending["issues"]])
    for row in data["trial_balance"]["rows"]:
        rows.append(["trial_balance", row["name"], row["code"], base, f"{row['closing_debit']} Dr / {row['closing_credit']} Cr"])
    rows.append(["trial_balance", "balanced", "", "", "yes" if data["trial_balance"]["balanced"] else "no"])
    sheet = data["balance_sheet"]
    for side, blocks in (("assets", ("current", "non_current")), ("liabilities", ("current", "non_current"))):
        for block in blocks:
            for row in sheet[side][block]["rows"]:
                rows.append([f"balance_sheet_{side}", row["name"], row["code"], base, row["amount"]])
        rows.append([f"balance_sheet_{side}", "total", "", base, sheet[side]["total"]])
    for row in sheet["equity"]["rows"]:
        rows.append(["balance_sheet_equity", row["name"], row["code"], base, row["amount"]])
    rows.append(["balance_sheet_equity", "total", "", base, sheet["equity"]["total"]])
    rows.append(["balance_sheet", "balanced", "", "", "yes" if sheet["balanced"] else "no"])
    pnl = data["pnl"]
    for section in ("revenue", "cost_of_sales", "operating_expenses", "other_income", "finance_costs"):
        for row in pnl[section]["lines"]:
            rows.append([f"pnl_{section}", row["name"], row["code"], base, row["amount"]])
        rows.append([f"pnl_{section}", "total", "", base, pnl[section]["total"]])
    for key in ("gross_profit", "operating_profit", "fx_differences", "net_profit"):
        rows.append(["pnl", key, "", base, pnl[key]])
    for row in data["equity"]["rows"]:
        rows.append(["equity", row["key"], "", base, row["total"]])
    for item in data["cash_flow"]["by_currency"]:
        rows.append(["cash_flow", "opening", "", item["currency"], item["opening"]])
        for activity in ("operating", "investing", "financing"):
            for line in item[activity]["lines"]:
                rows.append([f"cash_flow_{activity}", line["key"], "", item["currency"], line["amount"]])
        rows.append(["cash_flow", "closing", "", item["currency"], item["closing"]])
    return _csv(rows, f"business-statements-{first.isoformat()}-{last.isoformat()}.csv")


def policies_for(context: TenantContext) -> list[dict[str, str]]:
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        feed = store.first("rate_feeds")
    return policies(settings, bool(feed and feed.enabled))


def policies(settings: Any, ecb: bool) -> list[dict[str, str]]:
    """The accounting policies the figures follow, in words from the settings (the web translates them by key)."""
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    out = [
        {"key": "basis", "text": f"The journal is kept on the accrual basis. The {settings.basis.value} basis is the default for the P&L derived from the records."},
        {"key": "functional_currency", "text": f"The functional and presentation currency is {settings.base_currency}. Different currencies are never added."},
        {"key": "rates", "text": ("Other currencies are converted at the rate of the transaction's day (IAS 21), recorded by an owner with its source"
                                  + (" or fetched from the European Central Bank" if ecb else "")
                                  + f", at most {settings.rate_max_age_days} days old. Without a rate an item is not posted."
                                  " Settlements book the difference to exchange gains and losses. At each month end a close covers, monetary"
                                  " balances in other currencies are revalued at the closing rate; the unrealised difference is reversed the next day.")},
        {"key": "financial_year", "text": f"The financial year starts in {months[settings.fiscal_year_start_month - 1]}."
                                          " At its end the result is closed to retained earnings."},
        {"key": "depreciation", "text": "Fixed assets are depreciated monthly over their useful life, straight line or reducing balance as set in the"
                                        " fixed asset register, down to their residual value, at the rate of the purchase day. Impairments are"
                                        " approved by a second person; a disposal posts its gain or loss."},
        {"key": "loans", "text": "Loan repayments are split into interest and principal from the loan schedule; interest accrued and unpaid at a"
                                 " month end is a finance cost of that month."},
        {"key": "revenue", "text": "Revenue is recognised when an invoice is issued; credit notes reduce it."},
    ]
    if getattr(settings, "policy_note", ""):
        out.append({"key": "owner_note", "text": settings.policy_note})
    return out


@router.get("/ledger/policies")
@refusals
def get_policies(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    return {"items": policies_for(context)}


# -- period close ----------------------------------------------------------------------------------------------------------


class CloseInput(BaseModel):
    through: date


class ReopenInput(BaseModel):
    month: date
    reason: str = Field(min_length=5, max_length=300)


@router.get("/close")
@refusals
@_ledger
def get_close(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    data = L.close_status(context, _today())
    return {**data, "can_close": can_account(context), "can_reopen": has_permission(context, Permission.BUSINESS_MANAGE)}


@router.post("/close")
@refusals
@_ledger
def close_period(payload: CloseInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Close every month up to ``through`` (a month end that has passed): owners and accountants."""
    require_accounting(context)
    result = L.close(context, payload.through, today=_today(), now=utc_now())
    _audit(repository, context, "period_closed", context.workspace_id, {"before": {"closed_through": result["previous"] or ""},
                                                                         "after": {"closed_through": result["closed_through"]},
                                                                         "months": len(result["closed"]),
                                                                         "years": sum(1 for row in result["closed"] if row["kind"] == "year")})
    return result


@router.post("/reopen")
@refusals
@_ledger
def reopen_period(payload: ReopenInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Reopen a month and every later closed month, with a reason: owners only."""
    _read(context)
    if not has_permission(context, Permission.BUSINESS_MANAGE):
        raise S.BooksError(403, REOPEN_DENIED, "forbidden")
    reason = S.clean(payload.reason, 300)
    if len(reason) < 5:
        raise S.BooksError(422, "Say why the period is reopened (it stays in the trail).", "reason_required")
    result = L.reopen(context, payload.month, reason, now=utc_now())
    _audit(repository, context, "period_reopened", context.workspace_id, {"before": {"closed_through": result["previous"]},
                                                                           "after": {"closed_through": result["closed_through"] or ""},
                                                                           "months": len(result["reopened"])})
    return result


# -- accountant review --------------------------------------------------------------------------------------------------------


@router.get("/review")
@refusals
@_ledger
def accountant_review(kind: str | None = Query(default=None, max_length=40), limit: int = Query(default=50, ge=1, le=MAX_PAGE),
                      offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    """Everything waiting on an owner or an accountant: draft bills to approve, posting issues, records with no exchange rate,
    allocations in review, and the close checklist of the previous and current month (docs/business-books.md#accountant-review)."""
    from . import books_review as RV

    require_accounting(context)
    if kind is not None and kind not in RV.KIND_ORDER:
        raise S.BooksError(422, "Unknown review kind.", "invalid_kind")
    return RV.build(context, _today(), kind=kind, limit=limit, offset=offset).model_dump(mode="json")


# -- accountants -------------------------------------------------------------------------------------------------------------


class AccountantInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:@-]+$")


@router.get("/accountants")
@refusals
def list_accountants(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        rows, _ = store.find("accountants", order_by="created_at", limit=100)
    return {"items": [{"user_id": row.user_id, "added_by": row.added_by, "added_at": row.created_at.isoformat()} for row in rows],
            "can_manage": has_permission(context, Permission.BUSINESS_MANAGE), "me": is_accountant(context)}


@router.post("/accountants", status_code=status.HTTP_201_CREATED)
@refusals
def add_accountant(payload: AccountantInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Name a member of this business accountant (owners only). Owners already hold every accountant right."""
    _manage(context)
    membership = lookup_membership(context.model_copy(update={"user_id": payload.user_id}))
    if membership is None or not membership.active or getattr(membership.role, "value", membership.role) not in ("owner", "member"):
        raise S.BooksError(422, "Only an active owner or member of this business can be its accountant.", "not_a_member")
    now = utc_now()
    item = Accountant(id=new_id("bacct"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, user_id=payload.user_id,
                      added_by=context.user_id)
    with open_books_store(context) as store:
        try:
            store.add("accountants", item)
        except DuplicateError as exc:
            raise S.BooksError(409, "This person is already an accountant of the business.", "duplicate") from exc
    _audit(repository, context, "accountant_added", item.id, {"user_id": payload.user_id})
    return {"user_id": item.user_id, "added_by": item.added_by, "added_at": now.isoformat()}


@router.delete("/accountants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@refusals
def remove_accountant(user_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_books_store(context) as store:
        found = store.first("accountants", user_id=user_id)
        if found is None:
            raise S.BooksError(404, "That person is not an accountant of this business.", "accountant_not_found")
        store.delete("accountants", found.id)
    _audit(repository, context, "accountant_removed", found.id, {"user_id": user_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["can_account", "is_accountant", "policies", "router"]
