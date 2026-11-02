"""Moyasar, the Saudi card payment adapter (docs/billing.md#moyasar).

Why Moyasar (docs/selling-readiness.md#payment-provider): a plain JSON REST API with HTTP
Basic auth on the secret key, a hosted payment page (an *invoice*: ANUM never sees a card
number), mada, Visa, Mastercard, Apple Pay and STC Pay, amounts in minor units like ANUM,
refunds by API, test keys (``sk_test_``) that work against the sandbox, and webhooks.

Moyasar's webhooks carry the shared ``secret_token`` the owner chose in the Moyasar
dashboard inside the JSON body, not an HMAC header. ANUM therefore does two things before it
records money: the token is compared in constant time with the stored one, and the payment
is fetched again from Moyasar's API with the secret key, so its status, amount, currency and
the ANUM invoice it belongs to come from Moyasar itself, never from the delivered body.

Off by default: the platform owner enters the secret key and the webhook token in the console;
both are stored only as Fernet ciphertexts in ``billing_settings``. Tests swap ``transport``
for an ``httpx.MockTransport`` (a fake Moyasar); nothing here is ever called against the real
service in tests.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from .providers import Checkout, ProviderError, SignatureError

MOYASAR_API = "https://api.moyasar.com/v1"
# Currencies ANUM sends to Moyasar; the account must also have them turned on (ask Moyasar for AED).
CURRENCIES = ("SAR", "AED", "USD")
TIMEOUT_SECONDS = 15.0
# Swapped in tests for an httpx.MockTransport; never a real network call there.
transport: httpx.BaseTransport | None = None


def key_mode(secret_key: str) -> str:
    return "live" if secret_key.startswith("sk_live_") else "test"


@dataclass(frozen=True)
class MoyasarPayment:
    """A payment as Moyasar's API reports it (the only source ANUM records money from)."""

    id: str
    status: str
    amount_minor: int
    currency: str
    refunded_minor: int
    invoice_id: str | None
    metadata: dict[str, str]


@dataclass(frozen=True)
class MoyasarRefund:
    payment_id: str
    refunded_minor: int
    status: str


def verify_webhook_token(body: dict[str, Any], expected: str) -> None:
    """The delivered ``secret_token`` must equal the stored one (constant time)."""
    token = body.get("secret_token") if isinstance(body, dict) else None
    if not expected or not isinstance(token, str) or not token:
        raise SignatureError("missing secret token")
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise SignatureError("secret token mismatch")


def _payment(body: dict[str, Any]) -> MoyasarPayment:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    return MoyasarPayment(
        id=str(body.get("id") or ""), status=str(body.get("status") or ""), amount_minor=int(body.get("amount") or 0),
        currency=str(body.get("currency") or "").upper(), refunded_minor=int(body.get("refunded") or 0),
        invoice_id=str(body["invoice_id"]) if body.get("invoice_id") else None,
        metadata={str(k): str(v) for k, v in (metadata or {}).items()},
    )


class MoyasarProvider:
    name = "moyasar"

    def __init__(self, secret_key: str) -> None:
        if not secret_key.startswith(("sk_test_", "sk_live_")):
            raise ProviderError("The Moyasar secret key must start with sk_test_ or sk_live_ (the publishable pk_ key is not enough).")
        self._key = secret_key

    def _call(self, method: str, path: str, *, json: dict[str, Any] | None = None, what: str) -> dict[str, Any]:
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS, auth=(self._key, "")) as client:
                response = client.request(method, f"{MOYASAR_API}{path}", json=json, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise ProviderError("Moyasar could not be reached; try again in a minute.") from exc
        if response.status_code == 401:
            raise ProviderError("Moyasar refused the secret key (HTTP 401); check the Moyasar key in Platform billing > Settings.")
        if response.status_code >= 400:
            raise ProviderError(f"Moyasar refused {what} (HTTP {response.status_code}).")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"Moyasar answered {what} with something that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"Moyasar answered {what} with something unexpected.")
        return body

    def create_checkout(self, *, invoice: dict[str, Any], success_url: str, cancel_url: str) -> Checkout:
        """A Moyasar invoice: a hosted payment page for the amount due (mada, cards, Apple Pay, STC Pay)."""
        currency = str(invoice["currency"]).upper()
        if currency not in CURRENCIES:
            raise ProviderError(f"Moyasar card payments are set up for {', '.join(CURRENCIES)}; this invoice is in {currency}.")
        body = self._call("POST", "/invoices", json={
            "amount": int(invoice["amount_due_minor"]),
            "currency": currency,
            "description": f"Invoice {invoice['number']}",
            "success_url": success_url,
            "back_url": cancel_url,
            "metadata": {"anum_tenant_id": invoice["tenant_id"], "anum_invoice_id": invoice["id"]},
        }, what="the payment page")
        url = str(body.get("url") or "")
        if not url.startswith("https://"):
            raise ProviderError("Moyasar did not return a payment page address.")
        return Checkout(url=url, session_id=str(body.get("id") or ""))

    def fetch_payment(self, payment_id: str) -> MoyasarPayment:
        if not payment_id or "/" in payment_id or len(payment_id) > 100:
            raise ProviderError("That is not a Moyasar payment id.")
        return _payment(self._call("GET", f"/payments/{payment_id}", what="the payment lookup"))

    def invoice_metadata(self, invoice_id: str) -> dict[str, str]:
        if not invoice_id or "/" in invoice_id or len(invoice_id) > 100:
            return {}
        body = self._call("GET", f"/invoices/{invoice_id}", what="the invoice lookup")
        metadata = body.get("metadata")
        return {str(k): str(v) for k, v in metadata.items()} if isinstance(metadata, dict) else {}

    def refund(self, payment_id: str, amount_minor: int) -> MoyasarRefund:
        """Refund part or all of a paid payment; Moyasar answers with the payment and its refunded total."""
        if not payment_id or "/" in payment_id or len(payment_id) > 100:
            raise ProviderError("That is not a Moyasar payment id.")
        body = self._call("POST", f"/payments/{payment_id}/refund", json={"amount": int(amount_minor)}, what="the refund")
        payment = _payment(body)
        return MoyasarRefund(payment_id=payment.id or payment_id, refunded_minor=payment.refunded_minor, status=payment.status)
