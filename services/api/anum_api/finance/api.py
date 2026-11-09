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
from . import obligations
from . import statement_import as SI
from . import refunds
from . import txn_types as TT
from .categories import categorize, classify_text, suspected_direction
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
    GoalPurpose,
    Holding,
    ImportRecord,
    Transaction,
    TransactionType,
    TypeSource,
)
from .guardian import Decision
from .money import MoneyError, normalize_currency, quantize, to_money
from .policy import check_record
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


def _guardian_meta(decision: Decision | None) -> dict[str, Any]:
    """The rule codes a record broke, never its amount or description."""
    if decision is None or decision.outcome == "allow":
        return {}
    return {"guardian": decision.outcome, "guardian_codes": [r.code for r in decision.reasons]}


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


def _txn_view(item: Transaction, kind: AccountKind | None = None) -> dict[str, Any]:
    """A transaction as the API shows it: with its type, derived when the row predates types (``txn_types.typed``)."""
    return _view(TT.fill(item.model_copy(), kind))


def _kinds(store: FinanceStore, context: TenantContext) -> dict[str, AccountKind]:
    return {a.id: a.kind for a in store.list_accounts(context)}


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
    links = obligations.load(context)
    with open_finance_store(context) as store:
        return build_summary(store, context, month, links=links)


# -- accounts --------------------------------------------------------------------------------


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    institution: str | None = Field(default=None, max_length=120)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$", description="Last four digits only, never the full number")
    opening_balance: Amount = "0"
    # Cards and loans: a positive opening balance is refused unless the account really starts in credit
    # (an overpaid card). What a card or loan owes is a negative balance (docs/finance.md#accounts).
    credit_balance: bool = False
    archived: bool = False


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: AccountKind | None = None
    institution: str | None = Field(default=None, max_length=120)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    opening_balance: Amount | None = None
    credit_balance: bool = False
    archived: bool | None = None


LIABILITY_ACCOUNTS = frozenset({AccountKind.CARD, AccountKind.LOAN})


def _opening(kind: AccountKind, value: Decimal, credit_balance: bool) -> Decimal:
    """A card or loan opens with what it owes as a negative balance; a positive one only when it is in credit."""
    if kind in LIABILITY_ACCOUNTS and value > 0 and not credit_balance:
        raise _invalid(
            "Opening balance: what a card or loan owes is entered as a negative amount (owes 3,000 is -3000). "
            "Tick 'In credit' only if the account was overpaid.")
    return value


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
            last4=body.last4,
            opening_balance=_opening(body.kind, _money(body.opening_balance, currency, field="Opening balance"), body.credit_balance),
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
            account.opening_balance = _opening(account.kind, _money(body.opening_balance, account.currency, field="Opening balance"),
                                               body.credit_balance)
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
    from .sms_service import owner_lock, reverse_for_transactions
    from .sms_store import open_sms_stores

    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        if store.get_account(context, account_id) is not None:
            rows, _ = store.list_transactions(context, TransactionFilter(account_id=account_id), limit=50_000, offset=0)
            reverse_for_transactions(store, sms, context, [t.id for t in rows], now=utc_now(), why="account deleted")
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
    # The owner's own reading of what the row is (docs/finance.md#transaction-types); never changed by a machine.
    transaction_type: TransactionType | None = None
    notes: str | None = Field(default=None, max_length=1000)


class TransactionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posted_on: date | None = None
    amount: Amount | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    category: Category | None = None
    transaction_type: TransactionType | None = None
    notes: str | None = Field(default=None, max_length=1000)
    remember_category_match: str | None = Field(default=None, min_length=4, max_length=80)


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
    # docs/finance.md#transaction-types: always set in answers (a row stored before types existed shows the type its
    # category stands for, ``type_reason`` ``legacy_category``). ``uncategorized`` rows count as neither income nor spend.
    transaction_type: TransactionType | None = None
    type_source: TypeSource | None = None
    type_confidence: Decimal | None = None
    type_reason: str | None = None
    related_transaction_id: str | None = None
    # Stage 4: what your Guardian rules say about this record (create and change only).
    # Recording money already gone is never blocked; a rule it breaks is a warning.
    guardian: Decision | None = None


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
        kinds = _kinds(store, context)
    return TransactionPage(items=[TransactionView(**_txn_view(t, kinds.get(t.account_id))) for t in items], total=total, limit=limit, offset=offset)


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
        kinds = _kinds(store, context)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "account", "description", "category", "amount", "currency", "notes", "type"])
    for t in items:
        writer.writerow([
            t.posted_on.isoformat(), safe_cell(names.get(t.account_id, "")), safe_cell(t.description), t.category.value,
            str(t.amount), t.currency, safe_cell(t.notes or ""), TT.effective(t, kinds.get(t.account_id)).value,
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


def record_manual_transaction(context: TenantContext, body: TransactionInput, *, transaction_id: str | None = None) -> tuple[Transaction, Any, str]:
    """A hand-entered transaction and its journal entry, in one unit of work under the owner's lock.

    Returns ``(transaction, guardian decision, canonical id)``. Used by ``POST /transactions`` and by modules that
    record a cash payment once and then link it (docs/vehicles.md#payments). With ``transaction_id`` it is
    idempotent: when the owner already has that transaction, it is returned unchanged. The caller checks
    ``finance:manage`` first."""
    from .ledger import EntrySource
    from .sms_service import owner_lock, record_plain
    from .sms_store import open_sms_stores

    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        if transaction_id is not None:
            existing = store.get_transaction(context, transaction_id)
            if existing is not None:
                entry = sms.entry_for_transaction(context, existing.id)
                return existing, None, (entry.canonical_id or entry.id) if entry is not None else existing.id
        account = _own_account(store, context, body.account_id)
        amount = _money(body.amount, account.currency, field="Amount")
        description = _clean(body.description, 500)
        if not description:
            raise _invalid("Description: enter what the transaction was.")
        found = classify_text(description, amount, settings_for(store, context).category_rules, account.kind)
        category, source, typed = found.category, found.source, found.typed
        if body.category is not None:
            category, source = body.category, CategorySource.MANUAL
            typed = TT.for_category(category, amount, account.kind, TypeSource.OWNER, "1", "owner_category")
        if body.transaction_type is not None:
            typed = TT.Typed(body.transaction_type, TypeSource.OWNER, Decimal(1), "owner_type")
            if body.category is None:
                category, source = TT.default_category(body.transaction_type, category, amount), CategorySource.MANUAL
        now = utc_now()
        item = Transaction(
            id=transaction_id or new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, posted_on=body.posted_on, amount=amount, currency=account.currency, description=description,
            category=category, category_source=source, notes=_clean(body.notes, 1000), import_id=None,
            # A hand-entered row is never a duplicate of another.
            dedupe_hash=csv_import.dedupe_hash(account.id, body.posted_on, amount, account.currency, f"manual {new_id('m')}", 0),
            created_at=now, updated_at=now,
        )
        TT.apply(item, typed)
        refunds.link_refund(store, context, item, account.kind)
        store.save_transaction(item)
        # The canonical transaction: hand-entered money has its journal entry like every other source.
        entry = record_plain(sms, context, item, account, EntrySource.MANUAL, now=now)
        decision = check_record(store, context, item, utc_now())
    return item, decision, (entry.canonical_id or entry.id) if entry is not None else item.id


@router.post("/transactions", response_model=TransactionView, status_code=status.HTTP_201_CREATED)
def create_transaction(
    body: TransactionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TransactionView:
    _manage(context)
    item, decision, _canonical = record_manual_transaction(context, body)
    _audit(repository, context, "finance.transaction.create", item.id, _guardian_meta(decision))
    return TransactionView(**_txn_view(item), guardian=decision)


@router.get("/transactions/{transaction_id}", response_model=TransactionView)
def get_transaction(transaction_id: str, context: TenantContext = Depends(tenant_context)) -> TransactionView:
    _read(context)
    with open_finance_store(context) as store:
        found = _own_transaction(store, context, transaction_id)
        owning = store.get_account(context, found.account_id)
        return TransactionView(**_txn_view(found, owning.kind if owning is not None else None))


@router.patch("/transactions/{transaction_id}", response_model=TransactionView)
def update_transaction(
    transaction_id: str,
    body: TransactionPatch,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TransactionView:
    _manage(context)
    changes = body.model_dump(exclude_unset=True)
    # Learning is opt-in, tied to a manually chosen category, and cannot match
    # unrelated merchants. Never infer a new category or money direction.
    match = " ".join((body.remember_category_match or "").split()).casefold()
    if match:
        if body.category is None or len(match) < 4 or not any(ch.isalpha() for ch in match):
            raise _invalid("Choose a category and a specific merchant phrase (at least 4 characters) to remember.")
        if match in {"rent", "food", "transfer", "bank", "payment", "card", "cash", "salary", "room", "debit", "credit"}:
            raise _invalid("The learning phrase is too broad. Choose a specific merchant or description.")
    from .ledger import EntrySource
    from .sms_service import owner_lock, repost_plain
    from .sms_store import open_sms_stores

    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        item = _own_transaction(store, context, transaction_id)
        entry = sms.entry_for_transaction(context, transaction_id)
        plain = entry is not None and entry.source in (EntrySource.MANUAL, EntrySource.STATEMENT)
        if entry is not None and not plain and {"amount", "posted_on", "category", "transaction_type"} & set(changes):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=(
                "An iPhone SMS or Apple Pay recorded this transaction. Correct its amount, date or category under Finance, iPhone SMS, "
                "Review, so the ledger records the change as a correction."))
        if match and match not in item.description.casefold():
            raise _invalid("The learning phrase must appear in this transaction description.")
        owning = store.get_account(context, item.account_id)
        kind = owning.kind if owning is not None else None
        TT.fill(item, kind)  # a row stored before types existed gets its type now (the backfill, docs/finance.md#transaction-types)
        before = (item.posted_on, item.amount, item.category, item.transaction_type)
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
            if body.transaction_type is None:
                TT.apply(item, TT.for_category(body.category, item.amount, kind, TypeSource.OWNER, "1", "owner_category"))
        elif not TT.owner_locked(item) and ({"description", "amount"} & set(changes)) and (entry is None or plain):
            found = classify_text(item.description, item.amount, settings_for(store, context).category_rules, kind)
            item.category, item.category_source = found.category, found.source
            TT.apply(item, found.typed)
        if body.transaction_type is not None:
            TT.apply(item, TT.Typed(body.transaction_type, TypeSource.OWNER, Decimal(1), "owner_type"))
            if body.category is None:
                item.category, item.category_source = TT.default_category(body.transaction_type, item.category, item.amount), CategorySource.MANUAL
        item.updated_at = utc_now()
        if match:
            settings = settings_for(store, context)
            existing = next((r for r in settings.category_rules if r.match.casefold() == match), None)
            if existing:
                existing.category = body.category
            else:
                if len(settings.category_rules) >= 100:
                    raise _invalid("You have reached 100 learned category rules. Remove an old rule under Finance Settings.")
                settings.category_rules.insert(0, CategoryRule(match=match, category=body.category))
            settings.updated_at = item.updated_at
            store.save_settings(context, settings)
        store.save_transaction(item)
        if plain and entry is not None and (item.posted_on, item.amount, item.category, item.transaction_type) != before:
            # A correction reverses the entry and reposts it in the same canonical chain; the row keeps its id.
            repost_plain(store, sms, context, entry, item, now=item.updated_at)
        decision = check_record(store, context, item, utc_now())
    _audit(repository, context, "finance.transaction.update", transaction_id, {"fields": sorted(changes), "learned_category": bool(match), **_guardian_meta(decision)})
    return TransactionView(**_txn_view(item, kind), guardian=decision)


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    _manage(context)
    from .sms_service import owner_lock, reverse
    from .sms_store import open_sms_stores

    # A transaction an SMS recorded is part of a journal entry: deleting it reverses the whole
    # entry (docs/finance-sms.md#ledger), so the journal keeps the record and stays balanced. The owner's
    # lock is taken as create and update take it, so a message being posted cannot race the reversal.
    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        entry = sms.entry_for_transaction(context, transaction_id)
        if entry is not None:
            reverse(store, sms, context, entry, now=utc_now(), why="transaction deleted by the owner")
        elif not store.delete_transaction(context, transaction_id):
            raise _not_found("Transaction")
    _audit(repository, context, "finance.transaction.delete", transaction_id, {"reversed_entry": entry.id} if entry else None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- imports ---------------------------------------------------------------------------------


class RowDecision(BaseModel):
    """The person's choice for one previewed row (by its line); anything not listed follows the preview's default."""

    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1, le=1_000_000)
    action: Literal["import", "skip"] | None = None  # import a duplicate or a possible duplicate anyway, or leave a row out
    bill: bool | None = None  # False: do not record the row as the bill's payment
    transfer: bool | None = None  # True: pair a weak transfer; False: do not pair
    correct: bool | None = None  # False: keep the recorded amount when the bank billed another


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(default="statement.csv", min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=csv_import.MAX_BYTES, description="The CSV file's text")
    preset: str | None = Field(default=None, max_length=40)
    date_format: str | None = Field(default=None, max_length=20)
    mapping: ColumnMapping | None = None
    decisions: list[RowDecision] = Field(default_factory=list, max_length=csv_import.MAX_ROWS)
    opening_balance: Decimal | None = Field(default=None, description="The statement's opening balance, when it prints one")
    closing_balance: Decimal | None = Field(default=None, description="The statement's closing balance, when it prints one")

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
    direction_warning: str | None = None
    notes: str | None = None
    original_amount: Decimal | None = None
    original_currency: str | None = None
    reported_fee: Decimal | None = None
    balance: Decimal | None = None
    reference: str | None = None
    match: SI.RowMatchView
    fx: SI.FxView | None = None


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
    counts: SI.MatchCounts
    reconciliation: SI.ReconciliationView


class ImportView(BaseModel):
    reconciled: int = 0  # rows that matched a payment already recorded (SMS, Apple Pay, by hand): attached, not added
    held_for_review: int = 0  # rows that could match more than one recorded payment: in Finance, iPhone SMS, Review
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
    # Stage 4: imported rows that break one of your Guardian rules (see Guardian, alerts).
    rule_breaks: int = 0
    corrected: int = 0  # records corrected to the bank-billed amount
    skipped: int = 0  # rows the person left out
    bills_paid: int = 0
    bills_review: int = 0  # possible bill payments waiting for the person under Bills
    transfers: int = 0  # own transfers paired
    reconciliation: dict[str, str | None] | None = None
    dropped: int = 0  # rows the reader could not use (unreadable, outside the statement period): listed on the import
    # Statement rows the ledger does not hold (held for review or left out by the person). A statement whose balances tie
    # reconciles the account only when this is 0 (docs/finance.md#reconciliation).
    not_in_ledger: int = 0


def import_view(record: ImportRecord, *, breaks: int = 0, outcome: ImportOutcome | None = None) -> ImportView:
    """An import as the API shows it: the counts stored with it, plus what this request linked afterwards."""
    data = _view(record)
    check = record.reconciliation or {}
    extra: dict[str, Any] = {"reconciled": record.attached_count, "held_for_review": record.held_count, "corrected": record.corrected_count,
                             "skipped": record.skipped_count, "dropped": record.dropped_count,
                             "not_in_ledger": int(check.get("not_in_ledger") or 0), "rule_breaks": breaks}
    if outcome is not None:
        extra.update(bills_paid=outcome.bills_paid, bills_review=outcome.bills_review, transfers=outcome.transfers)
    return ImportView(**{**data, **extra})


DISPOSITIONS = ("imported", "held_for_review", "attached_to_existing", "duplicate_skipped", "unticked", "dropped_by_reader")
# In the ledger: imported, attached to the record it confirms, or already recorded. Not in the ledger: the rest.
IN_LEDGER = frozenset({"imported", "attached_to_existing", "duplicate_skipped"})


class RowDisposition(BaseModel):
    """What the import did with one statement row (docs/finance.md#import-history)."""

    line: int
    posted_on: date | None = None
    amount: Decimal | None = None
    description: str = ""
    disposition: Literal["imported", "held_for_review", "attached_to_existing", "duplicate_skipped", "unticked", "dropped_by_reader"]
    # held_for_review: two_candidates, amount_date_only or waiting_review; duplicate_skipped: imported_before,
    # reconciled_before, same_statement_row or already_in_ledger; dropped_by_reader: unreadable, outside_period or
    # unattached_line, with the reader's message.
    reason: str | None = None
    message: str | None = None
    record_id: str | None = None  # the transaction or canonical record the row was imported as, attached to, or duplicates


class ImportDetail(ImportView):
    rows: list[RowDisposition]
    rows_total: int
    limit: int
    offset: int


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


def csv_reconciliation(parsed: csv_import.ParsedFile, currency: str, opening: Decimal | None, closing: Decimal | None) -> SI.ReconciliationView:
    try:
        opening = quantize(opening, currency) if opening is not None else None
        closing = quantize(closing, currency) if closing is not None else None
    except MoneyError as exc:
        raise _invalid(str(exc)) from None
    items = SI.chronological([(row.line, row.posted_on, row.amount, row.balance) for row in parsed.rows])
    return SI.reconcile(items, opening, closing, currency)


def statement_lines(parsed: csv_import.ParsedFile, references: dict[str, str] | None = None, times: dict[str, Any] | None = None) -> list[Any]:
    from .statement_match import Line

    return [Line(key=row.line, posted_on=row.posted_on, amount=row.amount, description=row.description, dedupe_hash=row.dedupe_hash,
                 reference=(references or {}).get(row.dedupe_hash) or row.reference, original_amount=row.original_amount,
                 original_currency=row.original_currency, at=(times or {}).get(row.dedupe_hash)) for row in parsed.rows]


def preview_matches(context: TenantContext, account: Account, lines: list[Any]) -> list[Any]:
    """The engine's answer for a preview, read in one short unit of work (nothing is written)."""
    from . import statement_ledger as SL
    from .links_store import open_link_stores

    with open_link_stores(context) as (finance, sms, links):
        ledger = SL.gather(finance, sms, links, context, account, [line.posted_on for line in lines])
    return SL.run(ledger, lines, file_hashes={line.dedupe_hash for line in lines})


@router.post("/imports/preview", response_model=ImportPreview)
def preview_import(body: ImportInput, context: TenantContext = Depends(tenant_context)) -> ImportPreview:
    """Read a statement and show what an import would do, row by row. Nothing is saved."""
    _manage(context)
    with open_finance_store(context) as store:
        account, parsed, _existing = _parse(store, context, body)
        rules = settings_for(store, context).category_rules
    matches = preview_matches(context, account, statement_lines(parsed))
    by_line = {m.line.key: m for m in matches}
    tally = SI.counts(matches)
    return ImportPreview(
        account_id=account.id, currency=account.currency, filename=_clean(body.filename, 200) or "statement.csv",
        preset=parsed.preset, headers=parsed.headers, mapping=parsed.mapping, date_format=parsed.date_format,
        date_formats=parsed.date_formats, date_format_ambiguous=parsed.date_format_ambiguous, row_count=parsed.row_count,
        valid_count=len(parsed.rows), new_count=tally.new, duplicate_count=tally.exact_duplicate,
        error_count=len(parsed.errors),
        rows=[
            PreviewRow(
                line=row.line, posted_on=row.posted_on, amount=row.amount, currency=account.currency, description=row.description,
                category=categorize(row.description, row.amount, rules, account.kind)[0], duplicate=by_line[row.line].status == "exact_duplicate",
                direction_warning=suspected_direction(row.description, row.amount),
                notes=row.notes, original_amount=row.original_amount, original_currency=row.original_currency,
                reported_fee=row.fee, balance=row.balance, reference=row.reference, match=SI.match_view(by_line[row.line]),
                fx=SI.fx_view(row.description, row.amount, account.currency, original_amount=row.original_amount,
                              original_currency=row.original_currency, rate=row.fx_rate, fee=row.fee, fee_currency=row.fee_currency),
            )
            for row in parsed.rows
        ],
        errors=[PreviewError(line=e.line, message=e.message) for e in parsed.errors[: csv_import.MAX_ERRORS_SHOWN]],
        counts=tally, reconciliation=csv_reconciliation(parsed, account.currency, body.opening_balance, body.closing_balance),
    )


@router.post("/imports", response_model=ImportView, status_code=status.HTTP_201_CREATED)
def confirm_import(
    body: ImportInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ImportView:
    """Import the statement previewed with the same choices; the matching engine runs again on the live ledger."""
    _manage(context)
    checks: dict[str, Any] = {}

    def build(store: FinanceStore, account: Account) -> csv_import.ParsedFile:
        parsed = _parse(store, context, body)[1]
        checks["reconciliation"] = SI.stored(csv_reconciliation(parsed, account.currency, body.opening_balance, body.closing_balance))
        return parsed

    record, rows_added, breaks, outcome = record_import(context, body.account_id, _clean(body.filename, 200) or "statement.csv", build,
                                                        decisions={d.line: d for d in body.decisions}, checks=checks)
    _audit(repository, context, "finance.import.create", record.id, outcome.audit(rows_added, record.duplicate_count, breaks))
    return outcome.view(record, breaks)


class ImportOutcome:
    """What one import did, beyond the rows it added."""

    def __init__(self) -> None:
        self.attached = self.review = self.corrected = self.skipped = self.dropped = 0
        self.bills_paid = self.bills_review = self.transfers = 0
        self.failed: list[str] = []

    def audit(self, imported: int, duplicates: int, breaks: int) -> dict[str, Any]:
        return {"imported": imported, "duplicates": duplicates, "rule_breaks": breaks, "reconciled": self.attached, "review": self.review,
                "corrected": self.corrected, "skipped": self.skipped, "dropped": self.dropped, "bills_paid": self.bills_paid,
                "bills_review": self.bills_review, "transfers": self.transfers, "link_failures": sorted(set(self.failed))}

    def view(self, record: ImportRecord, breaks: int) -> ImportView:
        return import_view(record, breaks=breaks, outcome=self)


def _disposition(line: int, row: Any, disposition: str, *, reason: str | None = None, record_id: str | None = None,
                 message: str | None = None) -> dict[str, Any]:
    posted = getattr(row, "posted_on", None)
    amount = getattr(row, "amount", None)
    return {"line": line, "posted_on": posted.isoformat() if posted else None, "amount": str(amount) if amount is not None else None,
            "description": (getattr(row, "description", "") or "")[:200], "disposition": disposition, "reason": reason,
            "message": (message or None) and message[:200], "record_id": record_id}


def record_import(
    context: TenantContext,
    account_id: str,
    filename: str,
    build: Any,
    *,
    references: dict[str, str] | None = None,
    times: dict[str, Any] | None = None,
    decisions: dict[int, Any] | None = None,
    checks: dict[str, Any] | None = None,
    dropped: list[dict[str, Any]] | None = None,
) -> tuple[ImportRecord, int, int, ImportOutcome]:
    """The one import path for every statement format (CSV and PDF), under the owner's lock: the matching engine
    (docs/finance.md#statement-matching) runs again on the live ledger, then each row follows its status and the
    person's choice (``decisions`` by line): already recorded rows are skipped, rows that confirm an SMS, Apple Pay or
    hand-entered record are attached to it (and its amount corrected to the bank-billed one), possible duplicates wait
    in Review unless ticked, the rest are journalled as ``statement`` entries. Own transfers are categorized
    ``transfer`` on both sides. After the unit of work, bill payments and transfer pairs are recorded as finance links.
    ``build(store, account)`` returns the parsed rows (and may put the balance check in ``checks``).

    Every row's disposition is stored on the import with its counts (docs/finance.md#import-history): rows the person
    left out (``skip``) are not matched at all, so they never take a record another row needs; ``dropped`` lists the rows
    the reader left out (a PDF's rows outside the period), the CSV's unreadable rows are added from the parse. A statement
    whose balances tie but whose rows are not all in the ledger says how many are not (``not_in_ledger``)."""
    from . import statement_ledger as SL
    from .commitments import bill_target_id
    from .ledger import EntrySource
    from .links_store import open_link_stores
    from .sms_service import owner_lock, record_plain

    decisions = decisions or {}
    outcome = ImportOutcome()
    jobs: list[SL.LinkJob] = []
    with owner_lock(context), open_link_stores(context) as (store, sms, links):
        sms.lock_owner(context)
        account = _own_account(store, context, account_id)
        parsed: csv_import.ParsedFile = build(store, account)
        if not parsed.rows:
            raise _invalid("No row of this file could be read. Check the column mapping and the date format.")
        lines = statement_lines(parsed, references, times)
        left_out = {line.key for line in lines if getattr(decisions.get(line.key), "action", None) == "skip"}
        wanted = [line for line in lines if line.key not in left_out]
        ledger = SL.gather(store, sms, links, context, account, [line.posted_on for line in (wanted or lines)])
        found = {m.line.key: m for m in SL.run(ledger, wanted, file_hashes={line.dedupe_hash for line in lines})}
        rules = settings_for(store, context).category_rules
        now = utc_now()
        record = ImportRecord(
            id=new_id("fin_imp"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, filename=filename, preset=parsed.preset,
            date_format=parsed.date_format, row_count=parsed.row_count, imported_count=0,
            duplicate_count=0, error_count=len(parsed.errors), file_sha256=parsed.file_sha256,
            created_at=now, reconciliation=(checks or {}).get("reconciliation"),
        )
        store.save_import(record)
        by_line = {row.line: row for row in parsed.rows}
        by_hash_line = {row.dedupe_hash: row.line for row in parsed.rows}
        fresh: list[Transaction] = []
        follow: list[tuple[Transaction | None, str | None, Any]] = []  # (new row, canonical of an attached record, match)
        rows_done: dict[int, dict[str, Any]] = {}
        for line in lines:
            row = by_line[line.key]
            if line.key in left_out:
                outcome.skipped += 1
                rows_done[row.line] = _disposition(row.line, row, "unticked")
                continue
            match = found[line.key]
            choice = decisions.get(row.line)
            action = getattr(choice, "action", None)
            reference = (references or {}).get(row.dedupe_hash) or row.reference
            classified = classify_text(row.description, row.amount, rules, account.kind)
            item = TT.apply(Transaction(
                id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                owner_user_id=context.user_id, account_id=account.id, posted_on=row.posted_on, amount=row.amount,
                currency=account.currency, description=row.description, category=classified.category, category_source=classified.source,
                notes=row.notes, import_id=record.id, dedupe_hash=row.dedupe_hash, created_at=now, updated_at=now,
            ), classified.typed)
            if match.status == "exact_duplicate" and match.code == "waiting_review":
                # The same row already waits in Review from an earlier import: not in the ledger yet.
                outcome.review += 1
                rows_done[row.line] = _disposition(row.line, row, "held_for_review", reason="waiting_review",
                                                   record_id=match.waiting.message_id if match.waiting else None)
                continue
            if match.status == "exact_duplicate" and not (action == "import" and match.code == "same_statement_row"):
                record.duplicate_count += 1
                rows_done[row.line] = _disposition(row.line, row, "duplicate_skipped", reason=match.code, record_id=_record_id(match.record))
                continue
            if match.status == "possible_duplicate" and action != "import":
                SL.hold(sms, context, account, item, record.id, now=now, reference=reference)
                outcome.review += 1
                rows_done[row.line] = _disposition(row.line, row, "held_for_review", reason=match.code,
                                                   record_id=_record_id(match.candidates[0]) if match.candidates else None)
                continue
            if match.status == "matched_existing" and action != "import" and match.record is not None:
                matched = match.record
                entry_id = matched.entry_id
                if match.amount_difference is not None and getattr(choice, "correct", None) is not False:
                    original = None
                    if match.line.original_amount and match.line.original_currency:
                        original = (match.line.original_amount, match.line.original_currency)
                    corrected = SL.correct_amount(store, sms, context, account, matched, row.amount, now=now, original=original)
                    if corrected is not None or matched.entry_id is None:
                        outcome.corrected += 1
                        entry_id = corrected or entry_id
                if entry_id is not None:
                    SL.attach(sms, context, account, item, entry_id, record.id, now=now, reference=reference)
                outcome.attached += 1
                rows_done[row.line] = _disposition(row.line, row, "attached_to_existing", reason=match.code, record_id=_record_id(matched))
                follow.append((None, matched.canonical_id, match))
                continue
            if _wants_transfer(match, choice):
                SL.mark_transfer(item, account.kind, by_owner=getattr(choice, "transfer", None) is True)
            fresh.append(item)
            rows_done[row.line] = _disposition(row.line, row, "imported", record_id=item.id)
            follow.append((item, None, match))
        refunds.link_refunds(store, context, fresh, account.kind)
        inserted = store.insert_transactions(fresh)
        added = len(inserted)
        for item in fresh:
            if item.id not in inserted:
                # Stored meanwhile by another import of the same row (ON CONFLICT DO NOTHING): no journal entry for it.
                record.duplicate_count += 1
                rows_done[by_hash_line[item.dedupe_hash]] = _disposition(by_hash_line[item.dedupe_hash], item, "duplicate_skipped",
                                                                         reason="already_in_ledger")
        follow = [(item, attached, match) for item, attached, match in follow if item is None or item.id in inserted]
        fresh = [item for item in fresh if item.id in inserted]
        canonical: dict[str, str] = {}
        originals = {row.dedupe_hash: (row.original_amount, row.original_currency) for row in parsed.rows
                     if row.original_amount is not None and row.original_currency and row.original_currency != account.currency}
        for item in fresh:
            entry = record_plain(sms, context, item, account, EntrySource.STATEMENT, now=now, original=originals.get(item.dedupe_hash))
            if entry is not None:
                canonical[item.id] = entry.canonical_id or entry.id
        for item, attached_canonical, match in follow:
            choice = decisions.get(match.line.key)
            mine = canonical.get(item.id) if item is not None else attached_canonical
            if mine is None:
                continue
            if _wants_transfer(match, choice):
                other = match.transfer.other
                if item is None and match.record is not None:
                    SL.recategorize_transfer(store, sms, context, match.record, now=now)
                SL.recategorize_transfer(store, sms, context, other, now=now)
                theirs = other.canonical_id
                if other.transaction_id and theirs is None:
                    theirs = SL.canonical_for(sms, context, other.transaction_id)
                if theirs is not None:
                    out_id, in_id = (mine, theirs) if match.line.amount < 0 else (theirs, mine)
                    out_amount = abs(match.line.amount) if match.line.amount < 0 else abs(other.amount)
                    out_currency = account.currency if match.line.amount < 0 else other.currency
                    jobs.append(SL.LinkJob("transfer", out_id, in_id, out_amount, out_currency))
            if match.bill is not None and getattr(choice, "bill", None) is not False:
                jobs.append(SL.LinkJob("bill", mine, bill_target_id(match.bill.bill.bill_id, match.bill.bill.due_on), abs(match.line.amount),
                                       account.currency, review=match.bill.strength != "strong"))
        record.imported_count = added
        for error in parsed.errors:
            rows_done.setdefault(-error.line, _disposition(error.line, None, "dropped_by_reader", reason="unreadable", message=error.message))
        for number, item in enumerate(dropped or [], start=1):
            rows_done[-1_000_000 - number] = {**_disposition(0, None, "dropped_by_reader", reason=item.get("reason"),
                                                             message=item.get("message")), **{k: item.get(k) for k in ("posted_on", "amount", "description")}}
        outcome.dropped = len(parsed.errors) + len(dropped or [])
        record.attached_count, record.held_count, record.skipped_count = outcome.attached, outcome.review, outcome.skipped
        record.dropped_count, record.corrected_count = outcome.dropped, outcome.corrected
        record.dispositions = [rows_done[key] for key in sorted(rows_done, key=lambda k: (k < 0, abs(k)))]
        missing = sum(1 for item in record.dispositions if item["disposition"] in ("held_for_review", "unticked"))
        if record.reconciliation is not None:
            # Balances that tie say the statement adds up; the account is reconciled only when the ledger holds every row.
            record.reconciliation = {**record.reconciliation, "not_in_ledger": str(missing)}
        store.save_import(record)
        breaks = _import_rule_breaks(store, context, fresh)
    made = SL.make_links(context, jobs)
    outcome.bills_paid, outcome.bills_review, outcome.transfers, outcome.failed = made.bills_paid, made.bills_review, made.transfers, made.failed
    return record, added, breaks, outcome


def _record_id(record: Any) -> str | None:
    if record is None:
        return None
    return record.transaction_id or record.canonical_id or record.record_id


def _wants_transfer(match: Any, choice: Any) -> bool:
    """A strong transfer pair unless the person unticked it; a weak one only when they ticked it."""
    if match.transfer is None:
        return False
    wanted = getattr(choice, "transfer", None)
    return wanted is not False if match.transfer.strength == "strong" else wanted is True


def _import_rule_breaks(store: FinanceStore, context: TenantContext, rows: list[Transaction]) -> int:
    """How many imported rows break a rule: blocked merchants and categories, single limits."""
    from .guardian import merchant_matches
    from .policy import current_rules

    rules, _ = current_rules(store, context)
    rules = rules.active()
    if rules.is_empty():
        return 0
    singles = {limit.currency: limit.amount for limit in rules.max_single}
    count = 0
    for t in rows:
        if t.amount >= 0:
            continue
        if (
            t.category in rules.blocked_categories
            or any(merchant_matches(p, t.description) for p in rules.blocked_merchants)
            or (t.currency in singles and -t.amount > singles[t.currency])
        ):
            count += 1
    return count


@router.get("/imports", response_model=ImportPage)
def list_imports(
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> ImportPage:
    _read(context)
    with open_finance_store(context) as store:
        items, total = store.list_imports(context, limit=limit, offset=offset)
    return ImportPage(items=[import_view(i) for i in items], total=total, limit=limit, offset=offset)


@router.get("/imports/{import_id}", response_model=ImportDetail)
def get_import(
    import_id: str,
    disposition: Literal["imported", "held_for_review", "attached_to_existing", "duplicate_skipped", "unticked", "dropped_by_reader",
                         "not_in_ledger"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> ImportDetail:
    """One import with what happened to each of its rows, filtered by disposition (``not_in_ledger``: held or left out)."""
    _read(context)
    with open_finance_store(context) as store:
        record = store.get_import(context, import_id)
    if record is None:
        raise _not_found("Import")
    rows = record.dispositions or []
    if disposition == "not_in_ledger":
        rows = [r for r in rows if r.get("disposition") in ("held_for_review", "unticked")]
    elif disposition is not None:
        rows = [r for r in rows if r.get("disposition") == disposition]
    page = [RowDisposition(**r) for r in rows[offset: offset + limit]]
    return ImportDetail(**import_view(record).model_dump(), rows=page, rows_total=len(rows), limit=limit, offset=offset)


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(
    import_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Undo an import: its transactions go with it."""
    _manage(context)
    from .sms_service import owner_lock, reverse_for_transactions
    from .sms_store import open_sms_stores

    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        record = store.get_import(context, import_id)
        if record is not None:
            rows, _ = store.list_transactions(context, TransactionFilter(account_id=record.account_id), limit=10_000, offset=0)
            reverse_for_transactions(store, sms, context, [t.id for t in rows if t.import_id == import_id], now=utc_now(), why="statement import undone")
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
    # What was owed when you started tracking it. Left out: kept as it is; "" clears it.
    original_balance: Amount | None = None
    notes: str | None = Field(default=None, max_length=1000)


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    target_amount: Amount
    saved_amount: Amount = "0"
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    target_on: date | None = None
    account_id: str | None = Field(default=None, max_length=80)
    # Stage 3: what you put aside each month (safe-to-spend sets it aside). Empty: worked
    # out from the target date, or nothing.
    monthly_contribution: Amount | None = None
    # Left out: kept as it is (a new goal is not paused).
    paused: bool | None = None
    # What the goal is for. Left out: kept as it is (a new goal is "general").
    purpose: GoalPurpose | None = None
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
    original_balance: Decimal | None = None
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
    monthly_contribution: Decimal | None = None
    paused: bool = False
    purpose: GoalPurpose = GoalPurpose.GENERAL
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


def _original_balance(body: DebtInput, currency: str, existing: Debt | None) -> Decimal | None:
    if body.original_balance is None:
        return existing.original_balance if existing and existing.currency == currency else None
    if str(body.original_balance).strip() == "":
        return None
    return _money(body.original_balance, currency, field="Starting balance", positive=True)


def _build_debt(store: FinanceStore, context: TenantContext, body: DebtInput, existing: Debt | None) -> Debt:
    account = _linked_account(store, context, body.account_id)
    currency = _currency(body.currency or (account.currency if account else None), settings_for(store, context).base_currency)
    balance = _money(body.balance, currency, field="Balance", positive=True)
    # ``updated_at`` is when what is owed was last entered: the payments matched after it are taken off the balance
    # (obligations.py). A name, rate, minimum or due-day edit keeps it, so earlier matched payments still count.
    same_owed = existing is not None and existing.balance == balance and existing.currency == currency and existing.account_id == (
        account.id if account else None)
    now = existing.updated_at if same_owed and existing is not None else utc_now()
    return Debt(
        id=existing.id if existing else new_id("fin_debt"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, name=_clean(body.name, 120) or "Debt", kind=body.kind,
        balance=balance, currency=currency, apr_percent=_apr(body.apr_percent),
        minimum_payment=_money(body.minimum_payment, currency, field="Minimum payment", positive=True), due_day=body.due_day,
        account_id=account.id if account else None, original_balance=_original_balance(body, currency, existing),
        notes=_clean(body.notes, 1000),
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
        target_on=body.target_on, account_id=account.id if account else None,
        monthly_contribution=(
            _money(body.monthly_contribution, currency, field="Monthly amount", positive=True)
            if body.monthly_contribution not in (None, "") else None
        ),
        paused=body.paused if body.paused is not None else (existing.paused if existing else False),
        purpose=body.purpose or (existing.purpose if existing else GoalPurpose.GENERAL),
        notes=_clean(body.notes, 1000), created_at=existing.created_at if existing else now, updated_at=now,
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
    links = obligations.load(context)
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    return upcoming_bills(bills, today, days, links=links.bill_links)


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
