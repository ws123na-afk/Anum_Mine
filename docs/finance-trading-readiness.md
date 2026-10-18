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
| Storage for instruments, bars, strategy versions, runs and agent decisions under forced RLS (migration `0065_finance_intel`) | `finance/intel/records.py`, `finance/store.py`, `db/finance_repository.py` | Built |
| Strategy lifecycle DRAFT, BACKTESTING, VALIDATION, PAPER_ACTIVE, PAPER_SUSPENDED, RETIRED with promotion criteria and versions | `finance/intel/lifecycle.py` | Built |
| Read-only dashboard API (`GET /api/v1/finance/intel/overview`, `/strategies`, `/strategies/{id}/runs`, `/decisions`, `/portfolios/{id}/equity`), `finance:read`, owner scope | `finance/intel_api.py`, `finance/equity.py` | Built |
| SIMULATED P&L and drawdown over time per paper and shadow portfolio, rebuilt daily from stored orders and recorded prices, gaps never guessed, currencies never mixed | `finance/equity.py`, `apps/web/src/FinanceEquity.tsx` | Built ([P&L and drawdown over time](finance-trading-intel.md#pl-and-drawdown-over-time)) |
| Buy and hold line on the P&L chart: same symbols, equal weights, bought with the portfolio's value on its first fully priced day, no costs; a stated reason when there is none | `finance/equity.py`, `apps/web/src/FinanceEquity.tsx`, `apps/mobile/lib/features/finance/finance_intel_view.dart` | Built (web and phone) |
| Daily reports: list and detail on the web (Markdown download) and the phone (Copy as Markdown); an in-app notification (email by the person's preference, no push) after each post-close report | `apps/web/src/FinanceReports.tsx`, `apps/mobile/lib/features/finance/finance_intel_actions.dart`, `finance/intel/report_notice.py` | Built |
| Phone actions: market sessions, new strategy, lifecycle moves, a Supervisor cycle now and the pause of every paper strategy, each behind a confirmation | `apps/mobile/lib/features/finance/finance_intel_actions.dart` | Built |
| Phone: Finance, Trading intelligence section with the simulation banner, live off, the kill switch behind a confirmation, strategies by lifecycle state with paging and runs, positions with P&L and drawdown over time, agent decisions with reasons, Guardian rules read only; English and Arabic | `apps/mobile/lib/features/finance/finance_intel_*.dart` | Built |
| Web dashboard: simulation banner, kill switch, venue sessions, strategies by lifecycle state with paging, backtest and walk-forward results with benchmark, folds, warnings and reproduction, paper and shadow positions and P&L, Supervisor and agent decisions with reasons, Guardian decisions, risk limits read only; English and Arabic | `apps/web/src/FinanceIntel.tsx`, `apps/web/src/lib/financeIntel.ts` | Built ([dashboard](finance-trading-intel.md#dashboard)) |
| Paper and shadow simulator, execution gateway, Guardian, kill switches, manual prices | stages 6 and 7, [Finance](finance.md) | Built earlier, unchanged |

## What is tested

| Suite | Covers |
|---|---|
| `services/api/tests/test_finance_intel.py` | Calendars, data checks, indicators without look-ahead, backtester, strategies, walk-forward |
| `services/api/tests/test_finance_intel_lifecycle.py` | Lifecycle moves, promotion criteria, versions, agent limits |
| `services/api/tests/test_postgres_finance_intel.py` | Migration `0065_finance_intel` under forced RLS, database checks |
| `services/api/tests/test_finance_intel_dashboard.py` | Dashboard routes: simulation only, live off, positions and P&L after an approved paper order, read only (no writes, no audit rows, only GET routes), `finance:read`, isolation from another person and another tenant, Guardian rules read back, paging and filters, recorded text returned as data, bounds |
| `services/api/tests/test_postgres_finance_intel_dashboard.py` | The overview and pages under RLS as the non-owner app role (runs in CI's database job) |
| `services/api/tests/test_finance_intel_equity.py` | The equity curve: hand-worked values, sales, other runs and unfilled orders ignored, shadow, currencies kept apart, recorded rates, no later price used early, gaps, window bounds, privacy, permission, read only |
| `services/api/tests/test_postgres_finance_intel_equity.py` | The curve under RLS as the app role: only the owner's orders and prices, 404 for everyone else |
| `services/api/tests/test_finance_trading.py` | Paper and shadow trading, gateway, Guardian, kill switches, live refused (unchanged) |
| `apps/web/test/financeIntel.test.ts` | Tab deep link, lifecycle labels in both languages, metric formatting, verdict wording, agents, venues, folds, catalogs with the same placeholders and no long dashes |
| `services/api/tests/test_finance_intel_reports_checks.py`, `test_finance_intel_api.py` | The report notice: one per post-close report (by hand and scheduled), counts only, no push, none for a repeated cycle |
| `apps/web/test/financeReports.test.ts`, `apps/web/e2e/finance-reports.spec.ts` | Reports list paging, deep links, sections, download, loading, empty, errors with retry, denied, Arabic, the responsive gate at the five sizes |
| `apps/web/test/financeEquity.test.ts`, `apps/web/e2e/finance-equity.spec.ts` | Buy and hold line (shared scale, reasons), chart scaling with gaps, words, catalogs; charts, paged table, ranges, currencies, shadow, loading, empty, error with retry, denied, Arabic, the responsive gate at the five sizes |
| `apps/mobile/test/finance_intel_test.dart` | Phone section: sessions, new strategy with checks and confirmation, moves with confirmation and the server's refusal, Supervisor cycle and pause with confirmation, actions denied, daily reports list, paging, detail, retry, empty and copy, the buy and hold line, the new screens at 320 to 768 wide with 200% text, Arabic; layout at 320 to 1440 wide with 200% text, strategies paging and runs, P&L and drawdown, decisions shown as text, limits, kill switch confirmation and cancel, empty, error with retry, denied, Arabic, the section hidden without a source |
| `apps/web/e2e/finance-intel.spec.ts` | Banner, live off, empty states with a next action, loading, error with retry, permission denied, strategy paging and lifecycle filter, walk-forward results, agent filter, kill switch with confirmation (the only write), Arabic right to left, no sideways overflow at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 on desktop and phone projects |

Evidence for this branch is the CI run of PR #104. Local runs before each push: API unit tests above, web typecheck, unit tests and build, the dashboard Playwright spec on both projects, `pnpm docs:check`, bandit, pip-audit and gitleaks. The PostgreSQL dashboard test needs CI's database job.

## Not built yet

Stated plainly so nothing is presented as finished:

* Phone push for the report notice. It needs a push kind of its own; the existing notification push says "Team work", which would mislead, so the notice is in-app (and email by the person's preference) only. In the phone's Notifications it opens Finance, Trading intelligence.
* On the phone, adding instruments, importing bars, running evaluations and choosing a strategy's paper portfolio stay on the web (bar files are imported from a computer).
* Model-written commentary in the reports, and a check of the Oracle VM's load before heavy evaluations.
* Market-data fetch from any provider: blocked on the owner decisions below (providers, their terms, egress). Corporate actions likewise.
* A future live-trading design for independent review (document only; live trading stays off by the hard boundary).

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
