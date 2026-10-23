# ANUM SAR 500 stock and crypto simulation

Scope: **research and paper calculations only**. Starting budget is SAR 500 simulated, no deposits into ANUM and no broker credentials. This is not a connected brokerage account, a price feed, or an investment recommendation.

## Eligibility checks

- Interactive Brokers lists Saudi Arabia among countries where account applications may be accepted. Account approval, individual instruments and crypto permissions must be confirmed by the broker and are **not** implied by account geography.
- Alpaca advertises international individual stock brokerage accounts and offers paper trading, but crypto is limited to eligible participants and jurisdictions. Eligibility for an individual resident of Saudi Arabia is **unverified**.
- Saudi regulators have issued cautions about virtual currencies. Crypto live execution is disabled until actual legal and broker-specific eligibility is established.
- Users must not fund ANUM itself. Any later live funds belong in their own authorized brokerage account.

## Available endpoint

`POST /api/v1/finance/broker/paper/simulate` uses the existing authenticated finance-read permission and accepts a portfolio, reviewed manual SAR-denominated quote, trade intent and estimated fee. The response includes both unchanged and simulated portfolios, reason for rejection, and explicit `live_trading_enabled=false`, `real_orders_sent=0`, `money_transferred=false`.

The endpoint is stateless and will not save paper positions. The web panel optionally applies a successful paper result **locally to its current browser state only**; a refresh discards the simulation. Never represent this as an executed broker order.

## Guardian limits (paper only)

- Simulated initial budget: up to SAR 500; no real deposit.
- Each simulated buy and aggregate asset exposure: 20% of the initial budget (SAR 100 at SAR 500).
- Paper daily loss threshold: 2% of initial balance (SAR 10), based on caller-provided realised P&L. Because this value is not independently verified, this endpoint is **not** an enforcement mechanism for live funds.
- No short selling, margin, leverage or order execution.
- A locally engaged paper halt blocks simulations. Reset restores default local sandbox.

## Production connection gates

Require approved broker, eligibility and custody verification, permissions and secure credential storage, scheduled price/snapshot ingestion, account mapping, persisted audit trail, broker outages/timeout behavior, idempotency, independently verified Guardian rules, dual-person live approval, and emergency stop. None of these gates are satisfied merely by paper-simulation tests. Live execution remains out of scope.

## Paper broker account reader (not yet tenant-connected)

`alpaca_paper_reader.py` implements fixed-origin read-only `GET /v2/account` and `GET /v2/positions` calls to the official Alpaca paper domain. It requires paper API credentials that ANUM does not have; tests use a fake injected transport and exercise the endpoint allowlist, response size limit and secret redaction. No browser can supply keys, and no public API endpoint currently exposes this reader. This is deliberate: production integration must first add a vault-backed, workspace-scoped credential grant with revocation and an audited connection lifecycle. The account reader does not issue orders and is not evidence of live account eligibility. Do not confuse its USD balances with the separate SAR 500 offline simulator; no automatic FX conversion is implemented.
