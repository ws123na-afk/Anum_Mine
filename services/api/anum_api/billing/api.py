"""Billing routes for organisation billing admins, the platform owner and Stripe (docs/billing.md#api)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import delivery
from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import repository_context, require_owner_or_org_admin, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import readers
from . import service as svc
from .enforce import clear_cache
from .pdf import invoice_filename, render_invoice
from .providers import ProviderError, SignatureError, StripeProvider, key_mode, verify_stripe_signature
from .rows import RowConflict, Rows, open_billing
from .usage import measure
from .views import (
    account_view,
    admin_plan_view,
    bank_details,
    invoice_detail_view,
    invoice_summary,
    plan_view,
    settings_view,
    subscription_view,
    usage_view,
)

logger = logging.getLogger("anum.billing")
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
console = APIRouter(prefix="/api/v1/platform/billing", tags=["billing"])
webhooks = APIRouter(prefix="/api/v1/billing/webhooks", tags=["billing"])

BILLING_ADMIN_REQUIRED = (
    "Only a workspace owner of this organisation or an organisation administrator (realm role org_admin) "
    "can see and change billing. Ask one of them, or ask a workspace owner to make you an owner."
)
INVOICE_ATTACHMENT_KIND = "billing_invoice"
PROOF_TYPES = {"application/pdf": b"%PDF", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
PROOF_MAX_BYTES = 5 * 1024 * 1024
MAX_PAGE = 100
COUNTRY = r"^[A-Za-z]{2}$"


def is_email(value: str) -> bool:
    """One plain address (``name@host.tld``), checked without a backtracking pattern."""
    if not value or len(value) > 254 or value.count("@") != 1 or any(c.isspace() or c in ",;<>" for c in value):
        return False
    local, domain = value.split("@")
    labels = domain.split(".")
    return bool(local) and len(labels) >= 2 and all(labels)


def _raise(exc: svc.BillingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=target, outcome="success", correlation_id=new_id("corr"), created_at=utc_now(),
            metadata=metadata or {},
        )
    )


def _admin(context: TenantContext, manage: bool = False) -> None:
    require_owner_or_org_admin(context, Permission.BILLING_MANAGE if manage else Permission.BILLING_READ, BILLING_ADMIN_REQUIRED)


def _operator(context: TenantContext) -> None:
    require_permission(context, Permission.PLATFORM_OPERATE)


# ----------------------------------------------------------------------------- inputs


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_name: str = Field(default="", max_length=160)
    contact_email: str = Field(default="", max_length=254)
    company_name: str = Field(default="", max_length=200)
    address_line1: str = Field(default="", max_length=200)
    address_line2: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=20)
    country: str = Field(default="SA", pattern=COUNTRY)
    vat_number: str = Field(default="", max_length=40)

    @field_validator("contact_email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and not is_email(value):
            raise ValueError("Enter one email address, such as billing@example.com.")
        return value


class ChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=80)
    seats: int | None = Field(default=None, ge=1, le=100_000)
    when: Literal["now", "period_end"] | None = None


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=80)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    seats: int = Field(default=1, ge=1, le=100_000)


class PlanLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seats: int | None = Field(default=None, ge=1, le=100_000)
    workspaces: int | None = Field(default=None, ge=1, le=10_000)
    agent_runs_per_month: int | None = Field(default=None, ge=0, le=100_000_000)
    storage_gb: int | None = Field(default=None, ge=1, le=100_000)
    messages_per_month: int | None = Field(default=None, ge=0, le=100_000_000)


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,59}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    price_minor: int = Field(ge=0, le=10**12)
    currency: str = Field(default="SAR", pattern=r"^[A-Z]{3}$")
    interval: Literal["month", "year"] = "month"
    trial_days: int = Field(default=14, ge=0, le=365)
    per_seat: bool = False
    limits: PlanLimits = Field(default_factory=PlanLimits)
    features: list[str] = Field(default_factory=list, max_length=20)
    published: bool = False
    active: bool = True
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("features")
    @classmethod
    def _features(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.split())[:120] for item in value if item.strip()]
        return cleaned


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price_minor: int | None = Field(default=None, ge=0, le=10**12)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    per_seat: bool | None = None
    limits: PlanLimits | None = None
    features: list[str] | None = Field(default=None, max_length=20)
    published: bool | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer_name: str = Field(default="", max_length=200)
    issuer_address: str = Field(default="", max_length=600)
    issuer_vat_number: str = Field(default="", max_length=40)
    issuer_email: str = Field(default="", max_length=254)
    bank_name: str = Field(default="", max_length=120)
    bank_account_name: str = Field(default="", max_length=160)
    bank_iban: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9 ]*$")
    bank_swift: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9]*$")
    invoice_prefix: str = Field(default="INV", pattern=r"^[A-Z0-9]{1,12}$")
    credit_note_prefix: str = Field(default="CN", pattern=r"^[A-Z0-9]{1,12}$")
    payment_terms_days: int = Field(default=14, ge=0, le=120)
    tax_rules: dict[str, int] = Field(default_factory=lambda: {"SA": 1500})
    default_tax_bp: int = Field(default=0, ge=0, le=10_000)
    reminder_days: list[int] = Field(default_factory=lambda: [1, 7, 14], max_length=10)
    grace_days: int = Field(default=7, ge=0, le=90)
    sending_workspace_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    stripe_enabled: bool = False
    # Write-only: a new value replaces the stored one, "" removes it, null keeps it.
    stripe_secret_key: str | None = Field(default=None, max_length=300)
    stripe_webhook_secret: str | None = Field(default=None, max_length=300)

    @field_validator("tax_rules")
    @classmethod
    def _tax(cls, value: dict[str, int]) -> dict[str, int]:
        out = {}
        for country, rate in value.items():
            if not re.fullmatch(r"[A-Za-z]{2}", country) or not 0 <= int(rate) <= 10_000:
                raise ValueError("Tax rules map a two-letter country code to a rate from 0 to 10000 basis points (15% is 1500).")
            out[country.upper()] = int(rate)
        return out

    @field_validator("reminder_days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 120 for day in value):
            raise ValueError("Reminder days are 0 to 120 days after the due date.")
        return sorted(set(value))


class ProofInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    content_type: Literal["application/pdf", "image/png", "image/jpeg"]
    data_base64: str = Field(min_length=4, max_length=8 * 1024 * 1024)


class PaymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0, le=10**12)
    method: Literal["bank_transfer", "cash", "card", "other"] = "bank_transfer"
    reference: str = Field(default="", max_length=200)
    received_at: datetime | None = None
    proof: ProofInput | None = None


class CreditNoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_minor: int = Field(gt=0, le=10**12)
    reason: str = Field(min_length=3, max_length=500)


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str | None = Field(default=None, max_length=254)
    channel: str = Field(default="email", pattern=r"^[a-z_]{2,30}$")


# ----------------------------------------------------------------------------- shared reads


def _pdf_response(detail: dict[str, Any]) -> Response:
    body = render_invoice(detail)
    return Response(
        content=body, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{invoice_filename(detail["invoice"])}"', "Cache-Control": "private, no-store"},
    )


def _invoice_or_404(rows: Rows, invoice_id: str) -> dict[str, Any]:
    invoice = svc.get_invoice(rows, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return invoice


def _page(rows: Rows, q: str | None, status_filter: str | None, limit: int, offset: int) -> dict[str, Any]:
    now = utc_now()
    items, total = svc.list_invoices(rows, q=q, status=status_filter, limit=limit, offset=offset)
    return {"items": [invoice_summary(item, now) for item in items], "total": total, "limit": limit, "offset": offset}


StatusFilter = Literal["draft", "open", "paid", "void", "uncollectible"]


# ----------------------------------------------------------------------------- organisation billing


@router.get("/overview")
def overview(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _admin(context)
    now = utc_now()
    usage = measure(context.tenant_id, now)
    with open_billing(context.tenant_id) as rows:
        account = svc.account_of(rows)
        sub = svc.latest_subscription(rows)
        open_rows = rows.select("billing_invoices", {"status": "open"})
        issuer = svc.issuer_settings(rows, account["issuer_tenant_id"]) if account else None
        if account:
            for key in ("seats", "workspaces", "agent_runs", "storage", "messages"):
                rows.upsert_usage(f"{now.year:04d}-{now.month:02d}", key, usage.get(key), now)
    return {
        "account": account_view(account),
        "subscription": subscription_view(sub),
        "usage": usage_view(sub if sub and sub["status"] != "cancelled" else None, usage, now),
        "state": account["state"] if account else "active",
        "open_balance_minor": sum(int(i["amount_due_minor"]) for i in open_rows),
        "overdue": any(i["due_at"] <= now for i in open_rows if i["due_at"]),
        "currency": sub["currency"] if sub else "SAR",
        "payment": {
            "bank_transfer": bank_details(issuer or {}),
            "card_enabled": bool(issuer and issuer["stripe_enabled"] and issuer["stripe_secret_ciphertext"]),
        },
        "issuer": {"name": (issuer or {}).get("issuer_name") or "", "email": (issuer or {}).get("issuer_email") or ""},
    }


@router.get("/plans")
def available_plans(context: TenantContext = Depends(tenant_context)) -> list[dict[str, Any]]:
    _admin(context)
    with open_billing(context.tenant_id) as rows:
        account = svc.account_of(rows)
        sub = svc.live_subscription(rows)
    if account is None:
        return []
    current = sub["plan_id"] if sub else ""
    return [plan_view(plan, current) for plan in readers.catalog(account["issuer_tenant_id"])]


def _plan_for(rows: Rows, plan_id: str) -> dict[str, Any]:
    account = svc.account_of(rows)
    if account is None:
        raise HTTPException(status_code=404, detail="This organisation has no billing account yet.")
    plan = svc.get_plan(rows, account["issuer_tenant_id"], plan_id)
    if plan is None or not plan["active"]:
        raise HTTPException(status_code=422, detail="That plan is not offered; choose one of the plans listed.")
    return plan


@router.get("/subscription/preview")
def preview(
    plan_id: str = Query(min_length=1, max_length=80), seats: int | None = Query(default=None, ge=1, le=100_000),
    when: Literal["now", "period_end"] | None = None, context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    _admin(context)
    now = utc_now()
    usage = measure(context.tenant_id, now)
    with open_billing(context.tenant_id) as rows:
        sub = svc.live_subscription(rows)
        if sub is None:
            raise HTTPException(status_code=404, detail="This organisation has no subscription to change.")
        plan = _plan_for(rows, plan_id)
        if not plan["published"] and plan["id"] != sub["plan_id"]:
            raise HTTPException(status_code=422, detail="That plan is not offered; choose one of the plans listed.")
        result = svc.preview_change(rows, sub, plan, seats or int(sub["seats"]), usage, now, when)
    return {"effective": result.effective, "amount_minor": result.amount_minor, "tax_minor": result.tax_minor,
            "total_minor": result.total_minor, "credit_minor": result.credit_minor, "blocked_by": result.blocked_by,
            "currency": sub["currency"]}


def _change(context: TenantContext, payload: ChangeInput, *, require_published: bool) -> dict[str, Any]:
    now = utc_now()
    usage = measure(context.tenant_id, now)
    try:
        with open_billing(context.tenant_id) as rows:
            plan = _plan_for(rows, payload.plan_id)
            sub = svc.live_subscription(rows)
            if require_published and not plan["published"] and (sub is None or plan["id"] != sub["plan_id"]):
                raise HTTPException(status_code=422, detail="That plan is not offered; choose one of the plans listed.")
            sub, issued = svc.change_plan(rows, plan, seats=payload.seats, when=payload.when, usage=usage, now=now, actor=context.user_id)
    except svc.BillingError as exc:
        if exc.blocked_by:
            raise _BlockedChange(exc) from None
        raise _raise(exc) from None
    return {"subscription": subscription_view(sub), "invoice": invoice_summary(issued.invoice, now) if issued else None}


class _BlockedChange(Exception):
    def __init__(self, error: svc.BillingError) -> None:
        self.error = error


async def blocked_change_handler(request: Request, exc: _BlockedChange) -> Response:
    from ..errors import ErrorCode, ErrorDetail, _error_response

    return _error_response(
        request, status_code=409, code=ErrorCode.CONFLICT, message=exc.error.message,
        details=[ErrorDetail(location=item["key"], message=f"{item['used']} of {item['limit']}", type="blocked_by") for item in exc.error.blocked_by],
    )


@router.post("/subscription/change")
def change(payload: ChangeInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _admin(context, manage=True)
    result = _change(context, payload, require_published=True)
    _audit(repository, context, "billing.subscription.change", result["subscription"]["id"],
           {"plan_id": payload.plan_id, "seats": payload.seats, "when": payload.when or "default"})
    return result


@router.post("/subscription/cancel")
def cancel(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _admin(context, manage=True)
    try:
        with open_billing(context.tenant_id) as rows:
            sub = svc.cancel(rows, now=utc_now())
    except svc.BillingError as exc:
        raise _raise(exc) from None
    _audit(repository, context, "billing.subscription.cancel", sub["id"], {"at_period_end": True})
    return {"subscription": subscription_view(sub)}


@router.post("/subscription/reactivate")
def reactivate(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _admin(context, manage=True)
    now = utc_now()
    try:
        with open_billing(context.tenant_id) as rows:
            sub, issued = svc.reactivate(rows, now=now, actor=context.user_id)
    except svc.BillingError as exc:
        raise _raise(exc) from None
    _audit(repository, context, "billing.subscription.reactivate", sub["id"], {})
    return {"subscription": subscription_view(sub), "invoice": invoice_summary(issued.invoice, now) if issued else None}


@router.put("/account")
def update_account(payload: AccountInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _admin(context, manage=True)
    now = utc_now()
    with open_billing(context.tenant_id) as rows:
        account = svc.account_of(rows)
        if account is None:
            raise HTTPException(status_code=404, detail="This organisation is not billed yet, so it has no billing contact to edit.")
        values = {**payload.model_dump(), "country": payload.country.upper(), "updated_at": now}
        rows.update("billing_accounts", {"tenant_id": context.tenant_id}, values)
        account = svc.account_of(rows)
    _audit(repository, context, "billing.account.update", context.tenant_id, {"fields": sorted(payload.model_fields_set), "country": payload.country.upper()})
    return account_view(account) or {}


@router.get("/invoices")
def invoices(
    q: str | None = Query(default=None, max_length=40), status_filter: StatusFilter | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    _admin(context)
    with open_billing(context.tenant_id) as rows:
        return _page(rows, q, status_filter, limit, offset)


@router.get("/invoices/{invoice_id}")
def invoice(invoice_id: str, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _admin(context)
    with open_billing(context.tenant_id) as rows:
        return invoice_detail_view(svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id)), utc_now())


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _admin(context)
    with open_billing(context.tenant_id) as rows:
        detail = svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id))
    return _pdf_response(detail)


def _stripe_secret(issuer: dict[str, Any], field: str) -> str | None:
    ciphertext = issuer.get(field)
    if not issuer.get("stripe_enabled") or not ciphertext:
        return None
    from ..secret_box import secret_cipher

    return secret_cipher().decrypt(ciphertext)


@router.post("/invoices/{invoice_id}/checkout")
def checkout(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _admin(context, manage=True)
    with open_billing(context.tenant_id) as rows:
        invoice = _invoice_or_404(rows, invoice_id)
        issuer = svc.issuer_settings(rows, invoice["issuer_tenant_id"])
    if invoice["status"] != "open" or int(invoice["amount_due_minor"]) <= 0:
        raise HTTPException(status_code=409, detail=f"Invoice {invoice['number']} has nothing left to pay.")
    secret = _stripe_secret(issuer, "stripe_secret_ciphertext")
    if secret is None:
        raise HTTPException(status_code=409, detail="Card payments are not turned on; pay by bank transfer with the details on the invoice.")
    base = (settings.web_app_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=409, detail="Card payments need ANUM_WEB_APP_URL set on the server; pay by bank transfer for now.")
    try:
        session = StripeProvider(secret).create_checkout(
            invoice=invoice, success_url=f"{base}/#settings/billing?paid={invoice_id}", cancel_url=f"{base}/#settings/billing?invoice={invoice_id}",
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    _audit(repository, context, "billing.checkout.create", invoice_id, {"provider": "stripe", "amount_minor": invoice["amount_due_minor"]})
    return {"url": session.url, "session_id": session.session_id}


# ----------------------------------------------------------------------------- onboarding checklist


def _email_connected(context: TenantContext) -> bool:
    try:
        from ..email_inbox import open_email_store
        from ..mailbox.store import open_mail_store

        with open_mail_store(context) as store:
            if store.list_accounts():
                return True
        with open_email_store(context) as inbox:
            return bool(inbox.list_accounts())
    except Exception:  # pragma: no cover - the checklist never fails for this
        logger.debug("billing_onboarding_mail_check_failed", exc_info=True)
        return False


@router.get("/onboarding")
def onboarding(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _admin(context)
    usage = measure(context.tenant_id, utc_now())
    with open_billing(context.tenant_id) as rows:
        account = svc.account_of(rows)
        sub = svc.live_subscription(rows)
    if account is None:
        raise HTTPException(status_code=404, detail="This organisation has no billing account, so there is no billing checklist.")
    items = [
        {"key": "invite_team", "done": usage.seats > 1},
        {"key": "connect_email", "done": _email_connected(context)},
        {"key": "choose_plan", "done": bool(account["plan_confirmed"] or (sub is not None and sub["status"] != "trialing"))},
        {"key": "add_payment_details", "done": bool(account["contact_email"] and account["address_line1"] and account["city"])},
    ]
    return {"items": items, "complete": all(item["done"] for item in items), "dismissed": bool(account["onboarding_dismissed"])}


@router.post("/onboarding/dismiss")
def dismiss_onboarding(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _admin(context)
    with open_billing(context.tenant_id) as rows:
        if not rows.update("billing_accounts", {"tenant_id": context.tenant_id}, {"onboarding_dismissed": True, "updated_at": utc_now()}):
            raise HTTPException(status_code=404, detail="This organisation has no billing account, so there is no billing checklist.")
    _audit(repository, context, "billing.onboarding.dismiss", context.tenant_id, {})
    return {"dismissed": True}


# ----------------------------------------------------------------------------- platform console


def _operated(context: TenantContext, tenant_id: str, rows: Rows) -> dict[str, Any] | None:
    """The customer's billing account when the caller may act on it; 404 otherwise."""
    if tenant_id == context.tenant_id:
        raise HTTPException(status_code=422, detail="Your own organisation is the issuer; it is not billed through the console.")
    account = svc.account_of(rows)
    if account is not None:
        if account["issuer_tenant_id"] != context.tenant_id:
            raise HTTPException(status_code=404, detail="You do not bill an organisation with that id.")
        return account
    from ..organisations import _operated as operated_record

    record = operated_record(getattr(rows, "session", None), tenant_id)
    if record is None or record["operator_user_id"] != context.user_id:
        raise HTTPException(status_code=404, detail="You do not bill an organisation with that id.")
    return None


