"""The category ontology and the learned merchant categories under ``/api/v1/finance``
(docs/finance.md#category-ontology, #learned-merchants).

* ``GET /categories?month=``: every group, its categories and their subcategories (built in, ``sys:...``, and the
  owner's own, archived ones flagged), with English and Arabic names and whether each is essential; with ``month``
  also the month's spending per group, category and subcategory, per currency, by the shared rule (``flows.py``:
  purchases less refunds in the category; transfers, card repayments, money lent or borrowed and uncategorized rows
  never count), so the totals equal the summary's.
* ``GET /category-mappings``: what the owner filed each merchant as (stored on every hand correction), which later rows
  of that merchant follow. ``PATCH /category-mappings`` switches one off or on; ``DELETE /category-mappings`` removes
  it. Neither changes a row already recorded.

Reading needs ``finance:read``; changing ``finance:manage``. Everything is the caller's own (owner-only RLS).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from . import categories as C
from .api import _audit, _manage, _not_found, _read
from .flows import NOT_SPEND, spend_rows
from .models import Category, TransactionType
from .money import quantize
from .rules_store import open_rule_stores

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


class SubcategoryNode(BaseModel):
    id: str
    name: str  # English for built-in ones; the owner's own name for theirs
    name_ar: str | None
    built_in: bool
    archived: bool = False
    essential: bool


class CategoryNode(BaseModel):
    id: Category
    name: str
    name_ar: str
    spending: bool  # counts as spending (not a transfer, income, cashback or refund)
    essential: bool
    subcategories: list[SubcategoryNode]


class GroupNode(BaseModel):
    id: str
    name: str
    name_ar: str
    categories: list[CategoryNode]
    debt_types: list[TransactionType] = Field(default_factory=list)  # the debt flows group: by transaction type


class SpendLine(BaseModel):
    group: str
    category: Category
    subcategory_id: str | None
    currency: str
    amount: Decimal
    count: int


class CategoryTree(BaseModel):
    groups: list[GroupNode]
    month: str | None = None
    spend: list[SpendLine] = Field(default_factory=list)
    group_totals: dict[str, dict[str, Decimal]] = Field(default_factory=dict)  # group: currency: amount


@router.get("/categories", response_model=CategoryTree)
def category_tree(month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                  context: TenantContext = Depends(tenant_context)) -> CategoryTree:
    _read(context)
    from . import obligations

    lending = obligations.load(context).lending_ids if month else frozenset()
    with open_rule_stores(context) as (finance, store):
        owned = store.list_subcategories(context)
        rows = []
        label = None
        kinds: dict = {}
        if month:
            from .summary import month_range, settings_for, today_in

            label, start, end = month_range(month, today_in(settings_for(finance, context)))
            rows = finance.transactions_between(context, start, end)
            kinds = {a.id: a.kind for a in finance.list_accounts(context)}
    groups: list[GroupNode] = []
    for group in C.GROUPS:
        nodes = []
        for category in group.categories:
            en, ar = C.CATEGORY_NAMES[category]
            subs = [SubcategoryNode(id=s.id, name=s.en, name_ar=s.ar, built_in=True, essential=s.essential)
                    for s in C.BUILT_IN_SUBCATEGORIES if s.parent == category]
            subs += [SubcategoryNode(id=s.id, name=s.name, name_ar=None, built_in=False, archived=s.archived, essential=C.is_essential(category))
                     for s in owned if s.parent == category]
            nodes.append(CategoryNode(id=category, name=en, name_ar=ar, spending=category not in NOT_SPEND, essential=C.is_essential(category),
                                      subcategories=subs))
        groups.append(GroupNode(id=group.id, name=group.en, name_ar=group.ar, categories=nodes,
                                debt_types=sorted(C.DEBT_TYPES, key=lambda t: t.value) if group.id == "debt" else []))
    tree = CategoryTree(groups=groups, month=label)
    if month:
        sums: dict[tuple[str, Category, str | None, str], list[Decimal]] = {}
        for t in spend_rows(rows, kinds, lending):
            key = (C.group_of(t.category, t.transaction_type), t.category, t.subcategory_id, t.currency)
            sums.setdefault(key, []).append(-t.amount)
        for (group_id, category, sub, currency), values in sorted(sums.items(), key=lambda kv: (kv[0][0], kv[0][1].value, kv[0][3], kv[0][2] or "")):
            amount = quantize(sum(values, Decimal(0)), currency)
            tree.spend.append(SpendLine(group=group_id, category=category, subcategory_id=sub, currency=currency, amount=amount,
                                        count=sum(1 for v in values if v > 0)))
            bucket = tree.group_totals.setdefault(group_id, {})
            bucket[currency] = quantize(bucket.get(currency, Decimal(0)) + amount, currency)
    return tree


# -- learned merchant categories --------------------------------------------------------------------------


class MappingView(BaseModel):
    merchant: str
    direction: Literal["in", "out"]
    category: Category
    subcategory_id: str | None
    transaction_type: TransactionType | None
    confirmations: int
    enabled: bool
    example: str | None
    source_transaction_id: str | None
    created_at: datetime
    updated_at: datetime


class MappingPage(BaseModel):
    items: list[MappingView]
    total: int
    limit: int
    offset: int


@router.get("/category-mappings", response_model=MappingPage)
def list_mappings(q: str | None = Query(default=None, max_length=80), limit: int = Query(default=50, ge=1, le=200),
                  offset: int = Query(default=0, ge=0, le=10_000), context: TenantContext = Depends(tenant_context)) -> MappingPage:
    _read(context)
    from .store import open_finance_store

    with open_finance_store(context) as store:
        mappings = store.get_planning(context).category_mappings
    needle = " ".join((q or "").casefold().split())
    found = [m for m in mappings if not needle or needle in m.merchant or needle in (m.example or "").casefold()]
    found.sort(key=lambda m: m.updated_at, reverse=True)
    page = found[offset:offset + limit]
    return MappingPage(items=[MappingView(**m.model_dump()) for m in page], total=len(found), limit=limit, offset=offset)


class MappingKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: str = Field(min_length=2, max_length=120)
    direction: Literal["in", "out"]


class MappingSwitch(MappingKey):
    enabled: bool


@router.patch("/category-mappings", response_model=MappingView)
def switch_mapping(body: MappingSwitch, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> MappingView:
    """Switch a learned merchant category off (later rows of the merchant go back to the rules) or on again."""
    _manage(context)
    from .store import open_finance_store

    with open_finance_store(context) as store:
        planning = store.get_planning(context)
        found = next((m for m in planning.category_mappings if m.merchant == body.merchant and m.direction == body.direction), None)
        if found is None:
            raise _not_found("Learned merchant")
        found.enabled, found.updated_at = body.enabled, utc_now()
        store.save_planning(context, planning)
    _audit(repository, context, "finance.category_mapping.update", "category_mapping", {"enabled": body.enabled})
    return MappingView(**found.model_dump())


@router.delete("/category-mappings", status_code=status.HTTP_204_NO_CONTENT)
def delete_mapping(merchant: str = Query(min_length=2, max_length=120), direction: Literal["in", "out"] = Query(),
                   context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Forget what a merchant was filed as. Rows already recorded keep their category."""
    _manage(context)
    from .store import open_finance_store

    with open_finance_store(context) as store:
        planning = store.get_planning(context)
        kept = [m for m in planning.category_mappings if not (m.merchant == merchant and m.direction == direction)]
        if len(kept) == len(planning.category_mappings):
            raise _not_found("Learned merchant")
        planning.category_mappings = kept
        store.save_planning(context, planning)
    _audit(repository, context, "finance.category_mapping.delete", "category_mapping", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
