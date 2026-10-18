# Risk controls in ANUM (in the order they apply)

1. **Kill switches**: the organisation's (owners and organisation administrators) and the person's own. Either stops every simulated executor and halts a Supervisor cycle before any agent runs.
2. **Lifecycle**: only a PAPER_ACTIVE version with a paper portfolio may propose orders; only the person puts a version there (also a database check).
3. **Risk Guardian (Supervisor)**, before any order: allowed asset classes and a single currency; data fresh (one session behind skips the signal, more suspends); abnormal last move and missing volume circuit breakers; at most 25 percent of equity per symbol; a 3 percent daily simulated loss suspends the strategy. Limits can only be made stricter.
4. **Guardian gate** (the person's own rules, `finance/guardian.py`): allowed currencies, single and daily limits, category caps, blocked merchants, quiet hours, cooling-off. A deny stops the order.
5. **Approval**: every SIMULATED order still waits for the person's approval bound to its payload hash; it expires and is used once.
6. **Performance Reviewer and monthly revalidation**: suspend a strategy whose paper drawdown is worse than validation allowed (times 1.5) or that no longer passes walk-forward.

Every refusal is recorded with reason codes; the dashboard and `finance.intel.explain_order` show them.

## Sources

| Source | Link | Source date | Licence | Limitations |
|---|---|---|---|---|
| ANUM Finance documentation: the Guardian gate, execution gateway and kill switches | docs/finance.md in the ANUM repository | October 2026 | Repository documentation | Describes this release; live execution is off |
| ANUM trading intelligence documentation: the Supervisor's risk limits and lifecycle | docs/finance-trading-intel.md in the ANUM repository | October 2026 | Repository documentation | Default limits; a person may only make them stricter |
