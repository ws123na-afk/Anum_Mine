"""Read-only finance connectors, Stage 5 (docs/finance.md#connectors).

A connector brings outside data into the person's own ledger: exchange rates, and later
bank, card, broker and crypto account data. **Read only, everywhere:** every scope ends in
``:read`` (the database refuses anything else), no adapter has a write, order, payment or
transfer call, and nothing here can move money.

* ``PROVIDERS`` is the registry: what each source is, its read-only scopes, whether it needs
  the person's credentials, and, for sources that need a provider account ANUM does not
  have, what the owner must set up first. Those are listed, never faked: they have no
  adapter and cannot be connected until one is built against the provider's documented API.
* ``Connection`` is one person's connection to one provider: status, health, last sync,
  last error (a safe message), and credentials **encrypted** with ``ANUM_SECRETS_KEY``
  (``secret_box``, Fernet). Credentials are write only: the API returns a hint at most, and
  they never reach a log, an audit record, a prompt, an agent tool, the browser or an
  export.
* Imported data is untrusted (internet content): rates are checked by ``fx.py``; a
  transaction's description is cleaned like a statement's and can never pick an account,
  a tool or a rule. Transactions land in the Stage 2 ledger with the same dedupe hash, so
  a repeated sync adds nothing twice.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..secret_box import secret_cipher
from . import fx
from .models import _Owned

MAX_RESPONSE_BYTES = 256 * 1024
# Tests replace this with an httpx.MockTransport; production uses the network.
finance_transport: httpx.AsyncBaseTransport | None = None


class ConnectorKind(StrEnum):
    BANK = "bank"
    CARD = "card"
    BROKER = "broker"
    CRYPTO = "crypto"
    MARKET_DATA = "market_data"
    FX = "fx"


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class Connection(_Owned):
    id: str
    provider: str
    kind: ConnectorKind
    label: str
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    scopes: list[str]
    credentials_encrypted: str | None = None
    credential_hint: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    next_sync_at: datetime
    last_sync_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ConnectorError(Exception):
    """A connector call failed. ``message`` is safe to show (never a credential)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    kind: ConnectorKind
    name: str
    summary: str
    scopes: tuple[str, ...]
    available: bool
    needs_credentials: bool = False
    credential_fields: tuple[str, ...] = ()
    sync_hours: int = 12
    source_url: str | None = None
    terms: str | None = None
    # For a provider that is not built: what the owner must set up before it can be.
    owner_action: str | None = None


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            id="ecb_fx", kind=ConnectorKind.FX, name="European Central Bank reference rates",
            summary="Daily euro reference rates for about 30 currencies, plus the dirham through the CBUAE peg of 3.6725 per US dollar. No account or key needed.",
            scopes=("fx_rates:read",), available=True, sync_hours=12, source_url=fx.ECB_URL,
            terms="Published by the ECB for information; reuse is free with the source named. ANUM names the source and date on every rate.",
        ),
        ProviderSpec(
            id="uae_open_finance_bank", kind=ConnectorKind.BANK, name="Bank account (UAE Open Finance)",
            summary="Balances and transactions of a UAE bank account, read only.",
            scopes=("accounts:read", "balances:read", "transactions:read"), available=False, needs_credentials=True,
            owner_action="Needs a contract with a provider licensed for UAE Open Finance (Al Tareq), its sandbox and production credentials, and a review of its API before an adapter is built. Until then, import statements as CSV.",
        ),
        ProviderSpec(
            id="uae_open_finance_card", kind=ConnectorKind.CARD, name="Credit card (UAE Open Finance)",
            summary="Card balance and transactions, read only.",
            scopes=("accounts:read", "balances:read", "transactions:read"), available=False, needs_credentials=True,
            owner_action="Same as the bank account: a licensed UAE Open Finance provider account first. Until then, import the card statement as CSV.",
        ),
        ProviderSpec(
            id="broker_read_only", kind=ConnectorKind.BROKER, name="Broker account (read only)",
            summary="Positions and cash at a broker, read only. Never orders.",
            scopes=("positions:read", "balances:read"), available=False, needs_credentials=True,
            owner_action="Choose the broker, open its API access with a key that has no trading or withdrawal permission, and share the API's documentation so the adapter can be built and checked against it.",
        ),
        ProviderSpec(
            id="crypto_read_only", kind=ConnectorKind.CRYPTO, name="Crypto exchange (read only)",
            summary="Balances at a crypto exchange, read only. Never trades or withdraws.",
            scopes=("balances:read",), available=False, needs_credentials=True,
            owner_action="Choose the exchange and create a read-only API key (trading and withdrawals off). ANUM will refuse a key the exchange reports as able to trade.",
        ),
        ProviderSpec(
            id="market_prices", kind=ConnectorKind.MARKET_DATA, name="Market prices",
            summary="Prices for your holdings, to value them. Read only.",
            scopes=("prices:read",), available=False, needs_credentials=True,
            owner_action="Choose a market data provider whose licence allows storing prices for personal use (for example for UAE and US listings) and create an API key.",
        ),
    )
}


