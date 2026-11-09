"""Finance links and business allocations (docs/finance-links.md).

One payment is one canonical transaction in the owner's journal (docs/finance-sms.md#canonical-transactions).
Other modules never post money: they **link** to the canonical transaction. A link names a
dimension (``vehicle``, ``purchase``, ``document``, ``business``, ``trip``, ``project``), a target in
that module, a role and, except for evidence, an amount in the transaction's currency.

Rules, all enforced here under the owner's lock (the per-owner process lock and the PostgreSQL
transaction-level advisory lock ingestion uses) and again by the database at commit:

* Active links of one dimension never add up to more than the transaction (its debits in that
  currency). Dimensions are independent views of one posting: a fuel payment can be a vehicle
  cost **and** a business allocation, and the journal is never written by either.
* Links point at ``canonical_id``, which every correction keeps, so a corrected payment keeps its
  links. When the correction changes the amount below what is linked, or the currency, the links
  wait as ``needs_review``. When the payment is undone (no live entry left), every link becomes
  ``void``. When a statement copy is reconciled into a recorded payment, its links move to it.
* Every write takes an idempotency key: the same request again returns the first link.

A **business allocation** is a link in the ``business`` dimension plus a row in the business
workspace (``business_ledger_allocations``) that the business's members can read: amount,
currency, category, project, date, a short source reference and a memo; never the bank, the
account, the card or the message (owner decision 1, 4 November 2026). The owner must hold
``business:write`` in that workspace. Removing the allocation removes the row.

Everything here is synchronous (FastAPI's thread pool), in short units of work, and calls no
model or provider while a lock or transaction is open.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from .ledger import EntrySource, EntryStatus, JournalEntry
from .links_store import DIMENSIONS, ROLES, BusinessAllocation, FinanceLink, LinkStatus, LinkStore, links_store_for, open_link_stores
from .money import MoneyError, normalize_currency, quantize

SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TREATMENTS = ("owner_contribution", "reimbursable")
BUSINESS_TARGET = "business_workspace"


class LinkError(Exception):
    def __init__(self, status_code: int, message: str, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.extra = extra or {}


# -- targets: modules register what they link -----------------------------------------------------

TargetCheck = Callable[[TenantContext, str, str], bool]  # (owner context, target_type, target_id) -> exists and is theirs
# Called after a link of the dimension is made or changes (status, canonical id), on the link's own unit of work
# (``links.session`` in PostgreSQL), so a module's read copy of the link commits or rolls back with it.
LinkObserver = Callable[[Any, FinanceLink], None]
# Called on every new link of the dimension, from any route, with the request and the transaction's live entry, under the
# owner's lock: a module refuses a role or a kind of transaction its targets do not take by raising ``LinkError``
# (docs/purchases.md#payments-and-refunds: a refund must give money back to a spending category, never be income).
LinkVet = Callable[[TenantContext, "LinkRequest", JournalEntry], None]
_targets: dict[str, TargetCheck] = {}
_observers: dict[str, LinkObserver] = {}
_vets: dict[str, LinkVet] = {}
_single_payment: set[str] = set()


def register_target(dimension: str, check: TargetCheck, observer: LinkObserver | None = None, *, single_payment: bool = False,
                    vet: LinkVet | None = None) -> None:
    """A module (vehicles, purchases, the document vault, trips, projects) says which of its records a
    payment may link to. A dimension with no registered module refuses links (``target_unavailable``).
    An ``observer`` keeps the module's read copy of its links current (docs/vehicles.md#payments)."""
    if dimension not in DIMENSIONS or dimension == "business":
        raise ValueError(f"unknown link dimension {dimension}")
    _targets[dimension] = check
    # A target that takes one payment (a fuel entry) refuses a second live payment link from any route (``target_linked``).
    if single_payment:
        _single_payment.add(dimension)
    else:
        _single_payment.discard(dimension)
    if observer is None:
        _observers.pop(dimension, None)
    else:
        _observers[dimension] = observer
    if vet is None:
        _vets.pop(dimension, None)
    else:
        _vets[dimension] = vet


