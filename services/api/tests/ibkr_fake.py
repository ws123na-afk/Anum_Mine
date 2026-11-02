"""A fake IBKR Client Portal Gateway for contract tests. Never calls real IBKR.

It answers the documented Web API v1 paths ANUM uses with the shapes IBKR sends, keeps orders
and executions in memory and can be told to misbehave (expired session, a question before an
order, a refusal, a timeout after sending, a cancel that does not stick, a redirect).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from anum_api.finance.ibkr_client import HttpRequest, IbkrError

PIN = "ab" * 32
SPY = 756733


@dataclass
class FakeIbkr:
    account: str = "DU1234567"
    pin: str = PIN
    authenticated: bool = True
    competing: bool = False
    trading_type: str | None = "STKCASH"
    cash: Decimal = Decimal("100")
    daily_pnl: Decimal = Decimal("0")
    last: Decimal = Decimal("9.50")
    quote_age_seconds: float = 1.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    place_mode: str = "ok"  # ok | fill | partial | question | error | timeout | timeout_placed
    cancel_sticks: bool = True
    redirect: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)
    placed: list[dict[str, Any]] = field(default_factory=list)
    flex_ready: bool = True
    next_id: int = 1000
    now_ts: float = 0.0
    history: list[dict[str, Any]] | None = None  # bars for /iserver/marketdata/history (research); None answers an error
    availability: str = "DPB"  # free delayed data, as a USD 50 account without a subscription gets  # the test's clock; 0 = real time

    def __call__(self, request: HttpRequest) -> tuple[int, bytes]:
        parts = urlsplit(request.path)
        path, query = parts.path, parse_qs(parts.query)
        self.calls.append((request.method, path))
        if request.host.endswith("interactivebrokers.com"):
            assert request.pin is None
            return self._flex(path, query)
        assert request.port == 5000 and request.pin == self.pin, "pinned TLS to the gateway only"
        if self.redirect:
            return 302, b""
        body = json.loads(request.body) if request.body else None
        prefix = "/v1/api"
        assert path.startswith(prefix)
        path = path[len(prefix):]
        if path == "/iserver/auth/status":
            return self._json({"authenticated": self.authenticated, "connected": True, "competing": self.competing, "message": ""})
        if path in {"/tickle", "/iserver/auth/ssodh/init"}:
            return self._json({"session": "x"})
        if not self.authenticated:
            return 401, b""
        if path == "/iserver/accounts":
            return self._json({"accounts": [self.account], "selectedAccount": self.account})
        if path == "/portfolio/accounts":
            return self._json([{"accountId": self.account, "currency": "USD", "type": "DEMO" if self.account.startswith("D") else "INDIVIDUAL",
                                **({"tradingType": self.trading_type} if self.trading_type else {})}])
        if path == f"/portfolio/{self.account}/ledger":
            usd = {"currency": "USD", "cashbalance": float(self.cash), "settledcash": float(self.cash), "netliquidationvalue": float(self.cash),
                   "realizedpnl": 0.0, "unrealizedpnl": 0.0, "dividends": 0.0}
            return self._json({"USD": usd, "BASE": {**usd, "currency": "BASE"}})
        if path.startswith(f"/portfolio/{self.account}/positions/"):
            return self._json(self.positions if path.endswith("/0") else [])
        if path == "/iserver/account/orders":
            return self._json({"orders": list(self.orders.values()), "snapshot": True})
        if path == "/iserver/account/trades":
            return self._json(self.trades)
        if path == "/iserver/account/pnl/partitioned":
            return self._json({"upnl": {f"{self.account}.Core": {"rowType": 1, "dpl": float(self.daily_pnl), "nl": float(self.cash), "upl": 0.0,
                                                                  "mv": 0.0}}})
        if path == "/trsrv/stocks":
            symbol = query["symbols"][0]
            return self._json({symbol: [{"name": symbol + " ETF", "assetClass": "STK", "contracts": [{"conid": SPY, "exchange": "ARCA", "isUS": True}]}]})
        if path == "/iserver/marketdata/history":
            if self.history is None:
                return 500, b'{"error":"no history"}'
            return self._json({"symbol": "SPY", "data": self.history, "points": len(self.history)})
        if path == "/iserver/marketdata/snapshot":
            conid = int(query["conids"][0])
            updated = int(((self.now_ts or time.time()) - self.quote_age_seconds) * 1000)
            return self._json([{"conid": conid, "_updated": updated, "31": str(self.last), "84": str(self.last), "86": str(self.last), "6509": self.availability}])
        if path == f"/iserver/account/{self.account}/orders" and request.method == "POST":
            return self._place(body["orders"][0])
        if path.startswith("/iserver/reply/"):
            return self._json({"declined": True})
        if path.startswith(f"/iserver/account/{self.account}/order/") and request.method == "DELETE":
            order_id = path.rsplit("/", 1)[1]
            if order_id in self.orders and self.cancel_sticks:
                self.orders[order_id]["status"] = "Cancelled"
            return self._json({"msg": "Request was submitted", "order_id": order_id})
        return 404, b'{"error":"not found"}'

    def _place(self, order: dict[str, Any]) -> tuple[int, bytes]:
        assert order["orderType"] == "LMT" and order["tif"] == "DAY" and order["outsideRTH"] is False
        assert "cashQty" not in order
        self.placed.append(order)
        if self.place_mode == "question":
            return self._json([{"id": "reply1", "message": ["You are submitting an order without market data."], "messageIds": ["o354"]}])
        if self.place_mode == "error":
            return self._json({"error": "Order rejected: insufficient funds"})
        if any(o.get("order_ref") == order["cOID"] for o in self.orders.values()):
            return self._json({"error": "Local order ID is already registered"})
        self.next_id += 1
        oid = str(self.next_id)
        status = {"fill": "Filled"}.get(self.place_mode, "Submitted")
        qty = order["quantity"]
        filled = qty if self.place_mode == "fill" else (1 if self.place_mode == "partial" else 0)
        self.orders[oid] = {"acct": self.account, "conid": order["conid"], "orderId": int(oid), "ticker": "SPY", "side": order["side"],
                            "status": status, "totalSize": qty, "filledQuantity": filled, "remainingQuantity": qty - filled,
                            "price": str(order["price"]), "order_ref": order["cOID"], "origOrderType": "LIMIT"}
        if filled:
            self.trades.append({"execution_id": f"ex{oid}", "symbol": "SPY", "side": "B" if order["side"] == "BUY" else "S", "size": filled,
                                "price": str(order["price"]), "order_ref": order["cOID"], "order_id": int(oid), "account": self.account,
                                "conid": order["conid"], "commission": "0.35"})
        if self.place_mode == "timeout":
            raise IbkrError("no_answer", "IBKR did not answer in time.", sent=True)
        return self._json([{"order_id": oid, "order_status": status}])

    def _flex(self, path: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
        if path.endswith("SendRequest"):
            return 200, (b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>12345</ReferenceCode>"
                         b"<Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url></FlexStatementResponse>")
        if not self.flex_ready:
            return 200, b"<FlexStatementResponse><Status>Warn</Status><ErrorMessage>Statement generation in progress</ErrorMessage></FlexStatementResponse>"
        return 200, (b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement accountId="DU1234567" fromDate="20261001" toDate="20261031">'
                     b'<CashTransactions><CashTransaction type="Dividends" amount="0.42" currency="USD" symbol="SPY" dateTime="20261015"/>'
                     b'<CashTransaction type="Withholding Tax" amount="-0.13" currency="USD" symbol="SPY" dateTime="20261015"/>'
                     b'</CashTransactions><Trades><Trade/><Trade/></Trades></FlexStatement></FlexStatements></FlexQueryResponse>')

    @staticmethod
    def _json(value: Any) -> tuple[int, bytes]:
        return 200, json.dumps(value).encode()
