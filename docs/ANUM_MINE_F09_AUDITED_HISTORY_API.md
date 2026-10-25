# F09 :  ANUM MINE Work History & Replay (evidence API)

## Isolation and purpose

This increment is intentionally separated from the four active Codex agents'
frontend, backend execution, 3D, and QA worktrees. It changes only a new
read-only API module, its focused tests, this document, and one FastAPI router
registration. It does **not** modify the office renderer, agent model runtime,
live meeting coordinator, database migration files, or desktop/mobile code.

## Endpoints

All paths are under `/api/v1/office-history`.

* `GET /`: paginated audit-derived evidence with category, actor, target, and
  correlation filters. `category` can be `all`, `task`, `agent_run`,
  `approval`, `meeting`, `office`, `team_work`, `workspace`,
  `membership`, `automation`, `autonomy`, or `other`.
* `GET /` also takes optional `since` (inclusive), `until` (exclusive) and `q`
  (text in actor, action or target); a start that is not before the end answers 422.
  The headquarters history panel ([ANUM_MINE_HQ_HISTORY_PANEL.md](ANUM_MINE_HQ_HISTORY_PANEL.md)) is the first screen that uses it.
* `GET /at?at=2026-10-21T12:00:00Z`: audit evidence recorded *before*
  a timestamp, with counts and an explicit incomplete-history warning.
* `GET /correlations/{correlation_id}`: events related by a server-recorded
  correlation id, not by inferred task relationships.

All three reuse the existing `AuditFilter`, `AuditCursor`,
`AnumRepository.list_audit_page`, and permission gate
`require_owner_or_org_admin(context, Permission.AUDIT_READ)`. This means
the existing repository implementation controls workspace-scoped reads
and PostgreSQL RLS remains authoritative. No new elevated database role
or unscoped SQL is introduced.

## Pagination and safety

The API uses the existing keyset cursor over immutable audit records.
It deliberately pages underlying records **before** category filtering:
a page may have zero matching results while `next_cursor` remains set.
This is not a bug: clients should keep paginating until the cursor is null
or the user stops. It prevents unbounded category scans on sparse history.

The response does not include generic runtime payloads, secrets, or fabricated
agent conversations. Only sanitized audit record metadata is returned through
the existing `record_view` serialization. Existing audit retention rules
apply; this module does not add storage or delete audit history.

## Historical accuracy

`/at` is **not a complete room layout replay**. Historical occupancy,
agent presence, and exact layout are explicitly set to `unknown`,
because the append-only audit stream alone does not contain a complete
time-indexed room/desk configuration. `evidence_limited=true` and
`historical_coverage_complete=false` remain true even when the page
has records. This is necessary to avoid turning missing events into a false
claim of inactivity.

Later F09 integration can add time-indexed canonical snapshots and
retention-aware replay only after authorizing their schema, migrations,
RLS, and source event contracts.

## Compatibility, rollout and rollback

The route is additive; no existing paths change. No database migration
is required because the audit repository and append-only audit table are
already present. Rollback: remove the new router registration and its
standalone module. No stored user data is altered or deleted.

Tests cover deterministic categories, cursor advancement, tenant/workspace
scoping, correlation, exclusive timestamps, secret metadata redaction,
absence of invented presence, bad cursors, and field validation.
