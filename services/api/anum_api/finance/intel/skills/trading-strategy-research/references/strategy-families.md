# Strategy families and their evidence

Each family below is one of ANUM's research candidates. "Evidence" means published academic studies of past data. It is not a promise: published effects often weaken after publication, after costs, or in other markets.

| Candidate | ANUM rules | Hypothesis | Published evidence and its limits |
|---|---|---|---|
| 1. Periodic investing and rebalancing | `periodic`, `rebalance` | Buying in equal steps reduces the risk of one badly timed entry; rebalancing keeps the risk mix from drifting | Diversification lowers risk for a given expected return (Markowitz 1952); a simple equal-weight portfolio is hard to beat out of sample (DeMiguel, Garlappi and Uppal 2009). Periodic investing does not raise expected return |
| 2. Trend following with volatility controls | `trend_sma`, `trend_vol` | Assets that have risen tend to keep rising for some months; sizing by volatility steadies risk | Time-series momentum across futures markets (Moskowitz, Ooi and Pedersen 2012). Results depend on markets, period and costs; long flat or choppy periods lose |
| 3. Momentum and breakout | `momentum`, `breakout` | Recent winners keep outperforming recent losers over the next months | Cross-sectional momentum in U.S. shares (Jegadeesh and Titman 1993); prone to sharp crashes when trends reverse |
| 4. Mean reversion with regime filters | `rsi_reversion`, `rsi_regime` | Short, sharp falls in liquid instruments partly reverse, more reliably in a calm uptrend | Short-horizon reversal is documented but small and eaten quickly by costs; the regime filter is ANUM's research hypothesis, not an established result |
| 5. Statistical pairs | `pair_ratio` | Two related instruments drift apart and back | Pairs trading on U.S. shares earned excess returns in the studied period (Gatev, Goetzmann and Rouwenhorst 2006), with a long and a short leg. ANUM holds only the cheap leg (no shorting), which is a weaker version |
| 6. Crypto spot | `crypto_trend` | Crypto assets trend strongly and crash hard; a trend filter with volatility sizing may keep part of the trend | Evidence is short, unstable and venue dependent. Freqtrade is not adopted (GPL 3.0, its own bot loop and keys), so no Freqtrade strategy is run; this is a native rule tested like the others |

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Markowitz (1952), "Portfolio Selection", Journal of Finance 7(1), 77-91 | https://doi.org/10.1111/j.1540-6261.1952.tb01525.x | 1952 | Copyright the publisher; summarised | Assumes known means and covariances; estimates are noisy |
| DeMiguel, Garlappi and Uppal (2009), "Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?", Review of Financial Studies 22(5), 1915-1953 | https://doi.org/10.1093/rfs/hhm075 | 2009 | Copyright the publisher; summarised | Specific datasets and estimation windows |
| Moskowitz, Ooi and Pedersen (2012), "Time series momentum", Journal of Financial Economics 104(2), 228-250 | https://doi.org/10.1016/j.jfineco.2011.11.003 | 2012 | Copyright the publisher; summarised | Futures markets with low costs; a later period may differ |
| Jegadeesh and Titman (1993), "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency", Journal of Finance 48(1), 65-91 | https://doi.org/10.1111/j.1540-6261.1993.tb04702.x | 1993 | Copyright the publisher; summarised | U.S. shares, long and short; ANUM is long only |
| Gatev, Goetzmann and Rouwenhorst (2006), "Pairs Trading: Performance of a Relative-Value Arbitrage Rule", Review of Financial Studies 19(3), 797-827 | https://doi.org/10.1093/rfs/hhj020 | 2006 | Copyright the publisher; summarised | Long-short with short-selling costs; returns declined in later years of the sample |
| Freqtrade project | https://www.freqtrade.io | Accessed October 2026 | GPL 3.0 | Evaluated, not adopted: see the gap assessment |
