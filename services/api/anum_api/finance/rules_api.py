"""Rules, learning, merchant aliases, subcategories, the suggestion queue and automation policies under
``/api/v1/finance`` (docs/finance.md#rules-and-learning, #suggestions, #automation-policies).

``finance:read`` lists; ``finance:manage`` changes. Every row is the caller's own (owner-only RLS). Each route does
its database work in one short unit of work; nothing here holds a transaction across a model call (the AI
suggestion asks the model with no unit of work open). Audit records carry ids and counts only, never amounts,
descriptions or merchant names.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import rules_service as RS
from . import suggestions as SG
from . import txn_types as TT
from .api import _audit, _manage, _read
from .models import Category, CategorySource, TransactionType, TypeSource
from .rules import (
    MAX_ALIASES,
    MAX_SUBCATEGORIES,
    ActiveRule,
    AliasBook,
    AutomationPolicy,
    AutomationSettings,
    FinanceRule,
    MerchantAlias,
    RuleContent,
    Subcategory,
    current_versions,
    fold,
    require_automatable,
)
from .rules_store import RowState, SuggestionDecision, open_rule_stores, rules_store_for

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
MAX_PAGE = 100


def _fail(exc: RS.RuleError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found. It may have been removed.")


# -- rules ------------------------------------------------------------------------------------------------


class RuleVersionView(BaseModel):
    rule_id: str
    version: int
    status: str
    content: RuleContent
    provenance: str
    source_transaction_id: str | None
    confidence: Decimal
    payload_hash: str
    created_by: str
    created_at: Any
    decided_by: str | None
    decided_at: Any
    reverted_from: int | None


class RuleView(BaseModel):
    rule_id: str
    current: RuleVersionView | None  # the approved version that runs (None while the first version waits)
    pending: RuleVersionView | None  # a proposed version waiting for the owner
    versions: int


class RulePage(BaseModel):
    items: list[RuleView]
    total: int
    limit: int
    offset: int
    legacy_rules: int  # plain phrase rules still read from the older list (copied in on the first save)
    pending: int


def _version_view(v: FinanceRule) -> RuleVersionView:
    return RuleVersionView(rule_id=v.rule_id, version=v.version, status=v.status, content=v.content, provenance=v.provenance,
                           source_transaction_id=v.source_transaction_id, confidence=v.confidence, payload_hash=v.payload_hash,
                           created_by=v.created_by, created_at=v.created_at, decided_by=v.decided_by, decided_at=v.decided_at,
                           reverted_from=v.reverted_from)


def _rule_views(versions: list[FinanceRule]) -> list[RuleView]:
    by_rule: dict[str, list[FinanceRule]] = {}
    for v in versions:
        by_rule.setdefault(v.rule_id, []).append(v)
    current = current_versions(versions)
    out = []
    for rule_id, items in by_rule.items():
        newest = max(items, key=lambda v: v.version)
        now = current.get(rule_id)
        pending = newest if newest.status == "proposed" else None
        out.append(RuleView(rule_id=rule_id, current=_version_view(now) if now else None, pending=_version_view(pending) if pending else None,
                            versions=len(items)))
    out.sort(key=lambda r: (r.pending is None, (r.current or r.pending).content.priority if (r.current or r.pending) else 0, r.rule_id))  # type: ignore[union-attr]
    return out


@router.get("/rules", response_model=RulePage)
def list_rules(
    state: Literal["all", "active", "pending", "disabled"] = Query(default="all"),
    q: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> RulePage:
    """The owner's rules: the version that runs and any change waiting for approval."""
    _read(context)
    from .summary import settings_for

    with open_rule_stores(context) as (finance, store):
        versions = store.list_versions(context)
        legacy = 0 if versions else len(settings_for(finance, context).category_rules)
    views = _rule_views(versions)
    if state == "active":
        views = [v for v in views if v.current and v.current.content.enabled]
    elif state == "pending":
        views = [v for v in views if v.pending]
    elif state == "disabled":
        views = [v for v in views if v.current and not v.current.content.enabled]
    if q:
        needle = fold(q)

        def words(v: RuleView) -> str:
            shown = v.current or v.pending
            if shown is None:
                return ""
            m = shown.content.match
            return fold(" ".join(filter(None, [m.merchant, *m.words, m.reference, shown.content.category.value, shown.content.note])))

        views = [v for v in views if needle in words(v)]
    return RulePage(items=views[offset:offset + limit], total=len(views), limit=limit, offset=offset, legacy_rules=legacy,
                    pending=sum(1 for v in _rule_views(versions) if v.pending))


