"""Finance stages 6 to 8 (docs/finance.md): paper and shadow trading, the execution gateway,
limited live (framework only, off).

In-memory stores; ``test_postgres_finance.py`` repeats isolation and the database checks under
RLS. Fixtures are plainly fake: example symbols and round prices typed by the test.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import main
from anum_api.agent_planning import choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall
from anum_api.chat_tools import chat_intent
from anum_api.dependencies import memory_repository
from anum_api.finance import gateway, live
from anum_api.finance.config import FinanceConfig, finance_config
from anum_api.finance.execution import ExecMode, IntentStatus
from anum_api.finance.guardian import ActionKind, GuardianAction, GuardianRules, LedgerState, gate
from anum_api.finance.live import LiveAttempt, LiveLimits, LiveSpent, check_live_limits
from anum_api.finance.models import FxRate
from anum_api.finance.store import finance_store
from anum_api.finance.summary import FinanceSettings, today_in
from anum_api.finance.tools import plan_call
from anum_api.finance.trading import (
    FeeModel,
    InstrumentKind,
    OrderStatus,
    OrderType,
    PaperOrder,
    Portfolio,
    PortfolioMode,
    PricePoint,
    PriceRecord,
    Side,
    adjusted_price,
    compare_shadow,
    fill_at,
    limit_met,
    price_for,
    replay,
    value_book,
)
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_trade", "workspace_trade"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_trade", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_trade", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_trade", "x-user-roles": "viewer"}
API = "/api/v1/finance"
client = TestClient(main.app)
TODAY = today_in(FinanceSettings())


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()


def _portfolio(headers: dict[str, str] = MEMBER, **extra: object) -> dict:
    body = {"name": "Practice", "mode": "paper", "starting_cash": [{"currency": "USD", "amount": "10000"}], "fee_bps": "10", "fee_min": "1", "slippage_bps": "5"}
    body.update(extra)
    response = client.post(f"{API}/paper/portfolios", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _price(symbol: str = "EXMPL", price: str = "100", currency: str = "USD", as_of: date = TODAY, headers: dict[str, str] = MEMBER) -> dict:
    response = client.post(f"{API}/prices", json={"symbol": symbol, "currency": currency, "price": price, "as_of": as_of.isoformat()}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _intent(portfolio_id: str, headers: dict[str, str] = MEMBER, **extra: object) -> tuple[int, dict]:
    body = {"mode": "paper", "portfolio_id": portfolio_id, "symbol": "EXMPL", "currency": "USD", "side": "buy", "quantity": "10"}
    body.update(extra)
    response = client.post(f"{API}/execution/intents", json=body, headers=headers)
    return response.status_code, response.json()


def _approve(intent: dict, headers: dict[str, str] = MEMBER, payload_hash: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/execution/intents/{intent['id']}/approve", json={"payload_hash": payload_hash or intent["payload_hash"]}, headers=headers)


def _audits(prefix: str) -> list:
    return [r for r in memory_repository.store.audit_records if r.action.startswith(prefix)]


# -- stage 6: the pure simulation ---------------------------------------------------------------------


def test_slippage_moves_the_fill_against_the_order_and_the_fee_has_a_floor() -> None:
    model = FeeModel(fee_bps=Decimal("10"), fee_min=Decimal("1"), slippage_bps=Decimal("5"))
    point = PricePoint(price=Decimal("100"), as_of=TODAY, source="manual")
    buy = fill_at(point, Side.BUY, Decimal("10"), "USD", model)
    sell = fill_at(point, Side.SELL, Decimal("10"), "USD", model)
    assert buy.fill_price == Decimal("100.0500000000") and sell.fill_price == Decimal("99.9500000000")
    assert buy.notional == Decimal("1000.50") and buy.fee == Decimal("1.00") and buy.slippage == Decimal("0.50")
    small = fill_at(point, Side.BUY, Decimal("0.1"), "USD", model)
    assert small.fee == Decimal("1.00")  # 0.10 percent of 10.01 is below the 1.00 floor
    jpy = fill_at(PricePoint(price=Decimal("150.3"), as_of=TODAY, source="manual"), Side.BUY, Decimal("3"), "JPY", model)
    assert jpy.notional == Decimal("451") and jpy.notional.as_tuple().exponent == 0  # rounded once, to yen
    assert limit_met(buy, Side.BUY, Decimal("100.05")) and not limit_met(buy, Side.BUY, Decimal("100"))
    assert limit_met(sell, Side.SELL, Decimal("99.9")) and not limit_met(sell, Side.SELL, Decimal("100"))
    assert adjusted_price(Decimal("100"), Side.BUY, Decimal(0)) == Decimal("100")


def _order(**values: object) -> PaperOrder:
    base = dict(
        id="o1", tenant_id="t", workspace_id="w", owner_user_id="u", portfolio_id="p", intent_id="i1", epoch=1, mode=PortfolioMode.PAPER,
        instrument_kind=InstrumentKind.SECURITY, symbol="EXMPL", currency="USD", side=Side.BUY, order_type=OrderType.MARKET,
        quantity=Decimal("10"), status=OrderStatus.FILLED, fill_price=Decimal("100"), notional=Decimal("1000.00"), fee=Decimal("1.00"),
        slippage=Decimal("0"), created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), filled_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    base.update(values)
    return PaperOrder.model_validate(base)


def test_replay_uses_average_cost_for_realised_pnl_and_keeps_cash_exact() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    portfolio = Portfolio(id="p", tenant_id="t", workspace_id="w", owner_user_id="u", name="P", mode=PortfolioMode.PAPER,
                          starting_cash=[{"currency": "USD", "amount": Decimal("5000")}], created_at=now, updated_at=now)
    orders = [
        _order(id="a", intent_id="a", fill_price=Decimal("100"), notional=Decimal("1000.00"), fee=Decimal("1.00")),
        _order(id="b", intent_id="b", fill_price=Decimal("120"), notional=Decimal("1200.00"), fee=Decimal("1.00"), filled_at=now + timedelta(hours=1)),
        _order(id="c", intent_id="c", side=Side.SELL, quantity=Decimal("5"), fill_price=Decimal("130"), notional=Decimal("650.00"), fee=Decimal("1.00"),
               filled_at=now + timedelta(hours=2)),
        _order(id="d", intent_id="d", status=OrderStatus.REFUSED, notional=None, fee=None, fill_price=None),
        _order(id="e", intent_id="e", epoch=0),  # an earlier run: not in this book
    ]
    book = replay(portfolio, orders)
    # Bought 20 for 2202.00 (avg 110.10); sold 5 for 650 - 1 = 649: cost removed 550.50, realised 98.50.
    assert book.cash == {"USD": Decimal("5000") - Decimal("2202.00") + Decimal("649.00")}
    assert book.realised == {"USD": Decimal("98.50")}
    position = book.positions[("security", "EXMPL", "USD")]
    assert position.quantity == Decimal("15") and position.cost == Decimal("1651.50")
    prices = [PriceRecord(id="p1", tenant_id="t", workspace_id="w", owner_user_id="u", symbol="EXMPL", currency="USD", price=Decimal("110"),
                          as_of=date(2026, 10, 1), recorded_at=now)]
    valuation = value_book(book, prices, [], date(2026, 10, 2), 3)
    assert valuation.positions[0].market_value == Decimal("1650.00") and valuation.unrealised[0].amount == Decimal("-1.50")
    stale = value_book(book, prices, [], date(2026, 10, 9), 3)
    assert stale.unpriced == ["EXMPL"] and stale.unrealised == [] and stale.positions[0].market_value is None


def test_prices_are_never_invented_and_fx_uses_recorded_rates() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [PriceRecord(id=f"p{i}", tenant_id="t", workspace_id="w", owner_user_id="u", symbol="EXMPL", currency="USD", price=Decimal(p),
                        as_of=d, recorded_at=now) for i, (p, d) in enumerate([("90", date(2026, 9, 1)), ("100", date(2026, 10, 1))])]
    assert price_for(InstrumentKind.SECURITY, "EXMPL", "USD", rows, [], date(2026, 10, 2), 3).price == Decimal("100")
    assert price_for(InstrumentKind.SECURITY, "EXMPL", "EUR", rows, [], date(2026, 10, 2), 3) is None
    assert price_for(InstrumentKind.SECURITY, "OTHER", "USD", rows, [], date(2026, 10, 2), 3) is None
    assert price_for(InstrumentKind.SECURITY, "EXMPL", "USD", rows, [], date(2026, 10, 9), 3) is None  # too old
    assert price_for(InstrumentKind.SECURITY, "EXMPL", "USD", rows, [], date(2026, 9, 30), 60).price == Decimal("90")  # never a future price
    rate = FxRate(id="r", tenant_id="t", workspace_id="w", owner_user_id="u", base="EUR", quote="USD", rate=Decimal("1.1"), as_of=date(2026, 10, 1),
                  source="European Central Bank euro reference rates", fetched_at=now)
    aed = FxRate(**{**rate.model_dump(), "id": "r2", "quote": "AED", "rate": Decimal("4.03975"), "source": "ECB EUR/USD x CBUAE peg 3.6725 AED per USD"})
    usd_in_aed = price_for(InstrumentKind.FX, "USD", "AED", [], [rate, aed], date(2026, 10, 2), 3)
    assert usd_in_aed is not None and usd_in_aed.price == Decimal("3.6725000000") and usd_in_aed.via == "EUR"
    assert price_for(InstrumentKind.FX, "EUR", "USD", [], [rate], date(2026, 10, 2), 3).price == Decimal("1.1")
    assert price_for(InstrumentKind.FX, "JPY", "USD", [], [rate], date(2026, 10, 2), 3) is None
    assert price_for(InstrumentKind.FX, "USD", "USD", [], [rate], date(2026, 10, 2), 3) is None


def test_shadow_compares_each_order_with_the_newest_price() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    orders = [_order(mode=PortfolioMode.SHADOW), _order(id="s", intent_id="s", side=Side.SELL, quantity=Decimal("2"), fill_price=Decimal("100"), fee=Decimal("1.00"))]
    prices = [PriceRecord(id="p", tenant_id="t", workspace_id="w", owner_user_id="u", symbol="EXMPL", currency="USD", price=Decimal("104"),
                          as_of=date(2026, 10, 5), recorded_at=now)]
    entries, totals = compare_shadow(orders, prices, [], date(2026, 10, 5), 3)
    by_side = {e.side: e for e in entries}
    assert by_side[Side.BUY].result == Decimal("39.00") and by_side[Side.SELL].result == Decimal("-9.00") and by_side[Side.BUY].days == 4
    assert totals[0].amount == Decimal("30.00")


def test_the_gate_lets_rules_decide_for_simulated_orders_and_denies_real_ones() -> None:
    at = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    action = GuardianAction(kind=ActionKind.TRADE, amount=Decimal("100"), currency="USD", merchant="EXMPL", at=at)
    assert gate(action, GuardianRules(), LedgerState()).reasons[-1].code == "execution_not_available"
    simulated = gate(action, GuardianRules(), LedgerState(), simulated=True)
    assert simulated.outcome == "allow" and simulated.reasons[-1].code == "simulated_only"
    blocked = gate(action, GuardianRules(blocked_merchants=["exmpl"]), LedgerState(), simulated=True)
    assert blocked.outcome == "deny" and blocked.reasons[0].code == "merchant_blocked"
    agent = gate(action.model_copy(update={"requested_by": "agent"}), GuardianRules(), LedgerState(), simulated=True)
    assert agent.outcome == "needs_approval"


# -- stage 6 and 7 through the API ---------------------------------------------------------------------


def test_a_paper_order_runs_only_after_the_person_approves_the_code_shown() -> None:
    portfolio = _portfolio()
    assert portfolio["simulated"] and portfolio["cash"] == [{"currency": "USD", "amount": "10000.00"}] and "SIMULATED" in portfolio["note"]
    _price(price="100")
    status, intent = _intent(portfolio["id"])
    assert status == 201 and intent["status"] == "pending_approval" and intent["simulated"]
    assert intent["estimate"]["fill_price"] == "100.0500000000" and intent["estimate"]["fee"] == "1.00" and intent["estimate"]["notional"] == "1000.50"
    assert intent["decision"]["outcome"] == "allow" and intent["decision"]["reasons"][-1]["code"] == "simulated_only"
    # Nothing has run yet.
    assert client.get(f"{API}/paper/portfolios/{portfolio['id']}/orders", headers=MEMBER).json()["total"] == 0
    wrong = _approve(intent, payload_hash="0" * 64)
    assert wrong.status_code == 409 and "does not match" in wrong.json()["error"]["message"]
    done = _approve(intent)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed" and done.json()["result"]["order_status"] == "filled"
    again = _approve(intent)
    assert again.status_code == 409 and "cannot be approved" in again.json()["error"]["message"]
    view = client.get(f"{API}/paper/portfolios/{portfolio['id']}", headers=MEMBER).json()
    assert view["cash"] == [{"currency": "USD", "amount": "8998.50"}]
    holding = view["holdings"][0]
    assert holding["quantity"] == "10.00000000" and holding["market_value"] == "1000.00" and holding["unrealised"] == "-1.50"
    assert view["fees"] == [{"currency": "USD", "amount": "1.00"}] and view["slippage"] == [{"currency": "USD", "amount": "0.50"}]
    orders = client.get(f"{API}/paper/portfolios/{portfolio['id']}/orders", headers=MEMBER).json()
    assert orders["total"] == 1 and orders["items"][0]["status"] == "filled" and orders["items"][0]["simulated"]
    # Audits carry no amount, price, quantity or symbol.
    records = _audits("finance.exec.") + _audits("finance.paper.") + _audits("finance.price.")
    assert records and all(not ({"amount", "price", "quantity", "symbol", "notional"} & set(r.metadata)) for r in records)
    assert all("EXMPL" not in str(r.metadata) and "1000" not in str(r.metadata) for r in records)


def test_rules_block_simulated_orders_and_the_person_sees_why() -> None:
    portfolio = _portfolio()
    _price(price="100")
    change = client.post(f"{API}/guardian/changes", json={"rules": {"blocked_merchants": ["exmpl"], "max_single": [{"currency": "USD", "amount": "500"}]}}, headers=MEMBER).json()
    client.post(f"{API}/guardian/changes/{change['id']}/confirm", json={"payload_hash": change["payload_hash"]}, headers=MEMBER)
    status, intent = _intent(portfolio["id"])
    assert status == 201 and intent["status"] == "denied"
    assert [r["code"] for r in intent["decision"]["reasons"] if r["outcome"] == "deny"] == ["merchant_blocked", "over_single_limit"]
    assert _approve(intent).status_code == 409
    assert client.get(f"{API}/paper/portfolios/{portfolio['id']}/orders", headers=MEMBER).json()["total"] == 0


def test_rules_that_change_after_the_request_are_checked_again_at_approval() -> None:
    portfolio = _portfolio()
    _price(price="100")
    _, intent = _intent(portfolio["id"])
    change = client.post(f"{API}/guardian/changes", json={"rules": {"blocked_merchants": ["exmpl"]}}, headers=MEMBER).json()
    client.post(f"{API}/guardian/changes/{change['id']}/confirm", json={"payload_hash": change["payload_hash"]}, headers=MEMBER)
    refused = _approve(intent)
    assert refused.status_code == 409 and "Your rules block this now" in refused.json()["error"]["message"]
    assert client.get(f"{API}/execution/intents/{intent['id']}", headers=MEMBER).json()["status"] == "denied"
    assert any(r.outcome == "denied" and list(r.metadata.get("guardian_codes", ())) == ["merchant_blocked"] for r in _audits("finance.exec.intent.approve"))


def test_no_recorded_price_refuses_a_market_order_and_a_limit_order_waits() -> None:
    portfolio = _portfolio()
    status, market = _intent(portfolio["id"])
    assert status == 201 and market["status"] == "refused" and market["refusal_code"] == "no_price" and "Record a price" in market["refusal"]
    _, limit = _intent(portfolio["id"], order_type="limit", limit_price="95")
    assert limit["status"] == "pending_approval" and limit["estimate"] is None
    assert _approve(limit).json()["result"]["order_status"] == "waiting"
    view = client.get(f"{API}/paper/portfolios/{portfolio['id']}", headers=MEMBER).json()
    assert view["waiting"] == 1 and view["cash"][0]["amount"] == "10000.00"
    saved = _price(price="99")  # 99 x 1.0005 = 99.0495: above the limit, still waiting
    assert saved["orders_changed"] == 0
    saved = _price(price="94", as_of=TODAY - timedelta(days=1), symbol="EXMPL")
    # The newest price (today's 99) is what counts: still waiting.
    assert saved["orders_changed"] == 0
    client.delete(f"{API}/prices/{client.get(f'{API}/prices?symbol=EXMPL', headers=MEMBER).json()[0]['id']}", headers=MEMBER)
    checked = client.post(f"{API}/paper/portfolios/{portfolio['id']}/check", headers=MEMBER).json()
    assert checked["waiting"] == 0 and checked["holdings"][0]["quantity"] == "10.00000000"


def test_paper_cash_and_units_are_checked_and_a_reset_starts_a_new_run() -> None:
    portfolio = _portfolio(starting_cash=[{"currency": "USD", "amount": "500"}])
    _price(price="100")
    _, big = _intent(portfolio["id"])
    order = _approve(big).json()["result"]
    assert order["order_status"] == "refused" and order["reason_code"] == "insufficient_cash"
    _, sell = _intent(portfolio["id"], side="sell", quantity="1")
    assert _approve(sell).json()["result"]["reason_code"] == "insufficient_units"
    _, small = _intent(portfolio["id"], quantity="2")
    assert _approve(small).json()["result"]["order_status"] == "filled"
    reset = client.post(f"{API}/paper/portfolios/{portfolio['id']}/reset", json={"starting_cash": [{"currency": "USD", "amount": "750"}]}, headers=MEMBER).json()
    assert reset["epoch"] == 2 and reset["cash"] == [{"currency": "USD", "amount": "750.00"}] and reset["holdings"] == []
    current = client.get(f"{API}/paper/portfolios/{portfolio['id']}/orders", headers=MEMBER).json()
    every = client.get(f"{API}/paper/portfolios/{portfolio['id']}/orders?run=all", headers=MEMBER).json()
    assert current["total"] == 0 and every["total"] == 3


def test_waiting_orders_can_be_cancelled_and_shadow_portfolios_hold_no_cash() -> None:
    paper = _portfolio()
    _, limit = _intent(paper["id"], order_type="limit", limit_price="10")
    order_id = _approve(limit).json()["result"]["order_id"]
    cancelled = client.post(f"{API}/paper/orders/{order_id}/cancel", json={}, headers=MEMBER)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.post(f"{API}/paper/orders/{order_id}/cancel", json={}, headers=MEMBER).status_code == 409
    refused = client.post(f"{API}/paper/portfolios", json={"name": "Mirror", "mode": "shadow", "starting_cash": [{"currency": "USD", "amount": "1"}]}, headers=MEMBER)
    assert refused.status_code == 422
    shadow = _portfolio(name="Mirror", mode="shadow", starting_cash=[])
    _price(price="100")
    status, wrong_mode = _intent(shadow["id"])
    assert status == 422 and "shadow portfolio" in wrong_mode["error"]["message"]
    _, intent = _intent(shadow["id"], mode="shadow", quantity="1000")
    assert _approve(intent).json()["result"]["order_status"] == "filled"  # no cash limit in shadow
    _price(price="110", as_of=TODAY, symbol="EXMPL", currency="EUR")
    view = client.get(f"{API}/paper/portfolios/{shadow['id']}", headers=MEMBER).json()
    assert view["cash"] == [] and len(view["shadow"]) == 1 and view["shadow"][0]["result"] is not None


def test_idempotency_keys_return_the_same_request_and_refuse_a_different_one() -> None:
    portfolio = _portfolio()
    _price()
    _, first = _intent(portfolio["id"], idempotency_key="example-key-1")
    _, same = _intent(portfolio["id"], idempotency_key="example-key-1")
    assert same["id"] == first["id"]
    status, other = _intent(portfolio["id"], idempotency_key="example-key-1", quantity="11")
    assert status == 409 and "different request" in other["error"]["message"]


def test_approvals_expire_and_bind_every_field_of_the_payload() -> None:
    portfolio = _portfolio()
    _price()
    _, intent = _intent(portfolio["id"])
    stored = finance_store.intents[intent["id"]]
    for field, value in (("quantity", Decimal("11")), ("symbol", "OTHER"), ("currency", "EUR"), ("side", Side.SELL),
                         ("portfolio_id", "fin_port_other"), ("limit_price", Decimal("1")), ("account_id", "acct"), ("mode", ExecMode.SHADOW)):
        changed = stored.model_copy(update={field: value})
        assert gateway.intent_hash(changed) != stored.payload_hash, field
    # A change to the stored request (here the quantity) invalidates the approval shown.
    finance_store.intents[intent["id"]] = stored.model_copy(update={"quantity": Decimal("11")})
    assert _approve(intent).status_code == 409
    finance_store.intents[intent["id"]] = stored.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    lapsed = _approve(intent)
    assert lapsed.status_code == 409 and "expired" in lapsed.json()["error"]["message"]
    assert client.get(f"{API}/paper/portfolios/{portfolio['id']}/orders", headers=MEMBER).json()["total"] == 0


def test_only_the_owner_sees_and_approves_and_viewers_are_denied() -> None:
    portfolio = _portfolio()
    _price()
    _, intent = _intent(portfolio["id"])
    other = _approve(intent, headers=OWNER)  # a workspace owner, not this person
    assert other.status_code == 404
    assert client.get(f"{API}/paper/portfolios", headers=OWNER).json() == []
    for path in ("/paper/portfolios", "/execution/status", "/execution/intents", "/execution/live", "/prices"):
        denied = client.get(f"{API}{path}", headers=VIEWER)
        assert denied.status_code == 403 and "viewer to member" in denied.json()["error"]["message"]
    assert client.post(f"{API}/paper/portfolios", json={"name": "x"}, headers=VIEWER).status_code == 403


def test_kill_switches_stop_every_executor_and_are_audited() -> None:
    portfolio = _portfolio()
    _price()
    _, intent = _intent(portfolio["id"])
    on = client.put(f"{API}/execution/kill-switch", json={"scope": "person", "engaged": True, "reason": "pause"}, headers=MEMBER)
    assert on.status_code == 200 and on.json()["person_switch"]["engaged"]
    stopped = _approve(intent)
    assert stopped.status_code == 409 and "kill switch" in stopped.json()["error"]["message"]
    assert _intent(portfolio["id"])[0] == 409
    client.put(f"{API}/execution/kill-switch", json={"scope": "person", "engaged": False}, headers=MEMBER)
    # A member cannot change the organisation's switch; an owner can, and it stops everyone.
    denied = client.put(f"{API}/execution/kill-switch", json={"scope": "global", "engaged": True}, headers=MEMBER)
    assert denied.status_code == 403 and "organisation administrators" in denied.json()["error"]["message"]
    assert client.get(f"{API}/execution/status", headers=MEMBER).json()["global_switch"]["can_change"] is False
    assert client.put(f"{API}/execution/kill-switch", json={"scope": "global", "engaged": True}, headers=OWNER).status_code == 200
    status = client.get(f"{API}/execution/status", headers=MEMBER).json()
    assert status["global_switch"]["engaged"] and not status["global_switch"]["changed_by_you"]
    assert _approve(intent).status_code == 409
    client.put(f"{API}/execution/kill-switch", json={"scope": "global", "engaged": False}, headers=OWNER)
    assert _approve(intent).json()["status"] == "executed"
    actions = [r.action for r in _audits("finance.exec.kill_switch")]
    assert actions.count("finance.exec.kill_switch.person") == 2 and actions.count("finance.exec.kill_switch.global") == 2


# -- stage 8: live is refused --------------------------------------------------------------------------


def test_live_execution_is_refused_whatever_is_asked() -> None:
    portfolio = _portfolio()
    _price()
    for body in (
        {"mode": "live", "kind": "trade", "portfolio_id": portfolio["id"], "symbol": "EXMPL", "side": "buy", "quantity": "1"},
        {"mode": "live", "kind": "transfer", "amount": "10", "currency": "AED", "account_id": "acct"},
        {"mode": "live", "kind": "spend", "amount": "10"},
        {"mode": "live", "kind": "bill_payment", "amount": "10"},
    ):
        refused = client.post(f"{API}/execution/intents", json=body, headers=MEMBER)
        assert refused.status_code == 409 and "Live execution is not enabled" in refused.json()["error"]["message"]
    assert all(r.outcome == "denied" and r.metadata["code"] == "live_not_enabled" and "deployment_flag" in r.metadata["unmet"]
               for r in _audits("finance.exec.intent.refused"))
    assert client.get(f"{API}/execution/intents?mode=live", headers=MEMBER).json()["total"] == 0
    view = client.get(f"{API}/execution/live", headers=MEMBER).json()
    assert view["enabled"] is False and len(view["owner_steps"]) == 5
    met = {p["code"]: p["met"] for p in view["preconditions"]}
    assert met["deployment_flag"] is False and met["live_executor"] is False and met["write_connector"] is False
    status = client.get(f"{API}/execution/status", headers=MEMBER).json()
    assert {e["mode"]: e["available"] for e in status["executors"]} == {"paper": True, "shadow": True, "live": False}
    assert status["live_enabled"] is False


def test_the_live_flag_is_refused_at_startup_while_no_live_executor_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    assert FinanceConfig().live_enabled is False and live.LIVE_EXECUTORS == {}
    with pytest.raises(ValidationError, match="ANUM_FINANCE_LIVE_ENABLED must stay false"):
        FinanceConfig(live_enabled=True)
    monkeypatch.setenv("ANUM_FINANCE_LIVE_ENABLED", "true")
    with pytest.raises(ValidationError, match="no live executor"):
        FinanceConfig()


def test_live_limits_need_the_persons_approval_and_opting_in_is_refused_while_live_is_off() -> None:
    limits = {"max_per_action": [{"currency": "AED", "amount": "100"}], "max_per_day": [{"currency": "AED", "amount": "200"}],
              "max_per_month": [{"currency": "AED", "amount": "1000"}], "currencies": ["AED"], "instruments": ["exmpl"], "cooling_off_hours": 24}
    opt_in = client.post(f"{API}/execution/live/changes", json={"limits": limits, "opt_in": True}, headers=MEMBER)
    assert opt_in.status_code == 409 and "cannot opt in" in opt_in.json()["error"]["message"]
    bad = client.post(f"{API}/execution/live/changes", json={"limits": {**limits, "currencies": ["AEDX"]}}, headers=MEMBER)
    assert bad.status_code == 422
    change = client.post(f"{API}/execution/live/changes", json={"limits": limits}, headers=MEMBER).json()
    assert change["status"] == "pending_approval" and change["mode"] == "settings"
    assert client.get(f"{API}/execution/live", headers=MEMBER).json()["limits"]["max_per_action"] == []
    assert _approve(change, payload_hash="f" * 64).status_code == 409
    assert _approve(change).json()["status"] == "executed"
    view = client.get(f"{API}/execution/live", headers=MEMBER).json()
    assert view["version"] == 1 and view["limits"]["instruments"] == ["EXMPL"] and view["opt_in"] is False
    assert {p["code"]: p["met"] for p in view["preconditions"]}["limits"] is True
    assert client.post(f"{API}/execution/live/opt-out", headers=MEMBER).json()["opt_in"] is False


def test_even_with_the_flag_forced_on_and_an_opt_in_live_stays_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag cannot be turned on (startup refuses it); forced here, live still has no executor."""
    monkeypatch.setattr(finance_config, "live_enabled", True)
    limits = {"max_per_action": [{"currency": "AED", "amount": "100"}], "max_per_day": [{"currency": "AED", "amount": "200"}],
              "max_per_month": [{"currency": "AED", "amount": "1000"}], "currencies": ["AED"], "accounts": ["acct"]}
    change = client.post(f"{API}/execution/live/changes", json={"limits": limits, "opt_in": True}, headers=MEMBER).json()
    assert _approve(change).json()["status"] == "executed"
    view = client.get(f"{API}/execution/live", headers=MEMBER).json()
    met = {p["code"]: p["met"] for p in view["preconditions"]}
    assert view["opt_in"] and met["opt_in"] and met["limits"] and met["deployment_flag"]
    assert met["live_executor"] is False and met["write_connector"] is False and view["enabled"] is False
    refused = client.post(f"{API}/execution/intents", json={"mode": "live", "kind": "transfer", "amount": "10", "account_id": "acct"}, headers=MEMBER)
    assert refused.status_code == 409 and "Live execution is not enabled" in refused.json()["error"]["message"]