def unregister_target(dimension: str) -> None:
    _targets.pop(dimension, None)
    _observers.pop(dimension, None)
    _vets.pop(dimension, None)
    _single_payment.discard(dimension)


def _observe(links: LinkStore, link: FinanceLink) -> None:
    observer = _observers.get(link.dimension)
    if observer is not None:
        observer(links, link)


def available_dimensions() -> list[str]:
    return ["business", *sorted(_targets)]


# -- the canonical transaction ---------------------------------------------------------------------


def live_entry(chain: list[JournalEntry]) -> JournalEntry | None:
    """The entry that currently stands for the payment: the newest posted one that is not a reversal or clearing."""
    for entry in sorted(chain, key=lambda e: (e.created_at, e.id), reverse=True):
        if entry.status == EntryStatus.POSTED and entry.source not in (EntrySource.REVERSAL, EntrySource.CLEARING):
            return entry
    return None


def capacity(chain: list[JournalEntry]) -> dict[str, Decimal]:
    """What may be linked per currency: the debits of every live entry (the database check uses the same rule)."""
    out: dict[str, Decimal] = {}
    for entry in chain:
        if entry.status == EntryStatus.POSTED and entry.source not in (EntrySource.REVERSAL, EntrySource.CLEARING):
            for posting in entry.postings:
                if posting.amount > 0:
                    out[posting.currency] = out.get(posting.currency, Decimal(0)) + posting.amount
    return out


def used(links: list[FinanceLink], dimension: str, currency: str, *, skip: str | None = None) -> Decimal:
    return sum((l.amount or Decimal(0) for l in links if l.dimension == dimension and l.currency == currency and l.id != skip
                and l.status in (LinkStatus.ACTIVE, LinkStatus.NEEDS_REVIEW)), Decimal(0))


def idem_key(context: TenantContext, canonical_id: str, dimension: str, target_type: str, target_id: str, role: str, client_key: str | None) -> str:
    seed = client_key.strip() if client_key and client_key.strip() else "\x1f".join((canonical_id, dimension, target_type, target_id, role))
    return hashlib.sha256(f"{context.user_id}\x1f{seed}".encode()).hexdigest()


@dataclass
class LinkRequest:
    canonical_id: str
    dimension: str
    role: str
    target_type: str
    target_id: str
    amount: Decimal | None = None
    currency: str | None = None
    document_id: str | None = None
    idempotency_key: str | None = None


def _validate(request: LinkRequest) -> LinkRequest:
    if request.dimension not in DIMENSIONS:
        raise LinkError(422, "Choose what to link the payment to: vehicle, purchase, document, business, trip, project, bill, debt, "
                        "transfer, a loan with a person, a payslip or a remittance.", "invalid_dimension")
    if request.role not in ROLES:
        raise LinkError(422, "Choose the link's role: payment, refund, deposit or evidence.", "invalid_role")
    for value in (request.canonical_id, request.target_type, request.target_id, request.document_id or "x"):
        if not SAFE_ID.match(value):
            raise LinkError(422, "An id in the request is not valid.", "invalid_id")
    if request.role == "evidence":
        if request.amount is not None or request.currency is not None:
            raise LinkError(422, "Evidence (a receipt or document) carries no amount.", "evidence_amount")
        return request
    if request.amount is None or request.currency is None:
        raise LinkError(422, "Give the amount and currency of this part of the payment.", "missing_amount")
    try:
        currency = normalize_currency(request.currency)
        amount = quantize(Decimal(str(request.amount)), currency)
    except (MoneyError, ArithmeticError, ValueError):
        raise LinkError(422, "The amount or currency is not valid.", "invalid_amount") from None
    if amount <= 0:
        raise LinkError(422, "The amount must be more than zero.", "invalid_amount")
    request.amount, request.currency = amount, currency
    return request


# -- writes ----------------------------------------------------------------------------------------


@dataclass
class Created:
    link: FinanceLink
    replayed: bool = False


