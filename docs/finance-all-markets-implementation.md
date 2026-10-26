# ANUM_Mine: all-market trading implementation and release gates

Status: **plan committed; PAPER ONLY; real deposits and live order submission are not supported**.
Audited 2026-10-23 against `docs/finance.md`, `docs/finance-trading-readiness.md`, `docs/finance-trading-intel.md`, `services/api/anum_api/finance/live.py`, and existing dashboard and test paths. This document is not an approval to trade.

## Existing code verified in repository

- Finance ledger, holdings, plans, personal Guardian risk policy, read-only connections, recorded FX rates.
- Stage 6 paper/shadow portfolios and orders, Stage 7 execution gateway, owner-bound single-use approvals, idempotency and kill switches.
- Research components in `finance/intel/`: calendars, CSV/recorded public market-bar parsers, indicators, backtests with costs, walk-forward validation, strategy lifecycle and scheduled decision records.
- Web and Flutter simulation dashboards, reports, equity/drawdown, agent decision explanations, API and automated test suites.
- The existing code explicitly **rejects all live execution**: `LIVE_EXECUTORS = {}`, write-scoped finance connectors are forbidden, DB constraints block approved/executed live intents, and startup rejects live flags. Preserve these invariants during paper work.

## Market coverage and provider shortlist

| Market | Data for research | Broker exploration | Caveat |
| --- | --- | --- | --- |
| US equities and ETFs | Licensed broker feed or paid licensed feed; initially owner CSV | Interactive Brokers (IBKR), Alpaca paper | Account country, permissions, market-data entitlements and API access require confirmation. |
| Saudi equities (Tadawul) | Licensed Saudi market feed or owner CSV | IBKR + its Saudi exchange access, or a locally licensed broker that documents a retail API | Saudi exchange access and automation rights must be independently confirmed for the actual account. |
| UAE equities (DFM/ADX) | Licensed exchange/vendor feed or owner CSV | Locally authorised broker after API and permission verification | No retail execution API is assumed. |
| FX and spot gold | Licensed broker quotes, recorded CSV | IBKR FX or a locally authorised broker with documented spot products | Spot gold and leveraged CFDs are different instruments with different risks and legal permissions. |
| Crypto spot | Licensed/allowed exchange feed or owner CSV | Jurisdiction-eligible regulated venue to be selected later | Jurisdiction, residency, custody and withdrawal restrictions must be checked first. No default crypto live venue. |
| Futures and derivatives | Licensed broker data | IBKR subject to explicit permissions | **Out of scope for initial paper-to-live launch**, due to leverage and product risk. |

Sources to re-check before opening accounts:
- IBKR eligible regions: https://www.interactivebrokers.com/en/accounts/open-account-country-list.php
- IBKR Tadawul access: https://www.interactivebrokers.com/en/trading/saudi-exchange.php
- Alpaca paper/API separation: https://docs.alpaca.markets/
- IBKR market data requirements: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/introduction

Provider eligibility, licensing, API terms and crypto availability must be verified for the actual Saudi-resident client and nationality at onboarding, not assumed from these links. Money remains at a broker account in the owner's name, NEVER an ANUM deposit wallet.

## Implementation sequence (each step has explicit acceptance evidence)

### PR A: Market-data foundation (paper safe)
1. Create typed market provider contracts for candles/quotes, source timestamp, venue, delay, licence, currency and corporate actions.
2. Add explicit opt-in egress allowlists and credential references in server secret storage, never in prompts, client bundles or logs.
3. Build an adapter for a selected **allowed** provider. Store immutable source metadata, validate chronology, timezone, stale quotes and duplicate bars; block simulation on insufficient coverage.
4. Validate exchange sessions, DST, half-days, Saudi/UAE holidays and missing bars. Do not invent prices or holidays.
5. Add deterministic unit and integration tests with recorded fixtures, 429/backoff tests, stale-data fail-closed tests and license compliance review.
**Gate A:** approved feed rights and documented cost, CI green and owner can see price source/age/delay.

