# ANUM MINE headquarters: security review

Date: 22 October 2026, against `main` at `34dee88` plus this branch. Scope: the office registry, live
agent meetings, the office history API, the web office, and the checklist for tables and APIs that the
other headquarters branches add (presence, meeting room, executive). This is a code and test review. No
penetration test was run and no production system was touched.

Severity: High (fix before any rollout), Medium (fix before wide rollout), Low (track).
State: fixed here, open, or checked (no finding).

## Findings

| Id | Severity | State | Finding and what to do |
| --- | --- | --- | --- |
| S-01 | Info | checked | **RLS.** All nine tables on main (`office_buildings`, `office_floors`, `office_rooms`, `office_desks`, `office_create_keys`, `agent_meeting_sessions`, `agent_meeting_participants`, `agent_meeting_turns`, `agent_meeting_turn_leases`) enable and force RLS with one policy that pins `tenant_id` and `workspace_id` for reads and writes (`USING` and `WITH CHECK`), composite scope foreign keys, and a purge policy only for the NOLOGIN role `anum_tenant_purger` (select on three columns plus delete, `WITH CHECK (false)`). Migrations 0097 to 0100 contain no `SECURITY DEFINER` and no `BYPASSRLS`. The tenant lifecycle test already lists these tables (`services/api/anum_api/tenant_lifecycle_tables.py`). New: `services/api/tests/test_postgres_hq_rls.py` finds every table with the prefix `office_`, `agent_meeting`, `executive_`, `campus_` or `hq_` and fails if one lacks forced RLS, if the application role's policy lacks the tenant or workspace pin, or if a narrow role can log in, bypass RLS or write. It could not be run in the readiness sandbox (no database credentials), so CI's database job is its first run (H-10). |
| S-02 | Medium | open | **No workspace-wide meeting budget.** Limits are per meeting (default 24 turns, 30 minutes, 50,000 tokens, 2 USD; allowed up to 200 turns, 14,400 s, 1,000,000 tokens, 1,000 USD; `live_agent_meeting_domain.py`, `live_agent_meeting_api.py` `Limits`). A workspace manager can create any number of meetings, each with its own cap, so the sum is unbounded apart from the global request limiter (600 a minute, burst 120). Add a per-workspace daily cost and turn ceiling that every turn checks, and lower the per-meeting maximum for cost. Owner decision (ADR 0003, open point 2). |
| S-03 | Medium | open | **Transcript visibility.** Listing meetings and reading turns needs only `task:read` (`live_agent_meeting_api.py`), the same as the office registry. Every task reader in the workspace can read what agents said in any meeting. Decide who may read (participants and managers, or managers only) before meetings carry real content; add transcript delete and export with audit. Owner decision (ADR 0003, open point 1). |
| S-04 | Low | open | **Turn execution is not audited.** `agent_meeting.create`, `join_self`, `state_change` and `agent_add` write audit records; `execute-turn` and `run` do not (the turn row itself is durable and attributed). Add an audit record per executed turn with the request key, token and cost counts (never the text). |
| S-05 | Low | fixed | **Turn rate.** `execute-turn` and `run` need `workspace:manage`; `run` executes at most three sequential turns per call and `request_key` makes retries idempotent. Closed by a turn rate on top of the global limiter (`live_agent_meeting_rate.py`): one person may start 6 turns a minute and one workspace 20 (`ANUM_MEETING_TURNS_PER_MINUTE_ACTOR`, `ANUM_MEETING_TURNS_PER_MINUTE_WORKSPACE`; `run` counts one per turn, all or nothing). Over the rate the API answers 429 with `Retry-After` and a sentence that says who is over, the limit and the seconds to wait (Arabic follows `Accept-Language`). Each refusal is audited as `agent_meeting.turn_rate_limited` with ids, scope, requested turns, limit and seconds only. Checked after `workspace:manage`, so it grants nothing. State is in memory per API process: with N replicas the ceiling is N times the limit, and the monthly budget (S-02) stays the hard cost bound. Tests: `tests/test_live_agent_meeting_turn_rate.py`. |
| S-06 | Info | checked | **CSP for the scene.** The scene is hand-written WebGL: no `eval`, no worker, no extra origin, no inline script, no remote asset. `apps/web/e2e/hq-csp.spec.ts` loads the office under the exact production policy from `apps/web/nginx/default.conf.template`, opens the workstation panel, and asserts zero `securitypolicyviolation` events (with a control request that proves the policy is active). The office needs no CSP change. Remember: if glTF or textures are added later, `img-src` already allows `blob:` and `data:`; a model host would need `connect-src`, which must be named exactly, never a wildcard. |
| S-07 | Info | checked | **Secrets and logs.** The office registry, meeting store, API and runtime modules contain no `logger`, `logging` or `print` calls (searched `office_*.py` and `live_agent_meeting_*.py`), so prompts, transcripts and keys are not written to logs by them. Model provider keys stay in the model gateway. `scripts/hq-load/hq_load.py` never prints the token, reads it from `HQ_LOAD_TOKEN` and warns against `--token` (it shows in `ps`). Repeat this search for each new module (`live_agent_meeting_room.py`, `executive_pa.py`). |
| S-08 | Info | checked | **Cross-tenant joins.** The office client rejects any record whose tenant or workspace differs from the scope that started the request and aborts a multi-page load if the scope changes (`apps/web/src/lib/officeSpaceClient.ts`). `HeadquartersController` discards every response of an earlier scope (`apps/web/test/headquartersTenantRace.test.ts`, `e2e/hq-live-data.spec.ts`). The history API needs owner or organisation admin with `audit:read` (`office_history_api.py`). No endpoint on main reads two tenants. |
| S-09 | Medium | fixed | **Office events never refreshed** (pattern typo, H-03): a stale office is a correctness and trust problem rather than a leak, but the live view promised updates it did not deliver. Fixed with a test. |
| S-10 | Low | open | **Stream per scope.** The office opens one SSE stream per tab; after a scope switch it is re-opened for the new scope (fixed here). The API's per-client rate limit and the pool design (`scope="function"` on the stream dependency) already protect the database; the load test shows no connection growth locally, but confirm on Oracle (`docs/ANUM_MINE_HQ_LOAD_TESTING.md`). |
| S-11 | Low | open | **Concept view and reception use example names** (`EXAMPLE_HOSTS`, `DEMO_ROOMS` in `ReceptionOperations.tsx`, the concept rooms in `OfficeTourView.tsx`). They are labelled "CONCEPT VIEW" or inside the reception panel, but a visitor log entry made with an example host is a persisted record. Replace the example hosts with workspace members before anyone logs visitors for real. |

