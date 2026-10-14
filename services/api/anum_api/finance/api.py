"""HTTP routes under ``/api/v1/finance`` (docs/finance.md#api).

Who can do what (checked here, on the server, for every route):

* ``finance:read`` (owners and members): the summary, accounts, transactions, imports,
  bills, debts, goals and holdings, all of them the caller's own.
* ``finance:manage`` (owners and members): add, change and delete those, import
  statements and change the finance settings, again only the caller's own.
* Viewers have neither. Nobody sees anyone else's ledger, workspace owners included: the
  store filters on the caller and the database refuses other people's rows (RLS).

Nothing here moves money. There is no payment, transfer, bank, card or broker call.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import repository_context, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import csv_import
from .categories import categorize
from .csv_import import ColumnMapping, ImportError_, safe_cell
from .models import (
    Account,
    AccountKind,
    Bill,
    BillSource,
    Cadence,
    Category,
    CategoryRule,
    CategorySource,
    Debt,
    DebtKind,
    FinanceSettings,
    Goal,
    Holding,
    ImportRecord,
    Transaction,
)
from .money import MoneyError, normalize_currency, quantize, to_money
from .store import (
    MAX_ACCOUNTS,
    MAX_ITEMS,
    MAX_PAGE,
    FinanceStore,
    ItemKind,
    TransactionFilter,
    open_finance_store,
)
from .summary import (
    BillSuggestion,
    FinanceSummary,
    UpcomingBill,
    build_summary,
    settings_for,
    suggest_bills,
    today_in,
    upcoming_bills,
    zone,
)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

READ_DENIED = (
    "Finance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member."
)
MANAGE_DENIED = "Only workspace owners and members can change their finance records."
EXPORT_LIMIT = 10_000
SCOPE_FIELDS = {"tenant_id", "workspace_id", "owner_user_id"}

Amount = Decimal | int | str


def _require(context: TenantContext, permission: Permission) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            message = READ_DENIED if permission == Permission.FINANCE_READ else MANAGE_DENIED
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message) from None
        raise


def _read(context: TenantContext) -> None:
    _require(context, Permission.FINANCE_READ)


def _manage(context: TenantContext) -> None:
    _require(context, Permission.FINANCE_READ)
    _require(context, Permission.FINANCE_MANAGE)


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> None:
    """A security record without amounts, descriptions or names: finance stays private."""
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome="success",
            correlation_id=target,
            created_at=utc_now(),
            metadata=metadata or {},
        )
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found. It may have been deleted.")


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _money(value: Amount | None, currency: str, *, field: str, positive: bool = False, allow_zero: bool = True) -> Decimal:
    try:
        amount = to_money(value if value is not None else 0, currency)
    except MoneyError as exc:
        raise _invalid(f"{field}: {exc}") from None
    if positive and (amount < 0 or (amount == 0 and not allow_zero)):
        raise _invalid(f"{field}: enter an amount above zero." if not allow_zero else f"{field}: enter zero or more.")
    return amount


def _currency(value: str | None, fallback: str) -> str:
    try:
        return normalize_currency(value or fallback)
    except MoneyError as exc:
        raise _invalid(str(exc)) from None


def _view(item: BaseModel) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude=SCOPE_FIELDS)


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = csv_import.clean_text(value, limit)
    return text or None


# -- settings --------------------------------------------------------------------------------


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: str = Field(default="AED", min_length=3, max_length=3)
    timezone: str = Field(default="Asia/Dubai", min_length=1, max_length=64)
    category_rules: list[CategoryRule] = Field(default_factory=list, max_length=100)


class FormatsView(BaseModel):
    presets: list[dict[str, str]]
    date_formats: list[str]
    categories: list[str]
    account_kinds: list[str]
    limits: dict[str, int]


@router.get("/settings", response_model=FinanceSettings)
def get_settings(context: TenantContext = Depends(tenant_context)) -> FinanceSettings:
    _read(context)
    with open_finance_store(context) as store:
        return settings_for(store, context)


@router.put("/settings", response_model=FinanceSettings)
def put_settings(
    body: SettingsInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> FinanceSettings:
    _manage(context)
    currency = _currency(body.base_currency, "AED")
    if zone(body.timezone).key != body.timezone:
        raise _invalid("Unknown time zone. Use a name such as Asia/Dubai.")
    rules = [CategoryRule(match=" ".join(r.match.split()), category=r.category) for r in body.category_rules]
    saved = FinanceSettings(base_currency=currency, timezone=body.timezone, category_rules=rules, updated_at=utc_now())
    with open_finance_store(context) as store:
        store.save_settings(context, saved)
    _audit(repository, context, "finance.settings.update", context.user_id, {"rules": len(rules)})
    return saved


@router.get("/formats", response_model=FormatsView)
def formats(context: TenantContext = Depends(tenant_context)) -> FormatsView:
    """What an import can read: the layouts, date formats, categories and limits."""
    _read(context)
    return FormatsView(
        presets=[{"id": p.id, "label": p.label} for p in csv_import.PRESETS.values()],
        date_formats=list(csv_import.DATE_FORMATS),
        categories=[c.value for c in Category],
        account_kinds=[k.value for k in AccountKind],
        limits={"max_bytes": csv_import.MAX_BYTES, "max_rows": csv_import.MAX_ROWS, "max_page": MAX_PAGE},
    )


# -- summary ---------------------------------------------------------------------------------


@router.get("/summary", response_model=FinanceSummary)
def summary(
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    context: TenantContext = Depends(tenant_context),
) -> FinanceSummary:
    """Balances, the month's spend by category, income versus spend, bills due in 30 days, debts."""
    _read(context)
    with open_finance_store(context) as store:
        return build_summary(store, context, month)


