"""Ready to sell (docs/selling-readiness.md): Moyasar card payments, refunds, receipts, the company
details behind the legal pages and invoices, and the platform owner's readiness checklist.

Routes:

* ``GET``, ``PUT /api/v1/platform/billing/providers/moyasar``: the Moyasar adapter (platform owner).
* ``POST /api/v1/billing/webhooks/moyasar/{issuer_tenant_id}``: Moyasar's deliveries.
* ``POST /api/v1/platform/billing/organizations/{tenant}/invoices/{id}/payments/{payment}/refunds``: a refund.
* ``GET .../payments/{payment}/receipt`` (console and the organisation's own billing): a receipt PDF.
* ``GET``, ``PUT /api/v1/platform/legal``: the company details and legal facts (platform owner).
* ``GET /api/v1/public/legal``: what the public legal pages fill in (anyone).
* ``GET /api/v1/platform/readiness`` and ``POST /api/v1/platform/readiness/dns``: the checklist and
  the SPF and DMARC lookup.

Every route checks permissions on the server, works inside the caller's own tenant (or the
customer's, after the same ``_customer_account`` check the console uses), and writes an audit
record with ids and amounts only. No transaction is open while Moyasar is called.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import company as co
from . import moyasar as moy
from . import service as svc
from .api import _admin, _audit, _customer_account, _email_connected, _invoice_or_404, _operator, _raise, console, is_email, router, webhooks
from .enforce import clear_cache
from .pdf import receipt_filename, render_receipt
from .providers import ProviderError, SignatureError
from .rows import RowConflict, Rows, open_billing
from .views import invoice_detail_view, moyasar_view, refund_view

logger = logging.getLogger("anum.billing")
platform = APIRouter(prefix="/api/v1/platform", tags=["billing"])
public_legal_router = APIRouter(prefix="/api/v1/public", tags=["billing"])
TENANT_ID = r"[a-z0-9][a-z0-9_-]{2,79}"


def moyasar_webhook_path(issuer_tenant_id: str) -> str:
    return f"/api/v1/billing/webhooks/moyasar/{issuer_tenant_id}"


def _decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    from ..secret_box import secret_cipher

    return secret_cipher().decrypt(ciphertext)


def moyasar_for(issuer: dict[str, Any]) -> moy.MoyasarProvider | None:
    """The issuer's Moyasar adapter when it is turned on with a key, else ``None``."""
    if not issuer.get("moyasar_enabled"):
        return None
    key = _decrypt(issuer.get("moyasar_secret_ciphertext"))
    return moy.MoyasarProvider(key) if key else None


# ----------------------------------------------------------------------------- Moyasar settings


class MoyasarInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Write-only: a new value replaces the stored one, "" removes it, null keeps it.
    secret_key: str | None = Field(default=None, max_length=300)
    webhook_secret: str | None = Field(default=None, max_length=300)