def test_live_limits_are_checked_per_action_day_month_and_list() -> None:
    limits = LiveLimits(max_per_action=[{"currency": "AED", "amount": "100"}], max_per_day=[{"currency": "AED", "amount": "150"}],
                        max_per_month=[{"currency": "AED", "amount": "500"}], currencies=["AED"], instruments=["EXMPL"], accounts=["acct"], cooling_off_hours=2)
    at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    ok = LiveAttempt(amount=Decimal("80"), currency="AED", instrument="exmpl", requested_at=at - timedelta(hours=3), at=at)
    assert check_live_limits(ok, limits, LiveSpent()) == [] and limits.is_set()
    codes = lambda attempt, spent=LiveSpent(): [r.code for r in check_live_limits(attempt, limits, spent)]  # noqa: E731
    assert codes(ok.model_copy(update={"amount": Decimal("101")})) == ["over_live_action"]
    assert codes(ok, LiveSpent(day={"AED": Decimal("80")})) == ["over_live_day"]
    assert codes(ok, LiveSpent(month={"AED": Decimal("450")})) == ["over_live_month"]
    assert codes(ok.model_copy(update={"currency": "USD"})) == ["currency_not_allowed", "over_live_action", "over_live_day", "over_live_month"]
    assert codes(ok.model_copy(update={"instrument": "OTHER"})) == ["instrument_not_allowed"]
    assert codes(ok.model_copy(update={"instrument": None, "account_id": "other"})) == ["account_not_allowed"]
    assert codes(ok.model_copy(update={"requested_at": at})) == ["live_cooling_off"]
    assert not LiveLimits().is_set()
    with pytest.raises(ValidationError):
        LiveLimits(max_per_action=[{"currency": "AED", "amount": "1"}, {"currency": "AED", "amount": "2"}])


