---
name: trading-strategy-research
description: How ANUM researches strategies - trend following, momentum and breakout, mean reversion, pairs, periodic investing, fundamentals and valuation, backtesting methodology and its biases, and risk-adjusted performance - with walk-forward validation and the rules that reject unreliable results. Use when evaluating, comparing or explaining a strategy and its results.
version: 1.0.0
anum_skill_id: anum.trading.strategy-research
---

# Strategy research

Everything here is SIMULATED and live trading is off. Every result is SIMULATED on past bars with stated costs. A strategy is never called profitable, approved or recommended because a backtest made money.

## When to use

- The person asks which strategy did best after costs, or why one was rejected.
- The Strategy Research agent runs a backtest, a walk-forward validation or the monthly revalidation.
- Comparing a strategy with buy and hold, or explaining a hypothesis and its entry and exit rules.

## Inputs

- A stored strategy version (rule key, code version, symbols, parameters) and its stored bars.
- Stored runs: data SHA-256, costs, metrics, verdict, reasons, folds, reproduced flag, report.

## Outputs

- Results with their verdict and every reason in words, the comparison with buy and hold over the same span, the worst drawdown and the worst test window, and the data fingerprint.
- For a change: a new version with a reason and the run that justifies it (never an edit of the old version).

## Permitted tools

See `tools.json`: `finance.intel.strategies`, `finance.intel.benchmark` (read only).

## Prohibited actions

- Promoting a strategy to paper trading, resuming it or retiring it (the person's actions only).
- Changing a version's rule, symbols or parameters in place, especially after a loss.
- Selecting the best of many runs and reporting it as if it were the only one tried.
- Presenting an online or social-media strategy or profit claim as proven.

## Failure handling

- Not enough bars or folds: report `insufficient_data` and what is missing.
- Rejected: list every reason (`data_issues`, `look_ahead`, `too_few_trades`, `overfit`, `no_edge`, `unstable`) and do not soften it.
- Not reproduced: the run cannot support promotion; re-run on the same bars and report the difference.

## Knowledge

- [Strategy families and their evidence](references/strategy-families.md)
- [Backtesting methodology and biases](references/backtesting-and-biases.md)
- [Risk-adjusted performance](references/performance-measures.md)
- [Fundamental analysis and valuation](references/fundamentals.md)