# -- accounts --------------------------------------------------------------------------------


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    institution: str | None = Field(default=None, max_length=120)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$", description="Last four digits only, never the full number")
    opening_balance: Amount = "0"
    archived: bool = False


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: AccountKind | None = None
    institution: str | None = Field(default=None, max_length=120)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    opening_balance: Amount | None = None
    archived: bool | None = None


class AccountView(BaseModel):
    id: str
    name: str
    kind: AccountKind
    currency: str
    institution: str | None
    last4: str | None
    opening_balance: Decimal
    balance: Decimal
    archived: bool
    transaction_count: int = 0
    created_at: Any
    updated_at: Any


def _account_view(store: FinanceStore, context: TenantContext, account: Account, sums: dict[str, Decimal] | None = None) -> AccountView:
    sums = sums if sums is not None else store.account_sums(context)
    _, count = store.list_transactions(context, TransactionFilter(account_id=account.id), limit=1, offset=0)
    return AccountView(
        **{k: v for k, v in account.model_dump().items() if k not in SCOPE_FIELDS},
        balance=quantize(account.opening_balance + sums.get(account.id, Decimal(0)), account.currency),
        transaction_count=count,
    )


def _own_account(store: FinanceStore, context: TenantContext, account_id: str) -> Account:
    found = store.get_account(context, account_id)
    if found is None:
        raise _not_found("Account")
    return found


@router.get("/accounts", response_model=list[AccountView])
def list_accounts(context: TenantContext = Depends(tenant_context)) -> list[AccountView]:
    _read(context)
    with open_finance_store(context) as store:
        sums = store.account_sums(context)
        return [_account_view(store, context, a, sums) for a in store.list_accounts(context)]


