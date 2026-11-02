"""Payment providers (docs/billing.md#payment-collection).

Manual collection (bank transfer, cash) needs no provider: the platform owner records the
payment. Card payments go through a :class:`PaymentProvider`: Stripe (here) or Moyasar
(``moyasar.py``), each off by default, with its keys entered by the platform owner and kept as
Fernet ciphertexts in ``billing_settings`` (never in the repository or the environment).

Stripe is the provider for an owner with a UAE company (docs/selling-readiness.md#stripe-uae):
hosted Checkout for ANUM invoices, hosted Payment Links for Business invoices, and refunds
through Stripe's Refunds API. ANUM never sees a card number. Amounts are integer minor units
(fils for AED, halalas for SAR, cents for USD), the unit Stripe expects for these two-decimal
currencies; any other currency is refused before Stripe is called. Every request that creates
something carries an ``Idempotency-Key``, so a retry never makes a second payment page or refund.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 300
TIMEOUT_SECONDS = 15.0
# Two-decimal currencies ANUM sends to Stripe, with Stripe's smallest charge in minor units.
# AED is the currency of a UAE Stripe account (amounts in fils: AED 1.00 = 100).
STRIPE_MINIMUM_MINOR = {"AED": 200, "SAR": 200, "USD": 50, "EUR": 50, "GBP": 30}
STRIPE_CURRENCIES = tuple(STRIPE_MINIMUM_MINOR)
# Refund states Stripe reports; only "succeeded" means the money went back to the card.
REFUND_DONE, REFUND_WAITING, REFUND_FAILED = ("succeeded",), ("pending", "requires_action"), ("failed", "canceled")
_ID = re.compile(r"^[A-Za-z0-9_]{3,200}$")
# Swapped in tests for an httpx.MockTransport; never a real network call there.
transport: httpx.BaseTransport | None = None


class ProviderError(Exception):
    """The provider refused or could not be reached; ``message`` is safe to show."""


class ProviderUnreachable(ProviderError):
    """No clear answer (network error, timeout or a provider 5xx): the request may or may not have
    happened, so a refund claim stays pending and is checked again instead of being marked failed."""


class SignatureError(Exception):
    """A webhook whose signature does not verify."""


@dataclass(frozen=True)
class Checkout:
    url: str
    session_id: str


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, *, invoice: dict[str, Any], success_url: str, cancel_url: str) -> Checkout: ...


@dataclass(frozen=True)
class StripeRefund:
    """A refund as Stripe reports it."""

    id: str
    payment_intent: str
    amount_minor: int
    currency: str
    status: str
    metadata: dict[str, str] = field(default_factory=dict)


def stripe_amount(amount_minor: int, currency: str) -> tuple[str, int]:
    """Check an amount for Stripe: a supported two-decimal currency and at least Stripe's minimum.

    Returns the lower-case currency code Stripe wants and the amount in minor units unchanged
    (ANUM already stores fils, halalas and cents).
    """
    code = str(currency).upper()
    if code not in STRIPE_MINIMUM_MINOR:
        raise ProviderError(f"Stripe card payments are set up for {', '.join(STRIPE_CURRENCIES)}; this invoice is in {code}.")
    amount = int(amount_minor)
    if amount < STRIPE_MINIMUM_MINOR[code]:
        minimum = STRIPE_MINIMUM_MINOR[code]
        raise ProviderError(f"Stripe takes at least {minimum // 100}.{minimum % 100:02d} {code} by card; pay this amount by bank transfer.")
    return code.lower(), amount


def _refund(body: dict[str, Any]) -> StripeRefund:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    return StripeRefund(
        id=str(body.get("id") or ""), payment_intent=str(body.get("payment_intent") or ""), amount_minor=int(body.get("amount") or 0),
        currency=str(body.get("currency") or "").upper(), status=str(body.get("status") or ""),
        metadata={str(k): str(v) for k, v in (metadata or {}).items()},
    )


class StripeProvider:
    name = "stripe"

    def __init__(self, secret_key: str) -> None:
        if not secret_key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
            raise ProviderError("The Stripe secret key must start with sk_test_, sk_live_, rk_test_ or rk_live_.")
        self._key = secret_key

    def _call(self, method: str, path: str, *, what: str, form: dict[str, str] | None = None, params: dict[str, str] | None = None,
              idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.request(method, f"{STRIPE_API}{path}", data=form, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderUnreachable("Stripe could not be reached; try again in a minute.") from exc
        if response.status_code >= 500:
            raise ProviderUnreachable(f"Stripe did not answer {what} clearly (HTTP {response.status_code}); try again in a minute.")
        if response.status_code == 401:
            raise ProviderError("Stripe refused the secret key (HTTP 401); check the Stripe key in Platform billing > Settings.")
        if response.status_code >= 400:
            raise ProviderError(f"Stripe refused {what} (HTTP {response.status_code}).")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"Stripe answered {what} with something that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"Stripe answered {what} with something unexpected.")
        return body

    def create_checkout(self, *, invoice: dict[str, Any], success_url: str, cancel_url: str) -> Checkout:
        currency, amount = stripe_amount(int(invoice["amount_due_minor"]), str(invoice["currency"]))
        form = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": invoice["id"],
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": currency,
            "line_items[0][price_data][unit_amount]": str(amount),
            "line_items[0][price_data][product_data][name]": f"Invoice {invoice['number']}",
            "metadata[anum_tenant_id]": invoice["tenant_id"],
            "metadata[anum_invoice_id]": invoice["id"],
            "payment_intent_data[metadata][anum_tenant_id]": invoice["tenant_id"],
            "payment_intent_data[metadata][anum_invoice_id]": invoice["id"],
        }
        # The same invoice and amount always maps to the same Checkout session.
        idempotency = hashlib.sha256(f"{invoice['id']}:{invoice['amount_due_minor']}".encode()).hexdigest()
        try:
            with httpx.Client(transport=transport, timeout=15.0) as client:
                response = client.post(
                    f"{STRIPE_API}/checkout/sessions", data=form,
                    headers={"Authorization": f"Bearer {self._key}", "Idempotency-Key": idempotency},
                )
        except httpx.HTTPError as exc:
            raise ProviderError("Stripe could not be reached; try again in a minute.") from exc
        if response.status_code >= 400:
            raise ProviderError(f"Stripe refused the payment page (HTTP {response.status_code}); check the Stripe key in the billing settings.")
        body = response.json()
        return Checkout(url=str(body.get("url") or ""), session_id=str(body.get("id") or ""))

    def create_payment_link(self, *, amount_minor: int, currency: str, description: str, redirect_url: str, metadata: dict[str, str],
                            idempotency_key: str) -> Checkout:
        """A hosted Stripe Payment Link for one fixed amount (a Business invoice's balance).

        Unlike a Checkout session (24 hours at most) a Payment Link stays open until ANUM turns it
        off, so it can go in an emailed invoice. It accepts one completed payment, and the ANUM ids
        travel in its metadata, which Stripe copies to each Checkout session and payment it makes.
        """
        code, amount = stripe_amount(amount_minor, currency)
        price = self._call("POST", "/prices", what="the price for the payment link", idempotency_key=f"{idempotency_key}:price", form={
            "currency": code, "unit_amount": str(amount), "product_data[name]": description[:250],
        })
        price_id = str(price.get("id") or "")
        if not _ID.match(price_id):
            raise ProviderError("Stripe did not return a price for the payment link.")
        form = {
            "line_items[0][price]": price_id, "line_items[0][quantity]": "1",
            "after_completion[type]": "redirect", "after_completion[redirect][url]": redirect_url,
            "restrictions[completed_sessions][limit]": "1",
        }
        for key, value in metadata.items():
            form[f"metadata[{key}]"] = value
            form[f"payment_intent_data[metadata][{key}]"] = value
        body = self._call("POST", "/payment_links", what="the payment link", idempotency_key=f"{idempotency_key}:link", form=form)
        url = str(body.get("url") or "")
        if not url.startswith("https://"):
            raise ProviderError("Stripe did not return a payment link address.")
        return Checkout(url=url, session_id=str(body.get("id") or ""))

    def deactivate_payment_link(self, link_id: str) -> None:
        if _ID.match(link_id or ""):
            self._call("POST", f"/payment_links/{link_id}", what="turning off the old payment link", form={"active": "false"})

    def payment_link_metadata(self, link_id: str) -> dict[str, str]:
        if not _ID.match(link_id or ""):
            return {}
        metadata = self._call("GET", f"/payment_links/{link_id}", what="the payment link lookup").get("metadata")
        return {str(k): str(v) for k, v in metadata.items()} if isinstance(metadata, dict) else {}

    def payment_intent_metadata(self, payment_intent: str) -> dict[str, str]:
        if not payment_intent.startswith("pi_") or not _ID.match(payment_intent):
            raise ProviderError("That is not a Stripe payment id.")
        metadata = self._call("GET", f"/payment_intents/{payment_intent}", what="the payment lookup").get("metadata")
        return {str(k): str(v) for k, v in metadata.items()} if isinstance(metadata, dict) else {}

    def refund(self, *, payment_intent: str, amount_minor: int, idempotency_key: str, metadata: dict[str, str]) -> StripeRefund:
        """Refund part or all of a card payment. The same ``idempotency_key`` never refunds twice."""
        if not payment_intent.startswith("pi_") or not _ID.match(payment_intent):
            raise ProviderError("This payment has no Stripe payment id, so it cannot be refunded through Stripe; refund it in the Stripe dashboard and record it by hand.")
        if int(amount_minor) <= 0:
            raise ProviderError("A refund must be more than zero.")
        form = {"payment_intent": payment_intent, "amount": str(int(amount_minor)), "reason": "requested_by_customer"}
        for key, value in metadata.items():
            form[f"metadata[{key}]"] = value
        refund = _refund(self._call("POST", "/refunds", what="the refund", form=form, idempotency_key=idempotency_key))
        if not refund.id:
            raise ProviderError("Stripe did not return a refund id.")
        return refund

    def refunds_of(self, payment_intent: str) -> list[StripeRefund]:
        """Every refund Stripe holds for a payment (the truth a refund webhook is checked against)."""
        if not payment_intent.startswith("pi_") or not _ID.match(payment_intent):
            raise ProviderError("That is not a Stripe payment id.")
        found: list[StripeRefund] = []
        params = {"payment_intent": payment_intent, "limit": "100"}
        for _ in range(10):
            body = self._call("GET", "/refunds", what="the refund list", params=params)
            items = [_refund(item) for item in body.get("data") or [] if isinstance(item, dict)]
            found.extend(items)
            if not body.get("has_more") or not items:
                break
            params = {**params, "starting_after": items[-1].id}
        return found


def key_mode(secret_key: str) -> str:
    return "live" if "_live_" in secret_key[:8] else "test"


def verify_stripe_signature(payload: bytes, header: str | None, secret: str, *, now: float | None = None,
                            tolerance: int = SIGNATURE_TOLERANCE_SECONDS) -> None:
    """Check ``Stripe-Signature`` (``t=<unix>,v1=<hex HMAC-SHA256 of "t.payload">``)."""
    if not header or not secret:
        raise SignatureError("missing signature")
    timestamp: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            timestamp = value
        elif name == "v1":
            signatures.append(value)
    if timestamp is None or not timestamp.isdigit() or not signatures:
        raise SignatureError("malformed signature")
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > tolerance:
        raise SignatureError("signature timestamp outside the tolerance")
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureError("signature mismatch")


def sign_stripe_payload(payload: bytes, secret: str, timestamp: int) -> str:
    """The header Stripe would send; used by tests and the local webhook check."""
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"