def create(context: TenantContext, request: LinkRequest, *, allocation: dict[str, Any] | None = None, now: datetime | None = None,
           review: bool = False) -> Created:
    """Link (part of) a canonical transaction to a target; for ``business``, also write the allocation row.

    ``review``: the link is proposed, not settled (``needs_review``, reason ``weak_match``): a statement import found
    a payment that might pay a bill. The person confirms or removes it; until then it never counts as paid."""
    from .sms_service import owner_lock

    request = _validate(request)
    now = now or utc_now()
    if request.dimension == "business" and allocation is None:
        raise LinkError(422, "Allocate to a business with the allocations address, which also writes the business's row.", "use_allocations")
    if request.dimension != "business":
        check = _targets.get(request.dimension)
        if check is None:
            raise LinkError(409, f"Linking to a {request.dimension} is not available in ANUM yet.", "target_unavailable")
        if not check(context, request.target_type, request.target_id):
            raise LinkError(404, f"That {request.dimension} was not found. It may have been deleted.", "target_not_found")
    key = idem_key(context, request.canonical_id, request.dimension, request.target_type, request.target_id, request.role, request.idempotency_key)
    with owner_lock(context), open_link_stores(context) as (_finance, store, links):
        store.lock_owner(context)  # the same lock ingestion and corrections take: no race with a reversal
        existing = links.link_by_idem(context, key)
        if existing is not None:
            same = (existing.canonical_id == request.canonical_id and existing.dimension == request.dimension and existing.role == request.role
                    and existing.target_type == request.target_type and existing.target_id == request.target_id
                    and existing.amount == request.amount and existing.currency == request.currency)
            if not same:
                raise LinkError(409, "This idempotency key was used for a different link.", "idempotency_conflict")
            return Created(existing, replayed=True)
        chain = store.chain(context, request.canonical_id)
        if not chain:
            raise LinkError(404, "Transaction not found. It may have been deleted.", "transaction_not_found")
        current = live_entry(chain)
        if current is None:
            raise LinkError(409, "This payment was undone, so nothing can be linked to it.", "transaction_undone")
        vet = _vets.get(request.dimension)
        if vet is not None:
            vet(context, request, current)
        if request.dimension in _single_payment and request.role == "payment":
            taken, _ = links.list_links(context, canonical_id=None, dimension=request.dimension, target_type=request.target_type,
                                        target_id=request.target_id, status=None, limit=50, offset=0)
            if any(l.status != LinkStatus.VOID and l.role == "payment" and l.canonical_id != request.canonical_id for l in taken):
                raise LinkError(409, f"This {request.dimension} record is already linked to another payment. Remove that link first.", "target_linked")
        current_links = links.live_links(context, request.canonical_id)
        if request.amount is not None and request.currency is not None:
            caps = capacity(chain)
            if request.currency not in caps:
                raise LinkError(422, f"The payment is not in {request.currency}. Link it in its own currency.", "currency_mismatch",
                                {"currencies": sorted(caps)})
            remaining = caps[request.currency] - used(current_links, request.dimension, request.currency)
            if request.amount > remaining:
                raise LinkError(409, f"Only {remaining} {request.currency} of this payment is left to link as {request.dimension}.", "over_allocated",
                                {"remaining": str(max(remaining, Decimal(0))), "currency": request.currency})
        sources = store.entry_sources(context, [e.id for e in chain])
        link = FinanceLink(
            id=new_id("fin_link"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            canonical_id=request.canonical_id, dimension=request.dimension, role=request.role, target_type=request.target_type,
            target_id=request.target_id, target_workspace_id=request.target_id if request.dimension == "business" else None,
            amount=request.amount, currency=request.currency, status=LinkStatus.NEEDS_REVIEW if review else LinkStatus.ACTIVE,
            status_reason="weak_match" if review else None, document_id=request.document_id,
            source_event_ids=sorted({m.id for rows in sources.values() for m in rows})[:50], idem_key=key, created_by=context.user_id,
            created_at=now, updated_at=now,
        )
        links.add_link(link)
        _observe(links, link)
        if request.dimension == "business" and allocation is not None:
            links.put_allocation(context, BusinessAllocation(
                id=new_id("biz_alloc"), tenant_id=context.tenant_id, workspace_id=request.target_id, source_link_id=link.id,
                source_workspace_id=context.workspace_id, allocated_by=context.user_id, source_ref=request.canonical_id[-6:],
                posted_on=current.posted_on, amount=request.amount, currency=request.currency,  # type: ignore[arg-type]
                category=allocation.get("category") or current.category or "other", project_id=allocation.get("project_id"),
                memo=allocation.get("memo"), treatment=allocation["treatment"], created_at=now, updated_at=now))
    return Created(link)


def void(context: TenantContext, link_id: str, *, expected_version: int | None, now: datetime | None = None) -> FinanceLink:
    """Remove a link (``void``); a business allocation's row leaves the business workspace with it."""
    from .sms_service import owner_lock

    now = now or utc_now()
    with owner_lock(context), open_link_stores(context) as (_finance, store, links):
        store.lock_owner(context)
        link = links.get_link(context, link_id)
        if link is None:
            raise LinkError(404, "Link not found. It may have been removed.", "link_not_found")
        if link.status == LinkStatus.VOID:
            return link
        if expected_version is not None and link.version != expected_version:
            raise LinkError(409, "This link changed since you opened it. Reload and try again.", "stale")
        _void(links, context, link, "removed_by_owner", now)
    return link


def confirm(context: TenantContext, link_id: str, *, expected_version: int | None, now: datetime | None = None) -> FinanceLink:
    """Keep a ``needs_review`` link after the payment changed, if it still fits."""
    from .sms_service import owner_lock

    now = now or utc_now()
    with owner_lock(context), open_link_stores(context) as (_finance, store, links):
        store.lock_owner(context)
        link = links.get_link(context, link_id)
        if link is None:
            raise LinkError(404, "Link not found. It may have been removed.", "link_not_found")
        if link.status != LinkStatus.NEEDS_REVIEW:
            raise LinkError(409, "Only a link waiting for review can be confirmed.", "not_in_review")
        if expected_version is not None and link.version != expected_version:
            raise LinkError(409, "This link changed since you opened it. Reload and try again.", "stale")
        chain = store.chain(context, link.canonical_id)
        if live_entry(chain) is None:
            raise LinkError(409, "This payment was undone, so the link cannot be kept.", "transaction_undone")
        if link.amount is not None and link.currency is not None:
            caps = capacity(chain)
            remaining = caps.get(link.currency, Decimal(0)) - used(links.live_links(context, link.canonical_id), link.dimension, link.currency, skip=link.id)
            if link.currency not in caps or link.amount > remaining:
                raise LinkError(409, "The payment changed and this link no longer fits. Remove it or reduce other links first.", "over_allocated",
                                {"remaining": str(max(remaining, Decimal(0))), "currency": link.currency})
        _save(links, link, status=LinkStatus.ACTIVE, reason=None, now=now)
        if link.dimension == "business" and link.target_workspace_id:
            current = live_entry(chain)
            links.set_allocation(context, link.target_workspace_id, link.id, status="active", posted_on=current.posted_on, now=now)  # type: ignore[union-attr]
    return link


def _save(links: LinkStore, link: FinanceLink, *, status: LinkStatus, reason: str | None, now: datetime) -> None:
    expected = link.version
    link.status, link.status_reason, link.updated_at, link.version = status, reason, now, expected + 1
    if status == LinkStatus.VOID:
        link.voided_at = now
    if not links.save_link(link, expected_version=expected):
        raise LinkError(409, "This link changed at the same time. Reload and try again.", "stale")
    _observe(links, link)


def _void(links: LinkStore, context: TenantContext, link: FinanceLink, reason: str, now: datetime) -> None:
    _save(links, link, status=LinkStatus.VOID, reason=reason, now=now)
    if link.dimension == "business" and link.target_workspace_id:
        links.delete_allocation(context, link.target_workspace_id, link.id)


# -- following the journal -------------------------------------------------------------------------


def sync(store: Any, links: LinkStore, context: TenantContext, canonical_id: str, now: datetime) -> None:
    """Bring one transaction's links in line with its journal chain (the caller holds the owner's lock)."""
    live = links.live_links(context, canonical_id)
    if not live:
        return
    chain = store.chain(context, canonical_id)
    current = live_entry(chain)
    if current is None:
        for link in live:
            _void(links, context, link, "transaction_undone", now)
        return
    caps = capacity(chain)
    flagged: set[str] = set()
    for link in live:
        if link.currency is not None and link.currency not in caps:
            flagged.add(link.id)
    groups: dict[tuple[str, str], list[FinanceLink]] = {}
    for link in live:
        if link.currency is not None and link.id not in flagged:
            groups.setdefault((link.dimension, link.currency), []).append(link)
    for (_dimension, currency), rows in groups.items():
        if sum((l.amount or Decimal(0) for l in rows), Decimal(0)) > caps[currency]:
            flagged.update(l.id for l in rows)
    for link in live:
        if link.id in flagged and link.status == LinkStatus.ACTIVE:
            _save(links, link, status=LinkStatus.NEEDS_REVIEW, reason="transaction_changed", now=now)
        if link.dimension == "business" and link.target_workspace_id:
            links.set_allocation(context, link.target_workspace_id, link.id, status=link.status.value if link.status != LinkStatus.VOID else "needs_review",
                                 posted_on=current.posted_on, now=now)


def carry_forward(store: Any, links: LinkStore, context: TenantContext, from_canonical: str, to_canonical: str, now: datetime) -> None:
    """A statement copy was reconciled into a recorded payment: its links now point at the recorded one."""
    kept = links.live_links(context, to_canonical)
    for link in links.live_links(context, from_canonical):
        # A business allocation is never merged away: its row may carry another project, category or memo. It moves
        # with its row, and sync puts the business dimension in review if the recorded payment is now over-allocated.
        twin = None if link.dimension == "business" else next((k for k in kept if (k.dimension, k.role, k.target_type, k.target_id, k.amount, k.currency)
                                                               == (link.dimension, link.role, link.target_type, link.target_id, link.amount, link.currency)), None)
        if twin is not None:
            _void(links, context, link, "merged_on_reconcile", now)  # the recorded payment already has exactly this link
            continue
        # A key of its own (derived from the old one), so links split by project or client key all move; sync then
        # puts any dimension that no longer fits the recorded payment in review.
        link.canonical_id = to_canonical
        link.idem_key = hashlib.sha256(f"moved\x1f{to_canonical}\x1f{link.idem_key}".encode()).hexdigest()
        _save(links, link, status=link.status, reason="moved_on_reconcile", now=now)
        if link.dimension == "business" and link.target_workspace_id:
            links.retarget_allocation(context, link.target_workspace_id, link.id, source_ref=to_canonical[-6:], now=now)


def settle(finance: Any, store: Any, context: TenantContext, notes: list[tuple[str, str, str]]) -> None:
    """Called by ``open_sms_stores`` before a unit of work that reversed or reconciled entries commits."""
    links = links_store_for(store)
    store.lock_owner(context)
    now = utc_now()
    for kind, first, second in notes:
        if kind == "move":
            carry_forward(store, links, context, first, second, now)
    for canonical in dict.fromkeys(first for kind, first, _ in notes if kind == "sync"):
        sync(store, links, context, canonical, now)


# -- business allocations --------------------------------------------------------------------------


def can_allocate_to(context: TenantContext, workspace_id: str) -> bool:
    """The caller is an active member of ``workspace_id`` (same tenant) with ``business:write`` there."""
    from ..authorization import ROLE_PERMISSIONS, Permission
    from ..dependencies import lookup_membership, workspace_archived

    if workspace_id == context.workspace_id or not SAFE_ID.match(workspace_id):
        return False
    target = context.model_copy(update={"workspace_id": workspace_id})
    membership = lookup_membership(target)
    if membership is None or not membership.active:
        return False
    if Permission.BUSINESS_WRITE not in ROLE_PERMISSIONS.get(membership.role, frozenset()):
        return False
    return not workspace_archived(target)


def allocate(context: TenantContext, *, canonical_id: str, workspace_id: str, amount: Any, currency: str, category: str | None,
             project_id: str | None, memo: str | None, treatment: str, idempotency_key: str | None, now: datetime | None = None) -> Created:
    if treatment not in TREATMENTS:
        raise LinkError(422, "Choose how the business treats it: paid by the owner, or to be reimbursed.", "invalid_treatment")
    if category is not None and not CATEGORY.match(category):
        raise LinkError(422, "The category is not valid.", "invalid_category")
    if project_id is not None and not SAFE_ID.match(project_id):
        raise LinkError(422, "The project id is not valid.", "invalid_id")
    if not SAFE_ID.match(workspace_id) or not can_allocate_to(context, workspace_id):
        # The same answer whether the workspace does not exist, is in another tenant or the caller may not write there.
        raise LinkError(403, "You can allocate only to a business workspace where you can change business records.", "workspace_not_allowed")
    if idempotency_key is None:
        # One payment can be split inside one business by project and category: one allocation for each pair.
        idempotency_key = f"allocation\x1f{canonical_id}\x1f{workspace_id}\x1f{project_id or '-'}\x1f{category or '-'}"
    request = LinkRequest(canonical_id=canonical_id, dimension="business", role="payment", target_type=BUSINESS_TARGET, target_id=workspace_id,
                          amount=amount, currency=currency, idempotency_key=idempotency_key)
    memo_text = " ".join((memo or "").split())[:200] or None
    return create(context, request, allocation={"category": category, "project_id": project_id, "memo": memo_text, "treatment": treatment}, now=now)


# -- the document vault -----------------------------------------------------------------------------


def _vault_document(context: TenantContext, target_type: str, target_id: str) -> bool:
    """A vault document the owner may edit (``vault_document``)."""
    from fastapi import HTTPException

    from ..vault.service import editable
    from ..vault.store import open_vault_unit

    if target_type != "vault_document":
        return False
    try:
        with open_vault_unit(context) as unit:
            editable(unit, context, target_id)
    except HTTPException:
        return False
    return True


class VaultLinkAdapter:
    """The vault's ``FinanceLinkPort``: a receipt or document becomes **evidence** of a canonical transaction.
    It carries no amount and never posts money; the link is active at once (the owner chose the transaction)."""

    def propose(self, context: TenantContext, document_id: str, transaction_ref: str) -> str:
        from fastapi import HTTPException

        from ..authorization import Permission
        from ..dependencies import has_permission

        if not has_permission(context, Permission.FINANCE_MANAGE):
            raise HTTPException(403, "Linking a document to a payment needs finance access in this workspace.")
        try:
            create(context, LinkRequest(canonical_id=transaction_ref, dimension="document", role="evidence", target_type="vault_document",
                                        target_id=document_id))
        except LinkError as exc:
            raise HTTPException(exc.status_code, exc.message) from None
        return "confirmed"

    def withdraw(self, context: TenantContext, document_id: str, transaction_ref: str) -> None:
        """Void the document's evidence link on that payment (or on the payment it moved to on reconciliation)."""
        from fastapi import HTTPException

        with open_link_stores(context) as (_finance, _store, links):
            rows, _ = links.list_links(context, canonical_id=None, dimension="document", target_type="vault_document", target_id=document_id,
                                       status=None, limit=200, offset=0)
        live = [l for l in rows if l.status != LinkStatus.VOID and l.role == "evidence"]
        doomed = [l for l in live if l.canonical_id == transaction_ref] or [l for l in live if l.status_reason == "moved_on_reconcile"]
        try:
            for link in doomed:
                void(context, link.id, expected_version=None)
        except LinkError as exc:
            raise HTTPException(exc.status_code, exc.message) from None


def plug_vault_links() -> bool:
    """Connect the vault's finance link point and its document targets, when the vault is installed."""
    try:
        from ..vault import links as vault_links
    except ModuleNotFoundError:
        return False
    vault_links.set_finance_links(VaultLinkAdapter())
    if "document" not in _targets:
        register_target("document", _vault_document)
    return True


__all__ = [
    "Created",
    "LinkError",
    "LinkRequest",
    "allocate",
    "available_dimensions",
    "can_allocate_to",
    "capacity",
    "carry_forward",
    "confirm",
    "create",
    "live_entry",
    "plug_vault_links",
    "register_target",
    "settle",
    "sync",
    "unregister_target",
    "void",
]
