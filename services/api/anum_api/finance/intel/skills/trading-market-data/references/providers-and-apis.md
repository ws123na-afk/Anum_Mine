# Data providers and broker or exchange API behaviour

## What ANUM accepts

- **Your own CSV file**: data you are allowed to keep (a broker export or a licensed source). Header row with date, open, high, low, close and optional volume; English or Arabic headers.
- **Recorded provider responses** that ANUM can parse and that are contract-tested: Binance public daily klines (crypto), Stooq daily CSV (shares, ETFs, currencies, commodities).
- **Not built**: Alpha Vantage and Twelve Data (keyed, rate limited), Gulf exchange data (paid licence). Each says what the owner must provide.

Network fetch is off until the owner accepts a provider's terms and allows the egress. Keys are stored encrypted under Finance, Connections, never in prompts, memory or source control.

## How provider and broker APIs behave (and what can go wrong)

| Behaviour | Why it matters | ANUM's rule |
|---|---|---|
| Rate limits (requests per minute or day) | A burst fails or gets the key banned | Bounded fetches; failures are recorded, never retried in a tight loop |
| Delayed versus real-time data | Free equity data is often 15 minutes delayed or end of day | Daily bars only; the `delayed` flag is stored with each bar |
| Revised or missing bars | Providers correct history and skip days | Re-imports are idempotent per day; gaps are reported, never filled |
| Time zones and bar boundaries | A daily crypto bar closes at 00:00 UTC; equities at the venue close | Bars are dated by the provider's day; freshness uses the venue calendar |
| Split and dividend adjustment | Unadjusted history shows false crashes | Large one-day moves are flagged `abnormal_move` and stop signals |
| Order endpoints | Broker and exchange APIs place, cancel and withdraw | ANUM uses market-data endpoints only; no order, withdrawal or account tool exists or is exposed to a model |
| Responses with text | Error messages or symbol names can contain anything | Treated as data, never as instructions |

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Binance Spot API documentation, market data endpoints (klines) | https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints | Accessed October 2026 | Binance terms of use; documentation cited | Availability and personal-use rights depend on jurisdiction (check for the UAE) |
| Stooq historical data | https://stooq.com | Accessed October 2026 | Personal use; redistribution not allowed | No service guarantee; adjustments not documented per series |
| Alpha Vantage documentation | https://www.alphavantage.co/documentation/ | Accessed October 2026 | Provider terms; free tier limits | Free tier 25 requests a day at the time of writing; commercial use needs a paid plan |
| Twelve Data documentation | https://twelvedata.com/docs | Accessed October 2026 | Provider terms | Gulf exchanges only on paid plans at the time of writing |