class RuleDetail(BaseModel):
    rule_id: str
    versions: list[RuleVersionView]


@router.get("/rules/{rule_id}", response_model=RuleDetail)
def get_rule(rule_id: str, context: TenantContext = Depends(tenant_context)) -> RuleDetail:
    """Every version of one rule, newest first."""
    _read(context)
    with open_rule_stores(context) as (_finance, store):
        versions = store.list_versions(context, rule_id)
    if not versions:
        raise _not_found("Rule")
    return RuleDetail(rule_id=rule_id, versions=[_version_view(v) for v in sorted(versions, key=lambda v: v.version, reverse=True)])


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: RuleContent
    source_transaction_id: str | None = Field(default=None, max_length=80)


@router.post("/rules", response_model=RuleVersionView, status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> RuleVersionView:
    """A new rule, proposed: it runs once the owner approves it with its payload hash."""
    _manage(context)
    provenance = "learned" if body.source_transaction_id else "owner"
    try:
        with open_rule_stores(context) as (finance, store):
            made = RS.propose(finance, store, context, body.content, provenance=provenance, by=context.user_id, now=utc_now(),
                              source_transaction_id=body.source_transaction_id)
    except RS.RuleError as exc:
        raise _fail(exc) from None
    _audit(repository, context, "finance.rule.propose", made.rule_id, {"version": made.version, "provenance": made.provenance})
    return _version_view(made)


@router.post("/rules/{rule_id}/versions", response_model=RuleVersionView, status_code=status.HTTP_201_CREATED)
def edit_rule(rule_id: str, body: RuleInput, context: TenantContext = Depends(tenant_context),
              repository: AnumRepository = Depends(repository_context)) -> RuleVersionView:
    """A changed version of a rule, proposed (the approved version keeps running until this one is approved)."""
    _manage(context)
    try:
        with open_rule_stores(context) as (finance, store):
            made = RS.propose(finance, store, context, body.content, provenance="owner", by=context.user_id, now=utc_now(), rule_id=rule_id)
    except RS.RuleError as exc:
        raise _fail(exc) from None
    _audit(repository, context, "finance.rule.propose", rule_id, {"version": made.version, "provenance": "owner"})
    return _version_view(made)


class DecideInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    payload_hash: str | None = Field(default=None, min_length=64, max_length=64)


@router.post("/rules/{rule_id}/approve", response_model=RuleVersionView)
def approve_rule(rule_id: str, body: DecideInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> RuleVersionView:
    """The owner approves a proposed version with the payload hash they were shown. An assistant never can."""
    _manage(context)
    try:
        with open_rule_stores(context) as (_finance, store):
            done = RS.decide(store, context, rule_id, body.version, approve=True, payload=body.payload_hash, by=context.user_id, now=utc_now())
    except RS.RuleError as exc:
        raise _fail(exc) from None
    _audit(repository, context, "finance.rule.approve", rule_id, {"version": body.version})
    return _version_view(done)


@router.post("/rules/{rule_id}/reject", response_model=RuleVersionView)
def reject_rule(rule_id: str, body: DecideInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> RuleVersionView:
    _manage(context)
    try:
        with open_rule_stores(context) as (_finance, store):
            done = RS.decide(store, context, rule_id, body.version, approve=False, payload=None, by=context.user_id, now=utc_now())
    except RS.RuleError as exc:
        raise _fail(exc) from None
    _audit(repository, context, "finance.rule.reject", rule_id, {"version": body.version})
    return _version_view(done)


class RevertInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_version: int = Field(ge=1)


@router.post("/rules/{rule_id}/revert", response_model=RuleVersionView, status_code=status.HTTP_201_CREATED)
def revert_rule(rule_id: str, body: RevertInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> RuleVersionView:
    """A new version with an older version's content, proposed. Nothing is deleted."""
    _manage(context)
    try:
        with open_rule_stores(context) as (finance, store):
            made = RS.revert(finance, store, context, rule_id, body.to_version, by=context.user_id, now=utc_now())
    except RS.RuleError as exc:
        raise _fail(exc) from None
    _audit(repository, context, "finance.rule.revert", rule_id, {"version": made.version, "from": body.to_version})
    return _version_view(made)


# -- preview and retroactive apply ------------------------------------------------------------------------


class PreviewRowView(BaseModel):
    transaction_id: str
    account_id: str
    posted_on: date
    description: str
    amount: Decimal
    currency: str
    before_category: Category
    before_subcategory_id: str | None
    before_type: TransactionType | None
    after_category: Category
    after_subcategory_id: str | None
    after_type: TransactionType | None


class PreviewView(BaseModel):
    matched: int
    changes: int
    unchanged: int
    manual_kept: int
    sms_kept: int
    truncated: bool
    preview_hash: str
    rows: list[PreviewRowView]


def _preview_view(found: RS.Preview) -> PreviewView:
    rows = [PreviewRowView(transaction_id=r.transaction.id, account_id=r.transaction.account_id, posted_on=r.transaction.posted_on,
                           description=r.transaction.description, amount=r.transaction.amount, currency=r.transaction.currency,
                           before_category=r.transaction.category, before_subcategory_id=r.transaction.subcategory_id,
                           before_type=r.transaction.transaction_type, after_category=r.after.category, after_subcategory_id=r.after.subcategory_id,
                           after_type=r.after.transaction_type) for r in found.rows[:RS.PREVIEW_ROWS]]
    return PreviewView(matched=found.matched, changes=len(found.rows), unchanged=found.unchanged, manual_kept=found.manual_kept,
                       sms_kept=found.sms_kept, truncated=found.truncated, preview_hash=found.digest, rows=rows)


def _active(store: Any, context: TenantContext, rule_id: str, content: RuleContent, version: int = 0) -> ActiveRule:
    return replace(ActiveRule.from_content(rule_id, version, content), book=AliasBook.of(store.list_aliases(context)))


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: RuleContent


@router.post("/rules/preview", response_model=PreviewView)
def preview_rule(body: PreviewInput, context: TenantContext = Depends(tenant_context)) -> PreviewView:
    """Which of your rows a rule would change, and which it leaves alone and why. Nothing is saved."""
    _read(context)
    from .links_store import open_link_stores

    try:
        with open_link_stores(context) as (finance, sms, _links):
            store = rules_store_for(finance)
            RS.check_content(finance, store, context, body.content)
            found = RS.preview(finance, sms, context, _active(store, context, "preview", body.content))
    except RS.RuleError as exc:
        raise _fail(exc) from None
    return _preview_view(found)


@router.post("/rules/{rule_id}/preview", response_model=PreviewView)
def preview_saved_rule(rule_id: str, context: TenantContext = Depends(tenant_context)) -> PreviewView:
    """The rows the approved version of a rule would change on earlier rows; its ``preview_hash`` is what Apply needs."""
    _read(context)
    from .links_store import open_link_stores

    with open_link_stores(context) as (finance, sms, _links):
        store = rules_store_for(finance)
        rule = current_versions(store.list_versions(context, rule_id)).get(rule_id)
        if rule is None:
            raise HTTPException(status_code=409, detail="Approve the rule before you preview it on earlier rows.")
        found = RS.preview(finance, sms, context, replace(ActiveRule.of(rule), book=AliasBook.of(store.list_aliases(context))))
    return _preview_view(found)


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_hash: str = Field(min_length=64, max_length=64)


class BatchView(BaseModel):
    id: str
    origin: str
    rule_id: str | None
    rule_version: int | None
    suggestion_key: str | None
    changed: int
    status: str
    created_by: str
    created_at: Any
    undone_at: Any


def _batch_view(batch: Any) -> BatchView:
    return BatchView(id=batch.id, origin=batch.origin, rule_id=batch.rule_id, rule_version=batch.rule_version, suggestion_key=batch.suggestion_key,
                     changed=len(batch.changes), status=batch.status, created_by=batch.created_by, created_at=batch.created_at, undone_at=batch.undone_at)


@router.post("/rules/{rule_id}/apply", response_model=BatchView, status_code=status.HTTP_201_CREATED)
def apply_rule(rule_id: str, body: ApplyInput, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> BatchView:
    """Apply the approved version of a rule to the rows the preview showed, as one batch that can be undone.

    The preview is worked out again under the owner's lock; if it changed since the owner looked, nothing is applied."""
    _manage(context)
    from .sms_service import owner_lock
    from .sms_store import open_sms_stores

    now = utc_now()
    with owner_lock(context), open_sms_stores(context) as (finance, sms):
        sms.lock_owner(context)
        store = rules_store_for(finance)
        rule = current_versions(store.list_versions(context, rule_id)).get(rule_id)
        if rule is None:
            raise HTTPException(status_code=409, detail="Approve the rule before you apply it to earlier rows.")
        if not rule.content.enabled:
            raise HTTPException(status_code=409, detail="This rule is turned off. Turn it on before you apply it.")
        active = replace(ActiveRule.of(rule), book=AliasBook.of(store.list_aliases(context)))
        found = RS.preview(finance, sms, context, active)
        if found.digest != body.preview_hash:
            raise HTTPException(status_code=409, detail="Your rows changed since the preview. Look at the preview again, then apply.")
        if not found.rows:
            raise HTTPException(status_code=409, detail="No row would change.")
        batch = RS.apply_rows(finance, sms, store, context, found.rows, origin="owner", by=context.user_id, now=now, rule=active)
    _audit(repository, context, "finance.rule.apply", rule_id, {"version": rule.version, "batch_id": batch.id, "rows": len(batch.changes)})
    return _batch_view(batch)


class BatchPage(BaseModel):
    items: list[BatchView]
    total: int
    limit: int
    offset: int


@router.get("/rule-batches", response_model=BatchPage)
def list_batches(limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
                 context: TenantContext = Depends(tenant_context)) -> BatchPage:
    """Every rule apply and accepted suggestion that changed rows, newest first, each with Undo while it stands."""
    _read(context)
    with open_rule_stores(context) as (_finance, store):
        items, total = store.list_batches(context, limit=limit, offset=offset)
    return BatchPage(items=[_batch_view(b) for b in items], total=total, limit=limit, offset=offset)


class UndoView(BaseModel):
    batch: BatchView
    restored: int
    kept: int  # rows changed since (by the owner or a later rule): left as they are now


@router.post("/rule-batches/{batch_id}/undo", response_model=UndoView)
def undo_batch(batch_id: str, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> UndoView:
    _manage(context)
    from .sms_service import owner_lock
    from .sms_store import open_sms_stores

    try:
        with owner_lock(context), open_sms_stores(context) as (finance, sms):
            sms.lock_owner(context)
            store = rules_store_for(finance)
            restored, kept = RS.undo_batch(finance, sms, store, context, batch_id, by=context.user_id, now=utc_now())
            batch = store.get_batch(context, batch_id)
    except RS.RuleError as exc:
        raise _fail(exc) from None
    _audit(repository, context, "finance.rule_batch.undo", batch_id, {"restored": restored, "kept": kept})
    return UndoView(batch=_batch_view(batch), restored=restored, kept=kept)


# -- learning from a correction ---------------------------------------------------------------------------


class SimilarInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["all", "account"] = "all"


class SimilarView(BaseModel):
    content: RuleContent
    preview: PreviewView
    source_transaction_id: str


@router.post("/transactions/{transaction_id}/similar", response_model=SimilarView)
def similar(transaction_id: str, body: SimilarInput, context: TenantContext = Depends(tenant_context)) -> SimilarView:
    """After a correction: the rule it suggests (same merchant, same side, the category you chose) and which other rows
    it would change. Nothing is saved; save the rule, approve it and apply it as three explicit steps."""
    _read(context)
    from .links_store import open_link_stores

    try:
        with open_link_stores(context) as (finance, sms, _links):
            row = finance.get_transaction(context, transaction_id)
            if row is None:
                raise _not_found("Transaction")
            store = rules_store_for(finance)
            content = RS.from_correction(row, AliasBook.of(store.list_aliases(context)), scope_account=body.scope == "account")
            found = RS.preview(finance, sms, context, _active(store, context, "similar", content))
    except RS.RuleError as exc:
        raise _fail(exc) from None
    return SimilarView(content=content, preview=_preview_view(found), source_transaction_id=transaction_id)


class AiSuggestionView(BaseModel):
    category: Category | None
    transaction_type: TransactionType | None
    note: str = "A suggestion from your workspace's AI model. It is not saved and ANUM never learns a rule from it."


@router.post("/transactions/{transaction_id}/ai-suggestion", response_model=AiSuggestionView)
def ai_suggestion(transaction_id: str, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> AiSuggestionView:
    """Ask the workspace's model for a category, only when the owner turned AI help on and a real model is set up.
    Shown only: never applied, never stored, never learned from (``rules.learnable``)."""
    _manage(context)
    from . import sms_ai
    from .sms_service import settings_for as sms_settings
    from .sms_store import open_sms_stores

    with open_sms_stores(context) as (finance, sms):
        settings = sms_settings(sms, context)
        row = finance.get_transaction(context, transaction_id)
    if row is None:
        raise _not_found("Transaction")
    if not settings.ai.enabled or settings.ai.opted_in_at is None:
        raise HTTPException(status_code=409, detail="AI help is off. Turn it on under Finance, iPhone SMS, Settings if you want it.")
    if not sms_ai.has_model(context):
        raise HTTPException(status_code=409, detail="This workspace has no AI model set up. Add one in Settings, Models, or choose the category yourself.")
    from .categories_ai import suggest_category

    try:
        category, kind = suggest_category(context, row.description, row.amount)  # no unit of work is open here
    except Exception:
        raise HTTPException(status_code=503, detail="The AI model did not answer. Try again, or choose the category yourself.") from None
    _audit(repository, context, "finance.transaction.ai_suggestion", transaction_id, {"answered": category is not None})
    return AiSuggestionView(category=category, transaction_type=kind)


# -- merchant aliases -----------------------------------------------------------------------------------


class AliasInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=2, max_length=80)
    merchant: str = Field(min_length=2, max_length=80)


class AliasView(BaseModel):
    id: str
    alias: str
    merchant: str
    created_at: Any
    updated_at: Any


@router.get("/merchants/aliases", response_model=list[AliasView])
def list_aliases(context: TenantContext = Depends(tenant_context)) -> list[AliasView]:
    _read(context)
    with open_rule_stores(context) as (_finance, store):
        return [AliasView(**a.model_dump(include={"id", "alias", "merchant", "created_at", "updated_at"})) for a in store.list_aliases(context)]


def _save_alias(context: TenantContext, body: AliasInput, alias_id: str | None) -> MerchantAlias:
    alias, merchant = " ".join(body.alias.split()), " ".join(body.merchant.split())
    if len(fold(alias)) < 2:
        raise HTTPException(status_code=422, detail="Enter the spelling as it appears in your bank's description.")
    now = utc_now()
    with open_rule_stores(context) as (_finance, store):
        existing = store.list_aliases(context)
        found = next((a for a in existing if a.id == alias_id), None) if alias_id else None
        if alias_id and found is None:
            raise _not_found("Merchant spelling")
        if not alias_id and len(existing) >= MAX_ALIASES:
            raise HTTPException(status_code=409, detail=f"You have {MAX_ALIASES} spellings. Remove some first.")
        item = MerchantAlias(id=alias_id or new_id("fin_alias"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                             owner_user_id=context.user_id, alias=alias, merchant=merchant, created_at=found.created_at if found else now, updated_at=now)
        try:
            store.save_alias(item)
        except ValueError:
            raise HTTPException(status_code=409, detail="You already have this spelling. Edit it instead.") from None
    return item


@router.post("/merchants/aliases", response_model=AliasView, status_code=status.HTTP_201_CREATED)
def create_alias(body: AliasInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> AliasView:
    """Another spelling of a merchant (English or Arabic): rules and history read every spelling as the one merchant."""
    _manage(context)
    item = _save_alias(context, body, None)
    _audit(repository, context, "finance.merchant_alias.create", item.id)
    return AliasView(**item.model_dump(include={"id", "alias", "merchant", "created_at", "updated_at"}))


@router.patch("/merchants/aliases/{alias_id}", response_model=AliasView)
def update_alias(alias_id: str, body: AliasInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> AliasView:
    _manage(context)
    item = _save_alias(context, body, alias_id)
    _audit(repository, context, "finance.merchant_alias.update", item.id)
    return AliasView(**item.model_dump(include={"id", "alias", "merchant", "created_at", "updated_at"}))


@router.delete("/merchants/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alias(alias_id: str, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_rule_stores(context) as (_finance, store):
        if not store.delete_alias(context, alias_id):
            raise _not_found("Merchant spelling")
    _audit(repository, context, "finance.merchant_alias.delete", alias_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- subcategories ----------------------------------------------------------------------------------------


class SubcategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent: Category
    name: str = Field(min_length=1, max_length=60)
    archived: bool = False


class SubcategoryView(BaseModel):
    id: str
    parent: Category
    name: str
    archived: bool


@router.get("/subcategories", response_model=list[SubcategoryView])
def list_subcategories(context: TenantContext = Depends(tenant_context)) -> list[SubcategoryView]:
    _read(context)
    with open_rule_stores(context) as (_finance, store):
        return [SubcategoryView(id=s.id, parent=s.parent, name=s.name, archived=s.archived) for s in store.list_subcategories(context)]


def _save_sub(context: TenantContext, body: SubcategoryInput, sub_id: str | None) -> Subcategory:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(status_code=422, detail="Enter a name for the subcategory.")
    now = utc_now()
    with open_rule_stores(context) as (_finance, store):
        existing = store.list_subcategories(context)
        found = next((s for s in existing if s.id == sub_id), None) if sub_id else None
        if sub_id and found is None:
            raise _not_found("Subcategory")
        if found is not None and found.parent != body.parent:
            raise HTTPException(status_code=422, detail="A subcategory stays under its category. Make a new one under the other category.")
        if not sub_id and len(existing) >= MAX_SUBCATEGORIES:
            raise HTTPException(status_code=409, detail=f"You have {MAX_SUBCATEGORIES} subcategories. Archive some first.")
        item = Subcategory(id=sub_id or new_id("fin_sub"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                           owner_user_id=context.user_id, parent=body.parent, name=name, archived=body.archived,
                           created_at=found.created_at if found else now, updated_at=now)
        try:
            store.save_subcategory(item)
        except ValueError:
            raise HTTPException(status_code=409, detail="This category already has a subcategory with that name.") from None
    return item


@router.post("/subcategories", response_model=SubcategoryView, status_code=status.HTTP_201_CREATED)
def create_subcategory(body: SubcategoryInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> SubcategoryView:
    _manage(context)
    item = _save_sub(context, body, None)
    _audit(repository, context, "finance.subcategory.create", item.id)
    return SubcategoryView(id=item.id, parent=item.parent, name=item.name, archived=item.archived)


@router.patch("/subcategories/{sub_id}", response_model=SubcategoryView)
def update_subcategory(sub_id: str, body: SubcategoryInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> SubcategoryView:
    """Rename or archive. Rows keep their subcategory; an archived one is no longer offered."""
    _manage(context)
    item = _save_sub(context, body, sub_id)
    _audit(repository, context, "finance.subcategory.update", item.id, {"archived": item.archived})
    return SubcategoryView(id=item.id, parent=item.parent, name=item.name, archived=item.archived)


class SubSpend(BaseModel):
    category: Category
    subcategory_id: str | None
    currency: str
    amount: Decimal
    count: int


class SubSpendView(BaseModel):
    month: str
    items: list[SubSpend]


@router.get("/subcategories/spend", response_model=SubSpendView)
def subcategory_spend(month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                      context: TenantContext = Depends(tenant_context)) -> SubSpendView:
    """The month's spending per category and subcategory, per currency (rows without a subcategory under ``null``).
    The category totals are the same as the summary's: subcategories only split them."""
    _read(context)
    from .summary import month_range, settings_for, today_in

    with open_rule_stores(context) as (finance, _store):
        label, start, end = month_range(month, today_in(settings_for(finance, context)))
        rows = finance.transactions_between(context, start, end)
        kinds = {a.id: a.kind for a in finance.list_accounts(context)}
    sums: dict[tuple[Category, str | None, str], list[Decimal]] = {}
    for t in rows:
        if TT.effective(t, kinds.get(t.account_id)) not in TT.SPEND_TYPES:
            continue
        key = (t.category, t.subcategory_id, t.currency)
        sums.setdefault(key, []).append(-t.amount)
    items = [SubSpend(category=c, subcategory_id=s, currency=cur, amount=sum(v, Decimal(0)), count=len(v)) for (c, s, cur), v in sums.items()]
    items.sort(key=lambda i: (i.category.value, i.currency, i.subcategory_id or ""))
    return SubSpendView(month=label, items=items)


# -- the suggestion queue ----------------------------------------------------------------------------------


@router.get("/suggestions", response_model=SG.Queue)
def list_suggestions(
    kind: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> SG.Queue:
    """Everything waiting for you in one list: what was found, why, the evidence, how sure, the money and the action."""
    _read(context)
    queue, _ = SG.build(context, utc_now(), kind=kind, limit=limit, offset=offset)
    return queue


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=3, max_length=200)
    payload_hash: str | None = Field(default=None, min_length=64, max_length=64)


class DecisionView(BaseModel):
    key: str
    status: str
    batch: BatchView | None = None
    link_id: str | None = None  # the link an attribution or payment suggestion made


def _decision(context: TenantContext, item: SG.Suggestion, status_: str, batch_id: str | None, now: Any) -> SuggestionDecision:
    return SuggestionDecision(id=new_id("fin_sdec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                              key=item.key, producer=item.producer, status=status_, payload_hash=item.payload_hash,  # type: ignore[arg-type]
                              decided_by=context.user_id, decided_at=now, batch_id=batch_id)


def classify_changes(finance: Any, context: TenantContext, item: SG.Suggestion, *, by_owner: bool) -> list[RS.PreviewRow]:
    """The rows a ``classify`` or ``confirm_refund`` suggestion changes and how (rows the owner set by hand are left)."""
    kinds = {a.id: a.kind for a in finance.list_accounts(context)}
    out: list[RS.PreviewRow] = []
    for transaction_id in item.action.transaction_ids:
        t = finance.get_transaction(context, transaction_id)
        if t is None or TT.owner_locked(t):
            continue
        kind = kinds.get(t.account_id)
        if item.action.kind == "confirm_refund":
            after = RowState(category=t.category, category_source=t.category_source, subcategory_id=t.subcategory_id,
                             transaction_type=TransactionType.REFUND, type_source=TypeSource.OWNER, type_confidence=Decimal(1), type_reason="refund_confirmed")
        else:
            category = item.action.category or t.category
            source = TypeSource.OWNER if by_owner else TypeSource.SYSTEM
            if item.action.transaction_type is not None:
                typed = TT.Typed(item.action.transaction_type, source, Decimal(1) if by_owner else (item.confidence or Decimal("0.9")),
                                 "owner_suggestion" if by_owner else "auto_history")
            else:
                typed = TT.for_category(category, t.amount, kind, source, "1" if by_owner else str(item.confidence or "0.9"),
                                        "owner_suggestion" if by_owner else "auto_history")
            after = RowState(category=category, category_source=CategorySource.MANUAL if by_owner else CategorySource.SYSTEM,
                             subcategory_id=item.action.subcategory_id, transaction_type=typed.type, type_source=typed.source,
                             type_confidence=typed.confidence, type_reason=typed.reason)
        out.append(RS.PreviewRow(TT.fill(t, kind), after))
    return out


def _find(context: TenantContext, key: str) -> SG.Suggestion:
    _queue, items = SG.build(context, utc_now(), limit=1)
    found = next((s for s in items if s.key == key), None)
    if found is None:
        raise HTTPException(status_code=409, detail="This suggestion is no longer open: it was decided, or what it was about changed.")
    return found


@router.post("/suggestions/accept", response_model=DecisionView)
def accept_suggestion(body: DecisionInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> DecisionView:
    """Accept what a suggestion proposes, with the payload hash you were shown. Of two accepts, one wins. Changes it
    makes to rows are one batch you can undo."""
    _manage(context)
    item = _find(context, body.key)
    if not body.payload_hash or body.payload_hash != item.payload_hash:
        raise HTTPException(status_code=409, detail="This suggestion changed since you looked at it. Look at it again, then accept.")
    if item.action.kind == "open":
        raise HTTPException(status_code=409, detail="This one is resolved on its own page. Open it there, or dismiss it here.")
    if item.action.kind == "link":
        return _accept_link(context, item, repository)
    from .sms_service import owner_lock
    from .sms_store import open_sms_stores

    now = utc_now()
    batch = None
    with owner_lock(context), open_sms_stores(context) as (finance, sms):
        sms.lock_owner(context)
        store = rules_store_for(finance)
        batch_id = new_id("fin_batch") if item.action.kind in ("classify", "confirm_refund") else None
        if not store.claim_decision(_decision(context, item, "accepted", batch_id, now)):
            raise HTTPException(status_code=409, detail="This suggestion was already decided.")
        if batch_id is not None:
            changes = classify_changes(finance, context, item, by_owner=True)
            batch = RS.apply_rows(finance, sms, store, context, changes, origin="owner", by=context.user_id, now=now, suggestion_key=item.key,
                                  batch_id=batch_id)
    _audit(repository, context, "finance.suggestion.accept", item.producer, {"kind": item.kind, "rows": len(batch.changes) if batch else 0})
    return DecisionView(key=item.key, status="accepted", batch=_batch_view(batch) if batch else None)


def _accept_link(context: TenantContext, item: SG.Suggestion, repository: AnumRepository) -> Any:
    """Make the proposed link through ``finance.links`` (every link check applies). The decision is claimed first, so of two
    accepts one wins; when the link is refused the claim is released and the refusal is the answer."""
    from fastapi.responses import JSONResponse

    from . import links as K

    proposal = item.action.link
    if proposal is None:
        raise HTTPException(status_code=409, detail="This suggestion has nothing to link.")
    with open_rule_stores(context) as (_finance, store):
        if not store.claim_decision(_decision(context, item, "accepted", None, utc_now())):
            raise HTTPException(status_code=409, detail="This suggestion was already decided.")
    try:
        if proposal.dimension == "business":
            made = K.allocate(context, canonical_id=proposal.canonical_id, workspace_id=proposal.target_id, amount=proposal.amount, currency=proposal.currency,
                              category=proposal.category, project_id=proposal.project_id, memo=None, treatment=proposal.treatment or "owner_contribution",
                              idempotency_key=None)
        else:
            made = K.create(context, K.LinkRequest(canonical_id=proposal.canonical_id, dimension=proposal.dimension, role=proposal.role or "payment",
                                                   target_type=proposal.target_type, target_id=proposal.target_id, amount=proposal.amount,
                                                   currency=proposal.currency))
    except K.LinkError as exc:
        with open_rule_stores(context) as (_finance, store):
            store.delete_decision(context, item.key)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})
    _audit(repository, context, "finance.suggestion.accept", item.producer,
           {"kind": item.kind, "dimension": proposal.dimension, "link": made.link.id, "replayed": made.replayed})
    return DecisionView(key=item.key, status="accepted", link_id=made.link.id)


@router.post("/suggestions/reject", response_model=DecisionView)
def reject_suggestion(body: DecisionInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> DecisionView:
    """Dismiss a suggestion: it stays dismissed (a different proposal for the same row is a new suggestion)."""
    _manage(context)
    item = _find(context, body.key)
    with open_rule_stores(context) as (_finance, store):
        if not store.claim_decision(_decision(context, item, "rejected", None, utc_now())):
            raise HTTPException(status_code=409, detail="This suggestion was already decided.")
    _audit(repository, context, "finance.suggestion.reject", item.producer, {"kind": item.kind})
    return DecisionView(key=item.key, status="rejected")


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=3, max_length=200)


@router.post("/suggestions/restore", response_model=DecisionView)
def restore_suggestion(body: RestoreInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> DecisionView:
    """Bring back a dismissed suggestion (an accepted one is undone through its batch instead)."""
    _manage(context)
    with open_rule_stores(context) as (_finance, store):
        found = store.get_decision(context, body.key)
        if found is None or found.status != "rejected":
            raise _not_found("Dismissed suggestion")
        store.delete_decision(context, body.key)
    _audit(repository, context, "finance.suggestion.restore", found.producer)
    return DecisionView(key=body.key, status="open")


# -- automation policies -------------------------------------------------------------------------------------


class AutomationView(BaseModel):
    policy: AutomationPolicy
    updated_at: Any
    never_automated: list[str]


NEVER_TEXT = ["post_adjustment", "delete_record", "change_bank_mapping", "approve_payment", "change_period"]


@router.get("/automation", response_model=AutomationView)
def get_automation(context: TenantContext = Depends(tenant_context)) -> AutomationView:
    _read(context)
    with open_rule_stores(context) as (_finance, store):
        found = store.get_automation(context)
    return AutomationView(policy=found.policy if found else AutomationPolicy(), updated_at=found.updated_at if found else None,
                          never_automated=NEVER_TEXT)


@router.put("/automation", response_model=AutomationView)
def put_automation(body: AutomationPolicy, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> AutomationView:
    """The owner's automation switches. Only classification and notifications can ever be automated."""
    _manage(context)
    if body.auto_classify:
        require_automatable("classify")
    if body.notify_unresolved:
        require_automatable("notify")
    now = utc_now()
    with open_rule_stores(context) as (_finance, store):
        store.save_automation(AutomationSettings(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                                 policy=body, updated_at=now, updated_by=context.user_id))
    _audit(repository, context, "finance.automation.update", context.user_id,
           {"auto_classify": body.auto_classify, "min_confidence": str(body.min_confidence), "notify": body.notify_unresolved})
    return AutomationView(policy=body, updated_at=now, never_automated=NEVER_TEXT)


__all__ = ["classify_changes", "router"]
