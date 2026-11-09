"""The fixed asset register and loan schedules in storage: registering capital purchases, and the views the API returns
(docs/business-books.md#fixed-asset-register, #loan-schedules).

Every capital bill and capital-item adjustment becomes one asset (``sync``): when the posting run runs (after every change in
the books, Post now and the daily job) and when the register is opened. One asset per source (a unique index), so a
second run, or two at the same time, never makes a duplicate. An asset made this way depreciates straight line over the
source's useful life from the source's start month with no residual value: exactly what the source posted before, under
the same event keys, so nothing posted changes. The source stays the record of the purchase: the asset's cost, currency
and acquisition day follow it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from ..schemas import TenantContext, new_id
from . import books_assets as A
from . import books_loans as LN
from .books_models import AdjustmentKind, Asset, BillStatus, DepreciationMethod, Section
from .books_report import month_start
from .books_store import MAX_SCAN, BooksStore, DuplicateError


def _s(value: Decimal | None, currency: str) -> str | None:
    return str(quantize(value, currency)) if value is not None else None


def sync(store: BooksStore, context: TenantContext, now: datetime, actor: str) -> int:
    """Register every capital bill and capital item that has no asset yet; keep registered assets' cost, currency and day in
    line with their source. Returns how many assets were made."""
    assets, _ = store.find("assets", limit=MAX_SCAN)
    by_source = {(row.source_kind, row.source_id): row for row in assets}
    bills, _ = store.find("bills", where={"section": Section.CAPITAL.value, "status": BillStatus.OPEN.value}, limit=MAX_SCAN)
    items, _ = store.find("adjustments", where={"kind": AdjustmentKind.CAPITAL_ITEM.value, "status": "active"}, limit=MAX_SCAN)
    sources: list[tuple[str, str, str, str, date, str, Decimal, int | None, date]] = []
    for bill in bills:
        if bill.subtotal > 0:
            sources.append(("bill", bill.id, bill.description, bill.category, bill.bill_date, bill.currency, bill.subtotal, bill.useful_life_months,
                            month_start(bill.bill_date)))
    for item in items:
        if item.amount > 0 and item.start_month and item.months:
            sources.append(("adjustment", item.id, item.description, item.category or "equipment", item.entry_date, item.currency, item.amount, item.months,
                            month_start(item.start_month)))
    made = 0
    for kind, source_id, name, category, acquired, currency, cost, life, start in sources:
        found = by_source.get((kind, source_id))
        if found is None:
            asset = Asset(id=new_id("basset"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, source_kind=kind,
                          source_id=source_id, name=(name or "Fixed asset")[:120], category=(category or "equipment")[:40], acquired_on=acquired,
                          currency=currency, cost=cost, useful_life_months=life, depreciation_start=start, created_by=actor, updated_at=now)
            try:
                store.add("assets", asset)
            except DuplicateError:
                continue
            made += 1
        elif (found.cost, found.currency, found.acquired_on) != (cost, currency, acquired) and found.status == "active":
            store.save("assets", found.model_copy(update={"cost": cost, "currency": currency, "acquired_on": acquired, "version": found.version + 1,
                                                          "updated_at": now}))
    return made


def source_live(store: BooksStore, asset: Asset) -> bool:
    if asset.source_kind == "bill":
        bill = store.get("bills", asset.source_id)
        return bill is not None and bill.status == BillStatus.OPEN and bill.section == Section.CAPITAL
    item = store.get("adjustments", asset.source_id)
    return item is not None and item.status == "active"


def asset_view(asset: Asset, impairments: list[Any], day: date, *, source_live: bool = True) -> dict[str, Any]:
    cur = asset.currency
    rows = [row for row in impairments if row.asset_id == asset.id]
    depreciation = A.depreciated_through(asset, rows, day)
    impaired = A.impaired_through(asset, rows, day)
    sold = A.disposal(asset, rows)
    return {
        "id": asset.id, "name": asset.name, "category": asset.category, "source_kind": asset.source_kind, "source_id": asset.source_id,
        "source_live": source_live, "acquired_on": asset.acquired_on.isoformat(), "currency": cur, "cost": _s(asset.cost, cur),
        "useful_life_months": asset.useful_life_months, "residual_value": _s(asset.residual_value, cur),
        "method": asset.method.value if isinstance(asset.method, DepreciationMethod) else asset.method,
        "rate_pct": format(asset.rate_pct.normalize(), "f") if asset.rate_pct is not None else None,
        "depreciation_start": asset.depreciation_start.isoformat(), "status": asset.status,
        "disposed_on": asset.disposed_on.isoformat() if asset.disposed_on else None, "disposal_kind": asset.disposal_kind,
        "proceeds": _s(asset.proceeds, cur), "accumulated_depreciation": _s(depreciation, cur), "impairment": _s(impaired, cur),
        "net_book_value": _s(A.net_book_value(asset, rows, day), cur), "as_of": day.isoformat(),
        "gain_or_loss": _s(sold["gain"], cur) if sold else None, "version": asset.version,
        "pending_impairments": sum(1 for row in rows if row.status == "pending"),
    }


def impairment_view(row: Any) -> dict[str, Any]:
    return {"id": row.id, "asset_id": row.asset_id, "entry_date": row.entry_date.isoformat(), "amount": _s(row.amount, row.currency),
            "currency": row.currency, "reason": row.reason, "status": row.status, "requested_by": row.requested_by, "decided_by": row.decided_by,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None, "created_at": row.created_at.isoformat(), "version": row.version}


def schedule_view(asset: Asset, impairments: list[Any], today: date, locked: date | None) -> list[dict[str, Any]]:
    rows = [row for row in impairments if row.asset_id == asset.id]
    out = []
    running = Decimal(0)
    for month in A.schedule(asset, rows):
        running += month.amount
        out.append({"month": month.month.isoformat(), "amount": _s(month.amount, asset.currency), "accumulated": _s(running, asset.currency),
                    "state": "closed" if locked is not None and month.month <= locked else ("posted" if month.month <= today else "scheduled")})
    return out


def roll_forward_view(assets: list[Asset], impairments: list[Any], start: date, end: date) -> list[dict[str, Any]]:
    out = []
    for currency, figures in sorted(A.roll_forward(assets, impairments, start, end).items()):
        out.append({"currency": currency, **{key: _s(value, currency) for key, value in figures.items()}})
    return out


# -- loans ---------------------------------------------------------------------------------------------------------------------


def loan_view(loan: Any, repayments: list[Any], today: date) -> dict[str, Any]:
    cur = loan.currency
    mine = [row for row in repayments if row.loan_id == loan.id]
    principal, interest = LN.owed_on(loan, mine, today) if loan.status == "active" else (Decimal(0), Decimal(0))
    plan = LN.planned(loan)
    upcoming = next((row for row in LN.revised(loan, mine) if row.due >= today), None) if loan.status == "active" else None
    return {
        "id": loan.id, "lender": loan.lender, "principal": _s(loan.principal, cur), "currency": cur,
        "annual_rate": format(Decimal(loan.annual_rate).normalize(), "f"), "start_date": loan.start_date.isoformat(), "term_months": loan.term_months,
        "frequency": loan.frequency, "method": getattr(loan.method, "value", loan.method), "early_repayment": loan.early_repayment,
        "received_adjustment_id": loan.received_adjustment_id, "status": loan.status, "version": loan.version,
        "payment": _s(plan[0].payment, cur) if plan else None, "periods": LN.periods(loan),
        "maturity": plan[-1].due.isoformat() if plan else None, "outstanding": _s(principal, cur), "interest_accrued": _s(interest, cur),
        "repaid": _s(Decimal(loan.principal) - principal, cur) if loan.status == "active" else None,
        "next_due": upcoming.due.isoformat() if upcoming else None, "next_payment": _s(upcoming.payment, cur) if upcoming else None,
        "repayments": sum(1 for row in mine if row.status == "active"), "void_reason": loan.void_reason,
    }


def row_view(row: LN.Row, currency: str) -> dict[str, Any]:
    return {"number": row.number, "due": row.due.isoformat(), "payment": _s(row.payment, currency), "interest": _s(row.interest, currency),
            "principal": _s(row.principal, currency), "balance": _s(row.balance, currency)}


def repayment_view(row: Any) -> dict[str, Any]:
    cur = row.currency
    return {"id": row.id, "loan_id": row.loan_id, "paid_on": row.paid_on.isoformat(), "amount": _s(row.amount, cur), "interest": _s(row.interest, cur),
            "principal": _s(row.principal, cur), "currency": cur, "bill_id": row.bill_id, "adjustment_id": row.adjustment_id, "status": row.status,
            "created_by": row.created_by, "created_at": row.created_at.isoformat(), "void_reason": row.void_reason}


def loan_detail(loan: Any, repayments: list[Any], today: date) -> dict[str, Any]:
    cur = loan.currency
    mine = [row for row in repayments if row.loan_id == loan.id]
    comparison = [{**item, "due": item["due"].isoformat(), **{key: _s(item[key], cur) for key in (
        "payment", "interest", "principal", "balance", "paid", "paid_interest", "paid_principal", "difference")}}
        for item in LN.schedule_vs_actual(loan, mine, today)]
    return {
        **loan_view(loan, mine, today),
        "schedule": [row_view(row, cur) for row in LN.planned(loan)],
        "actual": comparison,
        "revised": [row_view(row, cur) for row in LN.revised(loan, mine)] if loan.status == "active" else [],
        "repayment_rows": [repayment_view(row) for row in sorted(mine, key=lambda item: (item.paid_on, item.created_at), reverse=True)],
        "accruals": [{"month_end": end.isoformat(), "amount": _s(amount, cur)} for end, amount in LN.month_end_accruals(loan, mine, today)][-12:],
    }


__all__ = ["asset_view", "impairment_view", "loan_detail", "loan_view", "repayment_view", "roll_forward_view", "schedule_view", "source_live", "sync"]
