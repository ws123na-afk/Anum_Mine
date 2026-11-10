"""Personal trips and projects: tag a transaction to one (docs/finance.md#trips-and-projects). Under ``/api/v1/finance``.

ANUM has no trip or project module outside Business (a business project comes with its allocation), so the person keeps
their own: a trip (with its dates and place words) or a project. Tagging a payment is a **finance link** in the ``trip``
or ``project`` dimension (target ``finance_tag``) on the real ledger transaction, through ``links.create`` like every other
link: the owner's lock, idempotency, the payment's own currency and never more than the payment. A tag is a view only:
the payment still counts by its own category, nothing is posted, and nothing is tagged until the person tags it or
accepts a suggestion. Reading needs ``finance:read``; writing ``finance:manage``; every tag is the caller's own.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import links as K
from . import personal as P
from .api import _audit, _clean, _invalid, _manage, _read
from .commitments import is_money_out
from .links_api import LinkResult, refusals
from .links_store import open_link_stores
from .personal_api import MAX_PAGE, LinkInput, Movement, _delete, _link, _links, _movements, _own, _page, _save, _unlink
from .personal_store import open_personal_store

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

TagKind = Literal["trip", "project"]


class TagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TagKind
    name: str = Field(min_length=1, max_length=120)
    starts_on: date | None = None
    ends_on: date | None = None
    keywords: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)
    archived: bool = False


class TagView(BaseModel):
    id: str
    kind: TagKind
    name: str
    starts_on: date | None
    ends_on: date | None
    keywords: str | None
    notes: str | None
    archived: bool
    totals: dict[str, Decimal]  # what is tagged per currency, never added across currencies
    links: int


class TagDetail(TagView):
    movements: list[Movement]


class TagPage(BaseModel):
    items: list[TagView]
    total: int
    limit: int
    offset: int
    note: str = "A tag is a view: tagged payments still count by their own category, and nothing is posted or moved."


def _all_links(context: TenantContext) -> tuple[list, dict[str, Any]]:
    trips, entries = _links(context, P.TRIP_DIMENSION)
    projects, more = _links(context, P.PROJECT_DIMENSION)
    return trips + projects, {**entries, **more}


def _view(tag: P.PersonalTag, links: list) -> TagView:
    mine = [l for l in links if l.target_id == tag.id]
    return TagView(id=tag.id, kind=tag.kind, name=tag.name, starts_on=tag.starts_on, ends_on=tag.ends_on, keywords=tag.keywords, notes=tag.notes,
                   archived=tag.archived, totals=P.tag_totals(mine, tag.id), links=len(mine))


def _build(context: TenantContext, body: TagInput, existing: P.PersonalTag | None) -> P.PersonalTag:
    name = _clean(body.name, 120)
    if not name:
        raise _invalid("Give the trip or project a name.")
    if body.starts_on and body.ends_on and body.ends_on < body.starts_on:
        raise _invalid("The end date is before the start date.")
    now = utc_now()
    return P.PersonalTag(
        id=existing.id if existing else new_id("fin_tag"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, kind=body.kind, name=name, starts_on=body.starts_on, ends_on=body.ends_on,
        keywords=_clean(body.keywords, 200), notes=_clean(body.notes, 1000), archived=body.archived,
        created_at=existing.created_at if existing else now, updated_at=now)


@router.get("/tags", response_model=TagPage)
def list_tags(kind: TagKind | None = None, q: str | None = Query(default=None, max_length=80), archived: bool = False,
              limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
              context: TenantContext = Depends(tenant_context)) -> TagPage:
    """The caller's trips and projects, newest first, with what is tagged to each per currency."""
    _read(context)
    with open_personal_store(context) as store:
        tags = [t for t in store.list(context, "tag") if isinstance(t, P.PersonalTag)]
    needle = (q or "").strip().casefold()
    shown = [t for t in tags if (kind is None or t.kind == kind) and (archived or not t.archived)
             and (not needle or needle in t.name.casefold() or needle in (t.keywords or "").casefold())]
    links, _ = _all_links(context)
    return TagPage(items=[_view(t, links) for t in _page(shown, limit, offset)], total=len(shown), limit=limit, offset=offset)


