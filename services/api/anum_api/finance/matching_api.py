"""Automatic matching, the record-linking policy, history backfill, installments and capture health under
``/api/v1/finance`` (docs/finance-matching.md).

* ``GET, PUT /automation/matching``: the owner's record-linking policy. Saving bumps its revision; ``approve`` records
  the owner's approval (nothing links on its own before). Audited with the switches, never amounts or names.
* ``POST /matching/run``: match the recent weeks now (the daily job does the same).
* ``GET /matching/records``: the engine's decisions (``outcome`` review, auto_linked, ...), with evidence and confidence.
* ``POST /matching/records/{id}/accept``, ``/reject``, ``/undo`` (``{version}``): the owner's choice, never overwritten.
* ``GET /matching/installments``: every provider order and installment with its match status.
* ``GET, POST /matching/backfill``, ``GET /matching/backfill/{id}``, ``POST /matching/backfill/{id}/continue``,
  ``POST /matching/backfill/{id}/undo``: historical reprocessing (preview, apply, progress, undo).
* ``GET /capture-health``: SMS and Apple Pay ingestion health.

Plain ``def`` routes: the database work runs in FastAPI's thread pool, never on the event loop. Reading needs finance
read; every change needs finance manage.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from . import backfill as BF
from . import capture_health as CH
from . import matching as E
from . import matching_service as MS
from .api import _audit, _invalid, _manage, _read
from . import links as K
from .links_api import refusals
from .match_policy import MatchingPolicy, SourceKind
from .match_store import BackfillRun, MatchRecord
from .rules import AutomationPolicy, AutomationSettings, require_automatable

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

# The one purchase-versus-installment rule (docs/finance-matching.md#counting-installment-purchases).
SPENDING_POLICY = "installments_when_paid"


# -- the policy ----------------------------------------------------------------------------------------------------


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_transactions: bool = False
    reconcile_installments: bool = False
    link_recurring_bills: bool = False
    link_card_repayments: bool = False
    link_bank_loan_repayments: bool = False
    prevent_duplicates: bool = True
    reprocess_history: bool = False
    notify_exceptions: bool = False
    min_confidence: Decimal = Field(default=Decimal("0.9"), ge=Decimal("0.8"), le=1)
    review_confidence: Decimal = Field(default=Decimal("0.5"), ge=Decimal("0.3"), le=Decimal("0.79"))
    settled_sources: list[SourceKind] = Field(default_factory=lambda: ["statement", "sms"], max_length=4)
    source_account_ids: list[str] = Field(default_factory=list, max_length=50)
    approve: bool = False  # the owner approves this policy as shown
    expected_revision: int | None = None


class PolicyView(BaseModel):
    policy: MatchingPolicy
    version: str
    approved: bool
    never_automated: list[str]
    providers: list[dict[str, Any]]
    spending_policy: str


def _settings(context: TenantContext) -> AutomationSettings | None:
    from .rules_store import open_rule_stores

    with open_rule_stores(context) as (_finance, store):
        return store.get_automation(context)


def _view(policy: MatchingPolicy) -> PolicyView:
    from .rules_api import NEVER_TEXT

    return PolicyView(policy=policy, version=policy.version, approved=policy.approved_at is not None, never_automated=NEVER_TEXT,
                      providers=[{"key": p.key, "name": p.name, "reads": list(p.reads), "connected": bool(p.reads)} for p in E.providers()],
                      spending_policy=SPENDING_POLICY)


@router.get("/automation/matching", response_model=PolicyView)
def get_matching_policy(context: TenantContext = Depends(tenant_context)) -> PolicyView:
    _read(context)
    found = _settings(context)
    return _view(found.policy.matching if found else MatchingPolicy())


@router.put("/automation/matching", response_model=PolicyView)
def put_matching_policy(body: PolicyInput, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> PolicyView:
    """Change the switches. Any change withdraws the approval unless this request approves it as sent."""
    from .rules_store import open_rule_stores

    _manage(context)
    for switch, action in (("match_transactions", "match_transactions"), ("reconcile_installments", "reconcile_installments"),
                           ("link_recurring_bills", "link_recurring_bills"), ("link_card_repayments", "link_card_repayments"),
                           ("link_bank_loan_repayments", "link_bank_loan_repayment"), ("reprocess_history", "reprocess_history")):
        if getattr(body, switch):
            require_automatable(action)
    now = utc_now()
    with open_rule_stores(context) as (finance, store):
        accounts = {a.id for a in finance.list_accounts(context)}
        if any(a not in accounts for a in body.source_account_ids):
            raise _invalid("A source account is not one of your accounts.")
        found = store.get_automation(context)
        current = found.policy if found else AutomationPolicy()
        old = current.matching
        if body.expected_revision is not None and body.expected_revision != old.revision:
            raise _invalid("The policy changed since you opened it. Reload and try again.")
        values = body.model_dump(exclude={"approve", "expected_revision"})
        same = all(getattr(old, k) == v for k, v in values.items())
        approved_at, approved_by = (old.approved_at, old.approved_by) if same else (None, None)
        if body.approve:
            approved_at, approved_by = now, context.user_id
        policy = MatchingPolicy(**values, revision=old.revision + (0 if same and not body.approve else 1), approved_at=approved_at,
                                approved_by=approved_by)
        store.save_automation(AutomationSettings(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                                 policy=current.model_copy(update={"matching": policy}), updated_at=now, updated_by=context.user_id))
    _audit(repository, context, "finance.matching.policy", context.user_id, {
        "version": policy.version, "approved": policy.approved_at is not None, "min_confidence": str(policy.min_confidence),
        "switches": ",".join(k for k in ("match_transactions", "reconcile_installments", "link_recurring_bills", "link_card_repayments",
                                         "link_bank_loan_repayments", "prevent_duplicates", "reprocess_history", "notify_exceptions")
                             if getattr(policy, k)),
        "source_accounts": len(policy.source_account_ids), "settled": ",".join(policy.settled_sources)})
    return _view(policy)


# -- running and the decisions -------------------------------------------------------------------------------------


class RunResult(BaseModel):
    counts: dict[str, int]
    policy_version: str


@router.post("/matching/run", response_model=RunResult)
@refusals
def run_matching(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Match the recent weeks now. Links only what the approved policy allows; everything else waits for review."""
    _manage(context)
    policy = MS.policy_for(context)
    counts = MS.run_recent(context)
    _audit(repository, context, "finance.matching.run", context.user_id, {k: v for k, v in counts.as_dict().items() if v})
    return RunResult(counts=counts.as_dict(), policy_version=policy.version)