# -- property-style: the gateway never executes without every condition ----------------------------------


def test_the_gateway_never_executes_without_allow_and_a_valid_unexpired_single_use_approval() -> None:
    """Random requests, random tampering and random timing: an order exists only when the request
    was pending, the hash shown matched the stored request, the approval had not expired, no kill
    switch was on and the gate did not deny; and never twice. Live is always refused."""
    rng = random.Random(20261014)
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_trade", roles=["member"])
    now = datetime.now(timezone.utc)
    portfolio_id = _portfolio()["id"]
    _price(price="100")
    _price(symbol="BLOCKED", price="100")
    change = client.post(f"{API}/guardian/changes", json={"rules": {"blocked_merchants": ["blocked"]}}, headers=MEMBER).json()
    client.post(f"{API}/guardian/changes/{change['id']}/confirm", json={"payload_hash": change["payload_hash"]}, headers=MEMBER)
    executed = 0
    for round_ in range(150):
        live_mode = rng.random() < 0.1
        symbol = rng.choice(["EXMPL", "EXMPL", "BLOCKED", "NOPRICE"])
        spec = gateway.TradeSpec(mode=ExecMode.LIVE if live_mode else ExecMode.PAPER, portfolio_id=portfolio_id, instrument_kind=InstrumentKind.SECURITY,
                                 symbol=symbol, currency="USD", side=Side.BUY, order_type=OrderType.MARKET, quantity=Decimal(rng.choice(["1", "2"])))
        with gateway_store() as store:
            try:
                intent = gateway.create_trade_intent(store, context, spec, requested_by=rng.choice(["person", "agent"]), proposer="test", now=now)
            except gateway.GatewayRefusal as exc:
                assert live_mode and exc.code == "live_not_enabled"
                continue
        assert not live_mode
        tamper = rng.choice(["none", "none", "hash", "quantity", "expired", "kill", "replay"])
        shown = intent.payload_hash
        if tamper == "hash":
            shown = "%064x" % rng.getrandbits(256)
        elif tamper == "quantity":
            finance_store.intents[intent.id] = finance_store.intents[intent.id].model_copy(update={"quantity": intent.quantity + 1})
        elif tamper == "expired":
            finance_store.intents[intent.id] = finance_store.intents[intent.id].model_copy(update={"expires_at": now - timedelta(seconds=1)})
        elif tamper == "kill":
            finance_store.exec_settings[(TENANT, WORKSPACE, "member_trade")] = gateway.ExecSettings(kill_switch=True)
        ran = False
        for attempt in range(2 if tamper == "replay" else 1):
            with gateway_store() as store:
                try:
                    gateway.approve(store, context, intent.id, shown, now)
                    ran = True
                    assert attempt == 0, "an approval was used twice"
                except gateway.GatewayRefusal:
                    pass
        finance_store.exec_settings.pop((TENANT, WORKSPACE, "member_trade"), None)
        valid = intent.status == IntentStatus.PENDING and tamper in {"none", "replay"} and symbol != "BLOCKED"
        assert ran == valid, (round_, tamper, symbol, intent.status)
        order = finance_store.orders and [o for o in finance_store.orders.values() if o.intent_id == intent.id]
        assert bool(order) == valid and (not order or len(order) == 1)
        executed += int(valid)
    assert executed > 20
    assert not [i for i in finance_store.intents.values() if i.mode == ExecMode.LIVE]


