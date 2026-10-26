# ANUM MINE F06/F07: Live Meeting PostgreSQL Schema (Migration 0098)

## Scope
This branch is **dependent on the Office Registry branch** containing `0097_office_spaces`. Migration `0098_agent_live_meetings` follows that revision and must not be independently applied to the current `main` schema until `0097` is integrated.

It introduces scoped data storage for governed AI meetings, separate from the existing imported Zoom/Teams meeting feature:

| Table | Purpose |
| --- | --- |
| `agent_meeting_sessions` | Workspace meeting identity, state, revision and hard budgets |
| `agent_meeting_participants` | Scoped agent/human participation with unique actor per session |
| `agent_meeting_turns` | Persisted transcript turns with per-session sequence and idempotency keys |
| `agent_meeting_turn_leases` | Single-flight turn claims, expiry timestamps and revision guards |

Every table includes tenant and workspace identity, a scoped primary key, workspace foreign key, forced PostgreSQL row-level security, and an explicit tenant lifecycle purge policy. Child tables have composite tenant/workspace/session foreign keys and restrictive deletes. Turn and reservation constraints reject concurrent duplicate sequence numbers and request keys. Limits and nonnegative usage are checked in the database.

All four tables are registered for tenant export and purge, with children listed before their parent sessions.

## Important boundary
This migration establishes only database constraints. It does **not** activate autonomous model execution, expose new HTTP endpoints, store meetings automatically, or approve tools. There is no production adapter or service deployment in this branch. The existing runtime policy in PR #197 still needs an actual Postgres transaction store.

## Remaining release gates
1. Validate the migration against a disposable, migrated PostgreSQL database under the **non-owner application role**; test cross-tenant invisibility, unauthorized writes, unique collisions and concurrent claims.
2. Implement transactional compare-and-swap reservation; persist turns with ledger updates, committed audit records and outbox. Enforce latest status, budgets, agent membership and lease expiry at commit.
3. Authorize endpoint callers and agent selection using existing ANUM memberships and permission scopes.
4. Add crash/retry, lease recovery, retention/redaction and lifecycle export/purge integration tests.
5. Confirm that `0097` is on the exact target migration head before applying `0098`; plan a backup and dry-run migration with an operator.
6. Require full CI and owner approval before any production schema change or deployment.

This branch contains no production connection changes or migrations applied to a live database.
