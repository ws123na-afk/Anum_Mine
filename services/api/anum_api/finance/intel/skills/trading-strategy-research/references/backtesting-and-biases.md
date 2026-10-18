# Backtesting methodology and common biases

## How ANUM backtests

- Signals at the close of bar t; fills at the open of bar t+1 (never at a price the rule has seen).
- Every fill pays half the spread, slippage and a fee; fills are capped at a share of the bar's volume; no leverage and no shorting.
- Walk-forward validation: parameters chosen on a training window, measured on the next unseen window; windows never overlap and run forward in time. A stored version is validated with its own parameters fixed.
- The run is repeated on the same bars and marked reproduced only if identical. Every result carries the SHA-256 of the bars used.

## Biases and how each is handled

| Bias | What goes wrong | ANUM's check |
|---|---|---|
| Look-ahead | The rule uses data it could not have had | Indicators are causal (tested); a probe cuts the series at 8 points and refuses a rule whose earlier signals change (`look_ahead`) |
| Survivorship | Testing only instruments that still exist overstates the past | Instruments without listing dates carry `survivorship_unknown`; delisted instruments are kept to their last bar |
| Overfitting and data snooping | Tuning many settings finds noise | Small fixed grids, `overfit` when out-of-sample Sharpe falls below half of in-sample, `unstable` when chosen settings keep changing; the number of runs per version is capped and every run is kept |
| Selection bias across many strategies | The best of many backtests looks good by chance | Every run is stored; reports never hide rejected runs; the deflated Sharpe ratio is the reference for how much to discount |
| Costs ignored | Paper returns vanish after fees and spread | Costs on every fill; turnover is reported |
| Too few trades | A result from a handful of trades means little | `too_few_trades` below 5 closed trades out of sample |
| Regime dependence | A rule works only in one market phase | Several folds across time; monthly revalidation of active rules |

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| Bailey, Borwein, López de Prado and Zhu (2014), "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance", Notices of the AMS 61(5), 458-471 | https://www.ams.org/notices/201405/rnoti-p458.pdf | 2014 | Free to read from the AMS; summarised | Statistical argument; thresholds depend on assumptions |
| Bailey and López de Prado (2014), "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality", Journal of Portfolio Management 40(5), 94-107 | Journal citation; a preprint is on SSRN | 2014 | Copyright the publisher; summarised | Needs the number of trials and return moments; not yet computed by ANUM |
| Brown, Goetzmann, Ibbotson and Ross (1992), "Survivorship Bias in Performance Studies", Review of Financial Studies 5(4), 553-580 | https://doi.org/10.1093/rfs/5.4.553 | 1992 | Copyright the publisher; summarised | Fund returns; the principle applies to any universe chosen today |