@router.post("/accounts", response_model=AccountView, status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AccountView:
    _manage(context)
    with open_finance_store(context) as store:
        if len(store.list_accounts(context)) >= MAX_ACCOUNTS:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_ACCOUNTS} accounts. Archive or delete one first.")
        currency = _currency(body.currency, settings_for(store, context).base_currency)
        now = utc_now()
        account = Account(
            id=new_id("fin_acct"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            name=_clean(body.name, 120) or "Account", kind=body.kind, currency=currency, institution=_clean(body.institution, 120),
            last4=body.last4, opening_balance=_money(body.opening_balance, currency, field="Opening balance"),
            archived=body.archived, created_at=now, updated_at=now,
        )
        store.save_account(account)
        view = _account_view(store, context, account)
    _audit(repository, context, "finance.account.create", account.id, {"kind": account.kind.value})
    return view


@router.get("/accounts/{account_id}", response_model=AccountView)
def get_account(account_id: str, context: TenantContext = Depends(tenant_context)) -> AccountView:
    _read(context)
    with open_finance_store(context) as store:
        return _account_view(store, context, _own_account(store, context, account_id))


@router.patch("/accounts/{account_id}", response_model=AccountView)
def update_account(
    account_id: str,
    body: AccountPatch,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AccountView:
    _manage(context)
    with open_finance_store(context) as store:
        account = _own_account(store, context, account_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes and body.name is not None:
            account.name = _clean(body.name, 120) or account.name
        if "kind" in changes and body.kind is not None:
            account.kind = body.kind
        if "institution" in changes:
            account.institution = _clean(body.institution, 120)
        if "last4" in changes:
            account.last4 = body.last4
        if "opening_balance" in changes:
            account.opening_balance = _money(body.opening_balance, account.currency, field="Opening balance")
        if "archived" in changes and body.archived is not None:
            account.archived = body.archived
        account.updated_at = utc_now()
        store.save_account(account)
        view = _account_view(store, context, account)
    _audit(repository, context, "finance.account.update", account_id, {"fields": sorted(changes)})
    return view


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Deletes the account with its transactions, imports and holdings. Bills, debts and
    goals linked to it stay, without the link."""
    _manage(context)
    with open_finance_store(context) as store:
        if not store.delete_account(context, account_id):
            raise _not_found("Account")
    _audit(repository, context, "finance.account.delete", account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- transactions ----------------------------------------------------------------------------


class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    posted_on: date
    amount: Amount = Field(description="Signed: money in is positive, money out negative")
    description: str = Field(min_length=1, max_length=500)
    category: Category | None = None
    notes: str | None = Field(default=None, max_length=1000)


class TransactionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posted_on: date | None = None
    amount: Amount | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    category: Category | None = None
    notes: str | None = Field(default=None, max_length=1000)


class TransactionView(BaseModel):
    id: str
    account_id: str
    posted_on: date
    amount: Decimal
    currency: str
    description: str
    category: Category
    category_source: CategorySource
    notes: str | None
    import_id: str | None
    created_at: Any
    updated_at: Any


class TransactionPage(BaseModel):
    items: list[TransactionView]
    total: int
    limit: int
    offset: int


def _filter(
    q: str | None, account_id: str | None, category: Category | None, start: date | None, end: date | None, direction: str | None
) -> TransactionFilter:
    if start and end and start > end:
        raise _invalid("The start date is after the end date.")
    return TransactionFilter(
        account_id=account_id or None, category=category, query=(q or "").strip() or None, start=start, end=end,
        direction=direction if direction in {"in", "out"} else None,  # type: ignore[arg-type]
    )


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    q: str | None = Query(default=None, max_length=200),
    account_id: str | None = Query(default=None, max_length=80),
    category: Category | None = None,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    direction: Literal["in", "out"] | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> TransactionPage:
    _read(context)
    flt = _filter(q, account_id, category, start, end, direction)
    with open_finance_store(context) as store:
        items, total = store.list_transactions(context, flt, limit=limit, offset=offset)
    return TransactionPage(items=[TransactionView(**_view(t)) for t in items], total=total, limit=limit, offset=offset)


@router.get("/transactions/export.csv", response_class=Response)
def export_transactions(
    q: str | None = Query(default=None, max_length=200),
    account_id: str | None = Query(default=None, max_length=80),
    category: Category | None = None,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    direction: Literal["in", "out"] | None = None,
    context: TenantContext = Depends(tenant_context),
) -> Response:
    """The matching transactions as CSV (at most 10,000 rows). Text cells that a
    spreadsheet would run as a formula start with an apostrophe."""
    _read(context)
    flt = _filter(q, account_id, category, start, end, direction)
    with open_finance_store(context) as store:
        names = {a.id: a.name for a in store.list_accounts(context)}
        items, _ = store.list_transactions(context, flt, limit=EXPORT_LIMIT, offset=0)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "account", "description", "category", "amount", "currency", "notes"])
    for t in items:
        writer.writerow([
            t.posted_on.isoformat(), safe_cell(names.get(t.account_id, "")), safe_cell(t.description), t.category.value,
            str(t.amount), t.currency, safe_cell(t.notes or ""),
        ])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="anum-transactions.csv"', "Cache-Control": "no-store"},
    )


def _own_transaction(store: FinanceStore, context: TenantContext, transaction_id: str) -> Transaction:
    found = store.get_transaction(context, transaction_id)
    if found is None:
        raise _not_found("Transaction")
    return found


@router.post("/transactions", response_model=TransactionView, status_code=status.HTTP_201_CREATED)
def create_transaction(
    body: TransactionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TransactionView:
    _manage(context)
    with open_finance_store(context) as store:
        account = _own_account(store, context, body.account_id)
        amount = _money(body.amount, account.currency, field="Amount")
        description = _clean(body.description, 500)
        if not description:
            raise _invalid("Description: enter what the transaction was.")
        if body.category is not None:
            category, source = body.category, CategorySource.MANUAL
        else:
            category, source = categorize(description, amount, settings_for(store, context).category_rules)
        now = utc_now()
        item = Transaction(
            id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, posted_on=body.posted_on, amount=amount, currency=account.currency, description=description,
            category=category, category_source=source, notes=_clean(body.notes, 1000), import_id=None,
            # A hand-entered row is never a duplicate of another.
            dedupe_hash=csv_import.dedupe_hash(account.id, body.posted_on, amount, account.currency, f"manual {new_id('m')}", 0),
            created_at=now, updated_at=now,
        )
        store.save_transaction(item)
    _audit(repository, context, "finance.transaction.create", item.id)
    return TransactionView(**_view(item))


@router.get("/transactions/{transaction_id}", response_model=TransactionView)
def get_transaction(transaction_id: str, context: TenantContext = Depends(tenant_context)) -> TransactionView:
    _read(context)
    with open_finance_store(context) as store:
        return TransactionView(**_view(_own_transaction(store, context, transaction_id)))


@router.patch("/transactions/{transaction_id}", response_model=TransactionView)
def update_transaction(
    transaction_id: str,
    body: TransactionPatch,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TransactionView:
    _manage(context)
    changes = body.model_dump(exclude_unset=True)
    with open_finance_store(context) as store:
        item = _own_transaction(store, context, transaction_id)
        if body.posted_on is not None:
            item.posted_on = body.posted_on
        if "amount" in changes and body.amount is not None:
            item.amount = _money(body.amount, item.currency, field="Amount")
        if body.description is not None:
            item.description = _clean(body.description, 500) or item.description
        if "notes" in changes:
            item.notes = _clean(body.notes, 1000)
        if body.category is not None:
            item.category, item.category_source = body.category, CategorySource.MANUAL
        elif item.category_source != CategorySource.MANUAL and ({"description", "amount"} & set(changes)):
            item.category, item.category_source = categorize(item.description, item.amount, settings_for(store, context).category_rules)
        item.updated_at = utc_now()
        store.save_transaction(item)
    _audit(repository, context, "finance.transaction.update", transaction_id, {"fields": sorted(changes)})
    return TransactionView(**_view(item))


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    _manage(context)
    with open_finance_store(context) as store:
        if not store.delete_transaction(context, transaction_id):
            raise _not_found("Transaction")
    _audit(repository, context, "finance.transaction.delete", transaction_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- imports ---------------------------------------------------------------------------------


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(default="statement.csv", min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=csv_import.MAX_BYTES, description="The CSV file's text")
    preset: str | None = Field(default=None, max_length=40)
    date_format: str | None = Field(default=None, max_length=20)
    mapping: ColumnMapping | None = None

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, value: str | None) -> str | None:
        if value in (None, "", "auto", "custom"):
            return None
        if value not in csv_import.PRESETS:
            raise ValueError("Unknown layout.")
        return value


class PreviewRow(BaseModel):
    line: int
    posted_on: date
    amount: Decimal
    currency: str
    description: str
    category: Category
    duplicate: bool


class PreviewError(BaseModel):
    line: int
    message: str


class ImportPreview(BaseModel):
    account_id: str
    currency: str
    filename: str
    preset: str
    headers: list[str]
    mapping: ColumnMapping
    date_format: str
    date_formats: list[str]
    date_format_ambiguous: bool
    row_count: int
    valid_count: int
    new_count: int
    duplicate_count: int
    error_count: int
    rows: list[PreviewRow]
    errors: list[PreviewError]


class ImportView(BaseModel):
    id: str
    account_id: str
    filename: str
    preset: str
    date_format: str
    row_count: int
    imported_count: int
    duplicate_count: int
    error_count: int
    created_at: Any


class ImportPage(BaseModel):
    items: list[ImportView]
    total: int
    limit: int
    offset: int


def _parse(store: FinanceStore, context: TenantContext, body: ImportInput) -> tuple[Account, csv_import.ParsedFile, set[str]]:
    account = _own_account(store, context, body.account_id)
    try:
        parsed = csv_import.parse_file(
            body.content, account_id=account.id, currency=account.currency, preset=body.preset, mapping=body.mapping,
            date_format=body.date_format,
        )
    except ImportError_ as exc:
        raise _invalid(str(exc)) from None
    existing = store.existing_hashes(context, account.id, [row.dedupe_hash for row in parsed.rows])
    return account, parsed, existing


@router.post("/imports/preview", response_model=ImportPreview)
def preview_import(body: ImportInput, context: TenantContext = Depends(tenant_context)) -> ImportPreview:
    """Read a statement and show what an import would do. Nothing is saved."""
    _manage(context)
    with open_finance_store(context) as store:
        account, parsed, existing = _parse(store, context, body)
        rules = settings_for(store, context).category_rules
    duplicates = sum(1 for row in parsed.rows if row.dedupe_hash in existing)
    return ImportPreview(
        account_id=account.id, currency=account.currency, filename=_clean(body.filename, 200) or "statement.csv",
        preset=parsed.preset, headers=parsed.headers, mapping=parsed.mapping, date_format=parsed.date_format,
        date_formats=parsed.date_formats, date_format_ambiguous=parsed.date_format_ambiguous, row_count=parsed.row_count,
        valid_count=len(parsed.rows), new_count=len(parsed.rows) - duplicates, duplicate_count=duplicates,
        error_count=len(parsed.errors),
        rows=[
            PreviewRow(
                line=row.line, posted_on=row.posted_on, amount=row.amount, currency=account.currency, description=row.description,
                category=categorize(row.description, row.amount, rules)[0], duplicate=row.dedupe_hash in existing,
            )
            for row in parsed.rows[: csv_import.PREVIEW_ROWS]
        ],
        errors=[PreviewError(line=e.line, message=e.message) for e in parsed.errors[: csv_import.MAX_ERRORS_SHOWN]],
    )


@router.post("/imports", response_model=ImportView, status_code=status.HTTP_201_CREATED)
def confirm_import(
    body: ImportInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ImportView:
    """Import the statement previewed with the same choices; duplicates are skipped."""
    _manage(context)
    with open_finance_store(context) as store:
        account, parsed, existing = _parse(store, context, body)
        fresh = [row for row in parsed.rows if row.dedupe_hash not in existing]
        if not parsed.rows:
            raise _invalid("No row of this file could be read. Check the column mapping and the date format.")
        rules = settings_for(store, context).category_rules
        now = utc_now()
        record = ImportRecord(
            id=new_id("fin_imp"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, filename=_clean(body.filename, 200) or "statement.csv", preset=parsed.preset,
            date_format=parsed.date_format, row_count=parsed.row_count, imported_count=0,
            duplicate_count=len(parsed.rows) - len(fresh), error_count=len(parsed.errors), file_sha256=parsed.file_sha256,
            created_at=now,
        )
        store.save_import(record)
        rows = []
        for row in fresh:
            category, source = categorize(row.description, row.amount, rules)
            rows.append(
                Transaction(
                    id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                    owner_user_id=context.user_id, account_id=account.id, posted_on=row.posted_on, amount=row.amount,
                    currency=account.currency, description=row.description, category=category, category_source=source,
                    import_id=record.id, dedupe_hash=row.dedupe_hash, created_at=now, updated_at=now,
                )
            )
        added = store.add_transactions(rows)
        record.imported_count = added
        record.duplicate_count += len(rows) - added
        store.save_import(record)
    _audit(repository, context, "finance.import.create", record.id, {"imported": added, "duplicates": record.duplicate_count})
    return ImportView(**_view(record))


@router.get("/imports", response_model=ImportPage)
def list_imports(
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> ImportPage:
    _read(context)
    with open_finance_store(context) as store:
        items, total = store.list_imports(context, limit=limit, offset=offset)
    return ImportPage(items=[ImportView(**_view(i)) for i in items], total=total, limit=limit, offset=offset)


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(
    import_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Undo an import: its transactions go with it."""
    _manage(context)
    with open_finance_store(context) as store:
        removed = store.delete_import(context, import_id)
    if removed is None:
        raise _not_found("Import")
    _audit(repository, context, "finance.import.delete", import_id, {"removed": removed})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- bills, debts, goals and holdings ---------------------------------------------------------


class BillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    amount: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    cadence: Cadence = Cadence.MONTHLY
    next_due_on: date
    category: Category = Category.UTILITIES
    account_id: str | None = Field(default=None, max_length=80)
    source: BillSource = BillSource.MANUAL
    active: bool = True
    notes: str | None = Field(default=None, max_length=1000)


class DebtInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: DebtKind = DebtKind.LOAN
    balance: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    apr_percent: Amount = "0"
    minimum_payment: Amount = "0"
    due_day: int | None = Field(default=None, ge=1, le=31)
    account_id: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=1000)


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    target_amount: Amount
    saved_amount: Amount = "0"
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    target_on: date | None = None
    account_id: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=1000)


class HoldingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=1, max_length=40)
    name: str | None = Field(default=None, max_length=120)
    quantity: Amount
    cost_basis: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=1000)


