"""The public plan list and self-serve sign-up (docs/billing.md#self-serve-sign-up).

* ``GET /api/v1/public/billing/plans``: no sign-in. Only published, active plans of the
  configured issuer (``ANUM_BILLING_ISSUER_TENANT_ID``), public columns only, read through
  ``anum_platform_reader``. No tenant id or other organisation data is returned.
* ``POST /api/v1/signup/organization``: a Keycloak account with a **verified** email and no
  organisation yet creates one. ANUM never sees a password: the account comes from
  Keycloak's own registration page. Provisioning writes the tenant, the default workspace,
  the owner membership, the billing account and a trial subscription in one transaction
  inside the **new** tenant's RLS context (the platform console's onboarding path), then
  links the account to it through Keycloak's admin API when the provisioning client is
  configured. Abuse limits without a captcha: verified email, one organisation per account,
  disposable domains refused, per-IP and daily caps.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..audit import AuditRecord
from ..errors import ApplicationError, ErrorCode
from ..hardening import InMemoryTokenBucket
from ..identity import AccountPendingError, JwksUnavailableError, TokenValidationError, is_local_environment
from ..schemas import Tenant, TenantContext, Workspace, WorkspaceMembership, new_id, utc_now
from ..settings import settings
from . import readers
from . import service as svc
from .rows import memory_db, open_billing
from .views import plan_view

logger = logging.getLogger("anum.billing")
public = APIRouter(prefix="/api/v1/public/billing", tags=["billing"])
signup_router = APIRouter(prefix="/api/v1/signup", tags=["billing"])
# Swapped in tests for an httpx.MockTransport (Keycloak's admin API).
transport: httpx.BaseTransport | None = None

DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "10minutemail.com", "tempmail.com", "temp-mail.org",
    "yopmail.com", "trashmail.com", "getnada.com", "sharklasers.com", "dispostable.com", "maildrop.cc",
    "throwawaymail.com", "fakeinbox.com", "mintemail.com", "mohmal.com", "emailondeck.com", "tempail.com",
})
_ip_buckets: dict[int, InMemoryTokenBucket] = {}


def _ip_bucket() -> InMemoryTokenBucket:
    limit = settings.signup_ip_hourly_limit
    bucket = _ip_buckets.get(limit)
    if bucket is None:
        bucket = _ip_buckets[limit] = InMemoryTokenBucket(rate_per_second=limit / 3600.0, burst=limit)
    return bucket


def reset_limits() -> None:
    _ip_buckets.clear()


# ----------------------------------------------------------------------------- public plans


@public.get("/plans")
def public_plans() -> dict[str, Any]:
    """Published plans for the pricing page; nothing about any organisation."""
    issuer = settings.billing_issuer_tenant_id
    plans = readers.catalog(issuer) if issuer else []
    rate = 0
    if issuer:
        with open_billing(issuer) as rows:
            rate = svc.tax_rate(svc.issuer_settings(rows, issuer), "SA")
    return {
        "signup_enabled": bool(settings.signup_enabled and issuer and plans),
        "currency": plans[0]["currency"] if plans else "SAR",
        "tax": {"country": "SA", "rate_bp": rate},
        "plans": [plan_view(plan) for plan in plans],
    }


# ----------------------------------------------------------------------------- who is signing up


@dataclass(frozen=True)
class SignupIdentity:
    subject: str
    email: str | None
    email_verified: bool
    tenant_id: str | None
    workspace_id: str | None


async def signup_identity(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
    x_email_verified: str | None = Header(default=None),
) -> SignupIdentity:
    """A signed-in account, with or without an organisation (``tenant_id``) yet."""
    if settings.auth_mode == "oidc":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Sign in first: the sign-up needs your verified account.")
        from ..dependencies import get_oidc_validator

        try:
            claims = await get_oidc_validator().validate(authorization.split(" ", 1)[1].strip())
        except AccountPendingError as pending:
            if not pending.subject:
                raise HTTPException(status_code=401, detail="Bearer token signature or claims are invalid") from None
            return SignupIdentity(pending.subject, pending.email, pending.email_verified, None, None)
        except JwksUnavailableError:
            raise HTTPException(status_code=503, detail="Identity provider is unavailable") from None
        except TokenValidationError as exc:
            raise HTTPException(status_code=401, detail=exc.reason) from None
        return SignupIdentity(claims.subject, claims.email, claims.email_verified, claims.tenant_id, claims.workspace_id)
    if not is_local_environment(settings.environment):
        raise RuntimeError("Header authentication is only allowed in local/test environments")
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Sign in first: the sign-up needs your verified account.")
    verified = (x_email_verified or "true").strip().lower() == "true"
    return SignupIdentity(x_user_id, x_user_email, verified, x_tenant_id or None, x_workspace_id or None)


# ----------------------------------------------------------------------------- abuse limits


def _ip_hash(ip: str) -> str:
    salt = settings.secrets_key.get_secret_value() if settings.secrets_key else "anum-local-signup"
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()


_RECENT = "select tenant_id, user_id, ip_hash, created_at from billing_signups where created_at >= :since or user_id = :user"


def _recent_signups(user_id: str, since: datetime) -> list[dict[str, Any]]:
    if settings.repository_backend == "postgresql":
        from .rows import reader_rows

        return reader_rows("anum_maintenance", {}, _RECENT, {"since": since, "user": user_id})
    return [r for r in memory_db.all_rows("billing_signups") if r["created_at"] >= since or r["user_id"] == user_id]


def _existing_signup(user_id: str) -> dict[str, Any] | None:
    return next((r for r in _recent_signups(user_id, utc_now()) if r["user_id"] == user_id), None)


def _limited(message: str) -> ApplicationError:
    return ApplicationError(ErrorCode.RATE_LIMITED, message, status_code=429)


# ----------------------------------------------------------------------------- Keycloak link


def link_keycloak_account(subject: str, tenant_id: str, workspace_id: str) -> bool:
    """Set the account's ``tenant_id`` and ``default_workspace_id`` (never moves one that has another tenant)."""
    client_id = settings.keycloak_provisioner_client_id
    secret = settings.keycloak_provisioner_client_secret.get_secret_value() if settings.keycloak_provisioner_client_secret else ""
    if not client_id or not secret:
        return False
    issuer = settings.keycloak_issuer.rstrip("/")
    if "/realms/" not in issuer or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", subject):
        return False
    admin = issuer.replace("/realms/", "/admin/realms/", 1)
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            token = client.post(f"{issuer}/protocol/openid-connect/token",
                                data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
            token.raise_for_status()
            headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
            found = client.get(f"{admin}/users/{subject}", headers=headers)
            found.raise_for_status()
            user = found.json()
            attributes = dict(user.get("attributes") or {})
            current = attributes.get("tenant_id") or []
            if current and current != [tenant_id]:
                logger.warning("billing_signup_link_refused_other_tenant")
                return False
            attributes["tenant_id"] = [tenant_id]
            attributes["default_workspace_id"] = [workspace_id]
            user["attributes"] = attributes
            user.pop("access", None)
            saved = client.put(f"{admin}/users/{subject}", headers=headers, json=user)
            saved.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError):
        logger.warning("billing_signup_link_failed", exc_info=True)
        return False
    return True


