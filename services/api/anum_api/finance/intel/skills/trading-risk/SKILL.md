---
name: trading-risk
description: Trading risk controls in ANUM's SIMULATED trading - the Guardian gate, circuit breakers, position caps, daily loss stop, kill switches - plus regulatory and geographic restrictions, trading psychology and how automated trading systems fail. Use when explaining why an order was refused or a strategy suspended, or when the person asks about limits or rules.
version: 1.0.0
anum_skill_id: anum.trading.risk
---

# Trading risk

Everything here is SIMULATED and live trading is off. The Guardian is independent of every agent. No agent can change its own risk limits, approve an order, authorize live trading or bypass the Guardian.

## When to use

- "Explain why the Guardian rejected this simulated trade."
- "Pause all paper trading."
- Explaining a suspension (stale data, abnormal move, daily loss stop, failed revalidation, degrading drawdown).
- Questions about what a regulator allows where the person lives or trades.

## Inputs

- The execution request with its Guardian decision and reasons, the gateway's refusal and the agents' decisions for it.
- The person's Guardian rules (read only here) and the Supervisor's risk limits.

## Outputs

- The decision in plain words with each reason; what the person can change themselves and where.
- A pause of every active paper strategy when the person asks for it.

## Permitted tools

See `tools.json`: `finance.intel.explain_order`, `finance.intel.activity` (read), `finance.intel.pause_all` (tightens only).

## Prohibited actions

- Loosening a Guardian rule or a Supervisor risk limit, or proposing to.
- Approving, resuming or retrying a refused order; switching on live trading.
- Giving legal or tax advice; ANUM points to the regulator and says the date of its information.
- Repeating trading claims from social media, forums or advertisements as facts.

## Failure handling

- The request is not found: say so; ask for the request id from Paper trading.
- A kill switch is on: say who can turn it off (the person's own, or an owner for the organisation's).
- Regulatory question outside these notes: say it must be checked with the named regulator.

## Knowledge

- [Risk controls in ANUM](references/controls.md)
- [Regulatory and geographic restrictions](references/regulation.md)
- [Trading psychology and automated-system failure modes](references/failure-modes.md)
