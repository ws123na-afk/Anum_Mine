# Finance: all-market trading plan and release gates (live trading stays off)

Status on 2 November 2026: **a plan, not an approval to trade.** Live trading is **off** in ANUM and nothing in this document turns it on. ANUM places no real order, payment, deposit, withdrawal or transfer. Every order that runs today is SIMULATED (paper or shadow), checked by the Guardian and approved by the person with the code shown.

This replaces the planning PR #224 (written 23 October 2026 against an older `main`) and is checked against what `main` has today: [Finance](finance.md), [Trading intelligence](finance-trading-intel.md), the [readiness report](finance-trading-readiness.md), [Broker onboarding](finance-broker-onboarding.md), the [SAR 500 paper simulation](finance-sar500-paper-integration.md) and `services/api/anum_api/finance/`.

## What main has today

| Area | Where | State |
|---|---|---|
| Own ledger, holdings, plans, Guardian rules, read-only connectors, recorded exchange rates | `finance/*.py`, [Finance](finance.md) | Built |
| Paper and shadow portfolios and orders, execution gateway, single-use approvals with the payload hash, idempotency keys, kill switches | stages 6 and 7, `finance/trading.py`, `gateway.py`, `execution.py` | Built, SIMULATED only |
| Limited live and wider live: the frameworks only | `finance/live.py`, `finance/wider.py` | **Off**: `LIVE_EXECUTORS` is empty, startup and the Helm chart refuse `ANUM_FINANCE_LIVE_ENABLED` and `ANUM_FINANCE_WIDER_LIVE_ENABLED`, every connector scope is read only, and a database check refuses live intents |
| Calendars (NYSE and Nasdaq, London, Tadawul, DFM, ADX, FX 24/5, crypto 24/7), bars, series checks, indicators, backtests with costs, walk-forward validation, strategy lifecycle, Supervisor, daily reports | `finance/intel/` | Built; network fetch of market data is off |
| Market-data parsers: own CSV, Binance public daily klines, Stooq daily CSV | `finance/intel/marketdata.py` | Built and contract-tested on recorded payloads; no live fetch |
| Broker statement preview, reconciliation, exposure and history from the person's own CSV exports | `finance/broker_*.py`, `apps/web/src/FinanceBroker.tsx` | Built, in memory only |
| SAR 500 stock and crypto paper calculator | `finance/broker_paper.py` | Built, stateless |
| Alpaca paper account reader (fixed paper origin, two GET paths, redirects refused) | `finance/alpaca_paper_reader.py` | Built, **not exposed** and not connected: no key exists |
| Live eligibility check: what is still missing before live could ever be considered | `finance/broker_readiness.py`, `POST /api/v1/finance/broker/readiness`, Finance, Execution | Built, read only ([details](finance.md#live-eligibility-preflight)) |
| Web and phone dashboards, reports, P&L and drawdown over time | `apps/web/src/Finance*.tsx`, `apps/mobile/lib/features/finance/` | Built |

These invariants hold through every step below: the Guardian `gate` denies every real money-moving action; no agent can approve its own request, loosen a rule or enable live; prices and agent output are data, never instructions; paper results are never shown as expected live returns.

## Markets and providers to evaluate

Nothing here is chosen, paid for or connected. Each row needs the owner's decision first.

| Market | Research data | Broker to evaluate | Caveat |
|---|---|---|---|
| US stocks and ETFs | Own CSV now; later a licensed feed or the broker's own data | Interactive Brokers; Alpaca (paper first) | Account country, product permissions, market-data entitlements and API access must be confirmed for the actual account |
| Saudi stocks (Tadawul) | Own CSV now; a licensed Saudi feed later | Interactive Brokers' Saudi exchange access, or a locally licensed broker with a documented API | Access and automation rights must be confirmed for the actual account |
| UAE stocks (DFM, ADX) | Own CSV now; a licensed feed later | A locally authorised broker, once its API and permissions are verified | No retail execution API is assumed |
| FX and spot gold | Own CSV now; broker quotes later | Interactive Brokers FX, or a locally authorised broker with documented spot products | Spot gold and leveraged CFDs are different products with different risks and permissions |
| Crypto spot | Own CSV and recorded Binance public klines now | None by default: a regulated venue eligible for the account holder's country, chosen later | Saudi residents: see the [Saudi note](finance.md#live-eligibility-preflight); custody and withdrawal rules must be checked first |
| Futures, options, margin, leverage | None | None | **Out of scope** for any first live step |

Sources to re-check before opening any account (provider pages change; nothing here is verified for a specific person):

- Interactive Brokers country list: https://www.interactivebrokers.com/en/accounts/open-account-country-list.php
- Interactive Brokers, Saudi Exchange: https://www.interactivebrokers.com/en/trading/saudi-exchange.php
- Interactive Brokers market data subscriptions: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/introduction
- Alpaca documentation (paper and live are separate): https://docs.alpaca.markets/
- Saudi CMA statement on virtual currencies (2018): https://cma.gov.sa/en/MediaCenter/PR/Pages/Virtual_Currencies.aspx

Money always stays in a broker account in the owner's name. ANUM never becomes a deposit wallet.

## Steps, each with the evidence it needs

### A. Market data (paper safe)

1. A typed provider contract for bars and quotes: source time, venue, delay, licence, currency, corporate actions. The provider registry and three parsers already exist; this adds fetching.
2. Egress allowlist on the VM and provider keys in the server's encrypted secret store (`ANUM_SECRETS_KEY`), never in prompts, browser bundles or logs.
3. One adapter for one provider the owner allowed, with chronology, time zone, stale and duplicate checks; simulations refuse thin coverage. No invented prices or holidays.
4. Recorded fixtures, rate-limit and back-off tests, fail-closed tests for stale data, a licence review.

**Gate A:** the owner accepted the provider's terms and cost; local checks green; the screen shows each price's source, age and delay.

### B. Paper trading end to end on that data

1. Feed the allowed data into the existing gateway and Guardian, keeping approval with the payload hash and idempotency.
2. Paper and shadow accounts only: spread, fees, slippage, partial fills, market hours, insufficient cash, rejected orders, corporate actions.
3. Tests across assets: currencies kept apart, restart and replay, repeated clicks, two tenants isolated under RLS.
4. SIMULATED labels everywhere, cash per currency, drawdown, the rejection reason, a benchmark and price provenance on web and phone.

**Gate B:** acceptance by the owner on web and phone; zero broker keys at this stage; no profit claims.

### C. A read-only broker connection

1. The owner picks the broker and products after the eligibility check (the [live eligibility check](finance.md#live-eligibility-preflight) lists what is missing).
2. One broker-specific **read-only** adapter: account, balances, positions, filled-order history; scoped key check and rotation. The Alpaca paper reader is the template: fixed origin, fixed paths, no redirects, size limits.
3. Daily reconciliation between broker and ANUM (the statement reconciliation exists for CSV exports); any difference raises an alert and blocks progress.
4. Health on the dashboard: disconnections, stale sync, audit history, the kill switch.

**Gate C:** least-privilege review, contract tests, a credential security review, reconciliation running cleanly on staging.

### D. A limited live design: separate, reviewed, owner-signed

Not built and not switched on by anything here. Produce a design for an independent security and regulatory review: a documented regulated broker API, account eligibility and product permissions, order-only keys that cannot withdraw or transfer, no assistant access to keys, independent limits, owner approval per order, a cooling-off, an instrument allowlist, per order, day and month caps, position and loss guards, a circuit breaker, reconciliation by broker order id, and **no blind retry** when the network is uncertain. A first step would be small owner-approved orders only: no margin, derivatives, leverage or automatic withdrawals.

**Gate D:** the owner's separate written authorisation for a live change, an independent sign-off, a new reviewed change (executor, contract migration lifting the database's no-live check), supervised acceptance tests. Until then ANUM keeps refusing every live order.

## Owner decisions this plan needs

| Decision | Needed for | Until decided |
|---|---|---|
| Which market-data provider, accepting its terms and any cost | Step A | Own CSV and recorded data only |
| HTTPS egress from the Oracle VM to that provider | Step A | No egress |
| Paper cash per currency, trading universe, Guardian limits for paper trading | Step B | No rules beyond prices, costs and approval |
| Which broker, after checking it accepts the account holder's country and products | Step C | No broker connected |
| A read-only key for that broker, entered by the owner in the server's secret store (never in chat or GitHub) | Step C | No key exists |
| Whether to pursue live at all, and the written approval, limits and independent review it needs | Step D | **Live trading off** |
| Crypto for a Saudi resident: legal advice and the broker's terms | Any crypto beyond paper | Paper only |

## Not built yet

- Any network fetch of market data (A).
- A read-only broker connection; the Alpaca paper reader exists but has no key, no vault grant and no route (C).
- A live executor or any write-scoped broker key (D). By design, while live trading stays off.

## Checks used for this plan

- API: `test_finance_trading.py`, `test_finance_intel*.py`, `test_finance_broker_*.py`, `test_finance_live_preflight.py`, `test_finance_alpaca_paper_reader.py`; PostgreSQL: `test_postgres_finance*.py`.
- Web: finance unit tests, typecheck, build, the finance Playwright specs.
- Phone: `apps/mobile/test/finance_intel_test.dart`.
