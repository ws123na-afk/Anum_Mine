"""Interactive Brokers (IBKR) Client Portal Web API client (docs/finance-live-trading.md#connector).

How IBKR lets an individual account use its Web API: the owner runs IBKR's **Client Portal
Gateway** (a small Java program) next to ANUM and logs in to it in a browser with their IBKR
user name, password and two-factor approval. The gateway then holds the brokerage session and
answers REST calls on ``https://<gateway>:5000/v1/api``. ANUM never sees, asks for or stores
the IBKR password: it only needs the gateway's address (one of a fixed list), the SHA-256
fingerprint of the gateway's own TLS certificate (the gateway uses a self-signed one, so ANUM
pins it instead of turning verification off) and the account id. The brokerage session times
out after about five idle minutes (ANUM calls ``/tickle``) and the login itself ends at least
daily, after which the owner logs in to the gateway again: ANUM shows "Log in to IBKR again".

Statements and dividends come from IBKR's **Flex Web Service** (a token and a query id the
owner creates in IBKR's Client Portal), on two fixed IBKR hosts with normal certificate checks.

Safety properties of this module (tested in ``tests/test_finance_ibkr_client.py``):

* Fixed hosts only: the gateway on ``GATEWAY_HOSTS`` port 5000, Flex on ``FLEX_HOSTS``. Any
  other host, scheme, port or path prefix is refused before a socket opens.
* Pinned TLS for the gateway (the certificate's SHA-256 must match before a byte is sent),
  CA-verified TLS for Flex. TLS 1.2 or newer.
* No redirects are followed: any 3xx answer is an error.
* Bounded: 512 KB per answer, 8 second timeout, at most 8 requests a second (IBKR allows 10).
* Secrets never in ``repr``, errors or logs. The Flex token goes only in the query string IBKR
  documents, over TLS.
* The client only reads, places a limit DAY order with ``outsideRTH`` false, declines any
  "please confirm" question IBKR asks (it never answers yes for the owner) and cancels.

Endpoints (IBKR Web API v1.0 reference, interactivebrokers.com/campus/ibkr-api-page/cpapi-v1
and interactivebrokers.com/docs/web-api): see ``ENDPOINTS``.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict

GATEWAY_HOSTS = ("ibkr-gateway", "127.0.0.1", "localhost")
GATEWAY_PORT = 5000
GATEWAY_PREFIX = "/v1/api"
FLEX_HOSTS = ("ndcdyn.interactivebrokers.com", "gdcdyn.interactivebrokers.com")
FLEX_PREFIX = "/AccountManagement/FlexWebService/"
MAX_BODY_BYTES = 512_000
TIMEOUT_SECONDS = 8.0
MIN_INTERVAL_SECONDS = 0.125
USER_AGENT = "anum-ibkr/1"

ENDPOINTS = {
    "auth_status": ("POST", "/iserver/auth/status"),
    "tickle": ("POST", "/tickle"),
    "reauthenticate": ("POST", "/iserver/auth/ssodh/init"),
    "iserver_accounts": ("GET", "/iserver/accounts"),
    "portfolio_accounts": ("GET", "/portfolio/accounts"),
    "summary": ("GET", "/portfolio/{account}/summary"),
    "ledger": ("GET", "/portfolio/{account}/ledger"),
    "positions": ("GET", "/portfolio/{account}/positions/{page}"),
    "live_orders": ("GET", "/iserver/account/orders"),
    "trades": ("GET", "/iserver/account/trades"),
    "pnl": ("GET", "/iserver/account/pnl/partitioned"),
    "stocks": ("GET", "/trsrv/stocks"),
    "snapshot": ("GET", "/iserver/marketdata/snapshot"),
    "history": ("GET", "/iserver/marketdata/history"),
    "place_order": ("POST", "/iserver/account/{account}/orders"),
    "reply": ("POST", "/iserver/reply/{reply}"),
    "cancel_order": ("DELETE", "/iserver/account/{account}/order/{order}"),
}

ACCOUNT_RE = re.compile(r"^[A-Z]{1,3}[0-9]{4,12}$")
PIN_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
OPEN_STATUSES = frozenset({"PendingSubmit", "PreSubmitted", "Submitted", "PendingCancel", "ApiPending", "ApiCancelled"})
DONE_STATUSES = frozenset({"Filled", "Cancelled", "Inactive"})


class IbkrError(RuntimeError):
    """A failed IBKR call. ``code`` is stable; ``message`` is safe to show (no secrets, no raw body)."""

    def __init__(self, code: str, message: str, *, sent: bool = False) -> None:
        super().__init__(message)
        self.code, self.message = code, message
        # True when the request may have reached IBKR (timeout or reset after sending): the
        # caller must look the order up by its client order id before doing anything else.
        self.sent = sent


def normalize_pin(value: str) -> str:
    """A SHA-256 fingerprint as 64 lower-case hex digits (``AB:CD:...`` from openssl is accepted)."""
    cleaned = value.strip().lower().replace(":", "").replace(" ", "")
    if cleaned.startswith("sha256fingerprint="):
        cleaned = cleaned.split("=", 1)[1]
    if not PIN_RE.fullmatch(cleaned):
        raise ValueError("The certificate fingerprint is 64 hexadecimal characters (SHA-256).")
    return cleaned


def normalize_account(value: str) -> str:
    cleaned = value.strip().upper()
    if not ACCOUNT_RE.fullmatch(cleaned):
        raise ValueError("An IBKR account id is letters then digits, like U1234567 (paper accounts start with D).")
    return cleaned


def is_paper_account(account_id: str) -> bool:
    """IBKR paper (simulated) account ids start with ``D`` (for example ``DU1234567``)."""
    return account_id.upper().startswith("D")


@dataclass(frozen=True, repr=False)
class IbkrCredentials:
    """What ANUM keeps for one connection (encrypted at rest). No IBKR password: none is needed."""

    gateway_host: str
    tls_pin: str
    account_id: str
    flex_token: str = ""
    flex_query_id: str = ""

    def __repr__(self) -> str:
        return "IbkrCredentials(**REDACTED**)"

    def __post_init__(self) -> None:
        if self.gateway_host not in GATEWAY_HOSTS:
            raise ValueError("The gateway address must be one of the allowed ones.")
        normalize_pin(self.tls_pin)
        normalize_account(self.account_id)
        if self.flex_token and not re.fullmatch(r"[0-9A-Za-z]{8,64}", self.flex_token):
            raise ValueError("A Flex token is 8 to 64 letters and digits.")
        if self.flex_query_id and not re.fullmatch(r"[0-9]{1,12}", self.flex_query_id):
            raise ValueError("A Flex query id is digits only.")
        if bool(self.flex_token) != bool(self.flex_query_id):
            raise ValueError("Enter both the Flex token and the Flex query id, or neither.")

    def as_dict(self) -> dict[str, str]:
        return {"gateway_host": self.gateway_host, "tls_pin": self.tls_pin, "account_id": self.account_id,
                "flex_token": self.flex_token, "flex_query_id": self.flex_query_id}


@dataclass(frozen=True)
class HttpRequest:
    method: str
    host: str
    port: int
    path: str  # path plus query, already encoded
    body: bytes | None
    pin: str | None  # SHA-256 of the server certificate (gateway); None = CA verification (Flex)


Transport = Callable[[HttpRequest], tuple[int, bytes]]


def _https_transport(request: HttpRequest) -> tuple[int, bytes]:
    """Real HTTPS with TLS 1.2+, the pin checked before the request is written, no redirects."""
    if request.pin is None:
        context = ssl.create_default_context()
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # The gateway's certificate is self-signed: it is checked by its pinned SHA-256 below,
        # before any request byte is written, which is stricter than a CA check.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # nosec B501 - pinned certificate, compared below
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(request.host, request.port, timeout=TIMEOUT_SECONDS, context=context)
    try:
        try:
            connection.connect()
        except (OSError, ssl.SSLError) as exc:
            raise IbkrError("unreachable", "ANUM could not reach the IBKR gateway. Check that it is running.") from exc
        if request.pin is not None:
            sock = connection.sock
            der = sock.getpeercert(binary_form=True) if isinstance(sock, ssl.SSLSocket) else None
            if not der or not hmac.compare_digest(hashlib.sha256(der).hexdigest(), request.pin):
                raise IbkrError("pin_mismatch", "The gateway's certificate does not match the fingerprint you entered. Nothing was sent.")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if request.body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(request.method, request.path, body=request.body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_BODY_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            # The request may have reached IBKR: the caller looks the order up before anything else.
            raise IbkrError("no_answer", "IBKR did not answer in time.", sent=True) from exc
        return response.status, data
    finally:
        connection.close()


class _Pacer:
    """At most one request per ``MIN_INTERVAL_SECONDS`` per process (IBKR allows 10 a second)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self._last + MIN_INTERVAL_SECONDS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