@console.get("/providers/moyasar")
def get_moyasar(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _operator(context)
    with open_billing(context.tenant_id) as rows:
        return moyasar_view(svc.issuer_settings(rows, context.tenant_id), moyasar_webhook_path(context.tenant_id))


@console.put("/providers/moyasar")
def put_moyasar(payload: MoyasarInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    from ..secret_box import secret_cipher

    values: dict[str, Any] = {"moyasar_enabled": payload.enabled}
    if payload.secret_key is not None:
        key = payload.secret_key.strip()
        if key:
            try:
                moy.MoyasarProvider(key)
            except ProviderError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        values["moyasar_secret_ciphertext"] = secret_cipher().encrypt(key) if key else None
        values["moyasar_key_mode"] = moy.key_mode(key) if key else None
    if payload.webhook_secret is not None:
        secret = payload.webhook_secret.strip()
        if secret and len(secret) < 16:
            raise HTTPException(status_code=422, detail="Use a webhook secret token of at least 16 characters; a long random one is best.")
        values["moyasar_webhook_secret_ciphertext"] = secret_cipher().encrypt(secret) if secret else None
    now = utc_now()
    with open_billing(context.tenant_id) as rows:
        current = rows.get("billing_settings", tenant_id=context.tenant_id)
        merged = {**svc.DEFAULT_SETTINGS, **(current or {}), **values}
        if payload.enabled and not merged.get("moyasar_secret_ciphertext"):
            raise HTTPException(status_code=422, detail="Enter the Moyasar secret key before turning Moyasar card payments on.")
        if payload.enabled and not merged.get("moyasar_webhook_secret_ciphertext"):
            raise HTTPException(status_code=422, detail="Enter the webhook secret token you set in the Moyasar dashboard before turning Moyasar on; without it no payment can be confirmed.")
        values.update(updated_by=context.user_id, updated_at=now)
        if current is None:
            rows.insert("billing_settings", {**svc.DEFAULT_SETTINGS, **values, "tenant_id": context.tenant_id})
        else:
            rows.update("billing_settings", {"tenant_id": context.tenant_id}, values)
        view = moyasar_view(svc.issuer_settings(rows, context.tenant_id), moyasar_webhook_path(context.tenant_id))
    co.clear_cache()
    _audit(repository, context, "billing.moyasar.update", context.tenant_id, {
        "enabled": payload.enabled, "key_changed": payload.secret_key is not None, "webhook_secret_changed": payload.webhook_secret is not None,
    })
    return view


# ----------------------------------------------------------------------------- Moyasar webhook


@webhooks.post("/moyasar/{issuer_tenant_id}")
async def moyasar_webhook(issuer_tenant_id: str, request: Request) -> dict[str, Any]:
    """Moyasar's delivery: the secret token is checked, then the payment is read back from Moyasar."""
    if not re.fullmatch(TENANT_ID, issuer_tenant_id):
        raise HTTPException(status_code=404, detail="Not found.")
    payload = await request.body()
    if len(payload) > 512 * 1024:
        raise HTTPException(status_code=413, detail="The event is too large.")
    return await asyncio.to_thread(handle_moyasar_event, issuer_tenant_id, payload)


def handle_moyasar_event(issuer_tenant_id: str, payload: bytes, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    # 1. Read the issuer's keys in one short transaction.
    with open_billing(issuer_tenant_id) as rows:
        issuer = svc.issuer_settings(rows, issuer_tenant_id)
    secret = _decrypt(issuer.get("moyasar_webhook_secret_ciphertext")) if issuer.get("moyasar_enabled") else None
    provider = moyasar_for(issuer)
    if secret is None or provider is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="The event is not valid JSON.") from None
    try:
        moy.verify_webhook_token(event, secret)
    except SignatureError:
        logger.warning("billing_moyasar_token_rejected", extra={"anum_billing": {"issuer": issuer_tenant_id}})
        raise HTTPException(status_code=400, detail="The secret token does not match.") from None
    event_id, event_type = str(event.get("id") or ""), str(event.get("type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    payment_id = str(data.get("id") or "")
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="The event has no id or type.")
    with open_billing(issuer_tenant_id) as rows:
        seen = rows.select("billing_webhook_events", {"provider": "moyasar", "event_id": event_id[:200]}, limit=1)
    if seen:
        return {"received": True, "duplicate": True}
    if event_type not in ("payment_paid", "payment_refunded") or not payment_id:
        outcome = "ignored"
    else:
        # 2. The truth comes from Moyasar's API, with no transaction open.
        try:
            payment = provider.fetch_payment(payment_id)
            metadata = payment.metadata
            if not metadata.get("anum_invoice_id") and payment.invoice_id:
                metadata = provider.invoice_metadata(payment.invoice_id)
        except ProviderError as exc:
            # Not recorded as seen: Moyasar retries and the next delivery tries again.
            logger.warning("billing_moyasar_lookup_failed", extra={"anum_billing": {"issuer": issuer_tenant_id}})
            raise HTTPException(status_code=502, detail=str(exc)) from None
        outcome = "ignored"
        tenant_id, invoice_id = metadata.get("anum_tenant_id", ""), metadata.get("anum_invoice_id", "")
        if tenant_id and invoice_id and re.fullmatch(TENANT_ID, tenant_id):
            if event_type == "payment_paid" and payment.status == "paid":
                outcome = _apply_moyasar_payment(issuer_tenant_id, tenant_id, invoice_id, payment, now)
            elif event_type == "payment_refunded":
                outcome = _sync_moyasar_refunds(issuer_tenant_id, tenant_id, invoice_id, payment, now)
    # 3. Remember the event (a duplicate delivery after this answers 200 and does nothing).
    with open_billing(issuer_tenant_id) as rows:
        rows.insert_ignore("billing_webhook_events", {
            "tenant_id": issuer_tenant_id, "provider": "moyasar", "event_id": event_id[:200], "event_type": event_type[:100], "received_at": now,
        })
    return {"received": True, "duplicate": False, "outcome": outcome}


def _credit(rows: Rows, amount_minor: int, now: datetime) -> None:
    account = svc.account_of(rows)
    if account is not None and amount_minor > 0:
        rows.update("billing_accounts", {"tenant_id": rows.tenant_id},
                    {"credit_balance_minor": int(account["credit_balance_minor"]) + amount_minor, "updated_at": now})


def _apply_moyasar_payment(issuer_tenant_id: str, tenant_id: str, invoice_id: str, payment: moy.MoyasarPayment, now: datetime) -> str:
    """Record what Moyasar says was paid, once per Moyasar payment id (like the Stripe path)."""
    with open_billing(tenant_id) as rows:
        account = svc.account_of(rows)
        if account is None or account["issuer_tenant_id"] != issuer_tenant_id:
            logger.warning("billing_moyasar_foreign_invoice", extra={"anum_billing": {"issuer": issuer_tenant_id}})
            return "ignored"
        invoice = svc.get_invoice(rows, invoice_id)
        if invoice is None:
            return "ignored"
        if payment.currency != str(invoice["currency"]).upper() or payment.amount_minor <= 0:
            logger.warning("billing_moyasar_currency_mismatch", extra={"anum_billing": {"issuer": issuer_tenant_id}})
            return "ignored"
        with rows.as_tenant(issuer_tenant_id):
            first = rows.insert_ignore("billing_webhook_events", {
                "tenant_id": issuer_tenant_id, "provider": "moyasar", "event_id": f"payment:{payment.id}"[:200],
                "event_type": "payment.applied", "received_at": now,
            })
        if not first:
            return "duplicate_payment"
        amount = payment.amount_minor
        if invoice["status"] != "open":
            _credit(rows, amount, now)
            logger.warning("billing_moyasar_payment_credited", extra={"anum_billing": {"reason": "invoice_not_open", "amount_minor": amount}})
            return "credited"
        applied = min(amount, int(invoice["amount_due_minor"]))
        excess = amount - applied
        try:
            svc.record_payment(
                rows, invoice_id, amount_minor=applied, method="card",
                reference=f"Moyasar {payment.id}" + (f" (overpaid {excess}, credited to the account)" if excess else ""),
                received_at=now, now=now, actor="system:moyasar", provider="moyasar", provider_payment_id=payment.id[:200],
            )
        except RowConflict:
            return "duplicate_payment"
        except svc.BillingError:
            _credit(rows, amount, now)
            return "credited"
        if excess:
            _credit(rows, excess, now)
    clear_cache(tenant_id)
    return "paid"


def _sync_moyasar_refunds(issuer_tenant_id: str, tenant_id: str, invoice_id: str, payment: moy.MoyasarPayment, now: datetime) -> str:
    """Make ANUM's refunds of a Moyasar payment add up to what Moyasar says was refunded.

    Pending refunds that Moyasar's total now covers succeed; a refund made in the Moyasar
    dashboard (more refunded than ANUM knows) is recorded as one more refund, with a credit note.
    """
    with open_billing(tenant_id) as rows:
        account = svc.account_of(rows)
        if account is None or account["issuer_tenant_id"] != issuer_tenant_id:
            return "ignored"
        found = rows.select("billing_payments", {"provider": "moyasar", "provider_payment_id": payment.id[:200]}, limit=1)
        if not found or found[0]["invoice_id"] != invoice_id:
            return "ignored"
        ours = found[0]
        refunds = rows.select("billing_refunds", {"payment_id": ours["id"]}, order=[("created_at", False)])
        booked = sum(int(r["amount_minor"]) for r in refunds if r["status"] == "succeeded")
        outcome = "unchanged"
        for refund in refunds:
            if refund["status"] == "pending" and booked + int(refund["amount_minor"]) <= payment.refunded_minor:
                svc.apply_refund(rows, refund, now=now, actor="system:moyasar")
                booked += int(refund["amount_minor"])
                outcome = "refund_confirmed"
        extra = payment.refunded_minor - booked - sum(int(r["amount_minor"]) for r in refunds if r["status"] == "pending")
        if extra > 0:
            extra = min(extra, svc.refundable(rows, ours))
        if extra > 0:
            refund = {
                "id": new_id("refund"), "tenant_id": tenant_id, "issuer_tenant_id": issuer_tenant_id, "invoice_id": invoice_id,
                "payment_id": ours["id"], "amount_minor": extra, "currency": ours["currency"], "provider": "moyasar",
                "provider_refund_id": f"{payment.id}:{payment.refunded_minor}"[:200], "status": "pending",
                "reason": "Refunded in the Moyasar dashboard", "idempotency_key": f"moyasar:{payment.id}:{payment.refunded_minor}"[:120],
                "credit_note_id": None, "failure": None, "requested_by": "system:moyasar", "created_at": now, "updated_at": now,
            }
            if rows.insert_ignore("billing_refunds", refund):
                try:
                    svc.apply_refund(rows, refund, now=now, actor="system:moyasar")
                    outcome = "external_refund_recorded"
                except svc.BillingError:
                    rows.update("billing_refunds", {"id": refund["id"]}, {"status": "failed", "failure": "More than the invoice allows; check it by hand.", "updated_at": now})
                    outcome = "external_refund_needs_review"
    clear_cache(tenant_id)
    return outcome


# ----------------------------------------------------------------------------- refunds


class RefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0, le=10**12)
    reason: str = Field(min_length=3, max_length=500)
    # "provider": Moyasar refunds the card. "manual": the money was returned outside ANUM (bank transfer back).
    mode: Literal["provider", "manual"] = "provider"
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


def _same_request(existing: dict[str, Any], payment_id: str, payload: RefundInput) -> bool:
    return existing["payment_id"] == payment_id and int(existing["amount_minor"]) == payload.amount_minor


@console.post("/organizations/{tenant_id}/invoices/{invoice_id}/payments/{payment_id}/refunds", status_code=status.HTTP_201_CREATED)
def refund_payment(
    tenant_id: str, invoice_id: str, payment_id: str, payload: RefundInput, response: Response,
    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> dict[str, Any]:
    _operator(context)
    now = utc_now()
    issuer_id = context.tenant_id
    # 1. Claim: one short transaction writes the pending refund (or finds the earlier one).
    with open_billing(tenant_id) as rows:
        _customer_account(context, tenant_id, rows)
        existing = rows.select("billing_refunds", {"idempotency_key": payload.idempotency_key}, limit=1)
        if existing:
            if not _same_request(existing[0], payment_id, payload):
                raise HTTPException(status_code=409, detail="That idempotency key was already used for a different refund; use a new key.")
            claim, replay = existing[0], True
        else:
            replay = False
            invoice = _invoice_or_404(rows, invoice_id)
            payment = rows.get("billing_payments", id=payment_id)
            if payment is None or payment["invoice_id"] != invoice_id:
                raise HTTPException(status_code=404, detail="That payment is not on this invoice.")
            if payload.mode == "provider" and payment.get("provider") != "moyasar":
                raise HTTPException(status_code=409, detail=(
                    "Only a Moyasar card payment can be refunded through the provider. Return the money yourself "
                    "(bank transfer, or the Stripe dashboard) and record it here as a manual refund."))
            left = svc.refundable(rows, payment)
            limit = min(left, svc.creditable(invoice), int(invoice["amount_paid_minor"]))
            if payload.amount_minor > limit:
                raise HTTPException(status_code=422, detail=f"At most {max(limit, 0)} can be refunded from this payment.")
            claim = {
                "id": new_id("refund"), "tenant_id": tenant_id, "issuer_tenant_id": issuer_id, "invoice_id": invoice_id,
                "payment_id": payment_id, "amount_minor": payload.amount_minor, "currency": payment["currency"],
                "provider": "moyasar" if payload.mode == "provider" else None, "provider_refund_id": None, "status": "pending",
                "reason": payload.reason, "idempotency_key": payload.idempotency_key, "credit_note_id": None, "failure": None,
                "requested_by": context.user_id, "created_at": now, "updated_at": now,
            }
            try:
                rows.insert("billing_refunds", claim)
            except RowConflict:
                raise HTTPException(status_code=409, detail="That idempotency key was already used; reload and try again.") from None
            if payload.mode == "manual":
                try:
                    svc.apply_refund(rows, claim, now=now, actor=context.user_id)
                except svc.BillingError as exc:
                    raise _raise(exc) from None
                claim = rows.get("billing_refunds", id=claim["id"]) or claim
        payment_row = rows.get("billing_payments", id=claim["payment_id"])
        issuer = svc.issuer_settings(rows, issuer_id)
    if replay and claim["status"] != "pending":
        response.status_code = status.HTTP_200_OK
        return _refund_answer(tenant_id, invoice_id, claim, now)
    # 2. The provider call, with no transaction open.
    if claim["status"] == "pending" and claim["provider"] == "moyasar":
        provider = moyasar_for(issuer)
        if provider is None:
            _fail(tenant_id, claim, "Moyasar is turned off; turn it on again or record a manual refund.", now)
            raise HTTPException(status_code=409, detail="Moyasar card payments are turned off, so the card cannot be refunded from ANUM.")
        provider_payment_id = str((payment_row or {}).get("provider_payment_id") or "")
        try:
            # A replayed pending claim may already have reached Moyasar: compare its refunded total first.
            already = provider.fetch_payment(provider_payment_id).refunded_minor if replay else 0
            with open_billing(tenant_id) as rows:
                booked = sum(int(r["amount_minor"]) for r in rows.select("billing_refunds", {"payment_id": claim["payment_id"], "status": "succeeded"}))
            result = None if replay and already >= booked + int(claim["amount_minor"]) else provider.refund(provider_payment_id, int(claim["amount_minor"]))
        except ProviderError as exc:
            _fail(tenant_id, claim, str(exc), now)
            raise HTTPException(status_code=502, detail=str(exc)) from None
        # 3. Book it: credit note, invoice paid amount lowered, refund succeeded.
        with open_billing(tenant_id) as rows:
            current = rows.get("billing_refunds", id=claim["id"])
            if current and current["status"] == "pending":
                rows.update("billing_refunds", {"id": claim["id"]}, {
                    "provider_refund_id": f"{provider_payment_id}:{result.refunded_minor if result else already}"[:200], "updated_at": now,
                })
                try:
                    svc.apply_refund(rows, current, now=now, actor=context.user_id)
                except svc.BillingError as exc:
                    raise _raise(exc) from None
            claim = rows.get("billing_refunds", id=claim["id"]) or claim
    clear_cache(tenant_id)
    _audit(repository, context, "billing.refund.create", invoice_id, {
        "tenant": tenant_id, "refund_id": claim["id"], "payment_id": claim["payment_id"], "amount_minor": int(claim["amount_minor"]),
        "mode": payload.mode, "status": claim["status"],
    })
    if replay:
        response.status_code = status.HTTP_200_OK
    return _refund_answer(tenant_id, invoice_id, claim, now)


def _fail(tenant_id: str, claim: dict[str, Any], message: str, now: datetime) -> None:
    with open_billing(tenant_id) as rows:
        rows.update("billing_refunds", {"id": claim["id"], "status": "pending"}, {"status": "failed", "failure": message[:300], "updated_at": now})


def _refund_answer(tenant_id: str, invoice_id: str, refund: dict[str, Any], now: datetime) -> dict[str, Any]:
    with open_billing(tenant_id) as rows:
        detail = invoice_detail_view(svc.invoice_detail(rows, _invoice_or_404(rows, invoice_id)), now)
    return {"refund": refund_view(refund), "invoice": detail}


# ----------------------------------------------------------------------------- receipts


def _receipt(rows: Rows, invoice_id: str, payment_id: str) -> Response:
    invoice = _invoice_or_404(rows, invoice_id)
    payment = rows.get("billing_payments", id=payment_id)
    if payment is None or payment["invoice_id"] != invoice_id:
        raise HTTPException(status_code=404, detail="That payment is not on this invoice.")
    refunds = rows.select("billing_refunds", {"payment_id": payment_id}, order=[("created_at", False)])
    body = render_receipt(invoice, payment, refunds)
    return Response(content=body, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{receipt_filename(invoice, payment)}"', "Cache-Control": "private, no-store",
    })


@router.get("/invoices/{invoice_id}/payments/{payment_id}/receipt")
def own_receipt(invoice_id: str, payment_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _admin(context)
    with open_billing(context.tenant_id) as rows:
        return _receipt(rows, invoice_id, payment_id)


@console.get("/organizations/{tenant_id}/invoices/{invoice_id}/payments/{payment_id}/receipt")
def console_receipt(tenant_id: str, invoice_id: str, payment_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _operator(context)
    with open_billing(tenant_id) as rows:
        _customer_account(context, tenant_id, rows)
        return _receipt(rows, invoice_id, payment_id)


# ----------------------------------------------------------------------------- company details and legal facts


class CompanyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(default="", max_length=200)
    trading_name: str = Field(default="", max_length=200)
    registration_number: str = Field(default="", max_length=80)
    registration_country: str = Field(default="", pattern=r"^([A-Za-z]{2})?$")
    registration_authority: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=600)
    contact_email: str = Field(default="", max_length=254)
    support_contact: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=40, pattern=r"^[0-9+()\s-]*$")
    vat_number: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9 ]*$")
    dpo_name: str = Field(default="", max_length=160)
    dpo_email: str = Field(default="", max_length=254)
    security_email: str = Field(default="", max_length=254)
    server_location: str = Field(default="", max_length=200)
    hosting_provider: Literal["", "oracle", "render", "other"] = ""
    governing_law: str = Field(default="", max_length=300)
    legal_terms: dict[str, str] = Field(default_factory=dict, max_length=len(co.TERM_KEYS))
    published_docs: list[Literal["terms", "privacy", "dpa", "aup", "subprocessors"]] = Field(default_factory=list, max_length=5)
    # The owner confirms each published document was reviewed by a lawyer; refused without it.
    lawyer_reviewed: bool = False

    @field_validator("contact_email", "dpo_email", "security_email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and not is_email(value):
            raise ValueError("Enter one email address, like privacy@example.com.")
        return value

    @field_validator("legal_terms")
    @classmethod
    def _terms(cls, value: dict[str, str]) -> dict[str, str]:
        return co.clean_terms(value)


def _profile_view(row: dict[str, Any] | None, provider_flags: dict[str, bool]) -> dict[str, Any]:
    merged = {**co.EMPTY_PROFILE, **(row or {})}
    values = co.legal_values(merged, provider_flags)
    generated = co.generated_values(provider_flags)
    return {
        "company": {name: merged.get(name) or "" for name, _ in co.COMPANY_FIELDS},
        "legal_terms": dict(merged.get("legal_terms") or {}),
        "published_docs": list(merged.get("published_docs") or []),
        "terms": [{"key": key, "label": label, "help": help_text, "generated": generated.get(key) is not None}
                  for key, (label, help_text) in co.TERM_KEYS.items()],
        "values": values,
        "missing": co.missing_by_doc(values),
        "generated": [{"key": key, "value": value} for key, value in sorted(generated.items()) if value is not None and key in co.ALL_KEYS],
        "required_company": list(co.REQUIRED_COMPANY),
        "updated_by": (row or {}).get("updated_by"),
        "updated_at": (row or {}).get("updated_at"),
        "public_issuer": settings.billing_issuer_tenant_id == merged.get("tenant_id") if row else None,
    }


def _flags(issuer: dict[str, Any]) -> dict[str, bool]:
    return {"stripe": bool(issuer.get("stripe_enabled")), "moyasar": bool(issuer.get("moyasar_enabled"))}


@platform.get("/legal")
def get_legal(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _operator(context)
    with open_billing(context.tenant_id) as rows:
        row = rows.get("billing_company_profiles", tenant_id=context.tenant_id)
        issuer = svc.issuer_settings(rows, context.tenant_id)
    return _profile_view(row, _flags(issuer))


@platform.put("/legal")
def put_legal(payload: CompanyInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    _operator(context)
    if payload.published_docs and not payload.lawyer_reviewed:
        raise HTTPException(status_code=422, detail="Confirm that a lawyer reviewed the documents before marking any of them published.")
    values = payload.model_dump(exclude={"lawyer_reviewed"})
    values["registration_country"] = values["registration_country"].upper()
    values["vat_number"] = values["vat_number"].strip().upper()
    for key in ("legal_name", "trading_name", "registration_number", "registration_authority", "address", "support_contact", "dpo_name",
                "server_location", "governing_law", "phone"):
        values[key] = values[key].strip()
    now = utc_now()
    with open_billing(context.tenant_id) as rows:
        issuer = svc.issuer_settings(rows, context.tenant_id)
        flags = _flags(issuer)
        missing = co.missing_by_doc(co.legal_values({**co.EMPTY_PROFILE, **values}, flags))
        blocked = [doc for doc in payload.published_docs if missing[doc]]
        if blocked:
            raise HTTPException(status_code=422, detail=(
                "These documents still have facts not set, so they cannot be marked published: "
                + ", ".join(f"{doc} ({len(missing[doc])})" for doc in blocked) + "."))
        values.update(updated_by=context.user_id, updated_at=now)
        current = rows.get("billing_company_profiles", tenant_id=context.tenant_id)
        if current is None:
            rows.insert("billing_company_profiles", {**values, "tenant_id": context.tenant_id})
        else:
            rows.update("billing_company_profiles", {"tenant_id": context.tenant_id}, values)
        row = rows.get("billing_company_profiles", tenant_id=context.tenant_id)
    co.clear_cache()
    _audit(repository, context, "platform.legal.update", context.tenant_id, {
        "fields_set": sorted(name for name, _ in co.COMPANY_FIELDS if values.get(name)),
        "terms_set": len(values["legal_terms"]), "published_docs": list(payload.published_docs),
    })
    return _profile_view(row, flags)


@public_legal_router.get("/legal")
def public_legal(response: Response) -> dict[str, Any]:
    """The company details and legal facts of the configured issuer, for the public legal pages."""
    response.headers["Cache-Control"] = "public, max-age=60"
    return co.public_legal(settings.billing_issuer_tenant_id)


# ----------------------------------------------------------------------------- readiness


def _item(key: str, done: bool, fix: str, detail: dict[str, Any] | None = None, *, state: str | None = None) -> dict[str, Any]:
    return {"key": key, "state": state or ("done" if done else "missing"), "fix": fix, "detail": detail or {}}


@platform.get("/readiness")
def readiness(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """The Ready to sell checklist, read from real state (docs/selling-readiness.md#checklist)."""
    _operator(context)
    with open_billing(context.tenant_id) as rows:
        issuer = svc.issuer_settings(rows, context.tenant_id)
        profile = rows.get("billing_company_profiles", tenant_id=context.tenant_id)
        plans = rows.select("billing_plans", {"active": True})
    flags = _flags(issuer)
    merged = {**co.EMPTY_PROFILE, **(profile or {})}
    values = co.legal_values(merged, flags)
    missing = co.missing_by_doc(values)
    moyasar_ready = bool(issuer.get("moyasar_enabled") and issuer.get("moyasar_secret_ciphertext") and issuer.get("moyasar_webhook_secret_ciphertext"))
    stripe_ready = bool(issuer.get("stripe_enabled") and issuer.get("stripe_secret_ciphertext") and issuer.get("stripe_webhook_secret_ciphertext"))
    live = (moyasar_ready and issuer.get("moyasar_key_mode") == "live") or (stripe_ready and issuer.get("stripe_key_mode") == "live")
    bank = bool(issuer.get("bank_iban"))
    company_missing = [name for name in co.REQUIRED_COMPANY if not str(merged.get(name) or "").strip()]
    backups = co.backup_status()
    mail_connected = _email_connected(context)
    smtp = bool(settings.smtp_host and settings.smtp_from)
    published_plans = [p for p in plans if p["published"]]
    published_docs = [d for d in merged.get("published_docs") or [] if d in co.DOC_IDS]

    def backup_state(kind: str) -> str:
        info = backups.get(kind)
        if info is None:
            return "unknown"
        return "done" if info["ok"] else "missing"

    items = [
        _item("payment_provider", moyasar_ready or stripe_ready, "payments", {
            "moyasar": "ready" if moyasar_ready else ("off" if not issuer.get("moyasar_enabled") else "incomplete"),
            "stripe": "ready" if stripe_ready else ("off" if not issuer.get("stripe_enabled") else "incomplete"),
            "live_keys": bool(live), "bank_transfer": bank,
        }, state=("done" if live else "warning") if (moyasar_ready or stripe_ready) else "missing"),
        _item("company_details", not company_missing, "company", {"missing": company_missing}),
        _item("mail_sending", mail_connected, "mail", {"mailbox_connected": mail_connected, "smtp_configured": smtp}),
        _item("backups_local", backup_state("local") == "done", "backups", {"recorded": backups["recorded"], **(backups.get("local") or {})},
              state=backup_state("local")),
        _item("backups_offsite", backup_state("offsite") == "done", "backups", {"recorded": backups["recorded"], **(backups.get("offsite") or {})},
              state=backup_state("offsite")),
        _item("legal_pages", all(not keys for keys in missing.values()) and len(published_docs) == len(co.DOC_IDS), "legal", {
            "missing": {doc: len(keys) for doc, keys in missing.items()}, "published": published_docs,
        }, state="done" if all(not keys for keys in missing.values()) and len(published_docs) == len(co.DOC_IDS)
            else ("warning" if all(not keys for keys in missing.values()) else "missing")),
        _item("plans", bool(published_plans), "plans", {"active": len(plans), "published": len(published_plans)}),
        _item("public_issuer", settings.billing_issuer_tenant_id == context.tenant_id, "env", {
            "configured": bool(settings.billing_issuer_tenant_id), "is_you": settings.billing_issuer_tenant_id == context.tenant_id,
            "web_app_url": bool(settings.web_app_url),
        }),
    ]
    done = sum(1 for item in items if item["state"] == "done")
    domain = ""
    for candidate in (str(merged.get("contact_email") or ""), settings.smtp_from or "", str(issuer.get("issuer_email") or "")):
        if "@" in candidate:
            domain = candidate.rsplit("@", 1)[1].strip(" >").lower()
            break
    return {"items": items, "done": done, "total": len(items), "ready": done == len(items), "mail_domain": domain or None,
            "checked_at": utc_now()}


class DnsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=3, max_length=253, pattern=r"^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


def _txt(name: str) -> tuple[list[str], str | None]:
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    try:
        answer = resolver.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return [], None
    except (dns.exception.DNSException, OSError) as exc:
        return [], type(exc).__name__
    return ["".join(part.decode("utf-8", "replace") for part in record.strings) for record in answer], None


def analyse_spf(records: list[str]) -> dict[str, Any]:
    """What an SPF record allows: which senders it names and whether it allows any at all."""
    spf = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf:
        return {"record": None, "status": "missing", "senders": [], "all": None}
    if len(spf) > 1:
        return {"record": spf[0], "status": "multiple", "senders": [], "all": None}
    terms = spf[0].split()[1:]
    senders = [t for t in terms if re.match(r"^[+]?(include:|a\b|a:|mx\b|mx:|ip4:|ip6:|exists:|redirect=)", t, re.I)]
    final = next((t for t in reversed(terms) if t.lower().lstrip("+-~?") == "all"), None)
    if final is not None and (final.startswith("+") or final == "all"):
        status_ = "too_open"
    elif not senders:
        status_ = "no_senders"
    else:
        status_ = "ok"
    return {"record": spf[0], "status": status_, "senders": senders, "all": final}


@platform.post("/readiness/dns")
async def dns_check(payload: DnsInput, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Resolve the SPF and DMARC TXT records of a mail domain on the server (never cached)."""
    _operator(context)
    domain = payload.domain.lower().rstrip(".")
    (spf_records, spf_error), (dmarc_records, dmarc_error) = await asyncio.gather(
        asyncio.to_thread(_txt, domain), asyncio.to_thread(_txt, f"_dmarc.{domain}"),
    )
    dmarc = next((r for r in dmarc_records if r.lower().startswith("v=dmarc1")), None)
    policy = None
    if dmarc:
        match = re.search(r"\bp=([a-z]+)", dmarc, re.I)
        policy = match.group(1).lower() if match else None
    return {
        "domain": domain, "checked_at": utc_now(),
        "spf": {**analyse_spf(spf_records), "error": spf_error},
        "dmarc": {"record": dmarc, "policy": policy, "status": "missing" if not dmarc else ("ok" if policy in ("quarantine", "reject") else "monitor"),
                  "error": dmarc_error},
    }

