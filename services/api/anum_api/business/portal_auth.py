"""Access to the customer portal (docs/business-portal.md#access-model).

A customer is not a workspace member and never gets a workspace role. An owner creates an
access link for one contact of one client account. The link carries the tenant and workspace
(ids, not secrets), the access record's id and a 256-bit secret; only the SHA-256 of the
secret is stored. Every portal request resolves the link inside that tenant and workspace
under forced row-level security, so a link can only ever find rows of the workspace it was
issued in, and the portal's own code then reads nothing outside the one client account.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from ..schemas import TenantContext, utc_now
from .invoice_models import PortalAccess, PortalAccessStatus
from .models import Account, Contact
from .store import open_business_store

PREFIX = "anum_portal"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# How often a request refreshes "last used" (a write on a read route is kept rare).
TOUCH_EVERY = timedelta(minutes=10)
INVALID = "This link is not valid. Ask the business that sent it for a new one."


class PortalSession(BaseModel):
    context: TenantContext
    access: PortalAccess
    account: Account
    contact: Contact


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mint(tenant_id: str, workspace_id: str, access_id: str) -> tuple[str, str]:
    """A new link token and the hash to store. The token is shown once and never stored."""
    if not all(SAFE_ID.match(value) for value in (tenant_id, workspace_id, access_id)):
        raise ValueError("ids that cannot be part of a link")
    secret = secrets.token_urlsafe(32)
    return f"{PREFIX}.{tenant_id}.{workspace_id}.{access_id}.{secret}", secret_hash(secret)


def parse(token: str) -> tuple[str, str, str, str] | None:
    parts = token.strip().split(".")
    if len(parts) != 5 or parts[0] != PREFIX or not all(SAFE_ID.match(value) for value in parts[1:4]) or not (20 <= len(parts[4]) <= 100):
        return None
    return parts[1], parts[2], parts[3], parts[4]


def portal_context(tenant_id: str, workspace_id: str, access_id: str) -> TenantContext:
    """The context portal code runs in: the link's workspace, no workspace role, a distinct actor."""
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=f"portal:{access_id}", roles=[])


def _unauthorized(detail: str = INVALID) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def resolve(token: str) -> PortalSession:
    parsed = parse(token)
    if parsed is None:
        raise _unauthorized()
    tenant_id, workspace_id, access_id, secret = parsed
    context = portal_context(tenant_id, workspace_id, access_id)
    now = utc_now()
    with open_business_store(context) as store:
        access = store.get("portal_access", access_id)
        # The same answer for an unknown link and a wrong secret.
        if access is None or not hmac.compare_digest(access.token_hash, secret_hash(secret)):
            raise _unauthorized()
        if access.status != PortalAccessStatus.ACTIVE:
            raise _unauthorized("This link was turned off. Ask the business that sent it for a new one.")
        if access.expires_at <= now:
            raise _unauthorized("This link has expired. Ask the business that sent it for a new one.")
        account = store.get("accounts", access.account_id)
        contact = store.get("contacts", access.contact_id)
        if account is None or contact is None:
            raise _unauthorized("This link is no longer in use. Ask the business that sent it for a new one.")
        if account.do_not_contact or contact.do_not_contact:
            raise _unauthorized("This link is turned off. Ask the business that sent it for help.")
        if access.last_used_at is None or now - access.last_used_at >= TOUCH_EVERY:
            access = access.model_copy(update={"last_used_at": now, "updated_at": now})
            store.save("portal_access", access)
    return PortalSession(context=context, access=access, account=account, contact=contact)


def portal_session(authorization: str | None = Header(default=None)) -> PortalSession:
    """The caller's portal session, from ``Authorization: Bearer <link token>``. A plain function
    on purpose: FastAPI runs it in its thread pool, never on the event loop."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Open the link you were sent.")
    return resolve(authorization.split(" ", 1)[1])
