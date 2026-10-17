# Finance trading intelligence: gap assessment

Loop 1 of the trading intelligence upgrade (October 2026). It reads the finance module as it is on `main` after PR #90 (stages 2 to 9, [Finance](finance.md)) file by file, and lists what the multi-market research and SIMULATION brief needs that is missing. Nothing here changes behaviour; the later loops build on it. Results of each loop are recorded in [Trading intelligence](finance-trading-intel.md).

The brief's hard boundaries hold for every loop: live trading stays off, no live-order adapter is written, every order passes the existing Guardian `gate` and the existing execution gateway, no credentials go into git or agent memory, and no result is presented as a promise of profit.

## What exists (kept as it is)

| File | What it does today | Reused by the upgrade as |
|---|---|---|
| `finance/money.py` | Decimal money, minor units, banker's rounding once on entry | Money in every simulated fill and report |
| `finance/fx.py` | ECB and typed rates, direct, inverse and cross conversion with source and date | The only way currencies are compared; never silently mixed |
| `finance/safe_to_spend.py` | Money on hand minus bills, minimum debt payments, goals and buffer, per currency | "Investable capital": what is free after obligations |
| `finance/guardian.py`, `finance/policy.py` | The person's versioned rules, pure `evaluate` and the mandatory `gate`, loosening detection | The one policy system. Trading risk adds kernel checks inside the same path; no second policy store |
| `finance/trading.py` | Paper and shadow portfolios, fills at recorded prices with fee and slippage, replayed book, valuation, shadow comparison | The simulator every strategy order ends in |
| `finance/gateway.py`, `finance/execution.py` | The one governed path: intent, kill switches, Guardian, payload-hash approval, paper or shadow executor, idempotency | The only way a strategy signal becomes a simulated order |
| `finance/live.py`, `finance/wider.py`, `wider_api.py` | Live frameworks, shipped off, with no executor; refused at startup and in Helm | Untouched. The future live design (loop 9) is a document only |
| `finance/connectors.py`, `finance/sync.py` | Read-only connector registry, ECB adapter, safe background sync through `anum_maintenance` | Pattern for market-data providers: read-only, bounded fetch, no transaction across the network |
| `finance/tools.py` | Read tools, proposal tools, the request planner that reads only the user's own words | Home of the new trading intelligence tools and owner commands |
| `finance/store.py`, `db/finance_repository.py` | In-memory and PostgreSQL stores in the owner's own scope | Extended with the new tables |
| `migrations/0056` to `0059` | Owner-only forced RLS on every finance table, live check constraints | New migration `0060` follows the same pattern |
| `agent_skills.py` | `anum.finance` skill manifest and trigger words | Registry for the six new trading skills |
| `automation.py` | Cron schedules in an IANA zone that run `task.create` steps as their actor | The existing scheduler for pre-market, intraday, post-close, weekly and monthly jobs |
| Web `FinanceTrading.tsx`, `FinanceWider.tsx` | Paper trading and execution tabs | Extended with the trading intelligence tab |
| Flutter `features/finance/` | Paper portfolios, approvals, kill switch | Extended with a read-only trading intelligence section |

## Gaps, by area of the brief

| Area | On `main` | Gap |
|---|---|---|
| Market data | Prices typed by the person, ECB rates. No instrument master, no candles, no provider abstraction | Provider protocol, instrument master (asset class, exchange, currency, symbol map, listing dates), daily OHLCV with provenance, quote timestamps, freshness and session checks, candle validation (no invented bars, corruption refused), recorded-fixture contract tests, retention and rate limits |
| Calendars | None | Session rules per venue (US equities, London, Tadawul, DFM, ADX, forex 24/5, crypto 24/7), time zones with DST, a holiday list with an honest "not known" for lunar-calendar holidays |
| Technical analysis | None | Deterministic SMA, EMA, RSI, MACD, ATR, ADX, volatility, momentum, regime labels, contradiction flags |
| Strategy lab | None | Versioned strategy registry, benchmark strategies (buy and hold, trend following, mean reversion, momentum, periodic investing, rebalancing, limited pair trading), a backtester with next-bar fills, costs, spread, slippage and a volume cap, walk-forward validation with time-ordered splits, look-ahead and survivorship checks, the metric set and a rejection rule for unreliable or overfit results |
| Portfolio intelligence | Book, P&L and fees per portfolio | Exposure by asset class and currency, concentration, correlation and diversification from candles, investable capital after obligations |
| Trading risk | Guardian rules for spending; kill switches; the gateway | Trading controls inside the same Guardian path: instrument and asset-class allow-list, position size and exposure caps, daily simulated loss stop, stale price, abnormal move and missing volume circuit breakers. An agent may only tighten |
| Operations | Order states, approvals, waiting-order checks | Strategy evaluation runs, data freshness report, reconciliation of book against orders, duplicate and inconsistent state detection, incident journal, daily report, safe restart (idempotent runs) |
| Scheduling | Automation cron in a zone | Job kinds (pre-market, intraday, post-close, weekly, monthly) that check the venue calendar themselves, so a schedule on a holiday records "closed" instead of trading |
| Skills | `anum.finance` only | Six skills with inputs, outputs, permitted tools, prohibited actions and failure handling, registered in `agent_skills.py` |
| Clients | Paper trading and execution tabs | Market overview, strategy lab, paper command center, portfolio analytics, risk center, agent activity, provider health, simulated against benchmark, skills status; every figure labelled SIMULATED, VERIFIED (recorded data) or FORECAST |
| Owner commands | Paper orders and portfolio reads | "analyze my paper portfolio", "which strategies passed validation", "biggest simulated losses", "compare trend following with buy and hold", "enough free cash for investing", "stop all paper strategies", "prepare my daily trading report" |
| Live readiness | Frameworks off | A separate, disabled design for a future live path that needs independent review |

## Frameworks considered (summary; details in [Trading intelligence](finance-trading-intel.md#frameworks-evaluated))

| Project | Licence | ARM64 | Resources | Fit |
|---|---|---|---|---|
| QuantConnect LEAN | Apache 2.0 | .NET runs on ARM64; official images are x86_64 | Several GB image, 2 GB RAM or more | Heavy second runtime next to the API on a shared VM; its own order model would sit beside the gateway. Not adopted |
| freqtrade | GPL 3.0 | Yes (Python, ARM images) | About 1 GB RAM with its UI | Crypto only, its own bot loop and exchange keys; GPL would bind a linked module. Not adopted |
| hummingbot (and its MCP server) | Apache 2.0 | Images for ARM64 exist | 1 GB or more | Market making with live exchange connectors; the MCP server exposes order tools to a model, which the boundary forbids. Not adopted |
| ccxt | MIT | Pure Python | Light | Exchange client with order and withdrawal calls; only public market data would be allowed. Not needed while no crypto data provider is chosen |
| backtrader | GPL 3.0 | Pure Python | Light | Unmaintained since 2021 and GPL. Not adopted |

Conclusion: the minimum architecture is a small pure-Python package inside the API (`finance/intel/`): deterministic maths with tests, no new runtime, no new dependency. Any framework would add a second order model next to the gateway, which the brief rules out.

## Order of work

1. This assessment.
2. Skills, docs, tool contracts and tests.
3. Market-data layer and historical storage (migration `0060`).
4. Indicators, backtester, benchmark strategies and validation.
5. Strategy runs into paper and shadow through Guardian and the gateway, with circuit breakers.
6. Daily jobs, health, reports.
7. Web and Flutter screens, owner commands.
8. Security review, benchmark, regression.
9. Future live-trading design, disabled.