def _customer_account(context: TenantContext, tenant_id: str, rows: Rows) -> dict[str, Any]:
    account = _operated(context, tenant_id, rows)
    if account is None:
        raise HTTPException(status_code=404, detail="That organisation is not billed yet; start a subscription first.")
    return account


@console.get("/settings")
def get_settings(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _operator(context)
    with open_billing(context.tenant_id) as rows:
        return settings_view(svc.issuer_settings(rows, context.tenant_id))


@console.put("/settings")
def put_settings(payload: SettingsInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    from ..secret_box import secret_cipher

    now = utc_now()
    values = payload.model_dump(exclude={"stripe_secret_key", "stripe_webhook_secret"})
    values["bank_iban"] = values["bank_iban"].replace(" ", "").upper()
    values["bank_swift"] = values["bank_swift"].upper()
    if payload.stripe_secret_key is not None:
        key = payload.stripe_secret_key.strip()
        if key:
            try:
                StripeProvider(key)
            except ProviderError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        values["stripe_secret_ciphertext"] = secret_cipher().encrypt(key) if key else None
        values["stripe_key_mode"] = key_mode(key) if key else None
    if payload.stripe_webhook_secret is not None:
        secret = payload.stripe_webhook_secret.strip()
        values["stripe_webhook_secret_ciphertext"] = secret_cipher().encrypt(secret) if secret else None
    with open_billing(context.tenant_id) as rows:
        current = rows.get("billing_settings", tenant_id=context.tenant_id)
        if payload.stripe_enabled and not (values.get("stripe_secret_ciphertext") or (current or {}).get("stripe_secret_ciphertext")):
            raise HTTPException(status_code=422, detail="Enter the Stripe secret key before turning card payments on.")
        values.update(updated_by=context.user_id, updated_at=now)
        if current is None:
            rows.insert("billing_settings", {**{k: v for k, v in svc.DEFAULT_SETTINGS.items()}, **values, "tenant_id": context.tenant_id})
        else:
            rows.update("billing_settings", {"tenant_id": context.tenant_id}, values)
        view = settings_view(svc.issuer_settings(rows, context.tenant_id))
    _audit(repository, context, "billing.settings.update", context.tenant_id, {
        "stripe_enabled": payload.stripe_enabled, "stripe_key_changed": payload.stripe_secret_key is not None,
        "webhook_secret_changed": payload.stripe_webhook_secret is not None, "tax_rules": values["tax_rules"],
    })
    return view


@console.get("/plans")
def list_plans(context: TenantContext = Depends(tenant_context)) -> list[dict[str, Any]]:
    _operator(context)
    with open_billing(context.tenant_id) as rows:
        plans = rows.select("billing_plans", order=[("sort_order", False), ("price_minor", False), ("name", False)])
    return [admin_plan_view(plan) for plan in plans]


@console.post("/plans", status_code=status.HTTP_201_CREATED)
def create_plan(payload: PlanInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    plan = {
        **payload.model_dump(exclude={"limits"}), "limits": payload.limits.model_dump(), "id": new_id("plan"),
        "tenant_id": context.tenant_id, "created_by": context.user_id, "created_at": now, "updated_at": now,
    }
    try:
        with open_billing(context.tenant_id) as rows:
            rows.insert("billing_plans", plan)
    except RowConflict:
        raise HTTPException(status_code=409, detail=f"A plan with the code {payload.code} exists; choose another code.") from None
    _audit(repository, context, "billing.plan.create", plan["id"], {"code": payload.code, "price_minor": payload.price_minor, "published": payload.published})
    return admin_plan_view(plan)


@console.patch("/plans/{plan_id}")
def update_plan(plan_id: str, payload: PlanPatch, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    changes = payload.model_dump(exclude_unset=True, exclude={"limits"})
    if payload.limits is not None:
        changes["limits"] = payload.limits.model_dump()
    if "features" in changes and changes["features"] is not None:
        changes["features"] = PlanInput._features(changes["features"])  # type: ignore[call-arg]
    changes = {k: v for k, v in changes.items() if v is not None or k in ("description",)}
    with open_billing(context.tenant_id) as rows:
        if rows.get("billing_plans", id=plan_id) is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        if changes:
            rows.update("billing_plans", {"id": plan_id}, {**changes, "updated_at": utc_now()})
        plan = rows.get("billing_plans", id=plan_id)
    _audit(repository, context, "billing.plan.update", plan_id, {"fields": sorted(changes)})
    return admin_plan_view(plan or {})


@console.get("/summary")
def summary(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _operator(context)
    return readers.summary(context.tenant_id, utc_now())


@console.get("/subscriptions")
def subscriptions(
    q: str | None = Query(default=None, max_length=80), status_filter: str | None = Query(default=None, alias="status", pattern=r"^[a-z_]{2,20}$"),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    _operator(context)
    rows = readers.subscriptions(context.tenant_id, utc_now())
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [r for r in rows if needle in r["tenant_id"].casefold() or needle in str(r["display_name"]).casefold()]
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter or (status_filter == "read_only" and r["state"] == "read_only")]
    return {"items": rows[offset: offset + limit], "total": len(rows), "limit": limit, "offset": offset}


def _display_name(tenant_id: str, rows: Rows) -> str:
    from ..organisations import _operated as operated_record

    record = operated_record(getattr(rows, "session", None), tenant_id)
    return str(record["display_name"]) if record else tenant_id


@console.post("/organizations/{tenant_id}/subscription", status_code=status.HTTP_201_CREATED)
def start(tenant_id: str, payload: StartInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    try:
        with open_billing(tenant_id) as rows:
            account = _operated(context, tenant_id, rows)
            plan = svc.get_plan(rows, context.tenant_id, payload.plan_id)
            if plan is None:
                raise HTTPException(status_code=422, detail="That plan does not exist; create it under Plans first.")
            if account is None:
                account = svc.ensure_account(rows, issuer_tenant_id=context.tenant_id, display_name=_display_name(tenant_id, rows), now=now)
            sub, issued = svc.start_subscription(rows, account, plan, now=now, actor=context.user_id, trial_days=payload.trial_days, seats=payload.seats)
    except svc.BillingError as exc:
        raise _raise(exc) from None
    clear_cache(tenant_id)
    _audit(repository, context, "billing.subscription.start", tenant_id, {"plan_id": payload.plan_id, "subscription_id": sub["id"]})
    return {"subscription": subscription_view(sub), "invoice": invoice_summary(issued.invoice, now) if issued else None}


@console.post("/organizations/{tenant_id}/subscription/{action}")
def subscription_action(
    tenant_id: str, action: Literal["change", "pause", "resume", "cancel", "reactivate"], payload: ChangeInput | None = None,
    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    issued = None
    if action == "change":
        if payload is None:
            raise HTTPException(status_code=422, detail="Send the plan_id to change to.")
        with open_billing(tenant_id) as rows:
            _customer_account(context, tenant_id, rows)
        result = _change(TenantContext(tenant_id=tenant_id, workspace_id="", user_id=context.user_id, roles=[]), payload, require_published=False)
        clear_cache(tenant_id)
        _audit(repository, context, "billing.subscription.change", tenant_id, {"plan_id": payload.plan_id, "via": "console"})
        return result
    try:
        with open_billing(tenant_id) as rows:
            _customer_account(context, tenant_id, rows)
            if action == "pause":
                sub = svc.pause(rows, now=now)
            elif action == "resume":
                sub, issued = svc.resume(rows, now=now, actor=context.user_id)
            elif action == "cancel":
                sub = svc.cancel(rows, now=now)
            else:
                sub, issued = svc.reactivate(rows, now=now, actor=context.user_id)
    except svc.BillingError as exc:
        raise _raise(exc) from None
    clear_cache(tenant_id)
    _audit(repository, context, f"billing.subscription.{action}", tenant_id, {"subscription_id": sub["id"], "via": "console"})
    return {"subscription": subscription_view(sub), "invoice": invoice_summary(issued.invoice, now) if issued else None}


@console.get("/organizations/{tenant_id}")
def organisation(tenant_id: str, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    with open_billing(tenant_id) as rows:
        account = _operated(context, tenant_id, rows)
        sub = svc.latest_subscription(rows)
    return {"tenant_id": tenant_id, "account": account_view(account), "subscription": subscription_view(sub),
            "usage": usage_view(sub if sub and sub["status"] != "cancelled" else None, measure(tenant_id, now), now)}


@console.get("/organizations/{tenant_id}/invoices")
def organisation_invoices(
    tenant_id: str, q: str | None = Query(default=None, max_length=40), status_filter: StatusFilter | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    _operator(context)
    with open_billing(tenant_id) as rows:
        _operated(context, tenant_id, rows)
        return _page(rows, q, status_filter, limit, offset)


@console.get("/organizations/{tenant_id}/invoices/{invoice_id}")
def organisation_invoice(tenant_id: str, invoice_id: str, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _operator(context)
    with open_billing(tenant_id) as rows:
        _customer_account(context, tenant_id, rows)
        return invoice_detail_view(svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id)), utc_now())


@console.get("/organizations/{tenant_id}/invoices/{invoice_id}/pdf")
def organisation_invoice_pdf(tenant_id: str, invoice_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _operator(context)
    with open_billing(tenant_id) as rows:
        _customer_account(context, tenant_id, rows)
        detail = svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id))
    return _pdf_response(detail)


def _proof(proof: ProofInput | None) -> tuple[str, str, bytes] | None:
    if proof is None:
        return None
    try:
        content = base64.b64decode(proof.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The proof file is not valid base64.") from None
    if not content or len(content) > PROOF_MAX_BYTES:
        raise HTTPException(status_code=413 if content else 422, detail="The proof file must be 1 byte to 5 MB.")
    if not content.startswith(PROOF_TYPES[proof.content_type]):
        raise HTTPException(status_code=415, detail="The proof file must be a PDF, PNG or JPEG file of the type it says it is.")
    name = proof.name.replace("/", "_").replace("\\", "_")[:255]
    return name, proof.content_type, content


@console.post("/organizations/{tenant_id}/invoices/{invoice_id}/payments", status_code=status.HTTP_201_CREATED)
def record_payment(
    tenant_id: str, invoice_id: str, payload: PaymentInput, context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    proof = _proof(payload.proof)
    try:
        with open_billing(tenant_id) as rows:
            _customer_account(context, tenant_id, rows)
            payment = svc.record_payment(
                rows, invoice_id, amount_minor=payload.amount_minor, method=payload.method, reference=payload.reference,
                received_at=payload.received_at or now, now=now, actor=context.user_id, proof=proof,
            )
            detail = invoice_detail_view(svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id)), now)
    except svc.BillingError as exc:
        raise _raise(exc) from None
    clear_cache(tenant_id)
    _audit(repository, context, "billing.payment.record", invoice_id, {
        "tenant": tenant_id, "payment_id": payment["id"], "amount_minor": payload.amount_minor, "method": payload.method,
        "has_proof": proof is not None,
    })
    return {"payment_id": payment["id"], "invoice": detail}


@console.get("/organizations/{tenant_id}/invoices/{invoice_id}/payments/{payment_id}/proof")
def payment_proof(tenant_id: str, invoice_id: str, payment_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _operator(context)
    with open_billing(tenant_id) as rows:
        _customer_account(context, tenant_id, rows)
        payment = rows.get("billing_payments", id=payment_id)
    if payment is None or payment["invoice_id"] != invoice_id or not payment.get("proof_content"):
        raise HTTPException(status_code=404, detail="That payment has no proof file.")
    return Response(
        content=bytes(payment["proof_content"]), media_type=payment["proof_content_type"],
        headers={"Content-Disposition": f'attachment; filename="{payment["proof_name"]}"', "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, no-store"},
    )


@console.post("/organizations/{tenant_id}/invoices/{invoice_id}/credit-notes", status_code=status.HTTP_201_CREATED)
def credit_note(
    tenant_id: str, invoice_id: str, payload: CreditNoteInput, context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    try:
        with open_billing(tenant_id) as rows:
            _customer_account(context, tenant_id, rows)
            note = svc.issue_credit_note(rows, invoice_id, total_minor=payload.total_minor, reason=payload.reason, now=now, actor=context.user_id)
            detail = invoice_detail_view(svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id)), now)
    except svc.BillingError as exc:
        raise _raise(exc) from None
    clear_cache(tenant_id)
    _audit(repository, context, "billing.credit_note.issue", invoice_id, {"tenant": tenant_id, "number": note["number"], "total_minor": payload.total_minor})
    return {"credit_note": {k: note[k] for k in ("id", "number", "subtotal_minor", "tax_minor", "total_minor", "issued_at")}, "invoice": detail}


@console.post("/organizations/{tenant_id}/invoices/{invoice_id}/void")
def void(tenant_id: str, invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    try:
        with open_billing(tenant_id) as rows:
            _customer_account(context, tenant_id, rows)
            svc.void_invoice(rows, invoice_id, now=now)
            detail = invoice_detail_view(svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id)), now)
    except svc.BillingError as exc:
        raise _raise(exc) from None
    clear_cache(tenant_id)
    _audit(repository, context, "billing.invoice.void", invoice_id, {"tenant": tenant_id})
    return detail


def _email_text(invoice: dict[str, Any], reminder: int = 0) -> tuple[str, str]:
    from .pdf import money

    due = invoice["due_at"].date().isoformat() if invoice.get("due_at") else "-"
    amount = money(int(invoice["amount_due_minor"]), invoice["currency"])
    if reminder:
        subject = f"Reminder {reminder}: invoice {invoice['number']} is overdue"
        text = (f"Invoice {invoice['number']} for {amount} was due on {due} and is not paid yet. "
                "The invoice is attached with the bank details; please pay it or reply if something is wrong.")
    else:
        subject = f"Invoice {invoice['number']}"
        text = f"Invoice {invoice['number']} for {amount} is attached. It is due on {due}; the bank details are on the invoice."
    return subject, text


async def request_invoice_email(
    context: TenantContext, repository: AnumRepository, tenant_id: str, invoice: dict[str, Any], to: str, *, channel: str = "email", reminder: int = 0,
) -> delivery.DeliveryOutcome:
    """Plan the invoice email as a delivery: it waits in Approvals and nothing is sent before someone approves it."""
    reference = await asyncio.to_thread(delivery.attachment_reference, INVOICE_ATTACHMENT_KIND, f"{tenant_id}:{invoice['id']}", "pdf", context)
    subject, text = _email_text(invoice, reminder)
    return await delivery.request_delivery(
        context, repository, channel=channel, to=to, subject=subject, text=text, attachments=[reference],
        title=f"Send invoice {invoice['number']}" if not reminder else f"Send reminder for invoice {invoice['number']}",
    )


@console.post("/organizations/{tenant_id}/invoices/{invoice_id}/email")
async def email_invoice(
    tenant_id: str, invoice_id: str, payload: EmailInput, context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> dict[str, Any]:
    _operator(context)

    def load() -> tuple[dict[str, Any], dict[str, Any]]:
        with open_billing(tenant_id) as rows:
            account = _customer_account(context, tenant_id, rows)
            return account, _invoice_or_404(rows, invoice_id)

    account, invoice = await asyncio.to_thread(load)
    if invoice["status"] in ("draft", "void"):
        raise HTTPException(status_code=409, detail=f"Invoice {invoice['number'] or invoice_id} is {invoice['status']}; only issued invoices are emailed.")
    to = (payload.to or account["contact_email"] or "").strip()
    if not is_email(to):
        raise HTTPException(status_code=422, detail="Enter the email address to send to, or add a billing contact email to the organisation.")
    try:
        outcome = await request_invoice_email(context, repository, tenant_id, invoice, to, channel=payload.channel)
    except delivery.DeliveryUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    entry = {"status": outcome.status, "approval_id": outcome.approval_id, "task_id": outcome.task_id, "requested_at": utc_now().isoformat(),
             "requested_by": context.user_id}

    def remember() -> None:
        with open_billing(tenant_id) as rows:
            rows.update("billing_invoices", {"id": invoice_id}, {"last_email": entry, "updated_at": utc_now()})

    await asyncio.to_thread(remember)
    _audit(repository, context, "billing.invoice.email_requested", invoice_id, {"tenant": tenant_id, "status": outcome.status, "task_id": outcome.task_id})
    message = {"waiting_approval": "The email waits for an approval: approve it in Approvals and it will be sent.", "sent": "Sent."}.get(
        outcome.status, outcome.summary or "Not sent.")
    return {"status": outcome.status, "approval_id": outcome.approval_id, "payload_hash": outcome.payload_hash, "task_id": outcome.task_id, "message": message}


@console.post("/run")
async def run_now(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    from .jobs import run_once

    result = await run_once(issuer_tenant_id=context.tenant_id)
    _audit(repository, context, "billing.run", context.tenant_id, result)
    return result


def _resolve_invoice_attachment(context: TenantContext, item_id: str, fmt: str) -> tuple[str, str, bytes] | None:
    tenant_id, _, invoice_id = item_id.partition(":")
    if fmt != "pdf" or not tenant_id or not invoice_id:
        return None
    with open_billing(tenant_id) as rows:
        account = svc.account_of(rows)
        if account is None or account["issuer_tenant_id"] != context.tenant_id:
            return None
        invoice = svc.get_invoice(rows, invoice_id)
        if invoice is None:
            return None
        detail = svc.invoice_detail(rows, invoice)
    return invoice_filename(invoice), "application/pdf", render_invoice(detail)


delivery.register_attachment_resolver(INVOICE_ATTACHMENT_KIND, _resolve_invoice_attachment)


# ----------------------------------------------------------------------------- Stripe webhook


@webhooks.post("/stripe/{issuer_tenant_id}")
async def stripe_webhook(issuer_tenant_id: str, request: Request) -> dict[str, Any]:
    """Stripe's signed delivery; idempotent per event id (docs/billing.md#payment-collection)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,79}", issuer_tenant_id):
        raise HTTPException(status_code=404, detail="Not found.")
    payload = await request.body()
    if len(payload) > 512 * 1024:
        raise HTTPException(status_code=413, detail="The event is too large.")
    signature = request.headers.get("stripe-signature")
    return await asyncio.to_thread(handle_stripe_event, issuer_tenant_id, payload, signature)


def handle_stripe_event(issuer_tenant_id: str, payload: bytes, signature: str | None, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    with open_billing(issuer_tenant_id) as rows:
        issuer = svc.issuer_settings(rows, issuer_tenant_id)
        secret = _stripe_secret(issuer, "stripe_webhook_secret_ciphertext")
        if secret is None:
            raise HTTPException(status_code=404, detail="Not found.")
        try:
            verify_stripe_signature(payload, signature, secret, now=now.timestamp())
        except SignatureError:
            logger.warning("billing_stripe_signature_rejected", extra={"anum_billing": {"issuer": issuer_tenant_id}})
            raise HTTPException(status_code=400, detail="The signature does not verify.") from None
        try:
            event = json.loads(payload)
            event_id, event_type = str(event["id"]), str(event["type"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=400, detail="The event is not valid JSON.") from None
        fresh = rows.insert_ignore("billing_webhook_events", {
            "tenant_id": issuer_tenant_id, "provider": "stripe", "event_id": event_id[:200], "event_type": event_type[:100], "received_at": now,
        })
        if not fresh:
            return {"received": True, "duplicate": True}
        outcome = "ignored"
        obj = (event.get("data") or {}).get("object") or {}
        if event_type == "checkout.session.completed" and obj.get("payment_status") == "paid":
            outcome = _apply_checkout(rows, issuer_tenant_id, obj, now)
    return {"received": True, "duplicate": False, "outcome": outcome}


def _credit_overpayment(rows: Rows, amount_minor: int, now: datetime) -> None:
    """Add money Stripe collected beyond what was due to the organisation's credit balance."""
    account = svc.account_of(rows)
    if account is not None and amount_minor > 0:
        rows.update("billing_accounts", {"tenant_id": rows.tenant_id},
                    {"credit_balance_minor": int(account["credit_balance_minor"]) + amount_minor, "updated_at": now})


def _apply_checkout(rows: Rows, issuer_tenant_id: str, session: dict[str, Any], now: datetime) -> str:
    metadata = session.get("metadata") or {}
    tenant_id, invoice_id = str(metadata.get("anum_tenant_id") or ""), str(metadata.get("anum_invoice_id") or "")
    amount = int(session.get("amount_total") or 0)
    provider_id = str(session.get("payment_intent") or session.get("id") or "")[:200]
    if not tenant_id or not invoice_id or amount <= 0 or not provider_id:
        return "ignored"
    with rows.as_tenant(tenant_id):
        account = svc.account_of(rows)
        if account is None or account["issuer_tenant_id"] != issuer_tenant_id:
            logger.warning("billing_stripe_foreign_invoice", extra={"anum_billing": {"issuer": issuer_tenant_id}})
            return "ignored"
        # One Stripe payment is applied once, whatever event id carries it (and even when it only
        # became credit, which leaves no payment row to find it by).
        with rows.as_tenant(issuer_tenant_id):
            first = rows.insert_ignore("billing_webhook_events", {
                "tenant_id": issuer_tenant_id, "provider": "stripe", "event_id": f"payment:{provider_id}"[:200],
                "event_type": "payment.applied", "received_at": now,
            })
        if not first:
            return "duplicate_payment"
        invoice = svc.get_invoice(rows, invoice_id)
        if invoice is None:
            return "ignored"
        if invoice["status"] != "open":
            # Paid another way meanwhile: what Stripe collected is not dropped, it becomes credit.
            _credit_overpayment(rows, amount, now)
            logger.warning("billing_stripe_payment_credited", extra={"anum_billing": {"reason": "invoice_not_open", "amount_minor": amount}})
            return "credited"
        due = int(invoice["amount_due_minor"])
        applied = min(amount, due)
        excess = amount - applied
        try:
            svc.record_payment(
                rows, invoice_id, amount_minor=applied, method="stripe",
                reference=f"Stripe {provider_id}" + (f" (overpaid {excess}, credited to the account)" if excess else ""),
                received_at=now, now=now, actor="system:stripe", provider="stripe", provider_payment_id=provider_id,
            )
        except RowConflict:
            return "duplicate_payment"
        except svc.BillingError:
            _credit_overpayment(rows, amount, now)
            return "credited"
        if excess:
            _credit_overpayment(rows, excess, now)
            logger.warning("billing_stripe_overpayment_credited", extra={"anum_billing": {"amount_minor": excess}})
    clear_cache(tenant_id)
    return "paid"


