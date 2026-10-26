# ANUM MINE autonomous agent meeting execution

## Integration

The existing authenticated **ANUM MINE > Live Headquarters > Agent meeting
sessions** panel uses the same PostgreSQL meeting tables and API routes.
No separate mock tenant store or duplicated meeting database is created.

A meeting owner creates a session, adds an existing active workspace agent
profile, changes the state to active, then uses **Generate next agent turn**
or **Run agent meeting round**. A round executes up to three turns sequentially
and saves each before continuing. Each contribution is generated using the
agent's selected model route and its existing profile instructions.

## API

- `POST /api/v1/agent-meetings`: create a session and its limits
- `POST /api/v1/agent-meetings/{id}/agents`: attach an active visible agent profile
- `POST /api/v1/agent-meetings/{id}/state`: activate, pause, finish or cancel
- `POST /api/v1/agent-meetings/{id}/execute-turn`: idempotent single turn
- `POST /api/v1/agent-meetings/{id}/run`: idempotent bounded three-turn round
- `GET /api/v1/agent-meetings/{id}/turns`: saved transcript

### Turn rate (S-05)

`execute-turn` and `run` are limited per person (default 6 turns a minute) and per workspace (default 20), after the `workspace:manage` check. `run` asks for all its turns at once, so a refused round starts none. Over the rate the API answers `429 rate_limited` with a `Retry-After` header and a message such as "You have started the most agent meeting turns allowed in one minute (6). Wait 42 seconds and try again." (Arabic with `Accept-Language: ar`). The refusal is audited as `agent_meeting.turn_rate_limited` with the meeting id, scope (`actor` or `workspace`), requested turns, limit and seconds, never text. Set `ANUM_MEETING_TURNS_PER_MINUTE_ACTOR` and `ANUM_MEETING_TURNS_PER_MINUTE_WORKSPACE`. The counters live in the API process, so with several replicas the ceiling is the limit times the replicas. The web room shows the server's sentence through its normal error line.

Client requests authenticate through the existing session, tenant and
workspace headers. All database operations use the forced-RLS scoped session.
Human and agent role labels do not confer additional permissions.

## Safety and persistence

Each turn creates a PostgreSQL lease before invoking a real model. The lease
prevents concurrent speakers within a session and has a 90-second expiry.
Successful results and usage counters are committed transactionally. The
request key prevents duplicate turns after client retries. The owner can
pause or cancel a meeting; an in-flight turn rechecks state before writing.
A mock model cannot generate a meeting turn. No application tools, payments,
messages or outside actions are callable from this execution path.

Meeting-level cost, token, duration, participant and turn limits are enforced
before the model call and again before saving its output. Provider usage and
cost accounting depends on the configured model's reported cost estimate.
Uncommitted or rejected model calls may still incur provider charges.

## Release gates

1. Apply migration 0098 and run PostgreSQL RLS, concurrency, lease-expiry,
   ownership, idempotency and lifecycle tests with a real database.
2. Verify a configured non-mock model and workspace owner can generate
   and persist multiple turns. Verify mock mode rejects execution.
3. Exercise pause/cancel while a turn is in flight, and inspect final
   recorded token/cost totals.
4. Run TypeScript checks, frontend tests, six Playwright shards, API tests,
   security scans and CodeQL.
5. Deploy the API and frontend together; test from the signed-in headquarters.
6. Do not enable this capability for customers if any release gate fails.

## Current design constraints

This is a **bounded synchronous execution round**. It is not a durable
unattended recurring worker. Full autonomous background scheduling, external
agent tool actions, detailed decision extraction and cross-process provider
billing reservation are separate capabilities and must not be presented as
complete. Do not run a large unattended meeting through an HTTP request.


## Operational reliability hardening (October 22, 2026)

The unattended worker now transitions an active meeting to `finished` if its
persisted turn, duration, token or cost limits are exhausted. A provider
failure transitions an active session to `failed`, so the supervisor does
not automatically retry a potentially billable request. Both transitions
lock the scoped meeting and refuse to override a concurrent pause or
cancellation. Unit contracts cover owner-only transitions and the terminal
state guard. Operators can inspect the failed record rather than allowing a
paid retry storm.

The live headquarters consumes the authenticated event stream with a
300-millisecond coalescing window; many task or meeting events result in one
refresh rather than a thundering herd of data requests. A periodic refresh
is still used when the SSE connection is unavailable.

**Verification limits:** These changes require green GitHub Actions, staged
PostgreSQL tests, a real configured model and deployment smoke tests. The
approved photorealistic office reference has not been pixel-compared here,
and production emission of all domain events is not proven by this code.