### PR B: Paper execution end-to-end
1. Wire permitted data to the existing Gateway and Guardian, preserving owner approval, payload hash and unique idempotency.
2. Use only paper/shadow ledger accounts with simulated cash. Include spread, fees, slippage, partial fills, market-hours checks, insufficient funds, rejected orders and corporate-action adjustments.
3. End-to-end test all asset classes with deterministic fixtures, multi-currency accounting, reboot/replay, concurrency, repeated-click and two-tenant isolation tests.
4. Show explicit SIMULATED badge, NAV, realised/unrealised P&L, cash by currency, portfolio drawdown, rejected order reason, benchmark and exact price provenance in web and Flutter.
5. Add paper-only strategies with versioned configuration, walk-forward evidence and manual lifecycle promotion.
**Gate B:** regression tests and real user acceptance on web and Flutter, zero live broker credentials in this stage; no profit claims.

### PR C: Broker-readiness (read-only first)
1. Owner selects actual broker and product permissions after eligibility/licensing check.
2. Build one broker-specific **read-only** adapter for account metadata, balances, portfolio holdings and filled order history; scoped credential validation and secret rotation.
3. Reconcile daily between broker and ANUM: balances, positions, corporate actions, exchange rates and fees. Unmatched data produces alert and blocks progression.
4. Add operational dashboards for broker disconnections, stale account sync, audit history, support/export and kill switch.
**Gate C:** least privilege review, contract tests, credential security review, reconciliation and observed staging/production read-only health.

### PR D: Limited live design, separate review and explicit owner sign-off
NO implementation or flags enabled automatically. Produce independent security and regulatory review of a proposed order-only broker API. Required: documented regulated broker API and account eligibility, per-product permissions, order-only keys incapable of withdrawal or transfer, restricted IP/permissions, no AI secret access, independent limits, owner 2FA approval, cool-off, instrument allowlist, max per order/day/month, position and loss guards, circuit breaker, reconciliation and incident runbook. Ensure executions and fills are idempotently reconciled with broker order IDs, and network uncertainty **never** causes a blind retry. First launch is small owner-approved orders only, no autonomous withdrawals, margin, derivatives or levered positions.
**Gate D:** explicit separate written authorisation to initiate a new live-trading change; independent sign-off; new migrations and code review; supervised acceptance tests. Until then, the existing system must continue to reject all live orders.

## Regression and release commands (match repository tooling)
- API: targeted `services/api/tests/test_finance_trading.py`, `test_finance_intel.py`, `test_finance_intel_lifecycle.py`, `test_finance_intel_dashboard.py`, `test_finance_intel_equity.py`, then full tests.
- PostgreSQL: finance isolation tests with forced RLS and database constraints.
- Web: finance unit tests, typecheck, production build and Playwright finance specs.
- Flutter: `apps/mobile/test/finance_intel_test.dart`; platform build checks and user device test.
- CI: security scans, secret scans, migration tests, approved review, deployment smoke test and rollback plan.

## Defaults until owner configures a paper profile
- No real funds anywhere in ANUM. Simulated starting capital is chosen by owner; not silently assumed.
- Regions/timezones shown separately and currency balances never silently aggregated.
- Short/leveraged/margin orders and real transfers disabled.
- Agents may research, propose and pause paper strategies but cannot approve their own actions, loosen Guardian controls or enable live execution.
- Paper portfolios have user-defined allocation, per-trade limits, daily simulated loss cap and a single visible STOP control.
- Never show paper performance as expected live returns.

## Actual remaining blockers as of audit
- No verified external live market-data retrieval; currently CSV and recorded/synthetic data.
- No chosen licensed provider, accepted terms, provider key or VM outbound egress.
- No read-only broker account provider integration; no real-money deposit or custody support (by design).
- No live executor; no scoped order-only broker integration; no independent live review.
- CI and deployment state for the latest HEAD must be checked before claiming any end-to-end release is ready.

## Owner inputs to enable paper setup (not needed for this planning commit)
Broker account country/residency eligibility, selection of market-data licensing tier and consent, permitted VM egress; paper cash by currency, trading universe and risk budgets. No broker secrets should be pasted into GitHub or chat.

## Success definition
**Paper-ready:** validated licensed data -> strategy decision -> Guardian -> human approval -> simulated execution -> auditable report, reproducible end-to-end on web and Flutter with no possible path to live trade.
**Live-ready:** a separate reviewed change plus owner authorisation and broker permissions. Neither is satisfied by this document.
