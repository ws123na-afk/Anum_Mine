# Trading intelligence: loop 10 readiness report (live trading stays off)

Loop 10 of the trading intelligence upgrade ([Trading intelligence](finance-trading-intel.md), [gap assessment](finance-trading-gap-assessment.md)). It says what is built, what is tested and what the owner still has to decide. It is a readiness assessment for **SIMULATED** trading only. It does not enable, prepare or approve live trading.

## Live trading stays off

* `ANUM_FINANCE_LIVE_ENABLED` and `ANUM_FINANCE_WIDER_LIVE_ENABLED` default to false. The API refuses to start with either on, and the Helm chart refuses the values (`infra/helm/ci/lint.sh`), because no live executor exists ([Finance](finance.md#limited-live)).
* There is no live-order adapter, no write-scoped or withdrawal-scoped connector, and no transfer path. Every live request through the gateway is refused, also by a database check.
* Every simulated order goes through the existing Guardian `gate` and the execution gateway, and runs only after the person approves the payload hash shown, once. No agent can approve, loosen a rule, put a strategy into PAPER_ACTIVE or RETIRED, or skip the Guardian.
* External market data and any MCP or agent output are data. They are shown as text and never followed as instructions.
* No result is presented as a promise of profit. A passing backtest or walk-forward run alone never promotes a strategy and is never labelled profitable or approved.

LIVE_CANDIDATE is documented as a possible future state only; the database refuses it and it grants nothing. A future live path needs a separate design and an independent review, then every owner item in [What the owner must provide before live](finance.md#what-the-owner-must-provide-before-live).

## What is built

| Area | Where | State |
|---|---|---|
| Calendars and sessions (NYSE and Nasdaq, London, Tadawul, DFM, ADX, FX 24/5, crypto 24/7), DST, honest "holidays not known" | `finance/intel/markets.py` | Built |
| Market-data parsing and checks (CSV, Binance public klines, Stooq), series checks, labelled synthetic bars; network fetch off | `finance/intel/marketdata.py` | Built; no live fetch |
| Indicators (SMA, EMA, RSI, MACD, ATR, ADX, volatility, momentum), regimes, contradictions | `finance/intel/indicators.py` | Built |
| Backtester with next-bar fills, fee, spread, slippage, volume cap | `finance/intel/backtest.py` | Built |
| Benchmark strategies (buy and hold, trend, mean reversion, momentum, periodic, rebalance, pair ratio) | `finance/intel/strategies.py` | Built |
| Walk-forward validation with look-ahead probe and rejection reasons | `finance/intel/validation.py` | Built |
| Storage for instruments, bars, strategy versions, runs and agent decisions under forced RLS (migration `0060_finance_intel`) | `finance/intel/records.py`, `finance/store.py`, `db/finance_repository.py` | Built |
| Strategy lifecycle DRAFT, BACKTESTING, VALIDATION, PAPER_ACTIVE, PAPER_SUSPENDED, RETIRED with promotion criteria and versions | `finance/intel/lifecycle.py` | Built |
| Read-only dashboard API (`GET /api/v1/finance/intel/overview`, `/strategies`, `/strategies/{id}/runs`, `/decisions`), `finance:read`, owner scope | `finance/intel_api.py` | Built |
| Web dashboard: simulation banner, kill switch, venue sessions, strategies by lifecycle state with paging, backtest and walk-forward results with benchmark, folds, warnings and reproduction, paper and shadow positions and P&L, Supervisor and agent decisions with reasons, Guardian decisions, risk limits read only; English and Arabic | `apps/web/src/FinanceIntel.tsx`, `apps/web/src/lib/financeIntel.ts` | Built ([dashboard](finance-trading-intel.md#dashboard)) |
| Paper and shadow simulator, execution gateway, Guardian, kill switches, manual prices | stages 6 and 7, [Finance](finance.md) | Built earlier, unchanged |

## What is tested

| Suite | Covers |
|---|---|
| `services/api/tests/test_finance_intel.py` | Calendars, data checks, indicators without look-ahead, backtester, strategies, walk-forward |
| `services/api/tests/test_finance_intel_lifecycle.py` | Lifecycle moves, promotion criteria, versions, agent limits |
| `services/api/tests/test_postgres_finance_intel.py` | Migration `0060` under forced RLS, database checks |
| `services/api/tests/test_finance_intel_dashboard.py` | Dashboard routes: simulation only, live off, positions and P&L after an approved paper order, read only (no writes, no audit rows, only GET routes), `finance:read`, isolation from another person and another tenant, Guardian rules read back, paging and filters, recorded text returned as data, bounds |
| `services/api/tests/test_postgres_finance_intel_dashboard.py` | The overview and pages under RLS as the non-owner app role (runs in CI's database job) |
| `services/api/tests/test_finance_trading.py` | Paper and shadow trading, gateway, Guardian, kill switches, live refused (unchanged) |
| `apps/web/test/financeIntel.test.ts` | Tab deep link, lifecycle labels in both languages, metric formatting, verdict wording, agents, venues, folds, catalogs with the same placeholders and no long dashes |
| `apps/web/e2e/finance-intel.spec.ts` | Banner, live off, empty states with a next action, loading, error with retry, permission denied, strategy paging and lifecycle filter, walk-forward results, agent filter, kill switch with confirmation (the only write), Arabic right to left, no sideways overflow at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 on desktop and phone projects |

Evidence for this branch is the CI run of PR #104. Local runs before each push: API unit tests above, web typecheck, unit tests and build, the dashboard Playwright spec on both projects, `pnpm docs:check`, bandit, pip-audit and gitleaks. The PostgreSQL dashboard test needs CI's database job.

## Not built yet

Stated plainly so nothing is presented as finished:

* The Trading Supervisor cycle and the specialist agents that write decisions, the write endpoints for strategies and runs (`intel_control_api`), and the scheduled pre-open, intraday, post-close, weekly and monthly jobs. Until they record rows, the dashboard shows real empty states.
* Strategy signals turned into SIMULATED orders (loop 5), and the circuit breakers on stale data, abnormal moves and missing liquidity in that path.
* Market-data fetch from any provider (network egress is off), and corporate actions.
* Owner commands in the assistant ("pause all paper trading", "best paper strategy after costs") and the Flutter screens for this dashboard.
* P&L and drawdown charts over time (the dashboard shows the current figures and each run's metrics).
* A future live-trading design for independent review (document only).

## Open owner decisions

| Decision | Why it matters | Default until decided |
|---|---|---|
| Which market-data providers to allow, and accepting their terms (Binance public data, Stooq, a paid source for Gulf exchanges) | Network fetch stays off without it; delayed data must never be shown as live | Own CSV imports and synthetic bars only |
| Allowing HTTPS egress from the Oracle VM to the chosen providers | Required for any fetch | No egress |
| Paid data or broker costs, if any | Brief requires reporting every non-free dependency | None used |
| Promotion criteria values (folds, drawdown limit, benchmark requirement) | Decide what "validated" means before a strategy may trade on paper | Defaults in `lifecycle.Criteria` (3 folds, drawdown no worse than 35 percent, benchmark required) |
| Which paper portfolio each strategy trades in, and its simulated cash and costs | PAPER_ACTIVE needs a portfolio | None assigned |
| Guardian limits for simulated trading (currencies, per order, per day) | Every simulated order is checked against them | No rules: orders are still checked for prices and costs and still need approval |
| Whether assistants may propose strategy versions and suspensions automatically on a schedule | Scheduled jobs are not built yet | Off |
| Holiday calendars for Tadawul, DFM and ADX each year | Sessions on announced holidays are otherwise shown as possibly open, with a note | "Holidays not known" shown |
| Any future live trading: broker account, write-scoped key without withdrawals, limits, written approval, independent review | Live stays off until every item exists | Off and refused |

## Verdict

Ready for owner review of SIMULATED research and paper trading only, once the open items in "Not built yet" are finished and CI on PR #104 is green. Not ready for, and not a step toward enabling, live trading.
