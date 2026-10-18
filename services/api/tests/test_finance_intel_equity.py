"""SIMULATED P&L and drawdown over time (docs/finance-trading-intel.md#pl-and-drawdown-over-time).

In-memory store. Fixtures are plainly fake: example symbols and round prices typed by the test.
The pure curve is checked by hand-worked numbers; the route is checked against the overview after
an approved paper order, for privacy, permissions, bounds and for writing nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import equity
from anum_api.finance.models import FxRate
from anum_api.finance.money import quantize
from anum_api.finance.store import finance_store
from anum_api.finance.summary import FinanceSettings, today_in
from anum_api.finance.trading import (
    CashLine,
    InstrumentKind,
    OrderStatus,
    OrderType,
    PaperOrder,
    Portfolio,
    PortfolioMode,
    PriceRecord,
    Side,
)

TENANT, WORKSPACE, USER = "tenant_eq", "workspace_eq", "member_eq"
MEMBER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": USER, "x-user-roles": "member"}
OTHER = {**MEMBER, "x-user-id": "other_eq"}
OTHER_TENANT = {**MEMBER, "x-tenant-id": "tenant_eq_b", "x-workspace-id": "workspace_eq_b"}
VIEWER = {**MEMBER, "x-user-id": "viewer_eq", "x-user-roles": "viewer"}
API = "/api/v1/finance"
client = TestClient(main.app)
UTC = ZoneInfo("UTC")
D1 = date(2026, 9, 1)
OWNED = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "owner_user_id": USER}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _portfolio(mode: PortfolioMode = PortfolioMode.PAPER, start: date = D1, age: int = 1, pid: str = "port_eq") -> Portfolio:
    cash = [{"currency": "USD", "amount": Decimal(1000)}] if mode == PortfolioMode.PAPER else []
    return Portfolio(id=pid, name="Practice", mode=mode, starting_cash=cash, fee_bps=Decimal(0), fee_min=Decimal(1), slippage_bps=Decimal(0),
                     max_price_age_days=age, created_at=_at(start, 8), updated_at=_at(start, 8), **OWNED)


def _order(day: date, side: Side, qty: str, price: str, *, symbol: str = "EXMPL", currency: str = "USD", oid: str = "o1",
           mode: PortfolioMode = PortfolioMode.PAPER, status: OrderStatus = OrderStatus.FILLED, epoch: int = 1, pid: str = "port_eq") -> PaperOrder:
    notional = Decimal(qty) * Decimal(price)
    return PaperOrder(
        id=oid, portfolio_id=pid, intent_id=f"intent_{oid}", epoch=epoch, mode=mode, instrument_kind=InstrumentKind.SECURITY if symbol != "EUR" else InstrumentKind.FX,
        symbol=symbol, currency=currency, side=side, order_type=OrderType.MARKET, quantity=Decimal(qty), status=status, market_price=Decimal(price),
        fill_price=Decimal(price), price_as_of=day, price_source="manual", notional=notional, fee=Decimal(1), slippage=Decimal(0),
        created_at=_at(day), filled_at=_at(day) if status == OrderStatus.FILLED else None, updated_at=_at(day), **OWNED,
    )


def _price(day: date, price: str, symbol: str = "EXMPL", currency: str = "USD") -> PriceRecord:
    return PriceRecord(id=f"px_{symbol}_{currency}_{day}", symbol=symbol, currency=currency, price=Decimal(price), as_of=day, recorded_at=_at(day, 20), **OWNED)


def _day(n: int) -> date:
    return D1 + timedelta(days=n - 1)


def test_paper_curve_values_pnl_drawdown_and_gaps_by_hand() -> None:
    portfolio = _portfolio()
    orders = [_order(D1, Side.BUY, "10", "50")]
    # Day 5 has no price fresh enough (age 1): a gap. Day 7's price must not be used before day 7.
    prices = [_price(_day(1), "50"), _price(_day(2), "60"), _price(_day(3), "40"), _price(_day(6), "55"), _price(_day(7), "1000")]
    [usd] = equity.equity_curves(portfolio, orders, prices, [], _day(1), _day(6), UTC)
    assert usd.currency == "USD"
    values = [p.value for p in usd.points]
    assert values == [Decimal("999.00"), Decimal("1099.00"), Decimal("899.00"), Decimal("899.00"), None, Decimal("1049.00")]
    assert [p.pnl for p in usd.points] == [Decimal("-1.00"), Decimal("99.00"), Decimal("-101.00"), Decimal("-101.00"), None, Decimal("49.00")]
    assert [p.complete for p in usd.points] == [True, True, True, True, False, True]
    assert usd.points[2].drawdown == Decimal("-200.00") and usd.points[2].drawdown_pct == pytest.approx(-200 / 1099)
    assert usd.points[5].drawdown == Decimal("-50.00")
    assert usd.max_drawdown == Decimal("-200.00") and usd.max_drawdown_pct == pytest.approx(-200 / 1099)
    assert usd.peak_day == _day(2) and usd.trough_day == _day(3) and usd.gaps == 1 and usd.unpriced == ["EXMPL"]


def test_curve_matches_replay_after_a_sale_and_ignores_other_runs_and_unfilled_orders() -> None:
    portfolio = _portfolio().model_copy(update={"epoch": 2})
    orders = [
        _order(_day(1), Side.BUY, "10", "50", oid="a", epoch=2), _order(_day(2), Side.SELL, "4", "60", oid="b", epoch=2),
        _order(_day(1), Side.BUY, "99", "50", oid="old", epoch=1), _order(_day(2), Side.BUY, "5", "50", oid="w", epoch=2, status=OrderStatus.WAITING),
    ]
    prices = [_price(_day(1), "50"), _price(_day(2), "60"), _price(_day(3), "70")]
    [usd] = equity.equity_curves(portfolio, orders, prices, [], _day(1), _day(3), UTC)
    # Sold 4 of 10 (cost 501): removed 200.40, proceeds 239, realised 38.60; 6 left cost 300.60.
    last = usd.points[-1]
    assert last.value == Decimal(1000) - 501 + 239 + 6 * 70
    assert last.pnl == Decimal("38.60") + (420 - Decimal("300.60"))
    assert usd.max_drawdown == 0 and usd.max_drawdown_pct == 0.0 and usd.peak_day is None


def test_shadow_curve_has_no_cash_and_measures_drawdown_on_pnl() -> None:
    portfolio = _portfolio(PortfolioMode.SHADOW)
    orders = [_order(_day(1), Side.BUY, "10", "50", mode=PortfolioMode.SHADOW)]
    prices = [_price(_day(1), "50"), _price(_day(2), "55"), _price(_day(3), "45")]
    [usd] = equity.equity_curves(portfolio, orders, prices, [], _day(1), _day(3), UTC)
    assert [p.value for p in usd.points] == [None, None, None]
    assert [p.pnl for p in usd.points] == [Decimal("-1.00"), Decimal("49.00"), Decimal("-51.00")]
    assert usd.max_drawdown == Decimal("-100.00") and usd.max_drawdown_pct is None and all(p.drawdown_pct is None for p in usd.points)


def test_currencies_are_never_mixed_and_fx_holdings_use_recorded_rates() -> None:
    portfolio = _portfolio().model_copy(update={"starting_cash": [CashLine(currency="USD", amount=Decimal(1000)), CashLine(currency="AED", amount=Decimal(500))]})
    orders = [_order(_day(1), Side.BUY, "100", "1.1", symbol="EUR", oid="fx")]
    rates = [FxRate(id=f"r{n}", base="EUR", quote="USD", rate=Decimal(r), as_of=_day(n), source="manual", fetched_at=_at(_day(n)), **OWNED)
             for n, r in ((1, "1.1"), (2, "1.2"), (4, "9"))]
    aed, usd = equity.equity_curves(portfolio, orders, [], rates, _day(1), _day(3), UTC)
    assert aed.currency == "AED" and [p.value for p in aed.points] == [Decimal("500.00")] * 3
    assert usd.points[1].value == Decimal(1000) - 111 + 120
    assert usd.points[2].value == usd.points[1].value  # day 3 reads day 2's rate (age 1); day 4's rate is never used early


def test_window_starts_at_the_run_and_is_bounded() -> None:
    portfolio = _portfolio(start=date(2026, 1, 1))
    assert equity.curve_window(portfolio, [], date(2026, 1, 10), 180, UTC) == (date(2026, 1, 1), date(2026, 1, 10))
    assert equity.curve_window(portfolio, [], date(2026, 6, 30), 30, UTC) == (date(2026, 6, 1), date(2026, 6, 30))
    assert equity.curve_window(portfolio, [], date(2028, 1, 1), 10_000, UTC)[0] == date(2028, 1, 1) - timedelta(days=equity.MAX_DAYS - 1)


# -- the route -----------------------------------------------------------------------------------------------


def _api_portfolio(headers: dict[str, str] = MEMBER, mode: str = "paper") -> dict:
    body = {"name": "Practice", "mode": mode, "fee_bps": "10", "fee_min": "1", "slippage_bps": "5"}
    if mode == "paper":
        body["starting_cash"] = [{"currency": "USD", "amount": "10000"}]
    response = client.post(f"{API}/paper/portfolios", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _api_buy(portfolio_id: str, mode: str = "paper") -> None:
    today = today_in(FinanceSettings()).isoformat()
    client.post(f"{API}/prices", json={"symbol": "EXMPL", "currency": "USD", "price": "100", "as_of": today}, headers=MEMBER)
    intent = client.post(f"{API}/execution/intents", json={"mode": mode, "portfolio_id": portfolio_id, "symbol": "EXMPL", "currency": "USD",
                                                           "side": "buy", "quantity": "10"}, headers=MEMBER).json()
    approved = client.post(f"{API}/execution/intents/{intent['id']}/approve", json={"payload_hash": intent["payload_hash"]}, headers=MEMBER)
    assert approved.status_code == 200, approved.text


def test_route_matches_the_overview_after_an_approved_paper_order() -> None:
    portfolio = _api_portfolio()
    _api_buy(portfolio["id"])
    before = len(memory_repository.store.audit_records)
    response = client.get(f"{API}/intel/portfolios/{portfolio['id']}/equity", headers=MEMBER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulated"] is True and body["mode"] == "paper" and body["orders"] == 1 and body["days"] == 1 and "SIMULATED" in body["note"]
    assert "promise" in body["note"] and body["prices_truncated"] is False
    [usd] = body["series"]
    [point] = usd["points"]
    item = client.get(f"{API}/intel/overview", headers=MEMBER).json()["portfolios"][0]
    holding = item["holdings"][0]
    assert Decimal(point["value"]) == Decimal(item["cash"][0]["amount"]) + Decimal(holding["market_value"])
    assert Decimal(point["pnl"]) == Decimal(item["unrealised"][0]["amount"]) and point["complete"] is True
    assert Decimal(usd["max_drawdown"]) == 0
    assert len(memory_repository.store.audit_records) == before


def test_route_reads_stored_history_and_clips_the_window() -> None:
    today = today_in(FinanceSettings())
    start = today - timedelta(days=9)
    finance_store.save_portfolio(_portfolio(start=start, age=3))
    finance_store.add_order(_order(start, Side.BUY, "10", "50"))
    for n, price in enumerate(("50", "60", "45", "55")):
        finance_store.save_price(_price(start + timedelta(days=n * 3), price))
    body = client.get(f"{API}/intel/portfolios/port_eq/equity?days=5", headers=MEMBER).json()
    assert body["days"] == 5 and body["start"] == (today - timedelta(days=4)).isoformat() and body["end"] == today.isoformat()
    full = client.get(f"{API}/intel/portfolios/port_eq/equity", headers=MEMBER).json()
    [usd] = full["series"]
    assert full["days"] == 10 and usd["gaps"] == 0
    assert Decimal(usd["max_drawdown"]) == Decimal("-150.00") and usd["peak_day"] == (start + timedelta(days=3)).isoformat()


def test_shadow_route_has_no_value_only_pnl() -> None:
    portfolio = _api_portfolio(mode="shadow")
    _api_buy(portfolio["id"], mode="shadow")
    [usd] = client.get(f"{API}/intel/portfolios/{portfolio['id']}/equity", headers=MEMBER).json()["series"]
    assert usd["points"][0]["value"] is None and usd["points"][0]["pnl"] is not None and usd["max_drawdown_pct"] is None


def test_route_is_private_needs_finance_read_and_is_bounded() -> None:
    portfolio = _api_portfolio()
    path = f"{API}/intel/portfolios/{portfolio['id']}/equity"
    for headers in (OTHER, OTHER_TENANT):
        assert client.get(path, headers=headers).status_code == 404
    assert client.get(path, headers=VIEWER).status_code == 403
    assert client.get(f"{API}/intel/portfolios/nope/equity", headers=MEMBER).status_code == 404
    for days in (0, equity.MAX_DAYS + 1, -1):
        assert client.get(f"{path}?days={days}", headers=MEMBER).status_code == 422
    empty = client.get(path, headers=MEMBER).json()
    [usd] = empty["series"]
    assert empty["orders"] == 0 and Decimal(usd["points"][0]["value"]) == 10000 and Decimal(usd["points"][0]["pnl"]) == 0


# -- the buy and hold benchmark ----------------------------------------------------------------------


def test_benchmark_is_buy_and_hold_of_the_same_symbol_by_hand() -> None:
    portfolio = _portfolio()
    orders = [_order(D1, Side.BUY, "10", "50")]
    prices = [_price(_day(1), "50"), _price(_day(2), "60"), _price(_day(3), "40"), _price(_day(6), "55")]
    [usd] = equity.equity_curves(portfolio, orders, prices, [], _day(1), _day(6), UTC)
    bench = usd.benchmark
    assert bench is not None and bench.symbols == ["EXMPL"] and bench.start == _day(1) and bench.reason is None
    # Day 1: the portfolio is worth 999 (fee 1), all of it into EXMPL at 50 = 19.98 units, P&L anchored at -1.
    assert [p.pnl for p in bench.points] == [Decimal("-1.00"), Decimal("198.80"), Decimal("-200.80"), Decimal("-200.80"), None, Decimal("98.90")]
    assert [p.day for p in bench.points] == [p.day for p in usd.points]


def test_benchmark_splits_equally_and_waits_for_every_symbol_to_have_a_price() -> None:
    portfolio = _portfolio(age=5)
    orders = [_order(D1, Side.BUY, "4", "50", oid="o1"), _order(_day(2), Side.BUY, "2", "100", symbol="OTHER", oid="o2")]
    prices = [_price(_day(1), "50"), _price(_day(2), "100", symbol="OTHER"), _price(_day(3), "60"), _price(_day(3), "110", symbol="OTHER")]
    [usd] = equity.equity_curves(portfolio, orders, prices, [], _day(1), _day(3), UTC)
    bench = usd.benchmark
    assert bench is not None and bench.symbols == ["EXMPL", "OTHER"] and bench.start == _day(2)
    day2 = usd.points[1]
    assert day2.value == Decimal("998") and bench.points[0].pnl is None and bench.points[1].pnl == day2.pnl
    # 998 split 499 and 499: 9.98 EXMPL at 50 and 4.99 OTHER at 100, then 60 and 110.
    assert bench.points[2].pnl == quantize(day2.pnl + Decimal("9.98") * 60 + Decimal("4.99") * 110 - 998, "USD")


def test_benchmark_is_absent_with_a_reason_for_shadow_or_no_symbol() -> None:
    shadow = _portfolio(PortfolioMode.SHADOW)
    orders = [_order(D1, Side.BUY, "10", "50", mode=PortfolioMode.SHADOW)]
    [usd] = equity.equity_curves(shadow, orders, [_price(D1, "50")], [], D1, _day(2), UTC)
    assert usd.benchmark is not None and usd.benchmark.reason == "shadow" and all(p.pnl is None for p in usd.benchmark.points)
    [cash_only] = equity.equity_curves(_portfolio(), [], [], [], D1, _day(2), UTC)
    assert cash_only.benchmark is not None and cash_only.benchmark.reason == "no_symbols"
    [unpriced] = equity.equity_curves(_portfolio(), [_order(D1, Side.BUY, "10", "50")], [], [], D1, _day(2), UTC)
    assert unpriced.benchmark is not None and unpriced.benchmark.reason == "no_common_price" and unpriced.benchmark.start is None


def test_route_returns_the_benchmark_line() -> None:
    today = today_in(FinanceSettings())
    start = today - timedelta(days=2)
    finance_store.save_portfolio(_portfolio(start=start, age=3))
    finance_store.add_order(_order(start, Side.BUY, "10", "50"))
    finance_store.save_price(_price(start, "50"))
    finance_store.save_price(_price(today, "60"))
    [usd] = client.get(f"{API}/intel/portfolios/port_eq/equity", headers=MEMBER).json()["series"]
    bench = usd["benchmark"]
    assert bench["symbols"] == ["EXMPL"] and bench["start"] == start.isoformat() and bench["reason"] is None
    assert len(bench["points"]) == len(usd["points"]) and Decimal(bench["points"][-1]["pnl"]) == Decimal("198.80")