PACER = _Pacer()


def _dec(value: Any) -> Decimal | None:
    """A number from IBKR (number or text, maybe with a leading C/H marker or commas), else None."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if text[:1] in {"C", "H"}:
        text = text[1:]
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


# -- parsed answers ---------------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class AuthStatus(_Frozen):
    authenticated: bool
    connected: bool
    competing: bool
    message: str = ""


class AccountInfo(_Frozen):
    account_id: str
    paper: bool
    trading_type: str | None = None  # IBKR's account trading type (for example STKCASH); None when not given
    currency: str | None = None


class Ledger(_Frozen):
    currency: str
    cash: Decimal | None = None
    settled_cash: Decimal | None = None
    net_liquidation: Decimal | None = None
    realized_pnl: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    dividends: Decimal | None = None


class Position(_Frozen):
    conid: int
    symbol: str
    quantity: Decimal
    market_price: Decimal | None = None
    market_value: Decimal | None = None
    average_cost: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    realized_pnl: Decimal | None = None
    currency: str = "USD"
    asset_class: str = ""


class LiveOrder(_Frozen):
    order_id: str
    conid: int | None = None
    symbol: str = ""
    side: str = ""
    status: str
    quantity: Decimal | None = None
    filled_quantity: Decimal = Decimal(0)
    remaining_quantity: Decimal | None = None
    limit_price: Decimal | None = None
    average_price: Decimal | None = None
    order_ref: str | None = None  # the client order id (cOID) ANUM sent
    order_type: str = ""
    account: str = ""

    @property
    def is_open(self) -> bool:
        return self.status not in DONE_STATUSES


class Execution(_Frozen):
    execution_id: str
    conid: int | None = None
    symbol: str = ""
    side: str = ""
    quantity: Decimal
    price: Decimal
    commission: Decimal | None = None
    order_ref: str | None = None
    order_id: str | None = None
    trade_time: str = ""


class DailyPnl(_Frozen):
    daily: Decimal | None = None  # IBKR "dpl": today's profit or loss
    unrealized: Decimal | None = None  # "upl"
    net_liquidation: Decimal | None = None  # "nl"
    market_value: Decimal | None = None  # "mv"


class StockMatch(_Frozen):
    symbol: str
    name: str
    conid: int
    exchange: str
    is_us: bool


class Quote(_Frozen):
    conid: int
    last: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    updated_ms: int | None
    closing_price: bool  # the last price is the previous close ("C" marker), not a live trade
    # IBKR field 6509, first letter: R real time, D delayed (free, about 15 minutes), Z frozen,
    # Y frozen delayed, N no market data permission. Empty when IBKR did not say.
    availability: str = ""

    @property
    def delayed(self) -> bool:
        return self.availability[:1] in {"D", "Y"}

    @property
    def frozen(self) -> bool:
        return self.availability[:1] in {"Z", "Y", "N"}


class PlacedOrder(_Frozen):
    """IBKR's answer to a new order: an order id, or a question ANUM declined, or a refusal."""

    order_id: str | None
    status: str
    message: str = ""
    declined_question: bool = False