class RecordPage(BaseModel):
    items: list[MatchRecord]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


@router.get("/matching/records", response_model=RecordPage)
def list_records(outcome: Literal["auto_linked", "review", "accepted", "rejected", "undone", "stale"] | None = Query(default=None),
                 kind: Literal["installment", "bill", "card_repayment", "bank_loan"] | None = Query(default=None),
                 run_id: str | None = Query(default=None, max_length=80),
                 limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100_000),
                 context: TenantContext = Depends(tenant_context)) -> RecordPage:
    from .links_store import open_link_stores

    _read(context)
    with open_link_stores(context) as (_finance, _sms, links):
        items, total = links.list_matches(context, outcome=outcome, kind=kind, run_id=run_id, limit=limit, offset=offset)
        counts = {o: links.list_matches(context, outcome=o, limit=1, offset=0)[1] for o in ("review", "auto_linked", "stale")}
    return RecordPage(items=items, total=total, limit=limit, offset=offset, counts=counts)


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = None


@router.post("/matching/records/{record_id}/{action}", response_model=MatchRecord)
@refusals
def decide_record(record_id: str, action: Literal["accept", "reject", "undo"], body: VersionInput,
                  context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    handler = {"accept": MS.accept, "reject": MS.reject, "undo": MS.undo}[action]
    record = handler(context, record_id, version=body.version)
    _audit(repository, context, f"finance.matching.{action}", record.id, {"kind": record.kind, "link": record.link_id or ""})
    return record


# -- installments --------------------------------------------------------------------------------------------------


class InstallmentRow(BaseModel):
    installment: int
    total_installments: int
    due_on: date
    amount: Decimal
    still_due: Decimal
    currency: str
    status: Literal["verified_paid", "partly_paid", "provisional", "review", "provider_paid", "refunded", "overdue", "due_soon", "upcoming",
                    "no_bill"]
    bill_id: str | None = None
    paid_amount: Decimal = Decimal(0)
    canonical_ids: list[str] = Field(default_factory=list)
    record_id: str | None = None
    record_version: int | None = None
    record_outcome: str | None = None
    confidence: Decimal | None = None
    reason_code: str | None = None


class InstallmentOrder(BaseModel):
    provider: str
    order_id: str
    merchant: str
    purchase_on: date | None
    currency: str
    total: Decimal
    paid: Decimal  # verified by a bank payment
    still_due: Decimal
    next_due_on: date | None
    installments: list[InstallmentRow]


class InstallmentView(BaseModel):
    orders: list[InstallmentOrder]
    spending_policy: str
    today: date


@router.get("/matching/installments", response_model=InstallmentView)
def installment_status(context: TenantContext = Depends(tenant_context)) -> InstallmentView:
    """Each provider order with its installments: verified paid (a bank payment is linked), provisionally matched, waiting
    for review, provider-reported only, refunded, or still due."""
    from .commitments import BILL_DIMENSION, BILL_TARGET, bill_target_id
    from .installment_refunds import bill_installment
    from .links_store import LinkStatus, open_link_stores
    from .models import Bill
    from .summary import settings_for, today_in

    _read(context)
    with open_link_stores(context) as (finance, _sms, links):
        today = today_in(settings_for(finance, context))
        entries = finance.get_planning(context).installment_entries
        bills = [b for b in finance.list_items(context, "bill") if isinstance(b, Bill)]
        rows, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None, status=None,
                                   limit=50_000, offset=0)
        records, _ = links.list_matches(context, kind="installment", limit=50_000, offset=0)
    by_part = {part: b for b in bills if (part := bill_installment(b)) is not None}
    by_target: dict[str, list[Any]] = {}
    for link in rows:
        if link.status != LinkStatus.VOID:
            by_target.setdefault(link.target_id, []).append(link)
    record_for: dict[str, MatchRecord] = {}
    for r in sorted(records, key=lambda r: r.updated_at):
        if r.outcome in ("review", "auto_linked", "accepted", "stale"):
            record_for[r.target_id] = r
    orders: dict[tuple[str, str], InstallmentOrder] = {}
    for e in sorted(entries, key=lambda e: (e.order_id, e.installment)):
        bill = by_part.get((e.order_id.casefold(), e.installment))
        due_amount = max(e.amount - e.refunded_amount, Decimal(0))
        target = bill_target_id(bill.id, e.due_on) if bill is not None else None
        live = by_target.get(target or "", [])
        active = [l for l in live if l.status == LinkStatus.ACTIVE and K.settled_currency(l) == e.currency]
        paid = sum((K.settled(l, e.currency) for l in active), Decimal(0))
        provisional = [l for l in live if l.status == LinkStatus.NEEDS_REVIEW]
        record = record_for.get(target or "")
        days = (e.due_on - today).days
        if e.source_status == "refunded" or (e.refunded_amount > 0 and due_amount == 0):
            state = "refunded"
        elif active and paid >= due_amount:
            state = "verified_paid"
        elif active:
            state = "partly_paid"
        elif provisional:
            state = "provisional"
        elif record is not None and record.outcome in ("review", "stale"):
            state = "review"
        elif e.source_status == "paid":
            state = "provider_paid"
        elif bill is None:
            state = "no_bill"
        else:
            state = "overdue" if days < 0 else "due_soon" if days <= 7 else "upcoming"
        still = Decimal(0) if state in ("verified_paid", "refunded") else max(due_amount - paid, Decimal(0))
        key = (e.provider, e.order_id)
        order = orders.setdefault(key, InstallmentOrder(provider=e.provider, order_id=e.order_id, merchant=e.merchant, purchase_on=e.purchase_on,
                                                        currency=e.currency, total=Decimal(0), paid=Decimal(0), still_due=Decimal(0), next_due_on=None,
                                                        installments=[]))
        order.total += due_amount
        order.paid += min(paid, due_amount)
        order.still_due += still
        if still > 0 and state not in ("provider_paid",) and (order.next_due_on is None or e.due_on < order.next_due_on):
            order.next_due_on = e.due_on
        order.installments.append(InstallmentRow(
            installment=e.installment, total_installments=e.total_installments, due_on=e.due_on, amount=e.amount, still_due=still, currency=e.currency,
            status=state, bill_id=bill.id if bill else None, paid_amount=paid, canonical_ids=sorted({l.canonical_id for l in live}),
            record_id=record.id if record else None, record_version=record.version if record else None,
            record_outcome=record.outcome if record else None, confidence=record.confidence if record else None,
            reason_code=record.reason_code if record else None))
    ordered = sorted(orders.values(), key=lambda o: (o.still_due == 0, o.next_due_on or date.max, o.order_id))
    return InstallmentView(orders=ordered, spending_policy=SPENDING_POLICY, today=today)


