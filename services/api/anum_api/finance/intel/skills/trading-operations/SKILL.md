---
name: trading-operations
description: The Trading Supervisor's daily operations in ANUM's SIMULATED trading - pre-open, intraday, post-close, weekly and monthly cycles, the agents' decisions, daily reports, strategy lifecycle and the owner's commands. Use when the person asks what the trading agents did, which strategy needs revalidation, or how a cycle works.
version: 1.0.0
anum_skill_id: anum.trading.operations
---

# Trading operations

Everything here is SIMULATED and live trading is off. The Supervisor is deterministic code that coordinates seven agents and records every decision. It never approves an order and never trades live.

## When to use

- "ANUM, what did the trading agents do today?"
- "Which paper strategy performed best after transaction costs?" and "Show the worst drawdown for each strategy."
- "Which strategy needs revalidation?"
- Explaining a cycle, a phase, the lifecycle or a skipped or halted run.

## Inputs

- The agents' decisions grouped by cycle; strategy versions and their runs; the schedules the person created.

## Outputs

- A day's activity per agent with what was proposed, refused or suspended and why.
- Strategies ranked by return after costs or by worst drawdown, with buy and hold beside them, and which active ones need revalidation (missing, failed or older than 30 days).

## Permitted tools

See `tools.json`: `finance.intel.activity`, `finance.intel.strategies`, `finance.intel.benchmark` (read only).

## Prohibited actions

- Running a Supervisor cycle, promoting, resuming or retiring a strategy on the person's behalf from a conversation (those are the person's own actions in Finance).
- Describing a proposed order as executed: it waits for the person's approval.

## Failure handling

- No decisions on the day: say so, and that cycles run on the person's schedules or when they press "Run cycle".
- A cycle skipped as `already_ran`: explain that each phase runs once per person and day.

## Knowledge

- [Cycles, phases and the lifecycle](references/cycles-and-lifecycle.md)