class FlexDividend(_Frozen):
    date: str
    symbol: str
    currency: str
    amount: Decimal
    kind: str  # "dividend" or "withholding_tax"


class FlexStatement(_Frozen):
    ready: bool
    message: str = ""
    account_id: str | None = None
    from_date: str | None = None
    to_date: str | None = None
    dividends: list[FlexDividend] = []
    trades: int = 0


# -- the client -------------------------------------------------------------------------------------


class IbkrClient:
    """One connection's calls. Inject ``transport`` in tests (a fake gateway); never real IBKR there."""

    def __init__(self, credentials: IbkrCredentials, transport: Transport | None = None, pace: bool = True) -> None:
        self._credentials = credentials
        self._transport = transport or _https_transport
        self._pace = pace

    def __repr__(self) -> str:
        return "IbkrClient(**REDACTED**)"

    @property
    def account_id(self) -> str:
        return self._credentials.account_id

    # transport ----------------------------------------------------------------------------------

    def _send(self, request: HttpRequest) -> tuple[int, bytes]:
        if self._pace:
            PACER.wait()
        try:
            status, data = self._transport(request)
        except IbkrError:
            raise
        except TimeoutError as exc:
            raise IbkrError("no_answer", "IBKR did not answer in time.", sent=request.method != "GET") from exc
        except OSError as exc:
            raise IbkrError("unreachable", "ANUM could not reach IBKR.") from exc
        if len(data) > MAX_BODY_BYTES:
            raise IbkrError("bad_response", "IBKR's answer was larger than ANUM accepts.")
        if 300 <= status < 400:
            raise IbkrError("redirect_refused", "IBKR answered with a redirect. ANUM does not follow redirects.")
        if status == 401:
            raise IbkrError("session_expired", "Your IBKR session ended. Log in to IBKR again.")
        if status == 429:
            raise IbkrError("rate_limited", "IBKR asked ANUM to slow down. Wait a few minutes and try again.")
        return status, data

    def _call(self, name: str, *, params: Mapping[str, str] | None = None, body: Any = None, **segments: str) -> Any:
        method, template = ENDPOINTS[name]
        for key, value in segments.items():
            if not SAFE_SEGMENT.fullmatch(value):
                raise IbkrError("bad_request", "A request value had characters ANUM does not send.")
            template = template.replace("{" + key + "}", quote(value, safe=""))
        path = GATEWAY_PREFIX + template + (("?" + urlencode(params)) if params else "")
        payload = None if body is None else json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
        status, data = self._send(HttpRequest(method, self._credentials.gateway_host, GATEWAY_PORT, path, payload, self._credentials.tls_pin))
        try:
            parsed = json.loads(data.decode("utf-8")) if data.strip() else None
        except (ValueError, UnicodeError) as exc:
            raise IbkrError("bad_response", "IBKR's answer could not be read.") from exc
        if status >= 400:
            raise IbkrError("rejected", _error_text(parsed) or f"IBKR refused the request (HTTP {status}).")
        return parsed

    # session ------------------------------------------------------------------------------------

    def auth_status(self) -> AuthStatus:
        raw = self._call("auth_status")
        if not isinstance(raw, dict):
            raise IbkrError("bad_response", "IBKR's session answer could not be read.")
        return AuthStatus(authenticated=bool(raw.get("authenticated")), connected=bool(raw.get("connected")),
                          competing=bool(raw.get("competing")), message=str(raw.get("message") or "")[:200])

    def tickle(self) -> None:
        self._call("tickle")

    def reauthenticate(self) -> None:
        """Start a new brokerage session on a gateway that is still logged in (no password involved)."""
        self._call("reauthenticate", body={"publish": True, "compete": True})

    def require_session(self) -> AuthStatus:
        status = self.auth_status()
        if not status.authenticated:
            if status.competing:
                raise IbkrError("session_competing", "Another IBKR session is using this login. Log in to IBKR again in the gateway.")
            raise IbkrError("session_expired", "Your IBKR session ended. Log in to IBKR again.")
        return status

    # account ------------------------------------------------------------------------------------

    def accounts(self) -> list[AccountInfo]:
        """The accounts this login can trade. ``/iserver/accounts`` must be read before orders."""
        brokerage = self._call("iserver_accounts")
        ids = [str(a) for a in (brokerage.get("accounts") if isinstance(brokerage, dict) else []) or []][:50]
        portfolio = self._call("portfolio_accounts")
        details: dict[str, dict[str, Any]] = {}
        for item in portfolio if isinstance(portfolio, list) else []:
            if isinstance(item, dict) and item.get("accountId"):
                details[str(item["accountId"])] = item
        result = []
        for account in ids:
            info = details.get(account, {})
            trading = info.get("tradingType")
            result.append(AccountInfo(account_id=account, paper=is_paper_account(account) or str(info.get("type", "")).upper() == "DEMO",
                                      trading_type=str(trading)[:20] if trading else None,
                                      currency=str(info.get("currency"))[:3] if info.get("currency") else None))
        return result

    def ledger(self) -> dict[str, Ledger]:
        raw = self._call("ledger", account=self.account_id)
        result: dict[str, Ledger] = {}
        for key, item in (raw.items() if isinstance(raw, dict) else []):
            if not isinstance(item, dict) or len(result) >= 40:
                continue
            result[str(key)[:8]] = Ledger(
                currency=str(item.get("currency") or key)[:8], cash=_dec(item.get("cashbalance")), settled_cash=_dec(item.get("settledcash")),
                net_liquidation=_dec(item.get("netliquidationvalue")), realized_pnl=_dec(item.get("realizedpnl")),
                unrealized_pnl=_dec(item.get("unrealizedpnl")), dividends=_dec(item.get("dividends")),
            )
        return result

    def positions(self) -> list[Position]:
        result: list[Position] = []
        for page in range(10):
            raw = self._call("positions", account=self.account_id, page=str(page))
            rows = raw if isinstance(raw, list) else []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                conid, quantity = item.get("conid"), _dec(item.get("position"))
                if conid is None or quantity is None:
                    raise IbkrError("bad_response", "IBKR sent a position without a contract or quantity.")
                result.append(Position(
                    conid=int(conid), symbol=str(item.get("ticker") or item.get("contractDesc") or "")[:40], quantity=quantity,
                    market_price=_dec(item.get("mktPrice")), market_value=_dec(item.get("mktValue")), average_cost=_dec(item.get("avgCost")),
                    unrealized_pnl=_dec(item.get("unrealizedPnl")), realized_pnl=_dec(item.get("realizedPnl")),
                    currency=str(item.get("currency") or "USD")[:3], asset_class=str(item.get("assetClass") or "")[:10],
                ))
            if len(rows) < 100:
                break
        return result

    def live_orders(self) -> list[LiveOrder]:
        raw = self._call("live_orders", params={"force": "true"})
        rows = raw.get("orders") if isinstance(raw, dict) else []
        result = []
        for item in rows or []:
            if not isinstance(item, dict) or item.get("orderId") is None:
                continue
            if item.get("acct") and str(item.get("acct")) != self.account_id:
                continue
            result.append(LiveOrder(
                order_id=str(item["orderId"])[:40], conid=int(item["conid"]) if item.get("conid") is not None else None,
                symbol=str(item.get("ticker") or "")[:20], side=str(item.get("side") or "").upper()[:4],
                status=str(item.get("status") or "")[:20], quantity=_dec(item.get("totalSize")),
                filled_quantity=_dec(item.get("filledQuantity")) or Decimal(0), remaining_quantity=_dec(item.get("remainingQuantity")),
                limit_price=_dec(item.get("price")), average_price=_dec(item.get("avgPrice")),
                order_ref=str(item["order_ref"])[:64] if item.get("order_ref") else None,
                order_type=str(item.get("origOrderType") or item.get("orderType") or "")[:20], account=str(item.get("acct") or "")[:20],
            ))
        return result

    def trades(self) -> list[Execution]:
        raw = self._call("trades", params={"days": "7"})
        result = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("account") and str(item.get("account")) != self.account_id:
                continue
            size, price = _dec(item.get("size")), _dec(item.get("price"))
            if item.get("execution_id") is None or size is None or price is None:
                raise IbkrError("bad_response", "IBKR sent an execution without an id, size or price.")
            side = str(item.get("side") or "").upper()
            result.append(Execution(
                execution_id=str(item["execution_id"])[:60], conid=int(item["conid"]) if item.get("conid") is not None else None,
                symbol=str(item.get("symbol") or "")[:20], side="BUY" if side in {"B", "BUY", "BOT"} else "SELL" if side in {"S", "SELL", "SLD"} else side[:4],
                quantity=size, price=price, commission=_dec(item.get("commission")),
                order_ref=str(item["order_ref"])[:64] if item.get("order_ref") else None,
                order_id=str(item["order_id"])[:40] if item.get("order_id") is not None else None,
                trade_time=str(item.get("trade_time") or "")[:30],
            ))
        return result

    def pnl(self) -> DailyPnl:
        raw = self._call("pnl")
        rows = raw.get("upnl") if isinstance(raw, dict) else None
        for key, item in (rows.items() if isinstance(rows, dict) else []):
            if str(key).split(".", 1)[0] == self.account_id and isinstance(item, dict):
                return DailyPnl(daily=_dec(item.get("dpl")), unrealized=_dec(item.get("upl")), net_liquidation=_dec(item.get("nl")),
                                market_value=_dec(item.get("mv")))
        return DailyPnl()

    # contracts and quotes ------------------------------------------------------------------------

    def find_stock(self, symbol: str) -> list[StockMatch]:
        raw = self._call("stocks", params={"symbols": symbol})
        result = []
        for item in (raw.get(symbol) if isinstance(raw, dict) else None) or []:
            if not isinstance(item, dict) or str(item.get("assetClass") or "") != "STK":
                continue
            for contract in item.get("contracts") or []:
                if isinstance(contract, dict) and contract.get("conid") is not None:
                    result.append(StockMatch(symbol=symbol, name=str(item.get("name") or "")[:120], conid=int(contract["conid"]),
                                             exchange=str(contract.get("exchange") or "")[:20], is_us=bool(contract.get("isUS"))))
        return result

    def quote(self, conid: int) -> Quote:
        raw = self._call("snapshot", params={"conids": str(conid), "fields": "31,84,86,6509"})
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and str(item.get("conid")) == str(conid):
                last_text = str(item.get("31") or "")
                updated = item.get("_updated")
                return Quote(conid=conid, last=_dec(last_text), bid=_dec(item.get("84")), ask=_dec(item.get("86")),
                             updated_ms=int(updated) if isinstance(updated, (int, float)) else None, closing_price=last_text.startswith("C"),
                             availability=str(item.get("6509") or "")[:4])
        return Quote(conid=conid, last=None, bid=None, ask=None, updated_ms=None, closing_price=False)

    def history(self, conid: int, *, period: str = "10y", bar: str = "1w") -> Any:
        """Historical bars (research only, docs/finance-research.md#sources): IBKR's raw answer, parsed by
        ``research_data.parse_ibkr_history``. Split-adjusted by IBKR, not dividend-adjusted."""
        if not re.fullmatch(r"[0-9]{1,3}(d|w|m|y)", period) or not re.fullmatch(r"[0-9]{1,2}(d|w|m)", bar):
            raise IbkrError("bad_request", "A history request ANUM does not send.")
        return self._call("history", params={"conid": str(conid), "period": period, "bar": bar, "outsideRth": "false"})

    # orders -------------------------------------------------------------------------------------

    def place_limit_order(self, *, client_order_id: str, conid: int, side: str, quantity: Decimal, limit_price: Decimal) -> PlacedOrder:
        """One limit DAY order, regular hours only. A question from IBKR is declined, never confirmed."""
        if side not in {"BUY", "SELL"} or quantity <= 0 or limit_price <= 0 or not SAFE_SEGMENT.fullmatch(client_order_id):
            raise IbkrError("bad_request", "ANUM only sends a buy or sell limit order with a positive size and price.")
        order = {"acctId": self.account_id, "conid": conid, "cOID": client_order_id, "orderType": "LMT", "side": side,
                 "price": float(limit_price), "quantity": float(quantity), "tif": "DAY", "outsideRTH": False}
        try:
            raw = self._call("place_order", account=self.account_id, body={"orders": [order]})
        except IbkrError as exc:
            if exc.code in {"rejected", "bad_request"}:
                return PlacedOrder(order_id=None, status="rejected", message=exc.message)
            if exc.code == "no_answer":
                exc.sent = True
            raise
        return self._placed(raw)

    def _placed(self, raw: Any) -> PlacedOrder:
        if isinstance(raw, dict) and raw.get("error"):
            return PlacedOrder(order_id=None, status="rejected", message=str(raw["error"])[:300])
        first = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else None
        if first is None:
            raise IbkrError("bad_response", "IBKR's answer to the order could not be read.", sent=True)
        if first.get("order_id") is not None:
            status = str(first.get("order_status") or "Submitted")[:20]
            return PlacedOrder(order_id=str(first["order_id"])[:40], status=status, message=str(first.get("text") or "")[:300])
        if first.get("id") and first.get("message"):
            # IBKR asks a question before it accepts the order. ANUM never says yes on the owner's
            # behalf: it declines, and the order is not placed. The owner sees IBKR's words.
            words = first.get("message")
            text = " ".join(str(m) for m in words) if isinstance(words, list) else str(words)
            reply = str(first["id"])
            if SAFE_SEGMENT.fullmatch(reply):
                try:
                    self._call("reply", reply=reply, body={"confirmed": False})
                except IbkrError:
                    pass  # not confirming is enough: the order is never placed without a yes
            return PlacedOrder(order_id=None, status="rejected", message=("IBKR asked: " + text)[:300], declined_question=True)
        if first.get("error"):
            return PlacedOrder(order_id=None, status="rejected", message=str(first["error"])[:300])
        raise IbkrError("bad_response", "IBKR's answer to the order could not be read.", sent=True)

    def cancel_order(self, order_id: str) -> None:
        self._call("cancel_order", account=self.account_id, order=order_id)

    def find_order(self, client_order_id: str) -> tuple[LiveOrder | None, list[Execution]]:
        """The order IBKR holds for this client order id (if any) and its executions."""
        order = next((o for o in self.live_orders() if o.order_ref == client_order_id), None)
        fills = [e for e in self.trades() if e.order_ref == client_order_id or (order is not None and e.order_id == order.order_id)]
        return order, fills

    # statements (Flex Web Service) -------------------------------------------------------------

    def flex_statement(self, reference: str | None = None) -> FlexStatement:
        """Request (or collect) the owner's Flex statement. Never blocks to wait for IBKR."""
        token, query_id = self._credentials.flex_token, self._credentials.flex_query_id
        if not token or not query_id:
            raise IbkrError("not_configured", "Statements are not connected. Add a Flex token and query id.")
        if reference is None:
            root = self._flex("https://" + FLEX_HOSTS[0] + FLEX_PREFIX + "SendRequest", {"t": token, "q": query_id, "v": "3"})
            if _xml_text(root, "Status") != "Success":
                return FlexStatement(ready=False, message=(_xml_text(root, "ErrorMessage") or "IBKR could not prepare the statement.")[:200])
            reference = _xml_text(root, "ReferenceCode") or ""
            url = _xml_text(root, "Url") or ""
            if not re.fullmatch(r"[0-9]{1,20}", reference):
                raise IbkrError("bad_response", "IBKR's statement reference could not be read.")
            target = url
        else:
            target = "https://" + FLEX_HOSTS[0] + FLEX_PREFIX + "GetStatement"
        root = self._flex(target, {"t": token, "q": reference, "v": "3"})
        if root.tag == "FlexStatementResponse":
            return FlexStatement(ready=False, message=(_xml_text(root, "ErrorMessage") or "The statement is not ready yet. Try again in a minute.")[:200])
        return _parse_flex(root)

    def _flex(self, url: str, params: Mapping[str, str]) -> Any:
        from defusedxml import ElementTree
        from defusedxml.common import DefusedXmlException

        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in FLEX_HOSTS or parts.port not in (None, 443) or not parts.path.startswith(FLEX_PREFIX) \
                or parts.query or parts.fragment or parts.username or parts.password:
            raise IbkrError("host_refused", "IBKR pointed the statement to an address ANUM does not allow.")
        status, data = self._send(HttpRequest("GET", parts.hostname, 443, parts.path + "?" + urlencode(params), None, None))
        if status >= 400:
            raise IbkrError("rejected", f"IBKR refused the statement request (HTTP {status}).")
        try:
            return ElementTree.fromstring(data)
        except (ElementTree.ParseError, DefusedXmlException, ValueError) as exc:
            raise IbkrError("bad_response", "IBKR's statement could not be read.") from exc