class BillView(BaseModel):
    id: str
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    next_due_on: date
    category: Category
    account_id: str | None
    source: BillSource
    active: bool
    notes: str | None
    created_at: Any
    updated_at: Any


class DebtView(BaseModel):
    id: str
    name: str
    kind: DebtKind
    balance: Decimal
    currency: str
    apr_percent: Decimal
    minimum_payment: Decimal
    due_day: int | None
    account_id: str | None
    notes: str | None
    created_at: Any
    updated_at: Any


class GoalView(BaseModel):
    id: str
    name: str
    target_amount: Decimal
    saved_amount: Decimal
    currency: str
    target_on: date | None
    account_id: str | None
    notes: str | None
    created_at: Any
    updated_at: Any


class HoldingView(BaseModel):
    id: str
    account_id: str
    symbol: str
    name: str | None
    quantity: Decimal
    cost_basis: Decimal
    currency: str
    notes: str | None
    created_at: Any
    updated_at: Any


def _linked_account(store: FinanceStore, context: TenantContext, account_id: str | None) -> Account | None:
    return _own_account(store, context, account_id) if account_id else None


def _apr(value: Amount) -> Decimal:
    try:
        rate = Decimal(str(value).strip()).quantize(Decimal("0.0001"))
    except Exception:  # noqa: BLE001 - any unreadable value is the same mistake
        raise _invalid("APR: enter a yearly rate in percent, for example 18.99.") from None
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise _invalid("APR: enter a yearly rate between 0 and 100 percent.")
    return rate


