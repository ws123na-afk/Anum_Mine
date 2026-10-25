# ANUM trading: broker connection delivery plan (23 October 2026)

## What this branch actually delivers

A typed, testable, fail-closed onboarding checklist in `finance/broker_readiness.py`.
It reports missing evidence for regulated brokerage custody, read-only market-data access,
research validation and independent live-readiness review. It **does not** connect a
broker, hold deposits, transfer money, fetch real market data or place real orders.
No deployment variable or gate has been relaxed.

## Mandatory custody model

Deposits and withdrawals must occur directly with the owner's regulated brokerage
account. ANUM must never advertise itself as a deposit destination or accept customer
funds, brokerage passwords, withdrawal-scoped keys or bank-card credentials. A
read-only brokerage connection may eventually surface positions and cash balances.
Currency amounts must retain currency and source timestamp.

## Delivery work still required

1. **Provider decision:** choose actual permitted securities venues, a regulated
   broker serving the account's jurisdiction, supported markets, data licence,
   official API documentation, account eligibility and pricing.
2. **Broker connector:** build a credential-vault backed read-only adapter with
   short-lived permissions, secret rotation, least privilege, verified holdings
   and transaction reconciliation. Never log raw credentials. Distinguish
   quote time, server time and delayed vs real-time market data.
3. **Research and paper trading:** provision permitted price sources, test data
   freshness and corporate actions, validate walk-forward strategies and
   paper-trade through the existing execution gateway and Guardian. Record fees,
   slippage, exchange sessions, outages, limits and drawdown.
4. **Independent security review:** assess user consent, custody and trading
   law, accounting, order replay/idempotency, broker error modes, partial fills,
   order cancels, market closures, kill switch and loss caps. Run production-like
   dry tests against a sandbox only if a broker offers one.
5. **Live execution:** future separately reviewed change only. There is no
   live-order implementation in ANUM. Require explicit owner authorization,
   broker execution scopes with withdrawals disabled, tested reconciliation,
   and a new review before considering any live release.

## Acceptance checklist

- [ ] Provider selected and documented with relevant jurisdiction and permission
- [ ] Read-only API connected, encrypted and scoped
- [ ] Market data terms approved and delayed data labelled correctly
- [ ] Historical data and corporate actions verified
- [ ] Existing Guardian and paper approval regressions pass
- [ ] Emergency stop and order replay scenarios verified independently
- [ ] Independent custody, regulatory and security review completed
- [ ] Owner's written approval documented
- [ ] All CI checks green and deployment verified

**Do not enable** `ANUM_FINANCE_LIVE_ENABLED` or
`ANUM_FINANCE_WIDER_LIVE_ENABLED` based on this checklist.
The existing startup and gateway refusals are intentional.