@dataclass
class FetchedRate:
    base: str
    quote: str
    rate: Decimal
    as_of: date
    source: str
    source_url: str | None


@dataclass
class RemoteTransaction:
    """One transaction as a provider reports it (untrusted)."""

    external_id: str
    account_ref: str
    posted_on: date
    amount: Decimal
    currency: str
    description: str


@dataclass
class FetchResult:
    rates: list[FetchedRate] = field(default_factory=list)
    transactions: list[RemoteTransaction] = field(default_factory=list)


Adapter = Callable[[Connection, dict[str, str], date], Awaitable[FetchResult]]


async def http_get(url: str, *, timeout: float) -> str:
    """One GET to a fixed public source: no redirects, a capped body, a safe error message."""
    async with httpx.AsyncClient(transport=finance_transport, timeout=timeout, follow_redirects=False) as client:
        try:
            response = await client.get(url, headers={"Accept": "application/xml, text/xml"})
        except httpx.HTTPError:
            raise ConnectorError("The source could not be reached. ANUM will try again later.") from None
    if response.status_code != 200:
        raise ConnectorError(f"The source answered {response.status_code}. ANUM will try again later.")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ConnectorError("The source's answer was too large to be a rates file.")
    return response.text


async def ecb_adapter(connection: Connection, credentials: dict[str, str], today: date) -> FetchResult:
    from .config import finance_config

    text = await http_get(fx.ECB_URL, timeout=finance_config.request_timeout_seconds)
    try:
        published = fx.parse_ecb(text, today)
    except fx.FxError as exc:
        raise ConnectorError(str(exc)) from None
    return FetchResult(rates=[
        FetchedRate(base=published.base, quote=quote, rate=rate, as_of=published.as_of, source=source, source_url=fx.ECB_URL)
        for quote, (rate, source) in sorted(fx.with_aed_peg(published).items())
    ])


# Only providers with a real adapter can be connected. Tests may add their own here.
ADAPTERS: dict[str, Adapter] = {"ecb_fx": ecb_adapter}


def is_read_only(scopes: list[str] | tuple[str, ...]) -> bool:
    return bool(scopes) and all(isinstance(s, str) and s.endswith(":read") and ":" in s for s in scopes)


def encrypt_credentials(values: dict[str, str]) -> str:
    return secret_cipher().encrypt(json.dumps(values, sort_keys=True))


def decrypt_credentials(ciphertext: str | None) -> dict[str, str]:
    if not ciphertext:
        return {}
    value = json.loads(secret_cipher().decrypt(ciphertext))
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def credential_hint(values: dict[str, str]) -> str | None:
    if not values:
        return None
    longest = max(values.values(), key=len)
    return f"ends {longest[-4:]}" if len(longest) >= 12 else "set"
