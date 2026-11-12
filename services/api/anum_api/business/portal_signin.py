"""Signing in to the customer portal with an emailed code (docs/business-portal.md#signing-in-with-an-emailed-code).

Next to the access link (``portal_auth``), an owner may let customers sign in with a one-time code sent to the
email address of a contact who already has portal access. The owner turns it on per workspace (Business,
Customer portal) and shares the workspace's sign-in address ``/portal#signin=<tenant>.<workspace>``.

* ``POST /api/v1/portal/sign-in/code``: the customer types their address. The answer is the same whether or not
  the address may sign in (``202`` with a challenge id): an address with no access gets a challenge too, nothing
  is sent, and it counts towards the same limits. The email goes out after the answer, so the time it takes
  says nothing either. A code is six digits, works for ten minutes and takes five tries.
* ``POST /api/v1/portal/sign-in/verify``: the challenge id and the code. A right code makes a session token in
  the shape of an access link (``anum_portal.<tenant>.<workspace>.<sign-in id>.<secret>``) that lasts twelve
  hours, never past the access's own expiry, and stops when the access is turned off or the owner turns code
  sign-in off. ``POST /api/v1/portal/sign-in/end`` signs it out.
* Limits, stored in the workspace (so every server counts the same): five codes an hour and ten a day per address,
  twenty an hour per IP address. Above them the answer is ``429`` for everyone alike.
* Only hashes are stored: the address and the IP address (SHA-256 with a fixed label), the code (HMAC-SHA256 with
  a key from ``ANUM_SECRETS_KEY``) and the session (SHA-256, like a link). Every code sent, sign-in and failed
  code is in the business's activity log and the audit log, never with the address or the code.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from pydantic import BaseModel, Field

from ..schemas import new_id, utc_now
from ..settings import settings
from .invoice_models import PortalAccess, PortalAccessStatus, PortalSignIn, SignInStatus
from .portal_auth import PREFIX, SAFE_ID, SIGN_IN_PREFIX, PortalSession, portal_context, portal_session, secret_hash
from .service import audit, http_error, record_activity
from .store import BusinessStore, Query, open_business_store

logger = logging.getLogger("anum.business")

CODE_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(hours=12)
MAX_ATTEMPTS = 5
PER_ADDRESS_HOUR = 5
PER_ADDRESS_DAY = 10
PER_IP_HOUR = 20
EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
WORKSPACE = r"^[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{1,80}$"
SENT = "If this address may use this business's portal, a code is on its way. It works for 10 minutes."
OFF = "This business does not offer signing in with a code. Open the link the business sent you, or ask it for one."
LIMIT = "Too many codes were asked for. Wait an hour and try again, or open the link the business sent you."
WRONG = "This code is not right, or it has expired. Check the latest email, or ask for a new code."
SIGN_IN_USER = "portal:sign-in"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(prefix="/api/v1/portal/sign-in", tags=["portal"], dependencies=[Depends(_no_store)])


class CodeRequest(BaseModel):
    # ``<tenant>.<workspace>`` from the business's sign-in address.
    workspace: str = Field(min_length=3, max_length=161, pattern=WORKSPACE)
    email: str = Field(min_length=3, max_length=254)


class CodeAnswer(BaseModel):
    challenge_id: str
    expires_in_seconds: int
    message: str


class VerifyRequest(BaseModel):
    workspace: str = Field(min_length=3, max_length=161, pattern=WORKSPACE)
    challenge_id: str = Field(min_length=10, max_length=80, pattern=r"^bpsign_[a-f0-9]{32}$")
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class VerifyAnswer(BaseModel):
    token: str
    expires_at: datetime


def _label_hash(label: str, value: str) -> str:
    return hashlib.sha256(f"anum/portal-sign-in/{label}/v1:{value}".encode()).hexdigest()


def _code_key() -> bytes:
    secret = settings.secrets_key.get_secret_value() if settings.secrets_key is not None else "anum-local-portal-codes"
    return hashlib.sha256(f"anum/portal-sign-in/code-key/v1:{secret}".encode()).digest()


def code_hash(challenge_id: str, code: str) -> str:
    return hmac.new(_code_key(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def normalize(email: str) -> str:
    return email.strip().lower()


def _scope(workspace: str) -> tuple[str, str]:
    tenant_id, _, workspace_id = workspace.partition(".")
    if not SAFE_ID.match(tenant_id) or not SAFE_ID.match(workspace_id):
        raise http_error(422, "This sign-in address is not complete. Open the address the business shared again.")
    return tenant_id, workspace_id


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _access_for(store: BusinessStore, email: str, now: datetime) -> tuple[PortalAccess, str] | None:
    """The newest active access whose contact has this address, with the contact's name."""
    items, _ = store.find("portal_access", Query(where={"status": PortalAccessStatus.ACTIVE}, order_by="created_at", limit=1000))
    for item in items:
        if item.expires_at <= now:
            continue
        contact = store.get("contacts", item.contact_id)
        account = store.get("accounts", item.account_id)
        if contact is None or account is None or contact.do_not_contact or account.do_not_contact:
            continue
        if contact.email and normalize(contact.email) == email:
            return item, contact.name
    return None


