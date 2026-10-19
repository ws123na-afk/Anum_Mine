"""Owner routes for the customer portal (``/api/v1/business/portal``, docs/business-portal.md#giving-access).

An owner gives one contact of a client account a link; the link is shown once. Members can see
who has access; only an owner creates or turns off a link. The portal itself is
``/api/v1/portal`` (``portal.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .common import get_or_404, manage, read
from .invoice_models import PortalAccess, PortalAccessStatus
from .portal_auth import mint
from .service import audit, http_error, record_activity
from .store import BusinessStore, Query, open_business_store

router = APIRouter(prefix="/api/v1/business/portal", tags=["business"])

MAX_ACTIVE_PER_ACCOUNT = 10


class AccessCreate(BaseModel):
    account_id: str = Field(max_length=80)
    contact_id: str = Field(max_length=80)
    expires_in_days: int = Field(default=90, ge=1, le=365)
    can_decide: bool = True


class AccessView(BaseModel):
    id: str
    account_id: str
    contact_id: str
    contact_name: str
    contact_email: str | None
    status: PortalAccessStatus
    expired: bool
    can_decide: bool
    expires_at: datetime
    last_used_at: datetime | None
    created_at: datetime
    created_by: str
    revoked_at: datetime | None


class AccessCreated(AccessView):
    # The link is shown once: only its hash is kept.
    token: str
    portal_path: str


def _view(store: BusinessStore, item: PortalAccess, now: datetime) -> AccessView:
    contact = store.get("contacts", item.contact_id)
    return AccessView(
        id=item.id, account_id=item.account_id, contact_id=item.contact_id, contact_name=item.contact_name, contact_email=contact.email if contact else None,
        status=item.status, expired=item.status == PortalAccessStatus.ACTIVE and item.expires_at <= now, can_decide=item.can_decide, expires_at=item.expires_at,
        last_used_at=item.last_used_at, created_at=item.created_at, created_by=item.created_by, revoked_at=item.revoked_at,
    )


@router.get("/access", response_model=list[AccessView])
def list_access(account_id: str, context: TenantContext = Depends(tenant_context)) -> list[AccessView]:
    read(context)
    now = utc_now()
    with open_business_store(context) as store:
        get_or_404(store, "accounts", account_id, "Account")
        items, _ = store.find("portal_access", Query(where={"account_id": account_id}, order_by="created_at", limit=100))
        return [_view(store, item, now) for item in items]


@router.post("/access", response_model=AccessCreated, status_code=201)
def create_access(payload: AccessCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AccessCreated:
    """Give a contact their link. It stops working on its expiry date or when an owner turns it off."""
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        account = get_or_404(store, "accounts", payload.account_id, "Account")
        contact = get_or_404(store, "contacts", payload.contact_id, "Contact")
        if contact.account_id != account.id:
            raise http_error(422, "That contact belongs to another account.")
        if account.do_not_contact or contact.do_not_contact:
            raise http_error(409, "This account or contact is marked do not contact, so it cannot be given a portal link.")
        active, _ = store.find("portal_access", Query(where={"account_id": account.id, "status": PortalAccessStatus.ACTIVE}, limit=100))
        if len([item for item in active if item.expires_at > now]) >= MAX_ACTIVE_PER_ACCOUNT:
            raise http_error(409, f"This account already has {MAX_ACTIVE_PER_ACCOUNT} active links. Turn one off first.")
        access_id = new_id("bpacc")
        try:
            token, hashed = mint(context.tenant_id, context.workspace_id, access_id)
        except ValueError:
            raise http_error(409, "This workspace's ids cannot be used in a portal link. Contact support.") from None
        item = PortalAccess(
            id=access_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account.id, contact_id=contact.id, contact_name=contact.name,
            token_hash=hashed, can_decide=payload.can_decide, expires_at=now + timedelta(days=payload.expires_in_days), created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("portal_access", item)
        record_activity(store, context, "portal_access_created", f"Portal link created for {contact.name} ({account.name}), valid {payload.expires_in_days} days.",
                        account_id=account.id, metadata={"access_id": access_id, "can_decide": payload.can_decide})
        view = _view(store, item, now)
    audit(repository, context, "business.portal.access_created", access_id, metadata={"account_id": account.id, "expires_in_days": payload.expires_in_days, "can_decide": payload.can_decide})
    return AccessCreated(**view.model_dump(), token=token, portal_path=f"/portal#access={token}")


@router.post("/access/{access_id}/revoke", response_model=AccessView)
def revoke_access(access_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AccessView:
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        item: PortalAccess = get_or_404(store, "portal_access", access_id, "Portal access", for_update=True)
        if item.status == PortalAccessStatus.ACTIVE:
            item = item.model_copy(update={"status": PortalAccessStatus.REVOKED, "revoked_at": now, "revoked_by": context.user_id, "updated_at": now})
            store.save("portal_access", item)
            record_activity(store, context, "portal_access_revoked", f"Portal link of {item.contact_name} turned off.", account_id=item.account_id, metadata={"access_id": access_id})
        view = _view(store, item, now)
    audit(repository, context, "business.portal.access_revoked", access_id, metadata={"account_id": item.account_id})
    return view


class DecisionRow(BaseModel):
    id: str
    subject_kind: str
    subject_id: str
    version: int | None
    decision: str
    signer_name: str
    note: str
    decided_at: datetime
    contact_name: str


@router.get("/decisions", response_model=list[DecisionRow])
def list_decisions(account_id: str | None = None, subject_id: str | None = None, context: TenantContext = Depends(tenant_context)) -> list[DecisionRow]:
    """What customers decided in their portals, newest first."""
    read(context)
    where: dict[str, Any] = {}
    if account_id:
        where["account_id"] = account_id
    if subject_id:
        where["subject_id"] = subject_id
    with open_business_store(context) as store:
        items, _ = store.find("portal_decisions", Query(where=where, order_by="decided_at", limit=100))
        contacts = {item.contact_id: store.get("contacts", item.contact_id) for item in items}
    return [DecisionRow(id=item.id, subject_kind=item.subject_kind.value, subject_id=item.subject_id, version=item.version, decision=item.decision, signer_name=item.signer_name,
                        note=item.note, decided_at=item.decided_at, contact_name=contacts[item.contact_id].name if contacts.get(item.contact_id) else "") for item in items]
