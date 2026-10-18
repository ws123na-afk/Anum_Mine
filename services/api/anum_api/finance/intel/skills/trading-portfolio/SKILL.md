---
name: trading-portfolio
description: Paper portfolio intelligence for ANUM's SIMULATED trading - positions, simulated cash and P&L, fees and slippage, allocation, concentration and diversification, and comparison with buy and hold. Use when the person asks how their paper portfolio is doing or how it compares with a benchmark.
version: 1.0.0
anum_skill_id: anum.trading.portfolio
---

# Paper portfolio

Everything here is SIMULATED and live trading is off. Paper portfolios hold simulated cash only. Nothing in them is real money and nothing here moves real money.

## When to use

- "How is my paper portfolio doing?", "Compare my paper portfolio against an appropriate benchmark."
- The Portfolio agent's post-close review: cash, positions, realised and unrealised P&L, fees, unpriced holdings.

## Inputs

- Paper portfolios and their filled SIMULATED orders (replayed into a book), recorded prices, recorded exchange rates.
- The Performance Reviewer's records of each active strategy against buy and hold of the same symbols.

## Outputs

- Cash, positions and P&L per currency with fees and slippage shown; holdings without a recent price listed as not valued.
- The paper period's return and drawdown against buy and hold over the same days, with the dates.

## Permitted tools

See `tools.json`: `finance.paper.portfolios`, `finance.intel.benchmark` (read only).

## Prohibited actions

- Converting currencies with a rate that is not recorded, or adding different currencies together.
- Calling a simulated gain real, or projecting it forward.
- Proposing a real transfer, purchase or sale.

## Failure handling

- A holding with no recent price: list it as not valued instead of guessing a price.
- No reviewed paper period yet: say that the Performance Reviewer compares after the close, and when.

## Knowledge

- [Diversification and portfolio theory](references/diversification.md)