def _business_name(store: BusinessStore) -> str:
    profile = store.get_settings().billing
    return (profile.legal_name or "").strip() or "the business"


def code_email(business: str, code: str) -> tuple[str, str]:
    subject = f"Your sign-in code for {business}"[:200]
    text = (f"Your code to sign in to {business}'s customer portal is {code}.\n\n"
            "It works for 10 minutes and only once. If you did not ask for it, ignore this email: nobody can sign in without the code.\n\n"
            f"رمز الدخول إلى بوابة العملاء لدى {business} هو {code}. يعمل لمدة 10 دقائق ولمرة واحدة. إذا لم تطلبه فتجاهل هذه الرسالة.")
    return subject, text


def _send_code(to: str, subject: str, text: str) -> None:
    """Runs after the answer, in the thread pool: nothing waits on SMTP and no transaction is open."""
    from ..notifications import send_with_retries

    outcome = send_with_retries(to, subject, text)
    if outcome.status != "sent":
        logger.warning("business_portal_code_not_sent", extra={"anum_business": {"event": "portal_code_not_sent"}})


@router.post("/code", response_model=CodeAnswer, status_code=status.HTTP_202_ACCEPTED)
def request_code(payload: CodeRequest, request: Request, background: BackgroundTasks) -> CodeAnswer:
    """Ask for a code. The answer never says whether the address may sign in."""
    from ..notifications import email_configured

    tenant_id, workspace_id = _scope(payload.workspace)
    email = normalize(payload.email)
    if not EMAIL.match(email):
        raise http_error(422, "Enter your email address in full, like name@company.com.")
    context = portal_context(tenant_id, workspace_id, "sign-in").model_copy(update={"user_id": SIGN_IN_USER})
    now = utc_now()
    email_hash, ip_hash = _label_hash("email", email), _label_hash("ip", _client_ip(request))
    challenge_id = new_id(SIGN_IN_PREFIX.rstrip("_"))
    code = f"{secrets.randbelow(1_000_000):06d}"
    send: tuple[str, str, str] | None = None
    with open_business_store(context) as store:
        if not store.get_settings().portal_code_sign_in:
            raise http_error(403, OFF)
        hour, day = now - timedelta(hours=1), now - timedelta(days=1)
        if (store.count("portal_sign_ins", Query(where={"email_hash": email_hash}, created_from=hour)) >= PER_ADDRESS_HOUR
                or store.count("portal_sign_ins", Query(where={"email_hash": email_hash}, created_from=day)) >= PER_ADDRESS_DAY
                or store.count("portal_sign_ins", Query(where={"ip_hash": ip_hash}, created_from=hour)) >= PER_IP_HOUR):
            audit(None, context, "business.portal.sign_in_limited", workspace_id, outcome="denied")
            raise http_error(429, LIMIT)
        found = _access_for(store, email, now) if email_configured() else None
        row = PortalSignIn(
            id=challenge_id, tenant_id=tenant_id, workspace_id=workspace_id, email_hash=email_hash, ip_hash=ip_hash,
            access_id=found[0].id if found else None, code_hash=code_hash(challenge_id, code) if found else None,
            status=SignInStatus.PENDING if found else SignInStatus.UNMATCHED, expires_at=now + CODE_TTL, created_at=now, updated_at=now,
        )
        store.add("portal_sign_ins", row)
        if found:
            access, name = found
            record_activity(store, context, "portal_sign_in_code_sent", f"A sign-in code was emailed to {name} for the customer portal.",
                            account_id=access.account_id, metadata={"access_id": access.id, "sign_in_id": challenge_id})
            send = (email, *code_email(_business_name(store), code))
    audit(None, context, "business.portal.sign_in_code_requested", challenge_id, metadata={"matched": send is not None})
    if send is not None:
        background.add_task(_send_code, *send)
    return CodeAnswer(challenge_id=challenge_id, expires_in_seconds=int(CODE_TTL.total_seconds()), message=SENT)