def gateway_store():  # type: ignore[no-untyped-def]
    from anum_api.finance.store import open_finance_store

    return open_finance_store(TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_trade", roles=["member"]))


# -- the assistant ---------------------------------------------------------------------------------------


def test_assistants_can_only_propose_simulated_orders_and_never_approve() -> None:
    portfolio = _portfolio()
    _price()
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_trade", roles=["member"])
    names = set(main.tool_registry.names)
    assert {"finance.paper.portfolios", "finance.paper.propose"} <= names
    # No tool approves, executes, turns live on, sets live limits or changes a kill switch; the one
    # live-related tool only proposes a wider tier (stage 9) for the person to approve.
    risky = [n for n in names if n.startswith("finance.") and any(w in n for w in ("approve", "execute", "live", "kill", "confirm", "trade"))]
    assert risky == ["finance.live.widening.propose"]
    result = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.paper.propose", arguments={"symbol": "exmpl", "side": "buy", "quantity": "3"}), context))
    assert result.status == "succeeded" and "SIMULATED" in result.summary and result.output["status"] == "pending_approval"
    intent = finance_store.intents[result.output["intent_id"]]
    assert intent.requested_by == "agent" and intent.portfolio_id == portfolio["id"]
    assert any(r.code == "agent_request" for r in gateway.Decision.model_validate(intent.decision).reasons)
    blocked = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.paper.propose", arguments={"mode": "live", "symbol": "EXMPL", "side": "buy", "quantity": "1"}), context))
    assert blocked.status in {"blocked", "failed"} and not finance_store.orders
    read = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.paper.portfolios", arguments={}), context))
    assert read.status == "succeeded" and read.output["simulated"] and "no promise of returns" in read.summary
    assert "guarantee" not in read.summary.lower()
    # Nothing ran: the person approves in Finance, Paper trading.
    assert not finance_store.orders


def test_the_planner_proposes_only_simulated_orders_and_leaves_real_ones_to_governed_actions() -> None:
    assert plan_call("paper buy 10 EXMPL") == ToolCall(name="finance.paper.propose", arguments={"mode": "paper", "side": "buy", "quantity": "10", "symbol": "EXMPL"})
    assert plan_call("shadow sell 2.5 EXMPL").arguments["mode"] == "shadow"
    assert plan_call("اشتر 10 EXMPL تجريبي").name == "finance.paper.propose"
    assert plan_call("show my paper portfolio").name == "finance.paper.portfolios"
    assert plan_call("المحفظة التجريبية").name == "finance.paper.portfolios"
    for real in ("buy 10 EXMPL", "sell my EXMPL shares", "transfer 500 to the landlord"):
        assert plan_call(real) is None
    skills = default_skill_registry()
    tools = set(main.tool_registry.names)
    assert choose_tool_call("paper buy 10 EXMPL", skills.select("paper buy 10 EXMPL", tools), "", tools).name == "finance.paper.propose"
    assert chat_intent("how is my paper portfolio doing", skills, tools) == "finance"
