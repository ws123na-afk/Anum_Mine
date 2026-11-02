"""IBKR trading (docs/finance-live-trading.md): connector, read-only sync, reconciliation, the order
path with every hard limit, the kill switch and the live locks. In-memory stores and a fake IBKR
gateway (``ibkr_fake.py``); real IBKR is never called. ``test_postgres_finance_ibkr.py`` repeats
isolation and the database checks under RLS.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from ibkr_fake import PIN, SPY, FakeIbkr
from pydantic import ValidationError

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import ibkr_api
from anum_api.finance import ibkr_service as svc
from anum_api.finance.config import FinanceConfig, finance_config
from anum_api.finance.guardian import ActionKind, GuardianAction, GuardianRules, LedgerState, LiveClearance, gate
from anum_api.finance.ibkr_rules import Environment, OrderState
from anum_api.finance.ibkr_store import ibkr_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext
from anum_api.secret_box import secret_cipher

TENANT, WORKSPACE = "tenant_ibkr", "workspace_ibkr"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_ibkr", "x-user-roles": "owner"}
OTHER_OWNER = {**OWNER, "x-user-id": "owner_two", "x-workspace-id": "workspace_two"}
MEMBER = {**OWNER, "x-user-id": "member_ibkr", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_ibkr", "x-user-roles": "viewer"}
CTX = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_ibkr", roles=["owner"])
API = "/api/v1/finance/ibkr"
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)  # Wednesday 10:00 in New York
client = TestClient(main.app)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    tick = Clock()
    monkeypatch.setattr(ibkr_api, "utc_now", tick)
    return tick


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[FakeIbkr]:
    gateway = FakeIbkr(now_ts=NOW.timestamp())
    monkeypatch.setattr(svc, "TRANSPORT", gateway)
    finance_store.clear()
    ibkr_memory_store.clear()
    memory_repository.store.audit_records.clear()
    yield gateway
    finance_store.clear()
    ibkr_memory_store.clear()


def _connect(environment: str = "paper", account: str = "DU1234567", headers: dict[str, str] = OWNER, **extra: object) -> dict:
    body = {"environment": environment, "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": account,
            "cash_account_confirmed": True, **extra}
    response = client.post(f"{API}/connections", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _ready(fake: FakeIbkr) -> dict:
    connection = _connect()
    assert client.post(f"{API}/symbols", json={"symbol": "spy", "confirm_not_screened": True}, headers=OWNER).status_code == 200
    assert client.post(f"{API}/sync?environment=paper", headers=OWNER).status_code == 200
    return connection


def _order(side: str = "buy", quantity: str = "1", price: str = "9.50", headers: dict[str, str] = OWNER) -> dict:
    response = client.post(f"{API}/orders", json={"symbol": "SPY", "side": side, "quantity": quantity, "limit_price": price}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _approve(intent: dict, headers: dict[str, str] = OWNER, payload_hash: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/intents/{intent['id']}/approve", json={"payload_hash": payload_hash or intent["payload_hash"]}, headers=headers)


def _overview(headers: dict[str, str] = OWNER) -> dict:
    response = client.get(f"{API}/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# -- connecting --------------------------------------------------------------------------------------


def test_connecting_keeps_the_details_encrypted_and_never_returns_them(fake: FakeIbkr) -> None:
    view = _connect(flex_token="exampletoken", flex_query_id="123")
    assert view["verified_at"] and view["session"] == "authenticated" and view["hint"] == "DU...567"
    assert "DU1234567" not in json.dumps(view) and PIN not in json.dumps(view) and "exampletoken" not in json.dumps(view)
    stored = next(iter(ibkr_memory_store.connections.values()))
    assert "DU1234567" not in stored.secret and "exampletoken" not in stored.secret
    assert json.loads(secret_cipher().decrypt(stored.secret))["account_id"] == "DU1234567"
    assert "DU1234567" not in json.dumps(_overview()["connections"])
    assert all("DU1234567" not in json.dumps(dict(r.metadata)) for r in memory_repository.store.audit_records)
    # A second active paper connection is refused; disconnecting erases the details.
    again = client.post(f"{API}/connections", json={"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN,
                                                    "account_id": "DU1234567", "cash_account_confirmed": True}, headers=OWNER)
    assert again.status_code == 409
    assert client.delete(f"{API}/connections/{view['id']}", headers=OWNER).json()["status"] == "revoked"
    assert ibkr_memory_store.connections[view["id"]].secret == ""


@pytest.mark.parametrize(("body", "status"), [
    ({"gateway_host": "evil.example.com"}, 422),
    ({"tls_fingerprint": "zz" * 32}, 422),
    ({"account_id": "U1234567"}, 422),  # not a paper account
    ({"cash_account_confirmed": False}, 422),
    ({"flex_token": "exampletoken"}, 422),  # token without query id
    ({"password": "notapassword"}, 422),  # no password field exists
])
def test_the_form_refuses_unsafe_or_wrong_details(fake: FakeIbkr, body: dict, status: int) -> None:
    base = {"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "DU1234567", "cash_account_confirmed": True}
    response = client.post(f"{API}/connections", json={**base, **body}, headers=OWNER)
    assert response.status_code == status, response.text
    assert "notapassword" not in response.text and not ibkr_memory_store.connections


def test_only_the_workspace_owner_connects_or_trades_and_viewers_read_nothing_else(fake: FakeIbkr) -> None:
    for headers in (MEMBER, VIEWER):
        body = {"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "DU1234567", "cash_account_confirmed": True}
        assert client.post(f"{API}/connections", json=body, headers=headers).status_code == 403
        assert client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "1", "limit_price": "9"}, headers=headers).status_code == 403
    assert client.get(f"{API}/overview", headers=MEMBER).status_code == 200


def test_a_live_connection_needs_the_deployment_flag_and_a_live_account(fake: FakeIbkr, monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"environment": "live", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "U7654321", "cash_account_confirmed": True}
    assert client.post(f"{API}/connections", json=body, headers=OWNER).status_code == 409
    monkeypatch.setattr(finance_config, "live_enabled", True)
    assert client.post(f"{API}/connections", json=body, headers=OWNER).status_code == 409  # broker not named
    monkeypatch.setattr(finance_config, "live_broker", "ibkr")
    assert client.post(f"{API}/connections", json={**body, "account_id": "DU1234567"}, headers=OWNER).status_code == 422


def test_a_margin_account_is_never_verified(fake: FakeIbkr) -> None:
    fake.trading_type = "STKMRGN"
    view = _connect()
    assert view["verified_at"] is None and "margin" in view["last_error"]
    assert client.post(f"{API}/symbols", json={"symbol": "SPY", "confirm_not_screened": True}, headers=OWNER).status_code == 200
    assert client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "1", "limit_price": "9.5"}, headers=OWNER).status_code == 409


def test_an_ended_session_shows_log_in_again_and_raises_one_alert(fake: FakeIbkr) -> None:
    view = _connect()
    fake.authenticated = False
    checked = client.post(f"{API}/connections/{view['id']}/check", headers=OWNER).json()
    assert checked["session"] == "expired" and checked["session_message"] == "login_needed"
    client.post(f"{API}/connections/{view['id']}/check", headers=OWNER)
    alerts = client.get(f"{API}/events?alerts=true", headers=OWNER).json()
    assert [e["kind"] for e in alerts["items"]].count("session_expired") == 1 and alerts["unread"] == 1
    assert client.post(f"{API}/sync?environment=paper", headers=OWNER).status_code == 502
    fake.authenticated = True
    assert client.post(f"{API}/connections/{view['id']}/check?reauthenticate=true", headers=OWNER).json()["session"] == "authenticated"
    assert ("POST", "/v1/api/iserver/auth/ssodh/init") in fake.calls
    assert client.post(f"{API}/events/read", headers=OWNER).json()["marked"] == 1


# -- allowed ETFs and limits ---------------------------------------------------------------------------


def test_only_listed_us_etfs_can_be_allowed_and_their_contract_comes_from_ibkr(fake: FakeIbkr) -> None:
    _connect()
    assert client.post(f"{API}/symbols", json={"symbol": "TSLA"}, headers=OWNER).status_code == 422
    refused = client.post(f"{API}/symbols", json={"symbol": "SPY"}, headers=OWNER)
    assert refused.status_code == 422 and "not Shariah-screened" in refused.text
    allowed = client.post(f"{API}/symbols", json={"symbol": "SPY", "confirm_not_screened": True}, headers=OWNER).json()["allowed"]
    assert allowed == [{"symbol": "SPY", "conid": SPY, "name": "SPDR S&P 500 ETF Trust", "exchange": "ARCA", "shariah_screened": False}]
    screened = client.post(f"{API}/symbols", json={"symbol": "SPUS"}, headers=OWNER).json()["allowed"]
    assert screened[-1]["symbol"] == "SPUS" and screened[-1]["shariah_screened"] is True
    assert [a["symbol"] for a in client.delete(f"{API}/symbols/SPY", headers=OWNER).json()["allowed"]] == ["SPUS"]


def test_the_default_limits_are_the_owners_and_ceilings_hold(fake: FakeIbkr) -> None:
    limits = _overview()["settings"]["limits"]
    assert (limits["max_capital"], limits["max_order"], limits["max_daily_loss"]) == ("50", "10", "5")
    bad = {"max_capital": "50", "max_order": "60", "max_daily_loss": "5", "max_orders_per_day": 3, "price_band_percent": "2",
           "session_buffer_minutes": 15, "fee_buffer": "1"}
    assert client.put(f"{API}/limits", json={"limits": bad, "version": 0}, headers=OWNER).status_code == 422
    assert client.put(f"{API}/limits", json={"limits": {**bad, "max_order": "10", "max_capital": "20000"}, "version": 0}, headers=OWNER).status_code == 422
    ok = client.put(f"{API}/limits", json={"limits": {**bad, "max_order": "8", "max_daily_loss": "3"}, "version": 0}, headers=OWNER)
    assert ok.status_code == 200 and ok.json()["limits"]["max_daily_loss"] == "3"
    assert client.put(f"{API}/limits", json={"limits": {**bad, "max_order": "8"}, "version": 0}, headers=OWNER).status_code == 409  # stale


# -- the order path -----------------------------------------------------------------------------------


def test_a_paper_order_is_approved_once_with_its_hash_and_sent_as_a_limit_day_order(fake: FakeIbkr) -> None:
    _ready(fake)
    intent = _order()
    assert intent["status"] == "pending_approval" and intent["environment"] == "paper" and intent["order_type"] == "limit"
    assert datetime.fromisoformat(intent["expires_at"]) - datetime.fromisoformat(intent["created_at"]) == timedelta(minutes=5)
    assert _approve(intent, payload_hash="f" * 64).status_code == 409 and not fake.placed
    sent = _approve(intent)
    assert sent.status_code == 200, sent.text
    order = sent.json()["order"]
    assert order["state"] == "submitted" and order["broker_order_id"] and order["id"] == intent["id"]
    assert fake.placed == [{"acctId": "DU1234567", "conid": SPY, "cOID": intent["id"], "orderType": "LMT", "side": "BUY", "price": 9.5,
                            "quantity": 1.0, "tif": "DAY", "outsideRTH": False}]
    assert _approve(intent).status_code == 409 and len(fake.placed) == 1  # used once
    assert client.post(f"{API}/orders/{order['id']}/send", headers=OWNER).status_code == 409 and len(fake.placed) == 1
    kinds = [e["kind"] for e in client.get(f"{API}/events", headers=OWNER).json()["items"]]
    assert "order_sent" in kinds and "intent" in kinds
    assert {r.action for r in memory_repository.store.audit_records} >= {"finance.ibkr.order.create", "finance.ibkr.order.approve"}


def test_an_expired_approval_cannot_be_used(fake: FakeIbkr, clock: Clock) -> None:
    _ready(fake)
    intent = _order()
    clock.now = NOW + timedelta(minutes=6)
    assert _approve(intent).status_code == 409 and not fake.placed


def test_the_simulated_gateway_never_runs_an_ibkr_order(fake: FakeIbkr) -> None:
    _ready(fake)
    intent = _order()
    refused = client.post(f"/api/v1/finance/execution/intents/{intent['id']}/approve", json={"payload_hash": intent["payload_hash"]}, headers=OWNER)
    assert refused.status_code == 409 and not fake.placed


def _limit_case(fake: FakeIbkr, clock: Clock, case: str) -> tuple[str, str, str]:
    """Break exactly one condition; returns the order side, quantity and price to try."""
    side, quantity, price = "buy", "1", "9.50"
    if case == "max_capital":
        fake.positions = [{"conid": 1, "ticker": "VTI", "position": 1, "mktPrice": 45, "mktValue": 45, "currency": "USD"}]
    elif case == "cash":
        fake.cash = Decimal("5")
    elif case == "daily_loss":
        fake.daily_pnl = Decimal("-5")
    elif case == "market_hours":
        clock.now = NOW.replace(hour=21)  # 16:00 in New York
        fake.now_ts = clock.now.timestamp()
    elif case == "price_band":
        price = "9.00"  # more than 2 percent from 9.50
    elif case == "no_short":
        side = "sell"
    elif case == "session":
        fake.authenticated = False
    elif case == "stale_quote":
        fake.quote_age_seconds = 600
    return side, quantity, price


@pytest.mark.parametrize("case", ["max_capital", "cash", "daily_loss", "market_hours", "price_band", "no_short", "session", "stale_quote"])
def test_every_hard_limit_blocks_an_approval_on_its_own(fake: FakeIbkr, clock: Clock, case: str) -> None:
    _ready(fake)
    side, quantity, price = _limit_case(fake, clock, case)
    intent = _order(side, quantity, price)
    refused = _approve(intent)
    assert refused.status_code in {409, 502}, refused.text
    assert not fake.placed
    if case != "session":
        denied = [r for r in memory_repository.store.audit_records if r.action == "finance.ibkr.order.approve"]
        expected = "price_band" if case == "stale_quote" else case
        assert denied and expected in denied[-1].metadata.get("failed", [denied[-1].metadata["code"]])


def test_orders_per_day_and_the_per_order_cap(fake: FakeIbkr) -> None:
    _ready(fake)
    assert client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "2", "limit_price": "9.50"}, headers=OWNER).status_code == 422
    fake.cash = Decimal("1000")
    limits = {"max_capital": "50", "max_order": "10", "max_daily_loss": "5", "max_orders_per_day": 1, "price_band_percent": "2",
              "session_buffer_minutes": 15, "fee_buffer": "1"}
    assert client.put(f"{API}/limits", json={"limits": limits, "version": _overview()["settings"]["version"]}, headers=OWNER).status_code == 200
    assert _approve(_order()).status_code == 200
    second = _approve(_order())
    assert second.status_code == 409 and "orders_per_day" in second.json()["error"]["message"] or "1 orders today" in second.text
    assert len(fake.placed) == 1


def test_fractional_quantities_are_refused_until_the_owner_allows_them(fake: FakeIbkr) -> None:
    _ready(fake)
    assert client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "0.5", "limit_price": "9.5"}, headers=OWNER).status_code == 422
    assert client.put(f"{API}/fractional", json={"enabled": True}, headers=OWNER).json()["fractional_ok"] is True
    assert client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "0.5", "limit_price": "9.5"}, headers=OWNER).status_code == 201


def test_limits_are_checked_again_right_before_sending(fake: FakeIbkr, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(fake)
    intent = _order()
    real = svc._gather
    calls = {"n": 0}

    def gather(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:  # the loss limit is hit between the approval check and the send
            fake.daily_pnl = Decimal("-6")
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "_gather", gather)
    refused = _approve(intent)
    assert refused.status_code == 409 and "Not sent" in refused.json()["error"]["message"] and calls["n"] == 2
    assert not fake.placed
    order = client.get(f"{API}/orders", headers=OWNER).json()["items"][0]
    assert order["state"] == "rejected" and order["attempts"] == 0


def test_a_question_from_ibkr_is_declined_never_confirmed(fake: FakeIbkr) -> None:
    _ready(fake)
    fake.place_mode = "question"
    order = _approve(_order()).json()["order"]
    assert order["state"] == "rejected" and order["message"].startswith("IBKR asked:")
    assert ("POST", "/v1/api/iserver/reply/reply1") in fake.calls and not fake.orders


def test_a_rejection_is_recorded_and_alerted(fake: FakeIbkr) -> None:
    _ready(fake)
    fake.place_mode = "error"
    order = _approve(_order()).json()["order"]
    assert order["state"] == "rejected" and "insufficient funds" in order["message"]
    assert any(e["kind"] == "order_rejected" for e in client.get(f"{API}/events?alerts=true", headers=OWNER).json()["items"])


def test_a_timeout_is_looked_up_by_the_client_order_id_and_never_sent_twice(fake: FakeIbkr) -> None:
    _ready(fake)
    fake.place_mode = "timeout"
    intent = _order()
    assert _approve(intent).status_code == 502
    order = client.get(f"{API}/orders", headers=OWNER).json()["items"][0]
    assert order["state"] == "unknown" and order["attempts"] == 1
    fake.place_mode = "ok"
    resent = client.post(f"{API}/orders/{intent['id']}/send", headers=OWNER)
    assert resent.status_code == 200 and resent.json()["order"]["state"] == "submitted"
    assert len(fake.placed) == 1  # found by its cOID: adopted, not sent again


def test_partial_fills_then_fill_are_followed_and_alerted(fake: FakeIbkr) -> None:
    _ready(fake)
    fake.cash = Decimal("100")
    limits = {"max_capital": "50", "max_order": "20", "max_daily_loss": "5", "max_orders_per_day": 3, "price_band_percent": "2",
              "session_buffer_minutes": 15, "fee_buffer": "1"}
    assert client.put(f"{API}/limits", json={"limits": limits, "version": _overview()["settings"]["version"]}, headers=OWNER).status_code == 200
    fake.place_mode = "partial"
    order = _approve(_order(quantity="2")).json()["order"]
    assert order["state"] == "submitted"
    client.post(f"{API}/sync?environment=paper", headers=OWNER)
    order = client.get(f"{API}/orders", headers=OWNER).json()["items"][0]
    assert order["state"] == "partially_filled" and order["filled_quantity"] == "1"
    fake.positions = [{"conid": SPY, "ticker": "SPY", "position": 1, "mktValue": 9.5}]
    broker = next(iter(fake.orders.values()))
    broker.update(status="Filled", filledQuantity=2, remainingQuantity=0)
    fake.trades.append({"execution_id": "ex2", "symbol": "SPY", "side": "B", "size": 1, "price": "9.50", "order_ref": order["id"], "account": fake.account,
                        "conid": SPY})
    fake.positions = [{"conid": SPY, "ticker": "SPY", "position": 2, "mktValue": 19}]
    result = client.post(f"{API}/sync?environment=paper", headers=OWNER).json()
    assert result["reconciliation"] == "ok"
    order = client.get(f"{API}/orders", headers=OWNER).json()["items"][0]
    assert order["state"] == "filled" and order["average_price"] == "9.5000"
    kinds = [e["kind"] for e in client.get(f"{API}/events?alerts=true", headers=OWNER).json()["items"]]
    assert "order_partially_filled" in kinds and "order_filled" in kinds


def test_a_mismatch_blocks_new_orders_until_the_owner_explains_it(fake: FakeIbkr) -> None:
    _ready(fake)
    fake.orders["9"] = {"acct": fake.account, "conid": SPY, "orderId": 9, "status": "Submitted", "side": "BUY", "totalSize": 1, "filledQuantity": 0,
                        "remainingQuantity": 1, "price": "9.4", "order_ref": "placed-in-ibkr"}
    assert client.post(f"{API}/sync?environment=paper", headers=OWNER).json()["reconciliation"] == "mismatch"
    view = _overview()
    assert view["settings"]["reconciliation"]["paper"]["state"] == "mismatch"
    assert any(e["kind"] == "mismatch" for e in client.get(f"{API}/events?alerts=true", headers=OWNER).json()["items"])
    refused = _approve(_order())
    assert refused.status_code == 409 and not fake.placed
    assert client.post(f"{API}/reconciliation/accept", json={"environment": "paper", "note": "short"}, headers=OWNER).status_code == 422
    accepted = client.post(f"{API}/reconciliation/accept", json={"environment": "paper", "note": "I placed that order in IBKR myself."}, headers=OWNER)
    assert accepted.json()["reconciliation"]["paper"]["state"] == "ok" and accepted.json()["reconciliation"]["paper"]["explained"] == 1


def test_the_kill_switch_cancels_open_orders_at_ibkr_confirms_each_and_blocks_new_ones(fake: FakeIbkr) -> None:
    _ready(fake)
    _approve(_order())
    fake.orders["77"] = {"acct": fake.account, "conid": 5, "orderId": 77, "status": "PreSubmitted", "side": "BUY", "totalSize": 1, "filledQuantity": 0,
                         "remainingQuantity": 1, "price": "1"}
    result = client.post(f"{API}/kill-switch", json={"reason": "test stop"}, headers=MEMBER)
    assert result.status_code == 403 or result.status_code == 200
    result = client.post(f"{API}/kill-switch", json={"reason": "test stop"}, headers=OWNER).json()
    report = result["report"][0]["orders"]
    assert {o["order_id"] for o in report} == {"77", next(k for k in fake.orders if k != "77")} and all(o["cancelled"] for o in report)
    assert all(o["status"] == "Cancelled" for o in fake.orders.values())
    assert client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "1", "limit_price": "9.5"}, headers=OWNER).status_code == 409
    assert client.post(f"{API}/kill-switch/release", json={"confirm": "yes"}, headers=OWNER).status_code == 422
    assert client.post(f"{API}/kill-switch/release", json={"confirm": "RESUME"}, headers=OWNER).json()["kill_switch"] is False


def test_a_cancel_that_does_not_stick_raises_a_critical_alert(fake: FakeIbkr) -> None:
    _ready(fake)
    _approve(_order())
    fake.cancel_sticks = False
    report = client.post(f"{API}/kill-switch", json={}, headers=OWNER).json()["report"][0]["orders"]
    assert report and not report[0]["cancelled"]
    critical = [e for e in client.get(f"{API}/events?alerts=true", headers=OWNER).json()["items"] if e["severity"] == "critical"]
    assert any("not confirmed cancelled" in e["message"] for e in critical)


def test_statements_bring_dividends_from_ibkr_only(fake: FakeIbkr) -> None:
    _connect(flex_token="exampletoken", flex_query_id="123")
    statement = client.post(f"{API}/statements?environment=paper", headers=OWNER).json()
    assert statement["dividends_received"] == "0.42" and statement["withholding_tax"] == "-0.13" and statement["trades"] == 2
    assert _overview()["statements"]["paper"]["dividends_received"] == "0.42"


def test_records_stay_with_their_owner(fake: FakeIbkr) -> None:
    _ready(fake)
    intent = _order()
    assert client.get(f"{API}/overview", headers=OTHER_OWNER).json()["connections"] == []
    assert _approve(intent, headers=OTHER_OWNER).status_code == 404
    assert client.get(f"{API}/orders", headers=OTHER_OWNER).json()["total"] == 0
    assert client.get(f"{API}/events", headers=OTHER_OWNER).json()["total"] == 0
    assert not fake.placed


def test_an_assistant_can_only_propose_paper_orders(fake: FakeIbkr) -> None:
    _ready(fake)
    proposal = svc.create_order(CTX, svc.OrderInput(symbol="SPY", side="buy", quantity=Decimal(1), limit_price=Decimal("9.5")), NOW,
                                requested_by="agent", proposer="agent_x")
    assert proposal.requested_by == "agent" and proposal.mode.value == "paper"
    settings = ibkr_memory_store.get_settings(CTX)
    assert settings is not None
    settings.live.on = True
    ibkr_memory_store.save_settings(CTX, settings, expected_version=None)
    with pytest.raises(svc.BrokerRefusal) as refused:
        svc.create_order(CTX, svc.OrderInput(symbol="SPY", side="buy", quantity=Decimal(1), limit_price=Decimal("9.5")), NOW, requested_by="agent")
    assert refused.value.code == "agent_live"


def test_privacy_deletion_removes_ibkr_records(fake: FakeIbkr) -> None:
    _ready(fake)
    assert finance_store.count_everything(CTX) > 0
    finance_store.delete_everything(CTX)
    assert ibkr_memory_store.count_everything(CTX) == 0


def test_the_readiness_preflight_lists_the_ibkr_gates_and_keeps_live_off(fake: FakeIbkr) -> None:
    body = {"facts": {}, "profile": {"broker": "interactive_brokers", "country_of_residence": "SA", "assets": ["stock"]}}
    result = client.post("/api/v1/finance/broker/readiness", json=body, headers=OWNER).json()
    assert result["live_trading_enabled"] is False and result["orders_sent"] == 0
    assert {g["code"] for g in result["broker_gates"]} >= {"deployment_flag", "live_broker", "paper_proof", "owner_switch", "cooling_off"}
    other = client.post("/api/v1/finance/broker/readiness", json={**body, "profile": {**body["profile"], "broker": "alpaca"}}, headers=OWNER).json()
    assert other["broker_gates"] == []


# -- the live locks -----------------------------------------------------------------------------------


def test_the_startup_refusal_stays_unless_the_reviewed_ibkr_executor_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="ANUM_FINANCE_LIVE_ENABLED must stay false"):
        FinanceConfig(live_enabled=True)
    with pytest.raises(ValidationError):
        FinanceConfig(live_enabled=True, live_broker="alpaca")
    assert FinanceConfig(live_enabled=True, live_broker="ibkr").live_enabled is True
    monkeypatch.setenv("ANUM_FINANCE_LIVE_ENABLED", "true")
    with pytest.raises(ValidationError):
        FinanceConfig()
    monkeypatch.setenv("ANUM_FINANCE_LIVE_BROKER", "ibkr")
    assert FinanceConfig().live_enabled is True
    with pytest.raises(ValidationError):  # stage 9 stays refused without stage 8
        FinanceConfig(live_enabled=False, wider_live_enabled=True)


def test_the_guardian_kernel_denies_a_real_trade_unless_every_live_gate_is_cleared() -> None:
    at = datetime(2026, 11, 4, 15, tzinfo=timezone.utc)
    action = GuardianAction(kind=ActionKind.TRADE, amount=Decimal("10"), currency="USD", merchant="SPY", at=at)
    assert gate(action, GuardianRules(), LedgerState()).outcome == "deny"
    partial = LiveClearance(["deployment_flag", "live_broker"])
    assert gate(action, GuardianRules(), LedgerState(), live=partial).reasons[-1].code == "execution_not_available"
    full = LiveClearance(["deployment_flag", "live_broker", "live_connection", "paper_proof", "owner_switch", "cooling_off", "limits",
                          "reconciliation", "kill_switches"])
    assert gate(action, GuardianRules(), LedgerState(), live=full).outcome == "allow"
    assert gate(action, GuardianRules(blocked_merchants=["spy"]), LedgerState(), live=full).outcome == "deny"  # rules still decide
    transfer = GuardianAction(kind=ActionKind.TRANSFER, amount=Decimal("10"), currency="USD", at=at)
    assert gate(transfer, GuardianRules(), LedgerState(), live=full).outcome == "deny"  # only trades


def _all_live(fake: FakeIbkr, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> dict:
    """Paper proof done, live connected and switched on, cooling-off over."""
    monkeypatch.setattr(finance_config, "broker_paper_min_orders", 1)
    monkeypatch.setattr(finance_config, "broker_paper_min_days", 1)
    _ready(fake)
    assert _approve(_order()).status_code == 200
    clock.now = NOW + timedelta(days=2)
    fake.now_ts = clock.now.timestamp()
    monkeypatch.setattr(finance_config, "live_enabled", True)
    monkeypatch.setattr(finance_config, "live_broker", "ibkr")
    fake.account, fake.orders, fake.trades = "U7654321", {}, []
    live = _connect("live", "U7654321")
    client.post(f"{API}/sync?environment=live", headers=OWNER)
    code = client.post(f"{API}/live/request", headers=OWNER)
    assert code.status_code == 200, code.text
    assert client.post(f"{API}/live/confirm", json={"code": "WRONG1"}, headers=OWNER).status_code == 422
    on = client.post(f"{API}/live/confirm", json={"code": code.json()["code"]}, headers=OWNER)
    assert on.status_code == 200 and on.json()["live"]["on"] is True and on.json()["live"]["code_hash"] is None
    return live


def test_live_orders_run_only_after_every_gate_including_cooling_off(fake: FakeIbkr, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    _all_live(fake, monkeypatch, clock)
    blocked = client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "1", "limit_price": "9.5"}, headers=OWNER)
    assert blocked.status_code == 409 and "cooling-off" in blocked.json()["error"]["message"]
    clock.now = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)  # Monday 10:00 in New York, after the cooling-off
    fake.now_ts = clock.now.timestamp()
    intent = _order()
    assert intent["environment"] == "live" and intent["mode"] == "live"
    stored = finance_store.get_intent(CTX, intent["id"])
    assert stored is not None and stored.decision["reasons"][-1]["code"] == "live_cleared"
    sent = _approve(intent)
    assert sent.status_code == 200, sent.text
    assert fake.placed[-1]["acctId"] == "U7654321" and fake.placed[-1]["cOID"] == intent["id"]
    assert _overview()["mode"] == "live"
    # Loosening a limit is refused while live is on; stricter is fine.
    view = _overview()
    stricter = {**view["settings"]["limits"], "max_order": "5"}
    assert client.put(f"{API}/limits", json={"limits": stricter, "version": view["settings"]["version"]}, headers=OWNER).status_code == 200
    looser = {**stricter, "max_order": "10"}
    assert client.put(f"{API}/limits", json={"limits": looser, "version": view["settings"]["version"] + 1}, headers=OWNER).status_code == 409
    assert client.post(f"{API}/live/off", headers=OWNER).json()["live"]["on"] is False
    assert _overview()["mode"] == "paper"


GATES = ["deployment_flag", "live_broker", "live_connection", "paper_proof", "limits", "reconciliation", "kill_switches", "owner_switch", "cooling_off"]


@pytest.mark.parametrize("gate_code", GATES)
def test_each_live_gate_blocks_a_live_order_on_its_own(fake: FakeIbkr, monkeypatch: pytest.MonkeyPatch, clock: Clock, gate_code: str) -> None:
    live = _all_live(fake, monkeypatch, clock)
    clock.now = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)  # Monday 10:00 in New York, after the cooling-off
    fake.now_ts = clock.now.timestamp()
    with svc.open_ibkr_stores(CTX) as (fstore, store):
        before = {c.code: c.ok for c in svc.live_gates(fstore, store, CTX, svc._settings(store, CTX), clock.now)}
    assert all(before.values()), before
    settings = ibkr_memory_store.get_settings(CTX)
    assert settings is not None
    if gate_code == "deployment_flag":
        monkeypatch.setattr(finance_config, "live_enabled", False)
    elif gate_code == "live_broker":
        monkeypatch.setattr(finance_config, "live_broker", "")
    elif gate_code == "live_connection":
        client.delete(f"{API}/connections/{live['id']}", headers=OWNER)
        settings = ibkr_memory_store.get_settings(CTX)
        assert settings is not None
        settings.live.on, settings.live.effective_at = True, NOW
    elif gate_code == "paper_proof":
        monkeypatch.setattr(finance_config, "broker_paper_min_orders", 5)
    elif gate_code == "limits":
        settings.allowed = []
    elif gate_code == "reconciliation":
        settings.reconciliation["live"] = settings.recon(Environment.LIVE).model_copy(update={"state": "mismatch"})
    elif gate_code == "kill_switches":
        settings.kill_switch = True
    elif gate_code == "owner_switch":
        settings.live.on = False
    elif gate_code == "cooling_off":
        settings.live.effective_at = clock.now + timedelta(hours=1)
    ibkr_memory_store.save_settings(CTX, settings, expected_version=None)
    with svc.open_ibkr_stores(CTX) as (fstore, store):
        after = {c.code: c.ok for c in svc.live_gates(fstore, store, CTX, svc._settings(store, CTX), clock.now)}
    assert [code for code, ok in after.items() if not ok] == [gate_code]
    if gate_code == "owner_switch":
        return  # with the switch off, new orders go to paper by design
    response = client.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "1", "limit_price": "9.5"}, headers=OWNER)
    assert response.status_code in {409, 422}
    placed = len(fake.placed)
    assert placed == 1  # only the paper order from the proof


def test_a_live_request_refuses_while_gates_are_unmet(fake: FakeIbkr) -> None:
    _ready(fake)
    refused = client.post(f"{API}/live/request", headers=OWNER)
    assert refused.status_code == 409 and "deployment_flag" in json.dumps([dict(r.metadata) for r in memory_repository.store.audit_records])


def test_order_state_after_restart_while_submitting_is_unknown_then_looked_up(fake: FakeIbkr, clock: Clock) -> None:
    _ready(fake)
    intent = _order()
    with svc.open_ibkr_stores(CTX) as (fstore, store):
        assert fstore.claim_intent(CTX, intent["id"], NOW, CTX.user_id)
        store.add_order(svc.BrokerOrder(id=intent["id"], tenant_id=TENANT, workspace_id=WORKSPACE, owner_user_id=CTX.user_id,
                                        connection_id=_overview()["connections"][0]["id"], environment=Environment.PAPER, symbol="SPY", conid=SPY,
                                        side="buy", quantity=Decimal(1), limit_price=Decimal("9.5"), state=OrderState.SUBMITTING, attempts=1,
                                        created_at=NOW, updated_at=NOW))
    clock.now = NOW + timedelta(minutes=3)
    fake.now_ts = clock.now.timestamp()
    client.post(f"{API}/sync?environment=paper", headers=OWNER)
    assert client.get(f"{API}/orders", headers=OWNER).json()["items"][0]["state"] == "unknown"
    assert client.post(f"{API}/orders/{intent['id']}/send", headers=OWNER).json()["order"]["state"] == "submitted"
    assert len(fake.placed) == 1


# -- buy plans ----------------------------------------------------------------------------------------


def _plans(plans: list[dict]) -> object:
    return client.put(f"{API}/plans", json={"plans": plans, "version": _overview()["settings"]["version"]}, headers=OWNER)


def test_a_buy_plan_only_proposes_one_buy_a_month_for_approval_and_never_sends(fake: FakeIbkr, clock: Clock) -> None:
    _ready(fake)
    assert _plans([{"symbol": "VOO", "amount": "10", "day_of_month": 4}]).status_code == 422  # not allowed
    assert _plans([{"symbol": "SPY", "amount": "11", "day_of_month": 4}]).status_code == 422  # over the per-order limit
    assert _plans([{"symbol": "SPY", "amount": "10", "day_of_month": 29}]).status_code == 422
    saved = _plans([{"symbol": "SPY", "amount": "10", "day_of_month": 5}])
    assert saved.status_code == 200 and saved.json()["buy_plans"][0]["last_proposed"] is None
    assert client.post(f"{API}/plans/propose", headers=OWNER).json() == {"proposed": [], "skipped": []}  # 4th: not due yet
    clock.now = NOW + timedelta(days=1)  # Thursday the 5th, 10:00 in New York
    fake.now_ts = clock.now.timestamp()
    result = client.post(f"{API}/plans/propose", headers=OWNER).json()
    assert len(result["proposed"]) == 1
    proposal = result["proposed"][0]
    assert (proposal["side"], proposal["quantity"], proposal["limit_price"], proposal["status"]) == ("buy", "1", "9.5", "pending_approval")
    assert proposal["reason"].startswith("Buy plan") and not fake.placed
    assert [p["id"] for p in _overview()["pending"]] == [proposal["id"]]
    assert client.post(f"{API}/plans/propose", headers=OWNER).json()["proposed"] == []  # once a month
    assert _approve(proposal).status_code == 200 and len(fake.placed) == 1  # only the owner's approval sends it


def test_a_buy_plan_waits_for_market_hours_and_says_when_the_amount_is_too_small(fake: FakeIbkr, clock: Clock) -> None:
    _ready(fake)
    _plans([{"symbol": "SPY", "amount": "5", "day_of_month": 1}])
    clock.now = NOW.replace(hour=23)
    skipped = client.post(f"{API}/plans/propose", headers=OWNER).json()["skipped"]
    assert skipped and "allowed from" in skipped[0]["reason"]
    assert _overview()["settings"]["buy_plans"][0]["last_proposed"] is None
    clock.now = NOW
    result = client.post(f"{API}/plans/propose", headers=OWNER).json()
    assert result["proposed"] == [] and "less than one whole share" in result["skipped"][0]["reason"]
    assert any(e["kind"] == "plan_skipped" for e in client.get(f"{API}/events?alerts=true", headers=OWNER).json()["items"])


def test_shariah_screened_etfs_are_offered(fake: FakeIbkr) -> None:
    view = _overview()
    assert {"SPUS", "HLAL", "UMMA", "SPRE", "SPSK"} == set(view["shariah_etfs"]) and set(view["shariah_etfs"]) <= set(view["etfs"])


def test_the_ticket_quote_is_labelled_delayed_and_the_band_is_not_widened(fake: FakeIbkr) -> None:
    _ready(fake)
    quote = client.get(f"{API}/quote?symbol=SPY", headers=OWNER).json()
    assert quote["delayed"] is True and quote["last"] == "9.50" and quote["band_percent"] == "2"
    assert _approve(_order(price="9.30")).status_code == 409  # 2.1 percent away: refused with delayed data too
    fake.availability = "Z"  # frozen: no usable price
    assert client.get(f"{API}/quote?symbol=SPY", headers=OWNER).json()["last"] is None
    assert _approve(_order()).status_code == 409 and len(fake.placed) == 0
    fake.availability = "RpB"
    assert client.get(f"{API}/quote?symbol=SPY", headers=OWNER).json()["delayed"] is False
    assert _approve(_order()).status_code == 200