def _xml_text(root: Any, tag: str) -> str | None:
    node = root.find(tag)
    return node.text.strip() if node is not None and node.text else None


def _parse_flex(root: Any) -> FlexStatement:
    statement = root.find("./FlexStatements/FlexStatement")
    if statement is None:
        raise IbkrError("bad_response", "IBKR's statement had no account section.")
    dividends: list[FlexDividend] = []
    for item in statement.findall("./CashTransactions/CashTransaction"):
        kind = str(item.get("type") or "")
        if kind not in {"Dividends", "Payment In Lieu Of Dividends", "Withholding Tax"}:
            continue
        amount = _dec(item.get("amount"))
        if amount is None:
            continue
        dividends.append(FlexDividend(
            date=str(item.get("dateTime") or item.get("settleDate") or "")[:19], symbol=str(item.get("symbol") or "")[:20],
            currency=str(item.get("currency") or "")[:3], amount=amount, kind="withholding_tax" if kind == "Withholding Tax" else "dividend",
        ))
        if len(dividends) >= 500:
            break
    trades = len(statement.findall("./Trades/Trade"))
    return FlexStatement(ready=True, account_id=statement.get("accountId"), from_date=statement.get("fromDate"), to_date=statement.get("toDate"),
                         dividends=dividends, trades=trades)


def _error_text(parsed: Any) -> str | None:
    if isinstance(parsed, dict):
        for key in ("error", "message", "msg"):
            if parsed.get(key):
                return str(parsed[key])[:300]
    return None
