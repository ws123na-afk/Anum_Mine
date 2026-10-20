"""Payment providers (docs/billing.md#payment-collection).

Manual collection (bank transfer, cash) needs no provider: the platform owner records the
payment. Card payments go through a :class:`PaymentProvider`; the only adapter is Stripe,
off by default, with its keys entered by the platform owner and kept as Fernet
ciphertexts in ``billing_settings`` (never in the repository or the environment).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 300
# Swapped in tests for an httpx.MockTransport; never a real network call there.
transport: httpx.BaseTransport | None = None


class ProviderError(Exception):
    """The provider refused or could not be reached; ``message`` is safe to show."""


class SignatureError(Exception):
    """A webhook whose signature does not verify."""


@dataclass(frozen=True)
class Checkout:
    url: str
    session_id: str


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, *, invoice: dict[str, Any], success_url: str, cancel_url: str) -> Checkout: ...


class StripeProvider:
    name = "stripe"

    def __init__(self, secret_key: str) -> None:
        if not secret_key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
            raise ProviderError("The Stripe secret key must start with sk_test_, sk_live_, rk_test_ or rk_live_.")
        self._key = secret_key

    def create_checkout(self, *, invoice: dict[str, Any], success_url: str, cancel_url: str) -> Checkout:
        form = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": invoice["id"],
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": str(invoice["currency"]).lower(),
            "line_items[0][price_data][unit_amount]": str(int(invoice["amount_due_minor"])),
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