## First look at the open branches (not merged, not a full review)

- `claude/hq-data` migration 0101: `campus_workspace_profiles` and `executive_grants` have forced RLS on
  tenant and workspace plus the purge policy. The new NOLOGIN role `anum_executive_reader` may read only
  eight columns of the caller's own active, unexpired grants inside one tenant, through a select-only
  policy pinned to `anum.tenant_id` and `anum.user_id`; the migration says to grant it `WITH INHERIT FALSE,
  SET TRUE` and to reach it with `SET LOCAL ROLE`. That matches the repository rule. The RLS test
  accepts exactly this shape (select-only, tenant-pinned, NOLOGIN, no bypass) and fails for anything wider.
  Still to verify when it merges: that the API really uses `SET LOCAL ROLE`, that a grant approval needs the
  displayed payload hash, and that every executive read writes an audit record.
- `claude/hq-meetings` migration 0102 and `claude/hq-executive` were not reviewed line by line; apply the
  checklist below when they merge.

## Checklist for the tables and APIs on the other branches

Apply to presence, meeting room (`0102_meeting_room`), executive PA and any later migration; the RLS test
covers the first point automatically.

1. Forced RLS on tenant and workspace, both `USING` and `WITH CHECK`; composite scope foreign keys.
2. Purge policy for `anum_tenant_purger` and an entry in `tenant_lifecycle_tables.py`.
3. No `SECURITY DEFINER` over forced-RLS tables, no `BYPASSRLS`; a narrow role granted `WITH INHERIT FALSE`
   (CLAUDE.md, "Things learned the hard way").
4. Every route: `require_permission` with the narrowest key; writes audited with actor and target, never
   with message bodies or secrets.
5. A cross-workspace read (executive desk) needs an explicit owner grant row, minimum fields, an audit
   record per read, and tests that a missing, expired or revoked grant returns nothing.
6. Meeting turns: per-meeting and per-workspace caps, idempotent request keys, no tools for the model.
7. Redaction of secrets before text is stored or shown (`live_agent_meeting_redaction.py` on the meetings
   branch: review its patterns against the secret formats the workspace connects).
8. Rate limit on any route that calls a model.
9. Web: escaped text only (no raw HTML from agents), no tenant id taken from the URL or the body.

## Not covered

Penetration testing, dependency audit of new packages (none were added), model prompt-injection
resistance of meeting turns, and the Flutter surface (not built).
