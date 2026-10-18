# Risk-adjusted performance measures (as computed in `finance/intel/backtest.py`)

| Measure | Definition | Read it as |
|---|---|---|
| Total return | last equity / first equity - 1 | What the run made or lost, after costs |
| CAGR | (last / first)^(1 / years) - 1 | Yearly rate; misleading over short periods |
| Volatility | standard deviation of bar returns x sqrt(periods per year) | How bumpy the path was |
| Sharpe ratio | mean bar return / standard deviation x sqrt(periods per year), no risk-free rate | Return per unit of total risk; noisy over short samples |
| Sortino ratio | mean bar return / downside deviation x sqrt(periods per year) | Return per unit of downside risk |
| Maximum drawdown | worst fall from a previous peak | The pain an owner would have sat through |
| Turnover | traded value per year / average equity | How much trading costs matter |
| Win rate, profit factor, expectancy | from closed trades after fees | Shape of results; meaningless with few trades |
| Exposure | share of bars with money in the market | How often the rule is invested |

Compare a strategy with buy and hold of the same symbols over the same span. A higher Sharpe with a lower return, or a higher return with a much deeper drawdown, is a trade-off for the owner to judge, not a win.

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Sharpe (1966), "Mutual Fund Performance", Journal of Business 39(1), 119-138 | Journal citation | 1966 | Copyright the publisher; summarised | Assumes roughly normal returns |
| Sharpe (1994), "The Sharpe Ratio", Journal of Portfolio Management 21(1), 49-58 | Journal citation | 1994 | Copyright the publisher; summarised | ANUM omits the risk-free rate |
| Sortino and Price (1994), "Performance Measurement in a Downside Risk Framework", Journal of Investing 3(3), 59-64 | Journal citation | 1994 | Copyright the publisher; summarised | Target return set to zero in ANUM |
| Lo (2002), "The Statistics of Sharpe Ratios", Financial Analysts Journal 58(4), 36-52 | Journal citation | 2002 | Copyright the publisher; summarised | Shows the large sampling error of Sharpe estimates |
