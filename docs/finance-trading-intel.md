# Trading intelligence: research and SIMULATION across markets

Work in progress on branch `claude/finance-trading-intel`; each loop adds its section. Read the [gap assessment](finance-trading-gap-assessment.md) first. Everything here is SIMULATED or research: live trading stays off ([Finance](finance.md#hard-limits)).

The code is the pure package `services/api/anum_api/finance/intel/`: no network calls, no database, no new dependency. Tests: `services/api/tests/test_finance_intel.py`.

## Status by loop

| Loop | Scope | State |
|---|---|---|
| 1 | Gap assessment | Done ([gap assessment](finance-trading-gap-assessment.md)) |
| 3 | Calendars, market-data parsing and checks, synthetic data | Pure code and tests done. Storage (migration `0060`), the instrument API and fetch jobs are **not built yet** |
| 4 | Indicators, backtester, benchmark strategies, walk-forward validation | Pure code and tests done. Stored runs and the API are **not built yet** |
| 2, 5 to 9 | Skills, strategy runs into paper and shadow, daily jobs, screens, owner commands, security review, live design | **Not started** |

Nothing in this package can place an order. Loop 5 is where a strategy signal may become a SIMULATED order, and only through the existing Guardian `gate` and execution gateway.

## Calendars

`intel/markets.py`. Each venue has a time zone (DST handled by `zoneinfo`), regular hours and trading weekdays:

| Code | Venue | Hours (local) | Days | Holidays |
|---|---|---|---|---|
| `XNYS` | New York Stock Exchange and Nasdaq | 09:30 to 16:00 | Monday to Friday | Full-day closures for 2026 and 2027 |
| `XLON` | London Stock Exchange | 08:00 to 16:30 | Monday to Friday | England and Wales bank holidays for 2026 and 2027 |
| `XSAU` | Saudi Exchange (Tadawul) | 10:00 to 15:00 | Sunday to Thursday | Not known in advance (Eid dates are announced each year) |
| `XDFM`, `XADS` | Dubai Financial Market, Abu Dhabi Securities Exchange | 10:00 to 15:00 | Monday to Friday | Not known in advance |
| `FX` | Currencies and spot metals | Sunday 17:00 to Friday 17:00 New York | | None |
| `CRYPTO` | Crypto assets | Always | Every day; a daily bar closes at 00:00 UTC | None |

`session(code, now)` says open, closed or holiday and whether the holidays for that day are known. Where they are not (Gulf venues, years outside the list), the answer says so instead of guessing: "Regular session, unless the exchange announced a holiday for today". Half days are not modelled. Early closes trade as full days in the simulation, which is stated rather than hidden.

`last_completed_session` gives the newest daily bar that should exist now, and `freshness` compares it with the stored one (`fresh`, `stale` with the number of sessions behind, or `none`). A gap on a venue with unknown holidays carries "a recent exchange holiday may explain the gap".

## Market data

`intel/marketdata.py`.

* **Bars** are `Candle`s: date, open, high, low, close as `Decimal` and volume as `Decimal` or `None`. A missing volume stays unknown and is never written as zero. Prices must be positive, finite and below 100,000,000, and the high and low must contain the open and close.
* **CSV import** (`parse_bars_csv`): a header row with date, open, high, low, close and optional volume, in English or Arabic, with or without a byte-order mark. At most 2 MB and 10,000 bars. Any bad row refuses the whole file with the row number.
* **Series checks** (`check_series`): `duplicate`, `future` and `out_of_order` are blocking (the import stops). `gap` (trading days missing by the venue calendar), `abnormal_move` (above 25 percent in one bar for an equity, 15 for an ETF or commodity, 40 for crypto, 8 for a currency) and `zero_volume` are reported. Gaps are never filled with invented bars.
* **Providers** (`PROVIDERS`): one registry with cost, licence terms, delay and whether a key is needed. Built parsers, contract-tested against recorded payloads: your own CSV, Binance public daily klines, Stooq daily CSV. Network fetch is off in this release: each built provider names the owner action needed first (accept the terms, allow HTTPS egress). Alpha Vantage, Twelve Data and Gulf exchange data are listed as **not built**, with what the owner must provide.
* **Synthetic bars** (`synthetic_bars`): a seeded geometric random walk for tests and demonstrations, labelled `synthetic: generated test data, not market data`. Draws come from SHA-256 of the seed and a counter (Box-Muller), so a seed gives the same bars on every Python version.

## Technical analysis

`intel/indicators.py`. Pure functions over bars, oldest first, each returning a list as long as its input with `None` during warm-up:

* SMA, EMA, RSI (Wilder), MACD 12/26/9, true range, ATR (Wilder), ADX (Wilder), annualised volatility of log returns, momentum (rate of change).
* `snapshot`: every indicator at the last bar, a trend label (`up`, `down`, `sideways`, `unknown`; sideways when ADX is under 20), a volatility regime against the series' own quartiles (`low`, `normal`, `high`, `unknown` with under 60 values) and contradictions (for example `uptrend_but_overbought`, `momentum_disagrees_with_average`).

The value at bar `t` uses bars `0..t` only. The tests cut the future off at several points and require every past value to stay the same.

Indicators are floats (research figures). Money is `Decimal`.

## Backtesting

`intel/backtest.py`, daily bars:

1. At the close of bar `t` the strategy sees bars `0..t` and returns target weights (long only, each 0 to 1, together at most 1; larger totals are scaled down).
2. The orders execute at the **open of bar `t+1`**, never at a price the strategy has seen. No signal is taken on the last bar.
3. A fill pays half the spread plus slippage against it, a fee in basis points with an optional minimum, and is capped at a share of that bar's volume (5 percent by default). A bar with unknown volume does not fill unless the run allows it. Trades smaller than the rebalance band (2 percent of equity) are skipped.
4. Sells go first, then buys within the cash left. No leverage, no short selling.

Defaults: fee 10 bps, spread 5 bps, slippage 5 bps. Symbols are aligned to the days every symbol has a bar; no bar is invented. At most 10 symbols and 20,000 bars a run.

Metrics: total return, CAGR, annualised volatility, Sharpe and Sortino (no risk-free rate), maximum drawdown, turnover, trades, closed trades, win rate, profit factor, expectancy after fees, exposure and fees. Each one is SIMULATED on past bars with the stated costs.

## Strategy lab

`intel/strategies.py`: a registry of small, readable rules, each with a key, a version, default parameters and a small grid. A logic change is a new version; a stored run will keep the version it used.

| Key | Kind | Rule | Defaults |
|---|---|---|---|
| `buy_and_hold` | Benchmark | Equal weights on the first bar, held | |
| `trend_sma` | Trend following | Hold while the fast SMA is above the slow one | 50 / 200 |
| `rsi_reversion` | Mean reversion | Buy below RSI `low`, sell above `exit` | 14, 30, 55 |
| `momentum` | Momentum | Every `every` bars, hold the `top` symbols with return over `lookback` above `threshold` | 120, 0, 1, 21 |
| `periodic` | Periodic investing | Invest in `tranches` equal steps every `every` bars | 12, 21 |
| `rebalance` | Rebalancing | Back to fixed weights every `every` bars | 63, equal |
| `pair_ratio` | Pair trading, long only | Hold the cheaper leg when the log price ratio is `entry_z` deviations from its mean; flat within `exit_z` | 60, 2.0, 0.5 |

Parameters are checked: unknown names, non-numbers, values out of range and contradictions (fast not shorter than slow, RSI levels out of order, weights over 1 or naming other symbols) are refused with a message. These are benchmark rules for research, not recommendations.

## Validation

`intel/validation.py`, walk-forward with time-ordered splits only. Each fold picks parameters from the grid on a training window (500 bars by default) by Sharpe, then is measured on the **next** window (125 bars) it never saw. Rules that read indicators get up to 250 earlier bars to warm up; calendar rules (buy and hold, periodic investing, rebalancing) start on the first test bar. Test windows are joined into one out-of-sample equity line, compared with buy and hold over the same span.

A strategy **passes** only when none of these apply; otherwise it is **rejected** with every reason:

| Reason | When |
|---|---|
| `data_issues` | The bars had a blocking problem |
| `look_ahead` | A signal changed when the bars after it were cut off. The probe cuts the series at 8 points and compares every signal up to and including the cut bar |
| `too_few_trades` | Fewer than 5 closed trades out of sample (buy and hold, periodic investing and rebalancing are exempt) |
| `overfit` | Average in-sample Sharpe above 0.5 and out-of-sample Sharpe below half of it |
| `no_edge` | Out-of-sample Sharpe at or below 0 |
| `unstable` | The chosen parameters changed in more than half of the folds |

Fewer than 2 test windows gives `insufficient_data`. An instrument with unknown listing dates carries the warning `survivorship_unknown` (a universe chosen from today's listings overstates the past).

Every result carries a SHA-256 of the exact bars it used and the cost assumptions. Passing means the rule held up on these past bars with these costs. It says nothing certain about the future.

## Frameworks evaluated

| Project | Licence | Why not adopted |
|---|---|---|
| QuantConnect LEAN | Apache 2.0 | A second runtime (.NET, several GB image, 2 GB RAM or more) on a shared VM, with its own order model next to the gateway |
| freqtrade | GPL 3.0 | Crypto only, its own bot loop and exchange keys; GPL would bind a linked module |
| hummingbot and its MCP server | Apache 2.0 | Market making with live exchange connectors; the MCP server gives a model order tools, which the boundary forbids |
| ccxt | MIT | Exchange client with order and withdrawal calls; only public market data would be allowed, and no crypto provider is chosen yet |
| backtrader | GPL 3.0 | Unmaintained since 2021, and GPL |

Conclusion: a small pure-Python package inside the API, deterministic and tested, with one order path (the existing gateway).
