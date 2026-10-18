---
name: trading-technical-analysis
description: Deterministic technical indicators (SMA, EMA, RSI, MACD, ATR, ADX, volatility, momentum, Donchian channels), their exact definitions, regime labels and contradictions, for ANUM's SIMULATED trading intelligence. Use when explaining an indicator, a trend or volatility regime, or why a rule fired.
version: 1.0.0
anum_skill_id: anum.trading.technical-analysis
---

# Technical analysis

Everything here is SIMULATED and live trading is off. Indicators describe past prices. They are research figures, not forecasts, and ANUM never presents one as a reason a price will move.

## When to use

- The person asks what RSI, ATR or a moving-average cross means for a strategy of theirs.
- The Technical Analysis agent records a snapshot: trend, volatility regime, contradictions.
- Explaining why a rule entered or left on a given bar.

## Inputs

- Stored daily bars, oldest first. Every value at bar `t` uses bars `0..t` only (tested by cutting the future off).

## Outputs

- The indicator values at the last bar, `trend` (up, down, sideways, unknown), `volatility_regime` (low, normal, high, unknown) and contradictions such as `uptrend_but_overbought`.

## Permitted tools

See `tools.json`: `finance.intel.strategies` and `finance.intel.activity` (read only).

## Prohibited actions

- Calling an indicator a prediction, a signal to buy or sell for real, or a guarantee.
- Computing a value from bars after the decision bar (look-ahead).
- Inventing a value during warm-up: it is "not enough history" (`None`).

## Failure handling

- Too few bars: say how many are needed (for example 200 for SMA 200, 60 for the volatility regime).
- Indicators that disagree: report the contradiction instead of choosing one.

## Knowledge

- [Indicator definitions](references/indicators.md)