# ----------------------------------------------------------------------------- sign-up


class SignupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(min_length=2, max_length=160)
    plan_id: str = Field(min_length=1, max_length=80)
    country: str = Field(default="SA", pattern=r"^[A-Za-z]{2}$")
    accept_terms: bool

    @field_validator("organization_name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2 or not re.search(r"\w", value):
            raise ValueError("Enter your organisation's name.")
        if re.search(r"https?://|www\.|<|>", value, re.IGNORECASE):
            raise ValueError("The organisation name cannot contain links or angle brackets.")
        return value

    @field_validator("accept_terms")
    @classmethod
    def _terms(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Accept the terms to create the organisation.")
        return value


def _tenant_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "org"
    if not base[0].isalnum():
        base = "org-" + base
    return f"{base}-{secrets.token_hex(3)}"


def _status_view(identity: SignupIdentity) -> dict[str, Any]:
    base = {"email": identity.email, "email_verified": identity.email_verified, "signup_enabled": bool(settings.signup_enabled and settings.billing_issuer_tenant_id)}
    if identity.tenant_id:
        return {**base, "state": "provisioned", "tenant_id": identity.tenant_id, "workspace_id": identity.workspace_id}
    existing = _existing_signup(identity.subject)
    if existing:
        workspace_id = f"{existing['tenant_id']}_main"[:80]
        linked = settings.auth_mode != "oidc" or link_keycloak_account(identity.subject, existing["tenant_id"], workspace_id)
        return {**base, "state": "provisioned", "tenant_id": existing["tenant_id"], "workspace_id": workspace_id, "identity_linked": linked}
    if not identity.email_verified:
        return {**base, "state": "verify_email"}
    return {**base, "state": "ready"}


@signup_router.get("/status")
async def signup_status(identity: SignupIdentity = Depends(signup_identity)) -> dict[str, Any]:
    return await asyncio.to_thread(_status_view, identity)


def provision(identity: SignupIdentity, payload: SignupInput, ip: str, now: datetime) -> dict[str, Any]:
    issuer = settings.billing_issuer_tenant_id
    if not settings.signup_enabled or not issuer:
        raise HTTPException(status_code=404, detail="Self-serve sign-up is not open on this server; contact the ANUM team for an account.")
    if identity.tenant_id:
        raise HTTPException(status_code=409, detail="Your account already belongs to an organisation; switch to it instead.")
    if not identity.email_verified or not identity.email:
        raise ApplicationError(ErrorCode.EMAIL_NOT_VERIFIED, "Verify your email address first: open the link Keycloak sent you, then try again.", status_code=403)
    domain = identity.email.rsplit("@", 1)[-1].strip().lower()
    if domain in DISPOSABLE_DOMAINS:
        raise HTTPException(status_code=422, detail="Sign up with your work or personal email address; disposable email addresses are not accepted.")
    ip_hash = _ip_hash(ip)
    recent = _recent_signups(identity.subject, now - timedelta(days=1))
    if any(r["user_id"] == identity.subject for r in recent):
        raise HTTPException(status_code=409, detail="This account already created an organisation; each account creates one.")
    if sum(1 for r in recent if r["ip_hash"] == ip_hash and r["created_at"] >= now - timedelta(hours=1)) >= settings.signup_ip_hourly_limit:
        raise _limited("Too many sign-ups from your network in the last hour. Try again later.")
    if len(recent) >= settings.signup_daily_limit:
        raise _limited("Sign-up is busy today. Try again tomorrow, or contact the ANUM team.")
    if not _ip_bucket().acquire(f"signup:{ip_hash}").allowed:
        raise _limited("Too many sign-ups from your network in the last hour. Try again later.")
    plan = next((p for p in readers.catalog(issuer) if p["id"] == payload.plan_id), None)
    if plan is None:
        raise HTTPException(status_code=422, detail="That plan is not offered; choose one of the plans on the pricing page.")

    from ..dependencies import _unit_of_work
    from ..organisations import _write_branding

    tenant_id = _tenant_slug(payload.organization_name)
    workspace_id = f"{tenant_id}_main"[:80]
    target = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=identity.subject, roles=["owner"])
    with _unit_of_work(target, require_membership=False) as repository:
        session = getattr(repository, "session", None)
        repository.create_tenant(Tenant(id=tenant_id, name=payload.organization_name, created_at=now, updated_at=now))
        repository.create_workspace(Workspace(id=workspace_id, tenant_id=tenant_id, name="Main", created_at=now, updated_at=now))
        repository.save_membership(WorkspaceMembership(
            tenant_id=tenant_id, workspace_id=workspace_id, user_id=identity.subject, role="owner", created_at=now, updated_at=now,
        ))
        _write_branding(session, tenant_id, {"display_name": payload.organization_name, "updated_by_user_id": identity.subject, "updated_at": now})
        with open_billing(tenant_id, session=session) as rows:
            full_plan = svc.get_plan(rows, issuer, plan["id"])
            if full_plan is None:
                raise HTTPException(status_code=422, detail="That plan is not offered; choose one of the plans on the pricing page.")
            account = svc.ensure_account(rows, issuer_tenant_id=issuer, display_name=payload.organization_name, now=now,
                                         country=payload.country.upper(), contact_email=identity.email)
            sub, _ = svc.start_subscription(rows, account, full_plan, now=now, actor=identity.subject,
                                            trial_days=settings.signup_trial_days)
            rows.insert("billing_signups", {
                "id": new_id("signup"), "tenant_id": tenant_id, "user_id": identity.subject, "email_domain": domain[:254],
                "ip_hash": ip_hash, "plan_id": plan["id"], "created_at": now,
            })
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=tenant_id, workspace_id=workspace_id, actor=identity.subject, action="billing.signup.provision",
            target=tenant_id, outcome="success", correlation_id=new_id("corr"), created_at=now,
            metadata={"plan_id": plan["id"], "country": payload.country.upper(), "trial_days": settings.signup_trial_days},
        ))
    linked = settings.auth_mode != "oidc" or link_keycloak_account(identity.subject, tenant_id, workspace_id)
    return {"tenant_id": tenant_id, "workspace_id": workspace_id, "trial_end": sub["trial_end"], "plan_id": plan["id"],
            "identity_linked": linked}


@signup_router.post("/organization", status_code=status.HTTP_201_CREATED)
async def create_organisation(payload: SignupInput, request: Request, identity: SignupIdentity = Depends(signup_identity)) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"
    return await asyncio.to_thread(provision, identity, payload, ip, utc_now())