# -- history backfill ----------------------------------------------------------------------------------------------


class BackfillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["preview", "apply"]


class RunPage(BaseModel):
    items: list[BackfillRun]
    total: int


@router.get("/matching/backfill", response_model=RunPage)
def list_backfill(limit: int = Query(default=10, ge=1, le=50), offset: int = Query(default=0, ge=0),
                  context: TenantContext = Depends(tenant_context)) -> RunPage:
    from .links_store import open_link_stores

    _read(context)
    with open_link_stores(context) as (_finance, _sms, links):
        items, total = links.list_runs(context, limit=limit, offset=offset)
    return RunPage(items=items, total=total)


@router.get("/matching/backfill/{run_id}", response_model=BackfillRun)
@refusals
def get_backfill(run_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    from .links_store import open_link_stores

    _read(context)
    with open_link_stores(context) as (_finance, _sms, links):
        found = links.get_run(context, run_id)
    if found is None:
        raise BF.BackfillError(404, "History run not found.", "run_not_found")
    return found


@router.post("/matching/backfill", response_model=BackfillRun, status_code=status.HTTP_201_CREATED)
@refusals
def start_backfill(body: BackfillInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    """Start a run over all history; the first months are processed right away, ``continue`` does the rest."""
    _manage(context)
    if body.mode == "apply":
        require_automatable("reprocess_history")
    run = BF.start(context, body.mode)
    _audit(repository, context, "finance.matching.backfill.start", run.id, {"mode": run.mode, "policy": run.policy_version})
    return BF.step(context, run.id)


@router.post("/matching/backfill/{run_id}/continue", response_model=BackfillRun)
@refusals
def continue_backfill(run_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    run = BF.step(context, run_id)
    if run.status != "running":
        _audit(repository, context, "finance.matching.backfill.finish", run.id, {"status": run.status, "batches": run.batches,
                                                                                 **{k: v for k, v in run.counts.items() if v}})
    return run


class UndoResult(BaseModel):
    run: BackfillRun
    links_removed: int


@router.post("/matching/backfill/{run_id}/undo", response_model=UndoResult)
@refusals
def undo_backfill(run_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    run, removed = BF.undo(context, run_id)
    _audit(repository, context, "finance.matching.backfill.undo", run.id, {"links_removed": removed})
    return UndoResult(run=run, links_removed=removed)


# -- capture health ------------------------------------------------------------------------------------------------


@router.get("/capture-health", response_model=CH.CaptureHealth)
def capture_health(context: TenantContext = Depends(tenant_context)) -> CH.CaptureHealth:
    _read(context)
    return CH.build(context)


__all__ = ["SPENDING_POLICY", "router"]
