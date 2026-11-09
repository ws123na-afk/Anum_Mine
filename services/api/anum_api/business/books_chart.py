"""The chart of accounts of a business (docs/business-books.md#chart-of-accounts).

Each business gets a standard small-business chart the first time its books are set up (or when the
posting run finds it missing). The accounts the posting rules use carry a ``system_key``; an owner or
an accountant may rename them or change their code within the same range, never archive them. Other
accounts can be added, renamed, archived and bound to an expense or revenue category (a bill or an
allocation of that category then posts to that account instead of the default one).

Codes are four to six digits whose first digit matches the kind: 1 assets, 2 liabilities, 3 equity,
4 revenue and other income, 5 cost of sales, 6 operating expenses, 7 exchange differences and finance
costs. A code is unique per business.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..schemas import TenantContext, new_id
from .books_models import SUBTYPES, AccountType, LedgerAccount
from .books_store import MAX_SCAN, BooksStore, DuplicateError

CODE = re.compile(r"^[1-7][0-9]{3,5}$")

# (system key, code, name, subtype): the standard chart. Names are in English; the web shows them translated by key.
STANDARD: tuple[tuple[str, str, str, str], ...] = (
    ("bank", "1000", "Bank and cash", "bank"),
    ("ar", "1100", "Accounts receivable", "receivable"),
    ("ic_receivable", "1150", "Owed by related businesses", "receivable"),
    ("tax_receivable", "1200", "VAT receivable (input tax)", "tax_receivable"),
    ("prepayments", "1300", "Prepayments", "prepayment"),
    ("fixed_assets", "1500", "Fixed assets at cost", "fixed_asset"),
    ("accumulated_depreciation", "1510", "Accumulated depreciation", "accumulated_depreciation"),
    ("ap", "2000", "Accounts payable", "payable"),
    ("ic_payable", "2050", "Owed to related businesses", "payable"),
    ("tax_payable", "2100", "VAT payable (output tax)", "tax_payable"),
    ("accruals", "2200", "Accruals", "accrual"),
    ("interest_payable", "2250", "Interest payable", "interest_payable"),
    ("loans", "2300", "Loans", "loan"),
    ("owed_to_members", "2400", "Owed to members", "owed_to_members"),
    ("capital", "3000", "Owner capital", "capital"),
    ("drawings", "3100", "Owner drawings", "drawings"),
    ("retained_earnings", "3200", "Retained earnings", "retained_earnings"),
    ("current_result", "3300", "Current year result", "current_result"),
    ("revenue_service", "4000", "Revenue: services", "revenue"),
    ("revenue_labor", "4010", "Revenue: labour", "revenue"),
    ("revenue_external_cost", "4020", "Revenue: passed-on costs", "revenue"),
    ("revenue_other", "4090", "Revenue: other", "revenue"),
    ("other_income", "4500", "Other income", "other_income"),
    ("disposal_gain", "4600", "Gains on disposal of assets", "other_income"),
    ("credit_notes", "4800", "Credit notes", "revenue"),
    ("cost_of_sales", "5000", "Cost of sales", "cost_of_sales"),
    ("operating", "6000", "Operating expenses", "operating_expense"),
    ("depreciation", "6100", "Depreciation", "depreciation"),
    ("impairment", "6150", "Impairment losses", "operating_expense"),
    ("disposal_loss", "6160", "Losses on disposal of assets", "operating_expense"),
    ("fx", "7000", "Exchange gains and losses", "fx"),
    ("fx_unrealised", "7010", "Unrealised exchange gains and losses", "fx"),
    ("finance_costs", "7100", "Finance costs and interest", "finance_cost"),
)
SYSTEM_KEYS = frozenset(key for key, _, _, _ in STANDARD)
REVENUE_KEYS = {"service": "revenue_service", "labor": "revenue_labor", "external_cost": "revenue_external_cost", "other": "revenue_other"}


class ChartError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        # The plain sentence shown to the person; only ever text this module writes.
        self.message = message
        self.code = code


def check_code(code: str, subtype: str) -> str:
    text = (code or "").strip()
    if not CODE.match(text):
        raise ChartError("An account code is four to six digits, for example 6200.", "invalid_code")
    if subtype not in SUBTYPES:
        raise ChartError("Choose what kind of account it is.", "invalid_subtype")
    digit = SUBTYPES[subtype][1]
    if text[0] != digit:
        raise ChartError(f"Codes of this kind of account start with {digit}.", "code_range")
    return text


def category_allowed(subtype: str) -> bool:
    """Only expense and revenue accounts take a category."""
    return SUBTYPES[subtype][0] in (AccountType.EXPENSE, AccountType.REVENUE) and subtype not in ("depreciation", "fx")


def accounts(store: BooksStore) -> list[LedgerAccount]:
    rows, _ = store.find("ledger_accounts", order_by="code", descending=False, limit=MAX_SCAN)
    return rows


def ensure_chart(store: BooksStore, context: TenantContext, now: datetime, actor: str) -> list[LedgerAccount]:
    """Every standard account of this business, created when missing (idempotent: one per system key). Returns the chart."""
    found = accounts(store)
    keys = {row.system_key for row in found if row.system_key}
    codes = {row.code for row in found}
    for key, code, name, subtype in STANDARD:
        if key in keys:
            continue
        free = code
        while free in codes:  # an owner already used the standard code for an account of their own: take the next free one
            free = str(int(free) + 1)
        account = LedgerAccount(id=new_id("bacc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, code=free,
                                name=name, subtype=subtype, system_key=key, created_by=actor, updated_at=now)
        try:
            store.add("ledger_accounts", account)
        except DuplicateError:
            continue  # another run made it at the same time
        found.append(account)
        codes.add(free)
    return sorted(found, key=lambda row: row.code)


__all__ = ["REVENUE_KEYS", "STANDARD", "SYSTEM_KEYS", "ChartError", "accounts", "category_allowed", "check_code", "ensure_chart"]
