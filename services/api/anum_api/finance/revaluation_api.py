"""``GET /api/v1/finance/fx-revaluation``: personal foreign-currency balances in the base currency (docs/finance.md#foreign-currency-balances).

Read only (``finance:read``), the caller's own accounts and stored rates. Never posted, never counted as spending or income.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..dependencies import tenant_context
from ..schemas import TenantContext
from . import revaluation as R
from .api import _not_found, _read
from .store import TransactionFilter, open_finance_store
from .summary import account_balances, settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

MAX_ROWS = 20_000
NOTE = ("Display only: what your foreign balances are worth in your base currency at the newest stored rate, against what they were "
        "worth on the days the money came in. Never posted, never counted as spending or income. A missing rate is never guessed.")


class RevaluationRow(BaseModel):
    account_id: str
    name: str
    currency: str
    base_currency: str
    balance: Decimal
    status: Literal["ok", "rate_not_available", "cost_rate_not_available", "nothing_to_revalue", "history_too_long"]
    value: Decimal | None
    rate: Decimal | None
    rate_as_of: date | None
    rate_source: str | None
    cost: Decimal | None
    unrealised: Decimal | None
    missing_rate_dates: list[date]


class RevaluationPage(BaseModel):
    base_currency: str
    as_of: date
    rate_max_age_days: int
    items: list[RevaluationRow]
    # Per base currency (there is one): the sum of the unrealised figures that could be worked out, and how many could not.
    unrealised_total: Decimal | None
    incomplete: int
    truncated: bool
    note: str = NOTE


@router.get("/fx-revaluation", response_model=RevaluationPage)
def fx_revaluation(account_id: str | None = Query(default=None, max_length=80), context: TenantContext = Depends(tenant_context)) -> RevaluationPage:
    _read(context)
    with open_finance_store(context) as store:
        settings = settings_for(store, context)
        today = today_in(settings)
        plan = store.get_plan(context)
        max_age = plan.fx_max_age_days if plan is not None else 7
        accounts = store.list_accounts(context)
        if account_id is not None:
            accounts = [a for a in accounts if a.id == account_id]
            if not accounts:
                raise _not_found("Account")
        rates = store.list_fx_rates(context, limit=10_000)
        base = settings.base_currency
        balances = {b.id: b.balance for b in account_balances(store, context)}
        found: list[R.Revaluation] = []
        truncated = False
        # Each foreign account's own history, in full: other accounts' rows never crowd it out, and an account whose history is
        # longer than MAX_ROWS is not valued from part of it.
        for account in R.revalued_accounts(accounts, base):
            rows, total = store.list_transactions(context, TransactionFilter(account_id=account.id), limit=MAX_ROWS, offset=0)
            if total > MAX_ROWS:
                truncated = True
                found.append(R.too_long(account, base, balances.get(account.id, account.opening_balance)))
            else:
                found.append(R.revalue(account, rows, base, rates, today, max_age))
    complete = [r.unrealised for r in found if r.unrealised is not None]
    incomplete = sum(1 for r in found if r.status in ("rate_not_available", "cost_rate_not_available", "history_too_long"))
    return RevaluationPage(base_currency=base, as_of=today, rate_max_age_days=max_age,
                           items=[RevaluationRow(**{k: getattr(r, k) for k in RevaluationRow.model_fields}) for r in found],
                           unrealised_total=sum(complete, Decimal(0)) if complete else None, incomplete=incomplete, truncated=truncated)


__all__ = ["router"]
