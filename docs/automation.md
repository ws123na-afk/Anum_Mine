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

`/api/v1/automation` manages automations (workflows), their schedules and their runs (`anum_api/automation.py`). The web page is **Automations** (`apps/web/src/AutomationWorkbench.tsx`); the Flutter app has the same screens (`apps/mobile/lib/features/workspace/automations_screen.dart`, `lib/features/automations/`).

### API

| Method and path | Who | What |
|---|---|---|
| `GET /workflows?query=&status=&limit=&offset=` | `automation:read` | Page of automations, newest first. `query` matches name or description (wildcards are literal); `status` is `active` or `disabled`; `limit` 1-200 (50). The total is in the `X-Total-Count` header. Each item carries a summary: `last_run_at`, `last_run_status`, `next_run_at` (earliest enabled schedule; null while paused), `active_runs`, `run_count`, `schedule_count`. |
| `GET /workflows/{id}` | read | One automation with its summary. |
| `POST /workflows` | `automation:manage` | Create. Body: `name`, `description`, `steps`, `overlap_policy`. |
| `PUT /workflows/{id}` | manage, creator or owner | Replace the definition; `version` goes up by one and `updated_by` is set. Runs already started keep the steps they started with. |
| `POST /workflows/{id}/disable`, `/enable` | manage, creator or owner | Pause or resume. A paused automation refuses Run now (`409`) and its schedules skip their fire times (`last_outcome = workflow_paused`). |
| `DELETE /workflows/{id}` | manage, creator or owner | Deletes the automation, its schedules and its run history in one transaction. Refused (`409`) while it has an active (queued, running or paused) run. Tasks its runs created are kept. |
| `POST /workflows/{id}/runs` | manage | Run now; optional `Idempotency-Key` (at most 200 characters). Applies the overlap policy. |
| `GET /runs?workflow_id=&schedule_id=&status=&limit=&offset=` | read | Run history, newest first, with `X-Total-Count`. Each run has `workflow_name`, `workflow_version`, `trigger` (`manual`, `schedule`, `retry`) and `error` (the failed step's message). |
| `GET /runs/{id}` | read | One run. |
| `POST /runs/{id}/cancel`, `/resume`, `/retry` | manage | Cancel an active run; resume a paused one; retry a failed or cancelled one (a new run with `retry_of`). |
| `GET /schedules?workflow_id=`, `GET /schedules/{id}` | read | Schedules, with `cron_description`, `next_run_at`, `last_run_at`, `last_outcome` and `created_by`. |
| `POST /schedules`, `PUT /schedules/{id}`, `POST /schedules/{id}/enable`, `/disable`, `DELETE /schedules/{id}` | manage; changes by the creator or an owner | Create, edit, turn on or off, delete. |
| `POST /schedules/preview` | read | `{cron, timezone, count}` (count 1-10): the schedule in words and its next fire times, before it is saved. Both clients show it live while typing. |

**Permissions.** Owners and members hold `automation:manage`; viewers only `automation:read`. A member may change (edit, pause, delete) only automations and schedules they created; owners may change any. Members may run any automation. Every refusal says who may do it, for example `Only the automation's creator (user_a) or a workspace owner can change this automation.` The clients read the caller's role from `GET /workspace-memberships/current` and hide what the role cannot do, with a sentence saying why.

**Validation.** Errors are `422` with one message per field in `error.details` that says how to fix it: an unknown step action lists the available ones; a "Create agent task" step without a title or prompt says so; cron errors name the field (`Hour field: 25 is outside 0-23.`); an unknown zone asks for an IANA name; an expression that never fires says to check the day and month fields.

### Steps

Steps run in order, in process, inside the engine's transaction. A run stops at the first step that fails or waits.

| Action | Does |
|---|---|
| `task.create` | Creates a real agent task in the workspace (`input.title`, `input.prompt`), created by the run's actor, with a `task.created` event. The step output carries `task_id`; both clients link to the task. The task waits under Tasks to be run. |
| `notify` | Notifies the run's actor in the app (`automation.notification` event with `input.message`). |
| `pause` | Pauses the run until someone resumes it. |
| `fail` | Fails the run with `input.message`. |

New and edited automations must use these actions. Older stored workflows with free-form actions (for example `task.execute`) still run, and those steps are recorded as accepted without effect; editing such a workflow asks for a supported action. `max_attempts` is stored but the in-process executor runs each step once: retry the run.

A run keeps a snapshot of each step (action and input) and the `workflow_version` it started with, so resuming a paused run after an edit runs the old steps.

### Overlap policy

`overlap_policy` decides what happens when a run starts while another run of the same automation is active:

- `allow` (default): runs overlap.
- `skip`: at most one active run. Run now is refused with `409` naming the active run; a scheduled fire time is skipped (`last_outcome = skipped_overlap`) and the schedule moves on.
- `cancel_previous`: the new run cancels the active runs first.

With PostgreSQL, starting a run locks the automation row, so the policy holds when replicas start the same automation at once.

### Events and notifications

Runs publish `automation_run.completed`, `automation_run.paused`, `automation_run.failed` and `automation_run.cancelled` (subject: the run id; payload: `run_id`, `workflow_id`, `workflow_name`, `schedule_id`, `status`, `trigger`) through the same repository and outbox as other domain events ([Events](events.md)), in the engine's transaction. A failure adds `error`, `failed_step` and `notify_user_ids`: the run's actor and the automation's creator, each only when their **automation failed** notification preference (Settings, Notifications) is on.

The web app listens on the event stream: a failure or `notify` step addressed to the signed-in user shows an alert with **Open run**, which opens the automation with that run highlighted; with the **desktop enabled** preference on and browser permission granted it also raises an operating-system notification. The **email enabled** preference has no sender yet, so automations never email.

Audit records: `automation.workflow.created`, `.updated`, `.paused`, `.resumed`, `.deleted` and `automation.schedule.created`, `.updated`, `.deleted`.

### Storage

| `ANUM_REPOSITORY_BACKEND` | Engine | Where |
|---|---|---|
| `memory` (local, tests) | `LocalAutomationEngine` | A SQLite file at `ANUM_AUTOMATION_DATABASE_PATH` (`.anum/automation.db`). One process only. Every statement is a fixed string, so there is no interpolated SQL and no bandit exception. |
| `postgresql` | `PostgresAutomationEngine` (`anum_api/db/automation_repository.py`) | `automation_workflows`, `automation_schedules` and `automation_runs` (migrations `0011_voice_automation` and `0031_automation_policy`: `overlap_policy`, `updated_by`, `workflow_version`, `last_outcome` and the per-workflow run index). Shared by every API replica. |

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
- Schedules return `next_run_at` (UTC, null while disabled), `last_run_at`, `last_outcome` (`fired`, `skipped_overlap`, `workflow_paused`, `owner_inactive`, `workspace_archived`: an archived workspace is read-only, so its schedules advance without running), `created_by` and `cron_description` (the expression in words, for example `At 09:00, Monday through Friday`).
  - Enabling a schedule, or changing its `cron` or `timezone`, recomputes `next_run_at` from now.

### Scheduler

`ANUM_AUTOMATION_SCHEDULER_ENABLED=true` starts a background loop in each API process. It runs every `ANUM_AUTOMATION_SCHEDULER_POLL_SECONDS` (30) and fires up to `ANUM_AUTOMATION_SCHEDULER_BATCH_SIZE` (100) due schedules per pass. It is off by default; until it is on, schedules are stored but never fire.

It is safe to enable on every replica. With PostgreSQL a fire time produces exactly one run:

1. **Discover.** A short read-only transaction runs as the `anum_maintenance` role. It lists the ids and scope of enabled schedules whose `next_run_at` has passed. That role cannot see anything else ([Multi-tenancy](multi-tenancy.md#maintenance-role)).
2. **Claim and fire.** Per schedule, one transaction as the application role, inside the schedule's tenant and workspace RLS context:
   - It claims the row with `SELECT ... FOR UPDATE SKIP LOCKED`, re-checking that it is still due.
   - It inserts the run with the idempotency key `schedule:<schedule id>:<fire time>` before any step runs, so a fire time that already has its run does no work twice.
   - It advances `next_run_at` in the same transaction.
3. **Races.** A replica that loses the race skips the locked row, or no longer sees it as due once the winner has committed. If a replica recorded the run and crashed before advancing the schedule, the unique idempotency key stops the fire time from running again.

Other rules:

- A scheduled run's actor is the schedule's creator (`created_by`), or `system:automation-scheduler` if none was recorded.
- With OIDC sign-in the actor must still be an active owner or member of the workspace at fire time. Otherwise nothing runs as them: the fire time records a failed run whose first step explains it (`last_outcome = owner_inactive`), and the automation's creator is notified. Delete the schedule and create it again as an active member.
- A paused automation's fire times are skipped (`workflow_paused`); under `skip` overlap a fire time with an active run is skipped (`skipped_overlap`).
- After downtime a missed fire time runs once, and the schedule continues from the next fire after now. Missed fires are not caught up.
- A schedule that fails to fire is logged and stays due, so the next pass retries it. Other schedules are not affected.
- Shutdown waits for a pass in progress to commit or roll back and close its sessions; it never abandons a transaction.
- The local SQLite engine holds SQLite's write lock (`BEGIN IMMEDIATE`) for a pass.

Deployment: the API login must be a member of `anum_maintenance` (`grant anum_maintenance to <api login> with inherit false, set true`; the code switches to it with `SET LOCAL ROLE`, and an inherited grant would let its cross-tenant discovery policies widen the API login's own queries). Without it, discovery fails with a permission error in the logs and nothing fires.

### From learned routines

[What ANUM learned](learning.md#routines) proposes routines mined from a user's own tasks. Accepting one creates a workflow (one `task.create` step per activity, with its prompt) and a **disabled** schedule through this engine, as the user (`automation:manage`); confirming enables the schedule. Steps of any action other than `pause` and `fail` are recorded as accepted; running `task.create` steps as agent tasks is not built yet.

## Not done yet

- Event triggers (run an automation when a domain event or webhook arrives) are not implemented; automations start manually or on a schedule.
- `task.create` creates the task; it does not run it. Running agent tasks unattended needs budget and approval policy for the scheduler's actor first.
- Email delivery of failure notifications (the `email_enabled` preference) has no sender.
- Steps do not retry in place (`max_attempts` is stored only); retry the run.

## Later

Add event and webhook triggers, a natural-language automation builder, organization automation libraries, monitor dashboards, and preapproved low-risk action bundles.
