# Cycles, phases and the lifecycle

| Phase | When (default, in the schedule's time zone) | What runs |
|---|---|---|
| `pre_open` | 08:30 daily | Market Research and Technical Analysis for every active strategy |
| `intraday` | 12:00 daily | Signals to SIMULATED paper order requests through the gateway |
| `post_close` | 17:30 daily | Signals, then the Portfolio review and the Performance Reviewer |
| `weekly` | Friday 18:00 | Analysis and the comparison with buy and hold |
| `monthly` | 1st at 07:00 | Out-of-sample revalidation of active strategies |

Daily phases skip themselves on days the venue does not trade (`market_closed`). A kill switch halts the cycle. Each phase runs once per person and day; repeats are skipped (`already_ran`).

**Lifecycle**: DRAFT, BACKTESTING, VALIDATION, PAPER_ACTIVE, PAPER_SUSPENDED, RETIRED. Agents research and suspend; only the person promotes to PAPER_ACTIVE, resumes and retires. Promotion needs a passed, reproduced walk-forward run of the exact version, at least 3 folds, a drawdown no worse than 35 percent, a benchmark comparison and a paper portfolio. A change is a new version with a reason. LIVE_CANDIDATE is a documented future idea only and grants nothing.

**Owner commands** and the tool each uses:

| The person says | Tool |
|---|---|
| "What did the trading agents do today?" | `finance.intel.activity` |
| "Which paper strategy performed best after transaction costs?" | `finance.intel.strategies` (sort after costs) |
| "Explain why the Guardian rejected this simulated trade." | `finance.intel.explain_order` |
| "Show the worst drawdown for each strategy." | `finance.intel.strategies` (sort by drawdown) |
| "Pause all paper trading." | `finance.intel.pause_all` |
| "Compare my paper portfolio against an appropriate benchmark." | `finance.intel.benchmark` |
| "Which strategy needs revalidation?" | `finance.intel.strategies` (needs revalidation) |

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| ANUM trading intelligence documentation | docs/finance-trading-intel.md in the ANUM repository | October 2026 | Repository documentation | Describes this release |
