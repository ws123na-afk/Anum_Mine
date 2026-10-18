# Trading intelligence: research and SIMULATION across markets

Work in progress on branch `claude/finance-trading-intel`; each loop adds its section. Read the [gap assessment](finance-trading-gap-assessment.md) first. Everything here is SIMULATED or research: live trading stays off ([Finance](finance.md#hard-limits)).

The code is the pure package `services/api/anum_api/finance/intel/`: no network calls, no database, no new dependency. Tests: `services/api/tests/test_finance_intel.py`.

## Status by loop

| Loop | Scope | State |
|---|---|---|
| 1 | Gap assessment | Done ([gap assessment](finance-trading-gap-assessment.md)) |
| 2 | Trading knowledge library and agent skills | Done: six skill packages with sourced references, registered skills, five agent tools and the owner's commands |
| 3 | Calendars, market data, storage | Done: calendars, CSV and recorded-provider parsing with checks, instruments and bars stored (migration `0065_finance_intel`), import API. Network fetch from providers is off until the owner accepts their terms |
| 4 | Indicators, backtester, the six candidates, walk-forward validation, reports | Done, with tests |
| 5 | Strategy signals into SIMULATED paper orders | Done: the Supervisor's Execution Coordinator through the existing gateway and Guardian; every order waits for the person's approval |
| 6 | Scheduled agent operations | Done: `finance.intel.cycle` automation step, phases by venue calendar, idempotent cycles |
| 7 | Dashboard | Done on the web: the read view (`intel_api.py`), the actions (market data, new strategies, evaluations, lifecycle moves, Supervisor cycles, pause), P&L and drawdown charts with a buy and hold line, and the daily reports. Phone: see [P&L and drawdown over time](#pl-and-drawdown-over-time) |
| 8 | Daily reports, monitoring, incidents | Done: the written post-close report (stored, API, assistant tool, web and phone views) with an in-app notification after each one, and every agent decision and cycle summary recorded |
| 9 | Security, performance, regression, Oracle checks | Done: see [Checks](#checks); walk-forward made 25 to 50 times faster, evaluations bounded, bar storage capped |
| 10 | Live-trading readiness assessment (nothing enabled) | [Readiness report](finance-trading-readiness.md) |

No code here can place a real order. A strategy signal becomes, at most, a SIMULATED paper order request through the existing Guardian `gate` and execution gateway, approved by the person.

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

The six research candidates of the brief, plus the benchmark (`CANDIDATES`, `by_candidate()`). Every rule carries its hypothesis, instruments, timeframe and exact entry and exit rules in the registry, and every one passes the look-ahead probe (tested).

| Candidate | Key | Rule | Defaults |
|---|---|---|---|
| Benchmark | `buy_and_hold` | Equal weights on the first bar, held | |
| 1. Diversified periodic investing and rebalancing | `periodic` | Invest in `tranches` equal steps every `every` bars | 12, 21 |
| | `rebalance` | Back to fixed weights every `every` bars | 63, equal |
| 2. Trend following with volatility controls | `trend_sma` | Hold while SMA(`fast`) is above SMA(`slow`) | 50 / 200 |
| | `trend_vol` | The same, sized to `target_vol` / 20-bar volatility, closed by a trailing stop `atr_stop` x ATR(14) below the highest close since entry | 50 / 200, 15%, 3 |
| 3. Momentum and breakout | `momentum` | Every `every` bars hold the `top` symbols with return over `lookback` above `threshold` | 120, 0, 1, 21 |
| | `breakout` | Buy a close above the highest close of the previous `entry` bars; sell a close below the lowest of the previous `exit` bars (Donchian) | 55, 20 |
| 4. Mean reversion with regime filters | `rsi_reversion` | Buy RSI below `low`, sell above `exit` | 14, 30, 55 |
| | `rsi_regime` | Buy RSI(14) below `low` only above SMA(`trend`) with ADX(14) under `max_adx`; sell above `exit` or below the average | 30, 55, 200, 25 |
| 5. Statistical pairs trading | `pair_ratio` | Hold the cheaper leg when the log price ratio is `entry_z` deviations from its mean; flat within `exit_z`. Long only: no short leg | 60, 2.0, 0.5 |
| 6. Crypto spot | `crypto_trend` | Hold while EMA(`fast`) is above EMA(`slow`) and RSI(14) is under `max_rsi`, sized to `target_vol` (365-day volatility). Spot only | 20 / 50, 75, 50% |

Candidate 6 and Freqtrade: the brief named crypto strategies supported by Freqtrade. Freqtrade is not adopted (GPL 3.0, its own bot loop, order model and exchange keys: see below), so no Freqtrade strategy file is imported or run. `crypto_trend` is a native rule in the same backtester, gateway and Guardian path as every other candidate, evaluated on daily crypto bars with 365 periods a year.

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

## Evaluation reports

`intel/report.py` and `intel/research.py`. Every walk-forward run of a stored version produces a report that restates what is needed to repeat and judge it: rule, code version, hypothesis, instruments, timeframe, entry and exit rules, the version's parameters, the data source and SHA-256 of the bars, costs, method, verdict with every reason in words, warnings, out-of-sample metrics against buy and hold over the same span, the worst drawdown and the worst test window, and the folds. `report_markdown` renders it for exports and the daily report.

A stored version is validated with **its own parameters fixed** in every fold (`walk_forward(..., params=...)`), so what is promoted is exactly what was tested; without `params`, each fold picks from the rule's grid (research only). The run is executed twice on the same bars and marked `reproduced` only if both results are identical. Reports are labelled SIMULATED and never say profitable, approved or recommended (tested).

## Trading Supervisor

`intel/supervisor.py`. Deterministic code, not a language model. One cycle runs one phase for one person and records a structured `AgentDecision` for everything each agent decided, grouped by a cycle id that is derived from the person, phase and day: a repeated or restarted cycle is skipped (`already_ran`), and an order request uses the idempotency key `intel:<version>:<bar day>:<symbol>:<side>`, so it is never doubled.

| Agent | Does | Can never |
|---|---|---|
| Market Research | Freshness against the venue calendar, blocking series problems, an abnormal last move, missing volume; the source of the newest bar | Fetch data or follow instructions found in data |
| Technical Analysis | Indicator snapshot, trend, volatility regime and contradictions per instrument | |
| Strategy Research | Backtests and walk-forward runs; monthly revalidation of active versions (suspends one that no longer passes) | Promote, resume or rewrite a version |
| Portfolio | The paper portfolio's simulated cash, positions, realised and unrealised P&L, fees and unpriced holdings | |
| Risk Guardian | Before any order: allowed asset classes and one currency, data fresh (one session behind skips the signal; more, or missing or corrupt data, suspends), abnormal move and missing volume circuit breakers, a per-symbol cap of 25 percent of equity, a 3 percent daily simulated loss stop (suspends). Then the existing Guardian `gate` decides inside the gateway | Loosen a limit (`RiskLimits` accepts only stricter values) |
| Execution Coordinator | Turns the rule's target weights into SIMULATED paper market order requests through `gateway.create_trade_intent` (`requested_by = agent`), recording the bar's close as a price with its source first, so the order is priced from stored data | Approve an order (each one waits for the person's hash-bound approval), use a shadow or live executor, or trade a currency pair |
| Performance Reviewer | Runs the rule over the bars since it became active; suspends it when the drawdown is worse than validation's times 1.5; weekly, flags it when behind buy and hold | Change the strategy |

Phases: `pre_open` (data checks and analysis for every active version), `intraday` and `post_close` (signals to order requests; after the close also portfolio and performance reviews), `weekly` (analysis and the benchmark comparison), `monthly` (revalidation). The person's or the organisation's kill switch halts a cycle before any agent runs.

Every proposed order records the strategy and version, the instrument, the bar date and data source, the target weight and whether it was capped, the quantity, the gateway's estimate (fill price, notional, fee, slippage, price source), the Guardian outcome and the payload hash. Fills, positions and P&L come from the existing paper executor once the person approves.

**Emergency pause**: `pause_all` suspends every PAPER_ACTIVE version at once (the person's action, recorded). The existing kill switch additionally stops every executor.

A daily trading phase on a day none of the strategy's venues trades (by each venue's own local date and holiday list; crypto every day) records `market_closed` instead of trading.

## Scheduled operations

`intel/schedules.py` uses the existing automation engine: a schedule is an ordinary workflow with one `finance.intel.cycle` step (`input.phase`) and an automation schedule (cron and IANA time zone), visible in the Workbench like any other. After the engine commits a run it calls the step handler, which re-checks that the schedule's creator is still an active member with `finance:manage`, acts as that person (their own finance rows only) and runs the phase. The cycle is idempotent per person, phase and day, so a retried, duplicated or restarted fire never runs twice.

`POST /api/v1/finance/intel/schedules` with `phase` and optional `time` (defaults: pre-open 08:30, intraday 12:00, post-close 17:30, weekly 18:00, monthly 07:00), `timezone` (default `Asia/Dubai`) and `weekday` (weekly, 0 is Sunday) creates one schedule per phase; `GET` lists your own, `DELETE /schedules/{id}` removes one. Daily phases are scheduled every day and skip themselves on days the venue does not trade.

Not built yet: model-written commentary in the reports, and a check of the Oracle VM's load before heavy evaluations (loop 9).

## API

Write routes, `finance:manage`, in `finance/intel_control_api.py` under `/api/v1/finance/intel` (the read routes are in `intel_api.py`):

| Route | Does |
|---|---|
| `POST /instruments`, `DELETE /instruments/{id}` | Add an instrument (symbol, asset class, venue, currency, listing dates); delete one no active strategy uses |
| `POST /instruments/{id}/bars` | Import daily bars: `provider` `csv_import`, `stooq` or `binance_public` with the file or recorded response as `payload`. Blocking problems refuse the whole import; the rest are returned as issues. Idempotent |
| `POST /strategies` | Version 1 of a strategy, in DRAFT, from a rule key and instruments |
| `POST /strategies/{id}/versions` | The next version with changed parameters or instruments and a reason |
| `POST /strategies/{id}/transition` | A lifecycle move by the person (409 when the rules refuse it) |
| `POST /strategies/{id}/portfolio` | The paper portfolio the version will trade in |
| `POST /strategies/{id}/evaluate` | A SIMULATED `backtest` or `walk_forward` run on the stored bars |
| `POST /supervisor/run` | One Supervisor cycle for a phase, now |
| `POST /pause-all` | The emergency pause |
| `GET`, `POST /schedules`, `DELETE /schedules/{id}` | Your scheduled Supervisor cycles |

Audit records carry ids, kinds, states, phases, counts and codes only; never a symbol, price or amount.

## Skills and tools

**Knowledge library** (`finance/intel/skills/`, loaded and checked by `finance/intel/knowledge.py`): six packages in the Claude skill format, each a folder with `SKILL.md` (front matter with name, description, version and ANUM skill id; when to use, inputs, outputs, permitted tools, prohibited actions, failure handling), `references/*.md` and `tools.json`.

| Skill | Covers |
|---|---|
| `anum.trading.market-data` | Terminology, instrument types, venues and hours, providers and broker or exchange API behaviour, liquidity, spread, volatility and slippage |
| `anum.trading.technical-analysis` | Exact indicator definitions as implemented, regimes and contradictions |
| `anum.trading.strategy-research` | The six candidate families and their evidence, backtesting methodology and biases, risk-adjusted measures, fundamentals and valuation |
| `anum.trading.portfolio` | Paper portfolios, diversification and portfolio theory, benchmarks |
| `anum.trading.risk` | ANUM's controls in order, regulatory and geographic restrictions (UAE, Saudi Arabia, United States, European Union), psychology and automated-system failures |
| `anum.trading.operations` | Cycles, phases, the lifecycle and the owner's commands |

Every reference ends with a sources table (source, link, source date, licence, limitations): peer-reviewed papers, regulators, exchanges and provider documentation. Copyrighted works are summarised, not reproduced. The loader refuses a package with a missing section, a reference without sources, a broken link or a long dash; tests keep each `tools.json` equal to the registered tools and check that no package promises profit and that every one says it is SIMULATED with live trading off. Each package is an agent skill in the runtime registry (selected by its words, only where its tools are registered) and can be published as a skill version (`knowledge.skill_version`).

**Agent tools** (`finance/intel/agent_tools.py`, registered with the finance tools in the API and the worker):

| Tool | Does |
|---|---|
| `finance.intel.activity` | What the Supervisor and its agents did on a day, with counts and what was proposed, refused or suspended |
| `finance.intel.strategies` | Versions with their newest evaluation: return after costs, worst drawdown, buy and hold, and whether an active one needs revalidation (missing, failed or older than 30 days) |
| `finance.intel.explain_order` | Why a SIMULATED order request was refused or denied: the Guardian decision, the gateway refusal and the agents' decisions |
| `finance.intel.benchmark` | Each active strategy's paper period against buy and hold of the same symbols |
| `finance.intel.pause_all` | Suspends every active paper strategy (tightens only; resuming is the person's action) |

No tool promotes, resumes or retires a strategy, approves an order, changes a limit or trades live. The request planner maps the owner's own words to these tools: "what did the trading agents do today", "which paper strategy performed best after transaction costs", "explain why the Guardian rejected this simulated trade", "show the worst drawdown for each strategy", "pause all paper trading", "compare my paper portfolio against an appropriate benchmark", "which strategy needs revalidation" (and Arabic forms).

## Daily report

`intel/daily_report.py`. At the end of every `post_close` cycle (also a halted one, which then says why nothing ran) the Supervisor writes a report from that cycle's own decisions, nothing recomputed or invented, and stores it as an owner-only decision (`action = "daily_report"`) with the structured report and its Markdown:

- summary: active strategies, orders proposed for approval and all waiting, signals refused, suspensions, problems, market-closed days;
- data health per instrument: newest bar, sessions behind, source, status and reasons;
- orders proposed: strategy and version, side and quantity, target weight, estimated fill with its price source, fee, the Guardian's outcome;
- refusals and suspensions with the agents' reasons;
- the paper portfolio: cash, realised and unrealised P&L, fees, holdings without a recent price;
- each active strategy against buy and hold over its paper period;
- problems (failed checks, degrading strategies, halts) and next steps for the person.

It is labelled SIMULATED, says that no real order was placed, quotes agent reasons as data and has no long dashes (tested). Read it with `GET /api/v1/finance/intel/daily-reports` (newest first, paged) and `GET /api/v1/finance/intel/daily-reports/{id}` (`finance:read`, own reports only), or ask the assistant to "prepare my daily trading report" (`finance.intel.daily_report`). After each post-close cycle that wrote a report (run by hand or by a schedule) the person gets one in-app notification through the existing notification path (`intel/report_notice.py`, kind `finance_daily_report`): the web bell and the phone's Notifications screen list it; opening it in the web bell goes to the reports (`#finance-intel-reports`, newest selected; `#finance-intel-report=<id>` opens one), and on the phone it opens Finance, Trading intelligence (`NotificationsScreen.openers`, registered by the app). Its text is counts only (active strategies, orders proposed, signals refused, suspensions) and says no real order was placed: no symbol, price, amount or recorded reason, because email leaves ANUM. Email follows the person's own email preference over the server's existing SMTP settings, with no new credentials. There is no phone push for it: the push dispatcher's fixed text for notifications says "Team work", which would mislead, so the row records push `not_requested`. A repeated or skipped cycle sends nothing; a failed notice is logged and never undoes the stored report.

**Web** (`apps/web/src/FinanceReports.tsx`, helpers in `apps/web/src/lib/financeReports.ts`): Trading intelligence has a **Daily reports** panel, a paged list (10 a page, newest first, "Needs you" when orders wait, a strategy was suspended or a problem was recorded) and the selected report: summary figures, next steps, orders proposed, refused and suspended, data health, the paper portfolio, against buy and hold and problems, with recorded text shown as data, and **Download as Markdown**. On screens below 1024 pixels the open report comes before the list. Loading, empty with the next step, error with retry (list and report separately) and permission denied are handled; English and Arabic (the report's own sentences, such as next steps, are recorded in English and shown as written). Tests: `apps/web/test/financeReports.test.ts`, `apps/web/e2e/finance-reports.spec.ts` (the responsive gate at the five sizes).

## Checks

Loop 9, all read only and without any paid resource.

**Security.** bandit (whole API and migrations), gitleaks over the full history, ruff on the new code: clean. No Python or web dependency was added, so the last pip-audit and OSV results stand; the desktop's `rustls` and `zip` advisories are fixed ([Security](security.md#scanning-in-ci)). The new code makes no network, shell, `eval` or unpickling call. Reviewed and tested:

| Area | Check | Evidence |
|---|---|---|
| Tenant and owner isolation | Five tables under forced RLS with owner-only restrictive policies; every route and tool reads only the caller's rows | `test_postgres_finance_intel.py`, the API and report tests (another person and another tenant see nothing) |
| Agent permissions | Agents cannot promote, resume or retire (code and database check), approve orders, loosen limits or trade live; the only write tool pauses | `test_finance_intel_lifecycle.py`, `test_finance_intel_supervisor.py`, `test_finance_intel_knowledge.py` |
| Approval integrity | Agent orders go through the gateway as `requested_by = agent`, priced from stored bars, and wait for the person's hash-bound approval | `test_finance_intel_supervisor.py` |
| Duplicate orders and restarts | Deterministic order keys per version, bar, symbol and side; one cycle per person, phase and day | Supervisor and API schedule tests |
| Prompt injection | Agent reasons, provider text and file contents are data; tools cannot act on them | Report and tool notes; e2e shows an injected reason as text |
| Data validation | Corrupt, duplicated or future bars refuse an import; gaps are never filled; abnormal moves stop signals | `test_finance_intel.py`, API tests |
| Resource limits | 2 MB and 10,000 bars per import, at most 50,000 bars per instrument (now enforced), 200 instruments, 200 versions, 200 runs per version, 10 symbols per run | API tests |

**Performance.** Measured on the development container (x86_64, 4 cores) before and after the shared indicator cache (`backtest.SeriesCache`: walk-forward shares one set of indicator series across its runs; results are identical, tested for every rule):

| Workload | Before | After | Peak memory |
|---|---|---|---|
| Walk-forward, 1 symbol, 2,500 bars, 8 parameter choices | 25.7 s | 1.0 s | 3 MB |
| Walk-forward, 10 symbols, 5,000 bars | 143 s | 2.6 s | 18 MB |
| Walk-forward, 1 symbol, 20,000 bars (the old cap) | not measured | 131 s | 36 MB |
| Supervisor `post_close`, 10 active strategies, 900 bars | 1.7 s | 1.7 s | 2 MB |

Evaluations run inside the API process, so they are bounded: only the newest 3,000 aligned bars are evaluated (the run carries `trimmed_to_recent_bars`), and one evaluation per person runs at a time (a second gets "already running"). A walk-forward evaluation of a stored version is then a few seconds.

**Oracle VM** (`infra/oracle/compose.yaml`, `docs/deploy-oracle.md`; read only, nothing changed):

| Check | Finding |
|---|---|
| Architecture | Ampere A1 arm64, 4 OCPU, 24 GB (Always Free). The trading code is pure Python with no new dependency, so the existing `aarch64` wheels cover it |
| Memory | The API container is limited to 2 GB. The largest measured run peaks at 36 MB of Python memory; a test process with every workload above stayed under 220 MB resident |
| CPU | Cycles and evaluations run on the API's worker threads. Monthly revalidation is scheduled at 07:00, outside business hours; evaluations are capped and serialized per person |
| Disk | An estimated 200 to 300 bytes per stored bar in PostgreSQL, indexes included (not measured on the VM). The 50,000-bar cap per instrument and 200 instruments bound one person's market data at roughly 2.5 GB in the worst case; typical use (10 instruments, 15 years of daily bars) is about 10 MB |
| Paid resources | None created or needed. Provider fetch stays off; a paid data licence (Gulf exchanges) is an owner decision |

The owner can confirm on the VM, read only: `uname -m` (aarch64), `nproc`, `free -h`, `df -h /`, `docker stats --no-stream` while a Supervisor cycle runs.

## Storage

Migration `0065_finance_intel`. Five tables, each under forced RLS with the tenant policy and a restrictive owner-only policy, like the rest of Finance: nobody but the owner sees a row, not even a workspace owner.

| Table | Holds | Database checks |
|---|---|---|
| `finance_instruments` | The person's instrument master: symbol, asset class, venue, currency, provider symbols, listing dates | One row per symbol and venue; delisting not before listing |
| `finance_market_bars` | Daily OHLCV bars with source, provider, delayed flag and fetch time | One bar per instrument and day (an import is idempotent); positive prices; the high and low contain the open and close |
| `finance_strategies` | One row per strategy **version** | The six lifecycle states only; PAPER_ACTIVE needs a paper portfolio; only the person puts a version into PAPER_ACTIVE or RETIRED; an agent's new version names its evidence run |
| `finance_strategy_runs` | Backtests and walk-forward runs: SHA-256 of the bars, costs, metrics, verdict, reasons, folds, whether it was reproduced | Known kinds and verdicts; the hash is 64 hex characters |
| `finance_agent_decisions` | Every structured output of the Supervisor and the specialist agents, and the person's lifecycle actions | Known agents only |

Store methods (`FinanceStore`): `list_instruments`, `get_instrument`, `save_instrument`, `delete_instrument`, `save_bars`, `list_bars`, `list_strategies`, `get_strategy`, `latest_version`, `save_strategy`, `list_strategy_runs`, `get_strategy_run`, `save_strategy_run`, `list_agent_decisions`, `save_agent_decision`. Lists page with `limit` and `offset` and return the rows and the total. The rows are in the privacy export and deleted with the rest of the person's finance data.

## Lifecycle

`intel/lifecycle.py`:

```
DRAFT -> BACKTESTING -> VALIDATION -> PAPER_ACTIVE <-> PAPER_SUSPENDED -> RETIRED
```

| Move | Agents | The person |
|---|---|---|
| DRAFT to BACKTESTING, BACKTESTING to VALIDATION (after a backtest of this version), and one step back | Yes | Yes |
| VALIDATION to PAPER_ACTIVE | No | Yes, when the criteria hold |
| PAPER_ACTIVE to PAPER_SUSPENDED | Yes (tightening) | Yes |
| PAPER_SUSPENDED to PAPER_ACTIVE | No | Yes, when the criteria hold |
| Anything to RETIRED | No | Yes |

**Promotion criteria** (`Criteria`, defaults; stricter values allowed): the newest walk-forward run of this exact version (same rule code, symbols and parameters) passed, it was reproduced (a second run on the same bars gave the same data hash and metrics), at least 3 folds, out-of-sample drawdown no worse than 35 percent, a benchmark comparison, and a paper portfolio to trade in. A positive backtest return alone never promotes.

**Versions.** The rule, symbols and parameters of a version never change. `new_version` makes the next version in the same lineage as a DRAFT, with a reason; an agent must also name the evaluation run of the current version that justifies it. The old version keeps its state and history, so a strategy that lost money cannot be quietly rewritten.

**LIVE_CANDIDATE** is a possible future state for a separately reviewed live design. It is not implemented as a state, the database refuses it, and nothing here grants any permission beyond SIMULATED paper orders through the Guardian gate and the execution gateway.

## Frameworks evaluated

| Project | Licence | Why not adopted |
|---|---|---|
| QuantConnect LEAN | Apache 2.0 | A second runtime (.NET, several GB image, 2 GB RAM or more) on a shared VM, with its own order model next to the gateway |
| freqtrade | GPL 3.0 | Crypto only, its own bot loop and exchange keys; GPL would bind a linked module |
| hummingbot and its MCP server | Apache 2.0 | Market making with live exchange connectors; the MCP server gives a model order tools, which the boundary forbids |
| ccxt | MIT | Exchange client with order and withdrawal calls; only public market data would be allowed, and no crypto provider is chosen yet |
| backtrader | GPL 3.0 | Unmaintained since 2021, and GPL |

Conclusion: a small pure-Python package inside the API, deterministic and tested, with one order path (the existing gateway).

## Dashboard

Finance, tab **Trading intelligence** (`#finance-intel`, `apps/web/src/FinanceIntel.tsx`). Read only and SIMULATED; the readiness report for loop 10 is [Trading readiness](finance-trading-readiness.md).

**Actions** (`apps/web/src/FinanceIntelActions.tsx`, through the write routes in [API](#api)), each with a confirmation where it changes research and every state handled (loading, empty with a next step, the server's own error text, permission denied):

* **Market data**: add an instrument (symbol, type, venue, currency, optional listing date) and import a CSV of daily bars per instrument; the table shows how many bars are stored, their range and source.
* **New strategy**: pick a rule (grouped by research candidate, with its idea, entry and exit), the instruments with stored bars and a reason; it is created as version 1 in Draft after a confirmation.
* **Per strategy** (with its results): run a backtest or a walk-forward validation, choose its paper portfolio, and move it to one of the states the person may choose from its current one. Starting paper trading and retiring ask for a stronger confirmation; the server's refusal (for example "Not ready for paper trading: not_reproduced") is shown as is.
* **Trading Supervisor**: run one phase now (the result says how many orders were proposed for approval, signals refused, strategies suspended) and the emergency pause of every paper strategy.

Tests: `apps/web/test/financeIntelActions.test.ts` and `apps/web/e2e/finance-intel.spec.ts`.

API (`finance/intel_api.py`, `finance:read`, every row the caller's own under forced RLS; GET only, nothing is written or audited):

| Route | Returns |
|---|---|
| `GET /api/v1/finance/intel/overview` | Simulation status (`live_enabled` is always false), the person's and the organisation's kill switches, venue sessions now, each paper and shadow portfolio with cash, realised and unrealised P&L, fees, slippage and up to 50 positions, the Guardian rules (read only), how many strategy versions are in each lifecycle state, requests waiting and requests proposed by assistants |
| `GET /api/v1/finance/intel/strategies?state=&limit=&offset=` | Strategy versions with lifecycle state, rule and code version, symbols, parameters, reason and who moved it last. `state` is one of the six lifecycle states (anything else is 422) |
| `GET /api/v1/finance/intel/strategies/{id}/runs?limit=&offset=` | Backtest and walk-forward runs of that version: data source and SHA-256, costs, metrics, benchmark, verdict, reasons, warnings, folds, whether it was reproduced. 404 for a version that is not the caller's |
| `GET /api/v1/finance/intel/decisions?agent=&strategy_id=&cycle_id=&limit=&offset=` | Decisions of the Trading Supervisor, the specialist agents and the person, with reasons, newest first |
| `GET /api/v1/finance/intel/portfolios/{id}/equity?days=` | Daily SIMULATED value, P&L and drawdown of one paper or shadow portfolio per currency, rebuilt from its stored orders and recorded prices ([P&L and drawdown over time](#pl-and-drawdown-over-time)). 404 for a portfolio that is not the caller's |

Pages take `limit` 1 to 200 and `offset`. Recorded reasons are returned as text to show and are never followed as instructions. Changes (lifecycle moves, runs, Supervisor cycles) belong to `intel_control_api`, not to these routes.

The page shows a "Simulation only, no real orders" banner, live off, the kill switch (the one change it offers: it stops every paper and shadow executor, after a confirmation), venue sessions, strategies filtered by lifecycle state with paging, a strategy's runs with metrics against the benchmark and its walk-forward folds, positions and P&L per portfolio, agent decisions filtered by agent, every Guardian decision through the gateway with its reasons, and the Guardian rules read only with a link to change them on the Guardian tab. Loading, empty with a next action, error with retry and permission denied are handled; tables become stacked cards below 1024 pixels so nothing scrolls sideways. English and Arabic.

## P&L and drawdown over time

Each portfolio on the dashboard shows two charts: SIMULATED P&L and drawdown, day by day (`apps/web/src/FinanceEquity.tsx`, pure helpers in `apps/web/src/lib/financeEquity.ts`). The API computes the curve (`finance/equity.py`, route `GET /api/v1/finance/intel/portfolios/{id}/equity?days=` in `finance/intel_api.py`, `finance:read`, owner only under forced RLS, GET only):

* **From stored records only.** For each day of the portfolio's current run, the API applies the run's starting cash and its filled paper or shadow orders up to that day, with the same average-cost rules as the portfolio view, and values every holding at the newest recorded price (or exchange rate, for currency holdings) dated on or before that day and no older than the portfolio's price age limit. A price dated after the day is never used for it.
* **Gaps, never guesses.** A day on which something held has no price that fresh is returned with `complete: false` and no figures; the chart breaks the line there and the table says "Gap". The days and symbols are listed.
* **Currencies never mixed.** One series per currency, nothing converted between them.
* **Paper and shadow.** A paper portfolio has a value (cash plus priced holdings), P&L (realised plus unrealised, fees and slippage included) and a drawdown from the running high value, as an amount and a percentage. A shadow portfolio has no cash: P&L only, and its drawdown is measured on P&L as an amount.
* **Buy and hold benchmark.** Each paper series also carries `benchmark`: buy and hold of the symbols this run traded in that currency (FX holdings excluded), in equal weights, bought with the portfolio's value on the first day of the window on which every one of them has a fresh price, without costs. Its P&L starts from the portfolio's own P&L that day, so the two lines compare; later, a day on which a symbol has no fresh price is a gap. There is no line, with a `reason`, for a shadow portfolio (`shadow`: no cash to invest), a run that traded nothing in that currency (`no_symbols`) or when no day had every price (`no_common_price`). The web draws it as a dashed line on the P&L chart with a legend, shows its newest P&L next to the portfolio's, says in words what it is (or why there is none) and adds a column to the table.
* **Bounded.** `days` is 1 to 730 (default 180; the page offers 30, 90, 180 and 365, default 90), at most 20 currencies, and the newest 5,000 recorded prices (`prices_truncated` says when more exist, so older days may show gaps).

The page shows the newest value and P&L, the deepest drawdown with its high and low dates, the period and filled orders, a range and currency picker, the two SVG charts (each with a text alternative) and the same figures as a paged table (31 days a page, newest first). Loading, no filled order yet (with a link to Paper trading), error with retry and permission denied are handled. English and Arabic; the time axis stays left to right in Arabic. Past simulated results are not a promise of future results.

On the phone, Finance has a **Trading intelligence** section (`apps/mobile/lib/features/finance/finance_intel_*.dart`): the simulation banner with live off, "Stop all simulated trading" behind a confirmation (the existing person kill switch; a second confirmation allows it again), strategies filtered by lifecycle state with paging and their runs, positions with P&L, the last 90 days of P&L as a line with the deepest drawdown in words, Supervisor and agent decisions with reasons shown as text, and the Guardian rules read only. It also shows the market sessions (each venue's state and its own wall clock, with the note where holidays are announced each year), **New strategy** (its own screen: a rule grouped by research candidate with its idea, entry and exit, instruments with stored bars, a reason, then a confirmation; created in Draft), **Move** on each strategy (the states the lifecycle allows, a reason, then a confirmation that is stronger for starting paper trading or retiring; the server's refusal is shown as is), the **Trading Supervisor** (a phase, "Run now" after a confirmation with what the cycle did, and "Pause all paper strategies" after a confirmation, off when none is active), the **Daily reports** (a paged list with "Needs you", and a report on its own screen with the summary, next steps, orders, refused, suspended, data health, portfolio, against buy and hold and problems, recorded text shown as written, and "Copy as Markdown"), and the buy and hold line dashed on the same scale as the P&L line with its newest value and what it is, or why there is none. People without `finance:manage` are told who can give access instead of seeing the actions. No new plugin: the lines are drawn with a `CustomPainter`.

Tests: `services/api/tests/test_finance_intel_equity.py` (hand-worked values, sales, other runs and unfilled orders ignored, shadow, currencies kept apart, recorded rates, no later price used early, window bounds, privacy, permission, read only), `services/api/tests/test_postgres_finance_intel_equity.py` (RLS as the app role), `apps/web/test/financeEquity.test.ts`, `apps/web/e2e/finance-equity.spec.ts` (states, paging, ranges, currencies, shadow, Arabic, the responsive gate at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844), `apps/mobile/test/finance_intel_test.dart`.