@router.post("/tags", response_model=TagView, status_code=status.HTTP_201_CREATED)
def create_tag(body: TagInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TagView:
    _manage(context)
    tag = _build(context, body, None)
    _save(context, "tag", tag, new=True)
    _audit(repository, context, "finance.tag.create", tag.id, {"kind": tag.kind})
    return _view(tag, [])


@router.get("/tags/{tag_id}", response_model=TagDetail)
def get_tag(tag_id: str, context: TenantContext = Depends(tenant_context)) -> TagDetail:
    _read(context)
    tag = _own(context, "tag", tag_id, "Trip or project")
    links, entries = _links(context, tag.kind, tag_id)
    return TagDetail(**_view(tag, links).model_dump(), movements=_movements(links, entries, tag_id))


@router.patch("/tags/{tag_id}", response_model=TagView)
def update_tag(tag_id: str, body: TagInput, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> TagView:
    """Replace the tag with the full body sent. Changing a trip into a project is refused while payments are tagged."""
    _manage(context)
    existing = _own(context, "tag", tag_id, "Trip or project")
    links, _ = _links(context, existing.kind, tag_id)
    if links and body.kind != existing.kind:
        raise _invalid("Untag its payments before changing a trip into a project, or the other way round.")
    tag = _build(context, body, existing)
    _save(context, "tag", tag, new=False)
    _audit(repository, context, "finance.tag.update", tag.id, {"kind": tag.kind})
    return _view(tag, links)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_tag(tag_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete the tag and remove its links; the transactions stay in the ledger as they were."""
    _manage(context)
    tag = _own(context, "tag", tag_id, "Trip or project")
    _delete(context, "tag", tag_id, "Trip or project", tag.kind)
    _audit(repository, context, "finance.tag.delete", tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _role(context: TenantContext, canonical_id: str) -> str:
    """Money out is tagged as a payment, money in (a refund on the trip) as a refund."""
    with open_link_stores(context) as (_finance, store, _links_):
        entry = K.live_entry(store.chain(context, canonical_id))
    if entry is None:
        raise K.LinkError(404, "Transaction not found. It may have been deleted or undone.", "transaction_not_found")
    return "payment" if is_money_out(entry) else "refund"


@router.post("/tags/{tag_id}/links", response_model=LinkResult, status_code=status.HTTP_201_CREATED)
@refusals
def tag_transaction(tag_id: str, body: LinkInput, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
                    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Tag a transaction (or a part of it, ``amount``) to the trip or project, in the transaction's own currency."""
    _manage(context)
    tag = _own(context, "tag", tag_id, "Trip or project")
    role = _role(context, body.canonical_id)
    with open_link_stores(context) as (_finance, store, _links_):
        caps = K.capacity(store.chain(context, body.canonical_id))
    if len(caps) != 1:
        raise K.LinkError(422, "This transaction is in more than one currency. Tag a part of it in one currency from the links page.",
                          "currency_mismatch", {"currencies": sorted(caps)})
    currency = next(iter(caps))
    return _link(context, repository, dimension=tag.kind, target=P.TAG_TARGET, target_id=tag.id, role=role, currency=currency, body=body,
                 expected=Decimal(0), key=idempotency_key, action="finance.tag.link")


@router.delete("/tags/{tag_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@refusals
def untag_transaction(tag_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    tag = _own(context, "tag", tag_id, "Trip or project")
    _unlink(context, tag.kind, tag_id, link_id)
    _audit(repository, context, "finance.tag.unlink", link_id, {"target": tag_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- the link targets -----------------------------------------------------------------------------------


def _exists(dimension: str) -> Any:
    def check(context: TenantContext, target_type: str, target_id: str) -> bool:
        if target_type != P.TAG_TARGET:
            return False
        with open_personal_store(context) as store:
            found = store.get(context, "tag", target_id)
        return isinstance(found, P.PersonalTag) and found.kind == dimension
    return check


def _vet(context: TenantContext, request: Any, entry: Any) -> None:
    if request.role == "payment" and not is_money_out(entry):
        raise K.LinkError(422, "Tag money that went out as a payment; money that came back is a refund.", "not_a_payment")
    if request.role == "refund" and is_money_out(entry):
        raise K.LinkError(422, "A refund on a trip or project is money that came back into your account.", "not_money_in")
    if request.role not in ("payment", "refund"):
        raise K.LinkError(422, "A trip or project takes payments and refunds.", "invalid_role")


def plug_tag_links() -> None:
    """Register the ``trip`` and ``project`` link dimensions (personal targets)."""
    for dimension in P.TAG_DIMENSIONS:
        K.register_target(dimension, _exists(dimension), vet=_vet)


__all__ = ["plug_tag_links", "router"]
