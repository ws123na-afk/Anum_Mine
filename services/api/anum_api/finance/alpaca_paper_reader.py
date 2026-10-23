"""Allowlisted, paper-only Alpaca account reader for future tenant-owned credentials.

No funds, orders, withdrawals or live-domain access. The integration remains
unconfigured until an approved vault injects credentials scoped to an owner.
Never pass secrets in HTTP request bodies or serialize them into API answers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator

PAPER_ORIGIN = "https://paper-api.alpaca.markets"
MAX_BODY_BYTES = 256_000
TIMEOUT_SECONDS = 8


class BrokerPaperReadError(RuntimeError):
    """Broker paper account could not be verified."""


@dataclass(frozen=True, repr=False)
class AlpacaPaperCredentials:
    key_id: str
    secret: str

    def __repr__(self) -> str:
        return "AlpacaPaperCredentials(**REDACTED**)"

    def __post_init__(self) -> None:
        if not self.key_id.strip() or not self.secret.strip():
            raise BrokerPaperReadError("Paper API credentials must be complete")
        if len(self.key_id) > 256 or len(self.secret) > 512:
            raise BrokerPaperReadError("Paper API credentials are invalid")


class PaperAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str = Field(min_length=1, max_length=120)
    currency: str
    status: str
    buying_power: Decimal
    cash: Decimal
    equity: Decimal
    paper_only: bool = True
    source_verified_live: bool = False
    funding_supported: bool = False


class PaperPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: str
    asset_class: str
    quantity: Decimal
    market_value: Decimal | None = None
    currency: str = "USD"
    paper_only: bool = True


class AlpacaPaperReader:
    """Inject transport for testing; fixed-origin GET only, no arbitrary URLs.

    This reader is intentionally NOT exposed directly as an HTTP endpoint: a
    production route needs an authenticated tenant credential vault, consent
    records and per-tenant isolation before it can be used.
    """

    def __init__(self, credentials: AlpacaPaperCredentials,
                 transport: Callable[[Request, int], bytes] | None = None):
        self._credentials = credentials
        self._transport = transport or self._urlopen

    @staticmethod
    def _urlopen(request: Request, limit: int) -> bytes:
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                data = response.read(limit + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise BrokerPaperReadError("Paper brokerage connection unavailable") from exc
        return data

    def _read(self, path: str) -> object:
        if path not in ("/v2/account", "/v2/positions"):
            raise BrokerPaperReadError("Unapproved brokerage endpoint")
        request = Request(
            PAPER_ORIGIN + path,
            headers={
                "APCA-API-KEY-ID": self._credentials.key_id,
                "APCA-API-SECRET-KEY": self._credentials.secret,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            data = self._transport(request, MAX_BODY_BYTES)
            if len(data) > MAX_BODY_BYTES:
                raise BrokerPaperReadError("Broker response exceeds safe size")
            return json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise BrokerPaperReadError("Paper broker returned invalid JSON") from exc

    def account(self) -> PaperAccount:
        raw = self._read("/v2/account")
        if not isinstance(raw, dict):
            raise BrokerPaperReadError("Paper account payload must be an object")
        try:
            return PaperAccount(
                account_id=str(raw["id"]),
                currency=str(raw.get("currency") or "USD"),
                status=str(raw["status"]),
                buying_power=Decimal(str(raw["buying_power"])),
                cash=Decimal(str(raw["cash"])),
                equity=Decimal(str(raw["equity"])),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BrokerPaperReadError("Paper account response missing required fields") from exc

    def positions(self) -> list[PaperPosition]:
        raw = self._read("/v2/positions")
        if not isinstance(raw, list) or len(raw) > 2000:
            raise BrokerPaperReadError("Invalid paper positions response")
        positions = []
        try:
            for item in raw:
                if not isinstance(item, dict):
                    raise BrokerPaperReadError("Invalid paper position row")
                market_value = item.get("market_value")
                positions.append(PaperPosition(
                    symbol=str(item["symbol"]),
                    asset_class=str(item["asset_class"]),
                    quantity=Decimal(str(item["qty"])),
                    market_value=Decimal(str(market_value)) if market_value is not None else None,
                ))
        except (KeyError, ValueError, TypeError) as exc:
            raise BrokerPaperReadError("Invalid paper broker position values") from exc
        return positions
