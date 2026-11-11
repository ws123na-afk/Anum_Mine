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
from datetime import datetime, timedelta

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from ..schemas import TenantContext, utc_now
from .invoice_models import PortalAccess, PortalAccessStatus, SignInStatus
from .models import Account, Contact
from .store import open_business_store

PREFIX = "anum_portal"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# How often a request refreshes "last used" (a write on a read route is kept rare).
TOUCH_EVERY = timedelta(minutes=10)
INVALID = "This link is not valid. Ask the business that sent it for a new one."
# The id part of a session token made by the emailed code sign-in (portal_signin.py) rather than an access link.
SIGN_IN_PREFIX = "bpsign_"
SESSION_INVALID = "You are signed out. Sign in again with a new code."


class PortalSession(BaseModel):
    context: TenantContext
    access: PortalAccess
    account: Account
    contact: Contact
    # How the customer got in: their access link, or an emailed code (a session that ends sooner).
    via: str = "link"
    sign_in_id: str | None = None
    expires_at: datetime | None = None


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
    now = utc_now()
    sign_in = None
    if access_id.startswith(SIGN_IN_PREFIX):
        # A session from the emailed code sign-in: it signs in to the access the code was sent for.
        with open_business_store(portal_context(tenant_id, workspace_id, access_id)) as store:
            sign_in = store.get("portal_sign_ins", access_id)
            if (sign_in is None or sign_in.status != SignInStatus.SIGNED_IN or sign_in.session_hash is None or sign_in.access_id is None
                    or not hmac.compare_digest(sign_in.session_hash, secret_hash(secret))):
                raise _unauthorized(SESSION_INVALID)
            if sign_in.session_expires_at is None or sign_in.session_expires_at <= now:
                raise _unauthorized("Your sign-in has ended. Sign in again with a new code.")
            if not store.get_settings().portal_code_sign_in:
                raise _unauthorized("This business turned off signing in with a code. Open the link it sent you.")
        access_id = sign_in.access_id
    context = portal_context(tenant_id, workspace_id, access_id)
    with open_business_store(context) as store:
        access = store.get("portal_access", access_id)
        # The same answer for an unknown link and a wrong secret.
        if access is None or (sign_in is None and not hmac.compare_digest(access.token_hash, secret_hash(secret))):
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
    if sign_in is not None:
        return PortalSession(context=context, access=access, account=account, contact=contact, via="code", sign_in_id=sign_in.id,
                             expires_at=min(access.expires_at, sign_in.session_expires_at or access.expires_at))
    return PortalSession(context=context, access=access, account=account, contact=contact, expires_at=access.expires_at)


def portal_session(authorization: str | None = Header(default=None)) -> PortalSession:
    """The caller's portal session, from ``Authorization: Bearer <link token>``. A plain function
    on purpose: FastAPI runs it in its thread pool, never on the event loop."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Open the link you were sent.")
    return resolve(authorization.split(" ", 1)[1])