def _quantity(value: Amount) -> Decimal:
    try:
        number = Decimal(str(value).strip()).quantize(Decimal("0.00000001"))
    except Exception:  # noqa: BLE001
        raise _invalid("Quantity: enter a number, for example 12 or 0.5.") from None
    if not number.is_finite() or number < 0 or number >= Decimal("1e19"):
        raise _invalid("Quantity: enter zero or more.")
    return number


def _build_bill(store: FinanceStore, context: TenantContext, body: BillInput, existing: Bill | None) -> Bill:
    account = _linked_account(store, context, body.account_id)
    currency = _currency(body.currency or (account.currency if account else None), settings_for(store, context).base_currency)
    now = utc_now()
    return Bill(
        id=existing.id if existing else new_id("fin_bill"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, name=_clean(body.name, 120) or "Bill",
        amount=_money(body.amount, currency, field="Amount", positive=True), currency=currency, cadence=body.cadence,
        next_due_on=body.next_due_on, category=body.category, account_id=account.id if account else None, source=body.source,
        active=body.active, notes=_clean(body.notes, 1000), created_at=existing.created_at if existing else now, updated_at=now,
    )


def _build_debt(store: FinanceStore, context: TenantContext, body: DebtInput, existing: Debt | None) -> Debt:
    account = _linked_account(store, context, body.account_id)
    currency = _currency(body.currency or (account.currency if account else None), settings_for(store, context).base_currency)
    now = utc_now()
    return Debt(
        id=existing.id if existing else new_id("fin_debt"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, name=_clean(body.name, 120) or "Debt", kind=body.kind,
        balance=_money(body.balance, currency, field="Balance", positive=True), currency=currency, apr_percent=_apr(body.apr_percent),
        minimum_payment=_money(body.minimum_payment, currency, field="Minimum payment", positive=True), due_day=body.due_day,
        account_id=account.id if account else None, notes=_clean(body.notes, 1000),
        created_at=existing.created_at if existing else now, updated_at=now,
    )


def _build_goal(store: FinanceStore, context: TenantContext, body: GoalInput, existing: Goal | None) -> Goal:
    account = _linked_account(store, context, body.account_id)
    currency = _currency(body.currency or (account.currency if account else None), settings_for(store, context).base_currency)
    now = utc_now()
    return Goal(
        id=existing.id if existing else new_id("fin_goal"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, name=_clean(body.name, 120) or "Goal",
        target_amount=_money(body.target_amount, currency, field="Target", positive=True, allow_zero=False),
        saved_amount=_money(body.saved_amount, currency, field="Saved so far", positive=True), currency=currency,
        target_on=body.target_on, account_id=account.id if account else None, notes=_clean(body.notes, 1000),
        created_at=existing.created_at if existing else now, updated_at=now,
    )


def _build_holding(store: FinanceStore, context: TenantContext, body: HoldingInput, existing: Holding | None) -> Holding:
    account = _own_account(store, context, body.account_id)
    if account.kind != AccountKind.INVESTMENT:
        raise _invalid("Holdings belong to an investment account. Pick one, or add an investment account first.")
    currency = _currency(body.currency or account.currency, account.currency)
    symbol = (_clean(body.symbol, 40) or "").upper()
    if not symbol:
        raise _invalid("Symbol: enter the ticker or a short name, for example EMAAR.")
    now = utc_now()
    return Holding(
        id=existing.id if existing else new_id("fin_hold"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, account_id=account.id, symbol=symbol, name=_clean(body.name, 120),
        quantity=_quantity(body.quantity), cost_basis=_money(body.cost_basis, currency, field="Cost", positive=True),
        currency=currency, notes=_clean(body.notes, 1000), created_at=existing.created_at if existing else now, updated_at=now,
    )


_ITEMS: dict[str, dict[str, Any]] = {
    "bill": {"path": "bills", "label": "Bill", "input": BillInput, "view": BillView, "build": _build_bill},
    "debt": {"path": "debts", "label": "Debt", "input": DebtInput, "view": DebtView, "build": _build_debt},
    "goal": {"path": "goals", "label": "Goal", "input": GoalInput, "view": GoalView, "build": _build_goal},
    "holding": {"path": "holdings", "label": "Holding", "input": HoldingInput, "view": HoldingView, "build": _build_holding},
}


@router.get("/bills/upcoming", response_model=list[UpcomingBill])
def bills_upcoming(days: int = Query(default=30, ge=1, le=366), context: TenantContext = Depends(tenant_context)) -> list[UpcomingBill]:
    """Every active bill's due dates in the next ``days`` days, soonest first."""
    _read(context)
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    return upcoming_bills(bills, today, days)


@router.get("/bills/suggestions", response_model=list[BillSuggestion])
def bill_suggestions(context: TenantContext = Depends(tenant_context)) -> list[BillSuggestion]:
    """Recurring payments found in the transactions, not yet saved as bills."""
    _read(context)
    with open_finance_store(context) as store:
        return suggest_bills(store, context)


def _register(kind: ItemKind) -> None:
    spec = _ITEMS[kind]
    path, label, input_model, view_model, build = spec["path"], spec["label"], spec["input"], spec["view"], spec["build"]

    def _own(store: FinanceStore, context: TenantContext, item_id: str) -> Any:
        found = store.get_item(context, kind, item_id)
        if found is None:
            raise _not_found(label)
        return found

    def list_route(context: TenantContext = Depends(tenant_context)) -> list[Any]:
        _read(context)
        with open_finance_store(context) as store:
            return [view_model(**_view(item)) for item in store.list_items(context, kind)]

    def create_route(
        body: input_model,  # type: ignore[valid-type]
        context: TenantContext = Depends(tenant_context),
        repository: AnumRepository = Depends(repository_context),
    ) -> Any:
        _manage(context)
        with open_finance_store(context) as store:
            if len(store.list_items(context, kind)) >= MAX_ITEMS:
                raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_ITEMS} of these. Delete one first.")
            item = build(store, context, body, None)
            store.save_item(kind, item)
        _audit(repository, context, f"finance.{kind}.create", item.id)
        return view_model(**_view(item))

    def get_route(item_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
        _read(context)
        with open_finance_store(context) as store:
            return view_model(**_view(_own(store, context, item_id)))

    def update_route(
        item_id: str,
        body: input_model,  # type: ignore[valid-type]
        context: TenantContext = Depends(tenant_context),
        repository: AnumRepository = Depends(repository_context),
    ) -> Any:
        _manage(context)
        with open_finance_store(context) as store:
            item = build(store, context, body, _own(store, context, item_id))
            store.save_item(kind, item)
        _audit(repository, context, f"finance.{kind}.update", item_id)
        return view_model(**_view(item))

    def delete_route(
        item_id: str,
        context: TenantContext = Depends(tenant_context),
        repository: AnumRepository = Depends(repository_context),
    ) -> Response:
        _manage(context)
        with open_finance_store(context) as store:
            if not store.delete_item(context, kind, item_id):
                raise _not_found(label)
        _audit(repository, context, f"finance.{kind}.delete", item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # The body's model is chosen per kind at import time (not a module-level name).
    create_route.__annotations__["body"] = input_model
    update_route.__annotations__["body"] = input_model
    router.add_api_route(f"/{path}", list_route, methods=["GET"], response_model=list[view_model], name=f"list_finance_{path}")
    router.add_api_route(
        f"/{path}", create_route, methods=["POST"], response_model=view_model, status_code=status.HTTP_201_CREATED,
        name=f"create_finance_{kind}",
    )
    router.add_api_route(f"/{path}/{{item_id}}", get_route, methods=["GET"], response_model=view_model, name=f"get_finance_{kind}")
    # PATCH replaces the item with the full body sent (the forms send every field).
    router.add_api_route(f"/{path}/{{item_id}}", update_route, methods=["PATCH"], response_model=view_model, name=f"update_finance_{kind}")
    router.add_api_route(
        f"/{path}/{{item_id}}", delete_route, methods=["DELETE"], status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response, name=f"delete_finance_{kind}",
    )


for _kind in ("bill", "debt", "goal", "holding"):
    _register(_kind)  # type: ignore[arg-type]
