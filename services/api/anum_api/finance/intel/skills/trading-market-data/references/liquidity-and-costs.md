# Liquidity, spread, volatility and slippage

- **Liquidity**: how much can be traded without moving the price. Thin markets have wide spreads and large price impact.
- **Spread**: paid on every round trip (buy at the ask, sell at the bid). ANUM charges half the spread on each fill.
- **Slippage**: the gap between the price a decision saw and the price actually obtained. ANUM adds it against every simulated fill.
- **Market impact**: a large order moves the price; ANUM caps a simulated fill at a share of the bar's volume (5 percent by default) and refuses a fill on a bar without volume.
- **Volatility**: the size of price moves, usually the annualised standard deviation of log returns. It sets position size in the volatility-targeted rules.
- **Fees**: broker or exchange commission; ANUM uses basis points with an optional minimum.

Default simulation costs: fee 10 bps, spread 5 bps, slippage 5 bps, volume cap 5 percent. They are assumptions, shown with every result, and real costs can be higher, especially for small or Gulf-listed instruments and in stressed markets.

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Amihud (2002), "Illiquidity and stock returns: cross-section and time-series effects", Journal of Financial Markets 5(1), 31-56 | https://doi.org/10.1016/S1386-4181(01)00024-6 | 2002 | Copyright the publisher; summarised | U.S. equities; a liquidity measure, not a cost model |
| Almgren and Chriss (2000), "Optimal execution of portfolio transactions", Journal of Risk 3(2), 5-39 | Journal citation; no free copy | 2000 | Copyright the publisher; summarised | A model of impact; parameters must be estimated per market |
| U.S. SEC, Investor.gov, "Bid-ask spread" and order types | https://www.investor.gov | Accessed October 2026 | Public domain (U.S. government work) | General guidance |
