"""Trading intelligence, loops 3 and 4: calendars, market data, indicators, backtester,
strategies and walk-forward validation (docs/finance-trading-intel.md).

Everything here is pure and deterministic; no network, no database. Synthetic bars are
generated test data, never market data.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from anum_api.finance.intel import indicators as ind
from anum_api.finance.intel.backtest import BacktestError, Costs, History, align, max_drawdown, metrics, run
from anum_api.finance.intel.markets import (
    AssetClass,
    SessionState,
    freshness,
    is_trading_day,
    last_completed_session,
    periods_per_year,
    session,
    venue,
)
from anum_api.finance.intel.marketdata import (
    PROVIDERS,
    MarketDataError,
    check_series,
    make_candle,
    normalize_symbol,
    parse_bars_csv,
    parse_binance_klines,
    parse_provider,
    synthetic_bars,
)
from anum_api.finance.intel.strategies import REGISTRY, StrategyError, build, check_params, spec
from anum_api.finance.intel.validation import MIN_TRADES, data_hash, grid, probe_look_ahead, walk_forward

START = date(2018, 1, 1)


def _bars(seed: int = 1, count: int = 1200, **kw: object) -> list:
    return synthetic_bars(seed, START, count, **kw)  # type: ignore[arg-type]


def _flat(days: int, price: str = "100", volume: object = 1_000_000) -> list:
    out, day = [], START
    while len(out) < days:
        if day.weekday() < 5:
            out.append(make_candle(day, price, price, price, price, volume))
        day += timedelta(days=1)
    return out


# -- calendars --------------------------------------------------------------------------------------


def test_new_york_session_follows_daylight_saving() -> None:
    # 13:45 UTC is 09:45 in New York in summer (EDT) and 08:45 in winter (EST).
    assert session("XNYS", datetime(2026, 7, 1, 13, 45, tzinfo=UTC)).state is SessionState.OPEN
    assert session("XNYS", datetime(2026, 1, 7, 13, 45, tzinfo=UTC)).state is SessionState.CLOSED
    assert session("XNYS", datetime(2026, 1, 7, 14, 45, tzinfo=UTC)).state is SessionState.OPEN


def test_exchange_holidays_and_weekends() -> None:
    assert session("XNYS", datetime(2026, 11, 26, 16, 0, tzinfo=UTC)).state is SessionState.HOLIDAY  # Thanksgiving
    assert session("XLON", datetime(2026, 12, 28, 10, 0, tzinfo=UTC)).state is SessionState.HOLIDAY  # Boxing Day, observed
    assert session("XNYS", datetime(2026, 10, 17, 15, 0, tzinfo=UTC)).state is SessionState.CLOSED  # Saturday


def test_tadawul_trades_sunday_and_says_lunar_holidays_are_unknown() -> None:
    sunday = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)  # 11:00 in Riyadh
    s = session("XSAU", sunday)
    assert s.state is SessionState.OPEN and not s.holidays_known
    assert "announced" in s.reason
    assert session("XSAU", datetime(2026, 10, 16, 8, 0, tzinfo=UTC)).state is SessionState.CLOSED  # Friday
    assert session("XDFM", datetime(2026, 10, 16, 8, 0, tzinfo=UTC)).state is SessionState.OPEN  # DFM trades Friday


def test_forex_week_and_crypto_always_open() -> None:
    assert session("FX", datetime(2026, 10, 17, 12, 0, tzinfo=UTC)).state is SessionState.CLOSED  # Saturday
    assert session("FX", datetime(2026, 10, 18, 20, 0, tzinfo=UTC)).state is SessionState.CLOSED  # Sunday 16:00 New York
    assert session("FX", datetime(2026, 10, 18, 22, 0, tzinfo=UTC)).state is SessionState.OPEN  # Sunday 18:00 New York
    assert session("FX", datetime(2026, 10, 16, 21, 30, tzinfo=UTC)).state is SessionState.CLOSED  # Friday 17:30 New York
    assert session("CRYPTO", datetime(2026, 10, 17, 3, 0, tzinfo=UTC)).state is SessionState.OPEN


def test_session_refuses_naive_times_and_unknown_venues() -> None:
    with pytest.raises(ValueError, match="aware"):
        session("XNYS", datetime(2026, 7, 1, 13, 0))
    with pytest.raises(ValueError, match="Unknown venue"):
        venue("XXXX")


def test_last_completed_session_and_freshness() -> None:
    # Friday 27 Nov 2026 after the close: the day after Thanksgiving traded.
    assert last_completed_session("XNYS", datetime(2026, 11, 27, 22, 0, tzinfo=UTC)) == date(2026, 11, 27)
    # Friday morning before the open: the newest complete bar is Wednesday (Thursday was a holiday).
    assert last_completed_session("XNYS", datetime(2026, 11, 27, 13, 0, tzinfo=UTC)) == date(2026, 11, 25)
    assert last_completed_session("CRYPTO", datetime(2026, 10, 18, 1, 0, tzinfo=UTC)) == date(2026, 10, 17)
    now = datetime(2026, 11, 30, 22, 0, tzinfo=UTC)  # Monday after the close
    assert freshness("XNYS", date(2026, 11, 30), now).status == "fresh"
    stale = freshness("XNYS", date(2026, 11, 24), now)
    assert stale.status == "stale" and stale.sessions_behind == 3  # 25, 27 and 30 November; 26 was a holiday
    assert freshness("XNYS", None, now).status == "none"
    gulf = freshness("XDFM", date(2026, 10, 1), datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    assert gulf.status == "stale" and "not all known" in gulf.message


def test_periods_per_year_by_asset_class() -> None:
    assert periods_per_year(AssetClass.CRYPTO) == 365
    assert periods_per_year(AssetClass.FOREX) == 260
    assert periods_per_year(AssetClass.EQUITY) == 252
    assert is_trading_day(venue("CRYPTO"), date(2026, 12, 25))
    assert not is_trading_day(venue("XNYS"), date(2026, 12, 25))


# -- market data ------------------------------------------------------------------------------------


CSV = """Date,Open,High,Low,Close,Volume
2026-10-13,10,11,9.5,10.5,1000
2026-10-12,9,10.2,8.8,10,900
2026-10-14,10.5,10.9,10.1,10.2,
"""


def test_csv_import_sorts_and_keeps_unknown_volume_unknown() -> None:
    bars = parse_bars_csv(CSV)
    assert [b.day.isoformat() for b in bars] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert bars[0].close == Decimal("10") and bars[1].volume == Decimal("1000")
    assert bars[2].volume is None  # never invented as zero


def test_csv_import_accepts_arabic_headers_and_a_bom() -> None:
    text = "﻿التاريخ,افتتاح,أعلى,أدنى,إغلاق,الحجم\n2026-10-12,9,10,8,9.5,10\n"
    assert parse_bars_csv(text)[0].high == Decimal("10")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Date,Open,High,Low\n2026-10-12,1,2,1\n", "Missing column"),
        ("Date,Open,High,Low,Close\n2026-10-12,1,0.5,1,1\n", "inconsistent"),
        ("Date,Open,High,Low,Close\n12/10/2026,1,2,1,1\n", "YYYY-MM-DD"),
        ("Date,Open,High,Low,Close\n2026-10-12,1,2,1,abc\n", "Row 2"),
        ("Date,Open,High,Low,Close\n2026-10-12,1,2,1\n", "too few cells"),
        ("", "empty"),
    ],
)
def test_csv_import_refuses_corrupt_files(text: str, message: str) -> None:
    with pytest.raises(MarketDataError, match=message):
        parse_bars_csv(text)


def test_csv_import_size_limit() -> None:
    with pytest.raises(MarketDataError, match="2 MB"):
        parse_bars_csv("Date,Open,High,Low,Close\n" + "x" * 2_000_001)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "1e12"])
def test_prices_must_be_positive_finite_and_bounded(value: str) -> None:
    with pytest.raises(MarketDataError):
        make_candle("2026-10-12", value, value, value, value)


def test_symbols_are_normalised_and_checked() -> None:
    assert normalize_symbol(" aapl ") == "AAPL"
    assert normalize_symbol("btc/usdt") == "BTC/USDT"
    with pytest.raises(MarketDataError):
        normalize_symbol("DROP TABLE;")


def test_series_checks_block_duplicates_and_future_bars_and_report_gaps() -> None:
    a = make_candle("2026-10-12", 10, 11, 9, 10, 100)
    b = make_candle("2026-10-12", 10, 11, 9, 10, 100)
    c = make_candle("2026-10-16", 10, 20, 9, 15, 0)  # three trading days missing, +50 percent, no volume
    d = make_candle("2026-10-30", 15, 16, 14, 15, 100)
    issues = check_series([a, b, c, d], AssetClass.EQUITY, "XNYS", today=date(2026, 10, 20))
    codes = {(i.code, i.blocking) for i in issues}
    assert ("duplicate", True) in codes and ("future", True) in codes
    assert ("gap", False) in codes and ("abnormal_move", False) in codes and ("zero_volume", False) in codes
    assert ("out_of_order", True) in codes  # b is not after a


def test_crypto_series_has_no_gaps_on_weekends_but_equities_do_not_count_them() -> None:
    fri, mon = make_candle("2026-10-16", 1, 1, 1, 1, 5), make_candle("2026-10-19", 1, 1, 1, 1, 5)
    assert not [i for i in check_series([fri, mon], AssetClass.EQUITY, "XNYS", date(2026, 10, 20)) if i.code == "gap"]
    assert [i for i in check_series([fri, mon], AssetClass.CRYPTO, "CRYPTO", date(2026, 10, 20)) if i.code == "gap"]


def test_binance_kline_contract() -> None:
    # Recorded shape of GET /api/v3/klines (open time ms, open, high, low, close, volume, ...).
    payload = '[[1760659200000,"67000.1","68000","66500","67500.5","1234.5",1760745599999,"0",1,"0","0","0"]]'
    (bar,) = parse_binance_klines(payload)
    assert bar.day == date(2025, 10, 17) and bar.close == Decimal("67500.5") and bar.volume == Decimal("1234.5")
    for bad in ("{}", "[[1]]", "not json", '[["x",1,1,1,1,1]]'):
        with pytest.raises(MarketDataError):
            parse_binance_klines(bad)


def test_stooq_daily_csv_contract() -> None:
    # Recorded shape of stooq.com/q/d/l/?s=aapl.us&i=d (header row, oldest first).
    payload = "Date,Open,High,Low,Close,Volume\n2026-10-15,247.1,249.9,246.2,249.3,48211000\n2026-10-16,249.0,250.6,247.5,248.1,39902000\n"
    bars = parse_provider("stooq", payload)
    assert [b.close for b in bars] == [Decimal("249.3"), Decimal("248.1")] and bars[0].volume == Decimal("48211000")


def test_unbuilt_providers_say_what_the_owner_must_do() -> None:
    with pytest.raises(MarketDataError, match="not built"):
        parse_provider("tadawul_dfm_adx", "")
    with pytest.raises(MarketDataError, match="Unknown provider"):
        parse_provider("made_up", "")
    assert all(p.owner_action or p.key == "csv_import" for p in PROVIDERS.values())


def test_synthetic_bars_are_reproducible_valid_and_labelled_by_seed() -> None:
    a, b, c = _bars(7, 300), _bars(7, 300), _bars(8, 300)
    assert a == b and a != c
    assert all(x.low <= min(x.open, x.close) and x.high >= max(x.open, x.close) for x in a)
    assert all(x.day.weekday() < 5 for x in a)
    assert not [i for i in check_series(a, AssetClass.EQUITY, "XNYS", date(2030, 1, 1)) if i.blocking]
    rets = ind.log_returns([float(x.close) for x in _bars(3, 5000, drift=0.0)])
    sd = math.sqrt(sum(r * r for r in rets) / len(rets))
    assert 0.010 < sd < 0.014  # close to the requested 0.012 daily volatility


# -- indicators -------------------------------------------------------------------------------------


def test_sma_ema_and_warm_up() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert ind.sma(values, 3) == [None, None, 2.0, 3.0, 4.0]
    e = ind.ema(values, 3)
    assert e[:2] == [None, None] and e[2] == pytest.approx(2.0) and e[3] == pytest.approx(3.0)
    with pytest.raises(ValueError):
        ind.sma(values, 0)


def test_rsi_extremes_and_textbook_value() -> None:
    assert ind.rsi([float(i) for i in range(1, 30)], 14)[-1] == pytest.approx(100.0)
    assert ind.rsi([float(i) for i in range(30, 1, -1)], 14)[-1] == pytest.approx(0.0)
    # The StockCharts RSI tutorial's worked example: it prints 70.53 from averages rounded to
    # two places (0.24 and 0.10); the unrounded Wilder averages give 70.464.
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert ind.rsi(closes, 14)[14] == pytest.approx(70.464, abs=0.001)


def test_atr_adx_volatility_momentum_basics() -> None:
    bars = [ind.Bar(10 + i, 11 + i, 9 + i, 10.5 + i) for i in range(60)]
    assert ind.atr(bars, 14)[-1] == pytest.approx(2.0, abs=0.01)
    assert ind.adx(bars, 14)[-1] > 50  # a straight line up is a strong trend
    assert ind.volatility([100.0] * 30, 20)[-1] == pytest.approx(0.0)
    assert ind.momentum([100.0, 105.0, 110.0], 2)[-1] == pytest.approx(0.10)


@pytest.mark.parametrize("name", ["sma", "ema", "rsi", "volatility", "momentum"])
def test_close_indicators_never_look_ahead(name: str) -> None:
    closes = [float(c.close) for c in _bars(5, 400)]
    fn = getattr(ind, name)
    full = fn(closes, 20)
    for cut in (25, 120, 399):
        assert fn(closes[:cut], 20) == full[:cut]


def test_bar_indicators_and_macd_never_look_ahead() -> None:
    bars = [c.bar() for c in _bars(6, 400)]
    for fn in (ind.atr, ind.adx):
        full = fn(bars, 14)
        assert fn(bars[:200], 14) == full[:200]
    closes = [b.close for b in bars]
    full_macd, cut_macd = ind.macd(closes), ind.macd(closes[:150])
    assert cut_macd.macd == full_macd.macd[:150] and cut_macd.signal == full_macd.signal[:150]


def test_snapshot_labels_and_contradictions() -> None:
    short = ind.snapshot([ind.Bar(1, 1, 1, 1)] * 10)
    assert short.trend == "unknown" and short.volatility_regime == "unknown" and short.sma_200 is None
    up = ind.snapshot([c.bar() for c in _bars(9, 400, drift=0.004, vol=0.004)])
    assert up.trend == "up"
    with pytest.raises(ValueError):
        ind.snapshot([])


# -- backtester -------------------------------------------------------------------------------------


class _BuyOnce:
    key, version = "test_buy_once", "0"

    def __init__(self, at: int = 0) -> None:
        self.at = at

    def targets(self, h: History) -> dict[str, float] | None:
        return {s: 1.0 for s in h.symbols} if h.t == self.at else None


def test_orders_fill_at_the_next_open_with_costs() -> None:
    bars = _flat(10)
    bars[1] = make_candle(bars[1].day, "101", "103", "100", "102", 1_000_000)
    res = run({"X": bars}, _BuyOnce(0), cash=Decimal("10000"), costs=Costs(fee_bps=Decimal("10"), spread_bps=Decimal("10"),
                                                                          slippage_bps=Decimal("5")))
    (trade,) = res.trades
    assert trade.day == bars[1].day  # signal at the close of bar 0, fill on bar 1
    assert trade.price == Decimal("101.1010")  # open 101 plus half the spread (5 bps) and slippage (5 bps)
    assert trade.fee == (trade.notional * Decimal("0.001")).quantize(Decimal("0.0001"))
    assert trade.notional + trade.fee <= Decimal("10000")


def test_no_trade_on_the_last_bar_and_no_leverage() -> None:
    class Greedy:
        key, version = "greedy", "0"

        def targets(self, h: History) -> dict[str, float]:
            return {"X": 5.0, "Y": 5.0}

    res = run({"X": _flat(30), "Y": _flat(30)}, Greedy(), cash=Decimal("1000"))
    assert res.targets[-1] is None
    probe = run({"X": _flat(30), "Y": _flat(30)}, Greedy(), cash=Decimal("1000"), final_signal=True)
    assert probe.targets[-1] is not None and probe.trades == res.trades  # the last signal is recorded, never filled
    assert all(e <= 1000.0 + 1e-6 for e in res.equity)  # flat prices: costs only, never more than the cash


def test_volume_cap_and_missing_volume() -> None:
    capped = run({"X": _flat(5, volume=10)}, _BuyOnce(0), cash=Decimal("100000"))
    assert capped.trades[0].quantity == Decimal("0.5")  # 5 percent of 10 units
    assert any(reason == "volume_cap" for _, _, reason in capped.skipped)
    unknown = run({"X": _flat(5, volume=None)}, _BuyOnce(0))
    assert not unknown.trades and unknown.skipped[0][2] == "no_volume"
    allowed = run({"X": _flat(5, volume=None)}, _BuyOnce(0), costs=Costs(allow_unknown_volume=True))
    assert allowed.trades


def test_align_keeps_common_days_and_never_fills_gaps() -> None:
    a, b = _flat(10), _flat(10)
    del b[4]
    aligned = align({"A": a, "B": b})
    assert len(aligned["A"]) == len(aligned["B"]) == 9
    assert a[4].day not in {c.day for c in aligned["A"]}
    with pytest.raises(BacktestError):
        align({})
    with pytest.raises(BacktestError, match="two"):
        run({"A": a}, _BuyOnce(0), start_index=9)


def test_metrics_on_a_known_line() -> None:
    assert max_drawdown([100, 120, 90, 130, 65]) == pytest.approx(-0.5)
    res = run({"X": _bars(2, 600)}, _BuyOnce(0))
    m = metrics(res)
    assert m.trades == 1 and m.closed_trades == 0 and m.win_rate is None
    assert m.total_return == pytest.approx(res.equity[-1] / res.equity[0] - 1)
    assert -1 < m.max_drawdown <= 0 and m.exposure > 0.99
    assert m.as_dict()["bars"] == 600


def test_realised_pnl_after_fees_on_a_round_trip() -> None:
    bars = _flat(6)
    bars[3] = make_candle(bars[3].day, "110", "110", "110", "110", 1_000_000)

    class RoundTrip:
        key, version = "round_trip", "0"

        def targets(self, h: History) -> dict[str, float] | None:
            return {0: {"X": 1.0}, 2: {"X": 0.0}}.get(h.t)

    res = run({"X": bars}, RoundTrip(), cash=Decimal("10000"), costs=Costs(fee_bps=Decimal("0"), spread_bps=Decimal("0"),
                                                                         slippage_bps=Decimal("0")))
    buy, sell = res.trades
    assert sell.pnl == pytest.approx(Decimal("10") * buy.quantity, abs=Decimal("0.01"))


# -- strategies -------------------------------------------------------------------------------------


def test_registry_has_the_benchmark_strategies_with_versions() -> None:
    assert set(REGISTRY) == {"buy_and_hold", "trend_sma", "rsi_reversion", "momentum", "periodic", "rebalance", "pair_ratio"}
    assert all(s.version and s.description for s in REGISTRY.values())
    with pytest.raises(StrategyError, match="Unknown strategy"):
        spec("martingale")


@pytest.mark.parametrize(
    ("key", "params", "symbols", "message"),
    [
        ("trend_sma", {"fast": 200, "slow": 50}, ("A",), "shorter"),
        ("trend_sma", {"lever": 3}, ("A",), "no parameter"),
        ("rsi_reversion", {"low": 60, "exit": 50}, ("A",), "low < exit"),
        ("momentum", {"lookback": "nan"}, ("A",), "out of range"),
        ("momentum", {"lookback": 0}, ("A",), "1 or more"),
        ("pair_ratio", {}, ("A",), "2 to 2"),
        ("rebalance", {"weights": {"A": 0.7, "B": 0.6}}, ("A", "B"), "at most 1"),
        ("rebalance", {"weights": {"Z": 0.5}}, ("A", "B"), "own symbols"),
    ],
)
def test_bad_parameters_are_refused(key: str, params: dict, symbols: tuple, message: str) -> None:
    with pytest.raises(StrategyError, match=message):
        check_params(key, params, symbols)


def test_each_strategy_runs_and_stays_long_only() -> None:
    a, b = _bars(1, 700, regime_every=120), _bars(2, 700, regime_every=120)
    for key, s in REGISTRY.items():
        data = {"A": a} if s.min_symbols == 1 else {"A": a, "B": b}
        res = run(data, build(key, tuple(data)))
        for wanted in res.targets:
            if wanted:
                assert all(0.0 <= w <= 1.0 for w in wanted.values()) and sum(wanted.values()) <= 1.0 + 1e-9
        assert min(res.equity) > 0


def test_trend_and_periodic_behave_as_described() -> None:
    up = _bars(4, 500, drift=0.003, vol=0.005)
    trend = run({"A": up}, build("trend_sma", ("A",), {"fast": 20, "slow": 50}))
    assert trend.trades and trend.trades[0].side == "buy" and trend.trades[0].day >= up[50].day
    periodic = run({"A": up}, build("periodic", ("A",), {"tranches": 4, "every": 10}))
    assert [t.side for t in periodic.trades] == ["buy"] * 4


# -- validation -------------------------------------------------------------------------------------


def test_grid_is_bounded_and_data_hash_is_stable() -> None:
    assert all(len(grid(k)) <= 24 for k in REGISTRY)
    assert grid("buy_and_hold") == [{}]
    a = _bars(1, 50)
    assert data_hash({"A": a}) == data_hash({"A": list(a)}) != data_hash({"A": _bars(2, 50)})


def test_look_ahead_probe_catches_a_strategy_that_reads_the_future(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.finance.intel import strategies

    class Cheat:
        key, version = "cheat", "0"

        def __init__(self, symbols: tuple, params: dict) -> None:
            self.symbols = symbols

        def targets(self, h: History) -> dict[str, float]:
            series = h._closes[self.symbols[0]]  # the whole series, future included
            tomorrow = series[h.t + 1] if h.t + 1 < len(series) else series[h.t]
            return {self.symbols[0]: 1.0 if tomorrow > series[h.t] else 0.0}

    cheat = strategies.Spec("cheat", "0", "Cheat", "test", {}, {}, factory=Cheat)
    monkeypatch.setitem(strategies.REGISTRY, "cheat", cheat)
    bars = {"A": _bars(3, 600)}
    assert probe_look_ahead(bars, "cheat", {}) is False
    assert probe_look_ahead(bars, "trend_sma", {"fast": 20, "slow": 100}) is True
    result = walk_forward(bars, "cheat", train_bars=250, test_bars=100)
    assert "look_ahead" in result.reasons and result.verdict == "rejected"


def test_walk_forward_folds_are_time_ordered_and_never_overlap() -> None:
    v = walk_forward({"A": _bars(1, 1200, regime_every=150)}, "rsi_reversion")
    assert len(v.folds) == 5
    for fold in v.folds:
        assert fold.train[1] < fold.test[0]
    for left, right in zip(v.folds, v.folds[1:]):
        assert left.test[1] < right.test[0]
    days = [d for d, _ in v.equity]
    assert days == sorted(days) and len(days) == len(set(days))
    assert v.equity[0][1] == pytest.approx(100000.0)


def test_walk_forward_verdicts_and_reasons() -> None:
    a, b = _bars(1, 1200, regime_every=150), _bars(2, 1200, regime_every=150)
    short = walk_forward({"A": a[:600]}, "trend_sma")
    assert short.verdict == "insufficient_data" and "too_few_folds" in short.reasons
    rare = walk_forward({"A": a}, "momentum")
    assert rare.verdict == "rejected" and "too_few_trades" in rare.reasons
    assert rare.out_of_sample is not None and rare.out_of_sample.closed_trades < MIN_TRADES
    pair = walk_forward({"A": a, "B": b}, "pair_ratio")
    assert pair.verdict == "rejected" and "no_edge" in pair.reasons
    bad = walk_forward({"A": a}, "buy_and_hold", blocking_issues=1, listing_known=False)
    assert "data_issues" in bad.reasons and "survivorship_unknown" in bad.warnings
    out = bad.as_dict()
    assert out["verdict"] == "rejected" and out["data_hash"] == data_hash({"A": a}) and len(out["equity"]) <= 400


def test_calendar_rules_start_on_the_test_bar() -> None:
    v = walk_forward({"A": _bars(1, 1200)}, "periodic")
    assert v.verdict in {"passed", "rejected"}
    # 12 monthly tranches cannot all fit in a 125-bar window: each fold is still investing.
    assert all(f.out_of_sample.exposure < 1.0 for f in v.folds)


def test_benchmark_covers_the_same_span_as_the_joined_line() -> None:
    v = walk_forward({"A": _bars(1, 1200)}, "trend_sma")
    assert v.benchmark_equity[0][0] == v.equity[0][0]
    assert v.benchmark_equity[-1][0] == v.equity[-1][0]
    assert v.beats_benchmark in {True, False}
