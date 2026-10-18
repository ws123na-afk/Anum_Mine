# Indicator definitions (as implemented in `finance/intel/indicators.py`)

| Indicator | Definition | Warm-up |
|---|---|---|
| SMA(n) | Mean of the last n closes | n bars |
| EMA(n) | Seeded with SMA(n), then `prev + (2 / (n + 1)) * (close - prev)` | n bars |
| RSI(n) | Wilder: average gain and loss over n changes, smoothed `avg = (avg * (n - 1) + x) / n`; `RSI = 100 - 100 / (1 + gain / loss)`; 100 when there is no loss | n + 1 bars |
| MACD | EMA(12) - EMA(26); signal line EMA(9) of MACD; histogram = MACD - signal | 34 bars for the signal |
| True range | max(high - low, abs(high - previous close), abs(low - previous close)) | 2 bars |
| ATR(n) | Wilder smoothing of true range | n + 1 bars |
| ADX(n) | Wilder: directional movement +DM and -DM, +DI and -DI from smoothed DM over ATR, DX = 100 abs(+DI - -DI) / (+DI + -DI), ADX = Wilder average of DX | about 2n bars |
| Volatility(n) | Sample standard deviation of n log returns times the square root of periods per year (252 exchanges, 260 currencies, 365 crypto) | n + 1 bars |
| Momentum(n) | close / close n bars ago - 1 | n + 1 bars |
| Donchian high or low(n) | Highest or lowest close of the n bars before the current one (the current bar is left out) | n + 1 bars |

**Regimes**: trend is `up` when close > SMA 50 > SMA 200 and ADX is at least 20, `down` in the mirror case, `sideways` otherwise, `unknown` without 200 bars. The volatility regime compares today's 20-bar volatility with the quartiles of its own history (at least 60 values).

**Worked check**: the StockCharts RSI tutorial's 14-period example prints 70.53 using averages rounded to two places; the unrounded Wilder averages give 70.46, which is what ANUM computes and tests.

**Limits**: indicators lag; they are computed in floating point from recorded closes; daily bars hide intraday moves; different vendors seed EMAs and Wilder averages differently, so their values can differ slightly from ANUM's.

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Wilder, J. Welles (1978), New Concepts in Technical Trading Systems, Trend Research | Book; no free copy | 1978 | Copyright the author; definitions summarised | Original definitions of RSI, ATR and ADX; no evidence of profitability is implied |
| Appel, Gerald (2005), Technical Analysis: Power Tools for Active Investors, FT Prentice Hall | Book; no free copy | 2005 | Copyright the publisher; summarised | MACD as described by its originator; trading claims in the book are not adopted |
| StockCharts ChartSchool, "Relative Strength Index (RSI)" | https://chartschool.stockcharts.com (Relative Strength Index article) | Accessed October 2026 | Copyright StockCharts; worked example cited | Rounds intermediate values; used only as a numeric cross-check |
| Donchian channels, as described in Faith, Curtis (2007), Way of the Turtle, McGraw-Hill | Book; no free copy | 2007 | Copyright the publisher; rule summarised | Historical trading results in the book are not verified and not presented as fact |