@router.post("/verify", response_model=VerifyAnswer)
def verify_code(payload: VerifyRequest) -> VerifyAnswer:
    """Use the code once: a session token comes back. A wrong code counts; the fifth wrong one ends the challenge."""
    tenant_id, workspace_id = _scope(payload.workspace)
    context = portal_context(tenant_id, workspace_id, "sign-in").model_copy(update={"user_id": SIGN_IN_USER})
    now = utc_now()
    with open_business_store(context) as store:
        if not store.get_settings().portal_code_sign_in:
            raise http_error(403, OFF)
        row: PortalSignIn | None = store.get("portal_sign_ins", payload.challenge_id, for_update=True)
        if row is None or row.status not in {SignInStatus.PENDING, SignInStatus.UNMATCHED} or row.expires_at <= now:
            raise http_error(401, WRONG)
        right = row.code_hash is not None and hmac.compare_digest(row.code_hash, code_hash(row.id, payload.code))
        access = store.get("portal_access", row.access_id) if right and row.access_id else None
        usable = (access is not None and access.status == PortalAccessStatus.ACTIVE and access.expires_at > now)
        if not right or not usable or row.status != SignInStatus.PENDING:
            attempts = row.attempts + 1
            ended = attempts >= MAX_ATTEMPTS or (right and not usable)
            store.save("portal_sign_ins", row.model_copy(update={"attempts": attempts, "status": SignInStatus.FAILED if ended else row.status, "updated_at": now}))
            if row.access_id and attempts >= MAX_ATTEMPTS:
                record_activity(store, context, "portal_sign_in_failed", "A customer portal sign-in code was tried five times without success and stopped.",
                                metadata={"access_id": row.access_id, "sign_in_id": row.id})
            failed = True
        else:
            failed = False
            secret = secrets.token_urlsafe(32)
            expires_at = min(now + SESSION_TTL, access.expires_at)  # type: ignore[union-attr]
            store.save("portal_sign_ins", row.model_copy(update={
                "status": SignInStatus.SIGNED_IN, "used_at": now, "attempts": row.attempts + 1, "session_hash": secret_hash(secret),
                "session_expires_at": expires_at, "updated_at": now}))
            record_activity(store, context, "portal_signed_in_with_code", f"{access.contact_name} signed in to the customer portal with an emailed code.",  # type: ignore[union-attr]
                            account_id=access.account_id, metadata={"access_id": access.id, "sign_in_id": row.id})  # type: ignore[union-attr]
    if failed:
        audit(None, context, "business.portal.sign_in_failed", payload.challenge_id, outcome="denied")
        raise http_error(401, WRONG)
    audit(None, context, "business.portal.signed_in_with_code", payload.challenge_id, metadata={"access_id": access.id})  # type: ignore[union-attr]
    return VerifyAnswer(token=f"{PREFIX}.{tenant_id}.{workspace_id}.{row.id}.{secret}", expires_at=expires_at)


@router.post("/end", status_code=status.HTTP_204_NO_CONTENT)
def end_session(session: PortalSession = Depends(portal_session)) -> Response:
    """Sign out of a code session (an access link has nothing to sign out of: the business turns it off)."""
    if session.via != "code" or session.sign_in_id is None:
        raise http_error(409, "You opened the portal with a link; close the page to leave. The business can turn the link off.")
    now = utc_now()
    with open_business_store(session.context) as store:
        row = store.get("portal_sign_ins", session.sign_in_id, for_update=True)
        if row is not None and row.status == SignInStatus.SIGNED_IN:
            store.save("portal_sign_ins", row.model_copy(update={"status": SignInStatus.ENDED, "ended_at": now, "updated_at": now}))
    audit(None, session.context, "business.portal.signed_out", session.sign_in_id, metadata={"access_id": session.access.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["CODE_TTL", "MAX_ATTEMPTS", "SESSION_TTL", "code_email", "code_hash", "router"]
