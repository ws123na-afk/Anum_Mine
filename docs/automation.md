# Automation

Automation in ANUM turns recurring intent into governed execution. It should support scheduled tasks, event-triggered workflows, reminders, monitors, and long-running processes without losing user control.

## Automation Types

- Scheduled automations: run at a defined time or interval.
- Event automations: react to inbound webhooks, NATS events, or integration changes.
- Monitors: periodically check a condition and report or act.
- Follow-ups: resume a task after a delay or external signal.
- Human-in-the-loop flows: pause until approval or additional input is available.

## Temporal Role

Temporal should own durable workflow execution, retries, timers, waits, and resumability. The application database remains the source of truth for user-visible tasks and policy. Temporal workflow IDs should be stored with ANUM tasks for traceability. Agent runs already do this: with `ANUM_RUNTIME_BACKEND=temporal` the workflow id is `anum-run/<tenant>/<workspace>/<task>` and the run's first step records it ([Agent runtime](agent-runtime.md#durable-execution)).

## Event Role

NATS JetStream should carry domain events that notify clients, workers, and integrations. Event consumers should be idempotent because retries and duplicate delivery are normal in distributed systems.

## Safety

Automations must run under an explicit actor and tenant context. They should have scopes, expiration, audit history, and clear cancellation. High-risk automated actions require policy-backed approval or preauthorization.

## Implementation

`/api/v1/automation` manages workflows, schedules and runs (`anum_api/automation.py`). Runs can be started (optionally with an `Idempotency-Key` header, at most 200 characters), paused by a `pause` step, resumed, cancelled and retried.

### Storage

| `ANUM_REPOSITORY_BACKEND` | Engine | Where |
|---|---|---|
| `memory` (local, tests) | `LocalAutomationEngine` | A SQLite file at `ANUM_AUTOMATION_DATABASE_PATH` (`.anum/automation.db`). One process only. Every statement is a fixed string, so there is no interpolated SQL and no bandit exception. |
| `postgresql` | `PostgresAutomationEngine` (`anum_api/db/automation_repository.py`) | `automation_workflows`, `automation_schedules` and `automation_runs` (migration `0009_voice_automation`). Shared by every API replica. |

In PostgreSQL every call is one transaction as the application role with the caller's tenant and workspace RLS context set. Queries also filter on both explicitly. All three tables have forced workspace RLS ([Multi-tenancy](multi-tenancy.md#control-plane-stores)).

- Writes need an onboarded workspace (`409` otherwise).
- Schedules and runs reference their workflow through a composite `(tenant_id, workspace_id, workflow_id)` foreign key, so they cannot point into another workspace.
- Cancel and resume lock the run row (`FOR UPDATE`).
- A start with an `Idempotency-Key` inserts with `ON CONFLICT DO NOTHING` on the unique `(tenant_id, workspace_id, idempotency_key)` index. Concurrent starts with one key on different replicas return the same run.

### Schedules

- `cron` is a five-field expression: minute, hour, day of month, month, day of week (`anum_api/cron.py`).
  - Fields accept `*`, numbers, ranges, steps, lists, and `JAN`-`DEC` / `SUN`-`SAT` names.
  - Day of week 0 and 7 are Sunday.
  - When both day fields are restricted, either one matching is enough (Vixie cron).
- `timezone` is an IANA zone name; fire times are computed in it and stored in UTC.
  - A local time skipped by a daylight-saving jump fires after the jump.
  - A repeated local time fires once.
- An invalid expression, an unknown zone, or an expression that never fires (for example `0 0 30 2 *`) is rejected with `422`.
- Schedules return `next_run_at` (UTC, null while disabled), `last_run_at` and `created_by`.
  - Enabling a schedule, or changing its `cron` or `timezone`, recomputes `next_run_at` from now.

### Scheduler

`ANUM_AUTOMATION_SCHEDULER_ENABLED=true` starts a background loop in each API process. It runs every `ANUM_AUTOMATION_SCHEDULER_POLL_SECONDS` (30) and fires up to `ANUM_AUTOMATION_SCHEDULER_BATCH_SIZE` (100) due schedules per pass. It is off by default; until it is on, schedules are stored but never fire.

It is safe to enable on every replica. With PostgreSQL a fire time produces exactly one run:

1. **Discover.** A short read-only transaction runs as the `anum_maintenance` role. It lists the ids and scope of enabled schedules whose `next_run_at` has passed. That role cannot see anything else ([Multi-tenancy](multi-tenancy.md#maintenance-role)).
2. **Claim and fire.** Per schedule, one transaction as the application role, inside the schedule's tenant and workspace RLS context:
   - It claims the row with `SELECT ... FOR UPDATE SKIP LOCKED`, re-checking that it is still due.
   - It records the run with the idempotency key `schedule:<schedule id>:<fire time>`.
   - It advances `next_run_at` in the same transaction.
3. **Races.** A replica that loses the race skips the locked row, or no longer sees it as due once the winner has committed. If a replica recorded the run and crashed before advancing the schedule, the unique idempotency key stops the fire time from running again.

Other rules:

- A scheduled run's actor is the schedule's creator (`created_by`), or `system:automation-scheduler` if none was recorded.
- After downtime a missed fire time runs once, and the schedule continues from the next fire after now. Missed fires are not caught up.
- A schedule that fails to fire is logged and stays due, so the next pass retries it. Other schedules are not affected.
- Shutdown waits for a pass in progress to commit or roll back and close its sessions; it never abandons a transaction.
- The local SQLite engine holds SQLite's write lock (`BEGIN IMMEDIATE`) for a pass.

Deployment: the API login must be a member of `anum_maintenance` (`grant anum_maintenance to <api login>`). Without it, discovery fails with a permission error in the logs and nothing fires.

## Now

Build scheduled task reminders, event emission, workflow pause/resume, and cancellation.

## Later

Add complex triggers, natural-language automation builder, organization automation libraries, monitor dashboards, and preapproved low-risk action bundles.