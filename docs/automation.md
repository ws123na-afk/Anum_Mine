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
| `GET /runs?workflow_id=&schedule_id=&trigger_id=&status=&limit=&offset=` | read | Run history, newest first, with `X-Total-Count`. Each run has `workflow_name`, `workflow_version`, `trigger` (`manual`, `schedule`, `retry`, `event`, `webhook`), `trigger_id`, `trigger_type` (the event type, or `webhook`), `waiting_for` (what its current step waits for) and `error` (the failed step's message). |
| `GET /runs/{id}` | read | One run. |
| `POST /runs/{id}/cancel`, `/resume`, `/retry` | manage | Cancel an active run; resume a paused one; retry a failed or cancelled one (a new run with `retry_of`). |
| `GET /schedules?workflow_id=`, `GET /schedules/{id}` | read | Schedules, with `cron_description` and `cron_description_ar` (the expression in English and Arabic), `next_run_at`, `last_run_at`, `last_outcome` and `created_by`. |
| `POST /schedules`, `PUT /schedules/{id}`, `POST /schedules/{id}/enable`, `/disable`, `DELETE /schedules/{id}` | manage; changes by the creator or an owner | Create, edit, turn on or off, delete. |
| `POST /schedules/{id}/take-over` | owners | The schedule runs as the owner from now on ([Taking over](#taking-over)). |
| `POST /schedules/preview` | read | `{cron, timezone, count}` (count 1-10): the schedule in words (`description`, `description_ar`) and its next fire times, before it is saved. Both clients show it live while typing. |
| `GET /trigger-events` | read | The events a trigger can start on, each with a label, a description and the payload fields its conditions can test. |
| `GET /triggers?workflow_id=`, `GET /triggers/{id}` | read | Triggers with `kind`, `event_type`, `conditions`, `enabled`, `rate_limit_per_minute`, `replay_window_seconds`, `secret_hint` (last four characters), `webhook_path`, `last_fired_at`, `last_outcome`, `fire_count` and `created_by`. Never the secret. |
| `POST /triggers` | manage | Create. Answers `{trigger, secret}`: a webhook's secret appears in this answer only. |
| `PUT /triggers/{id}`, `POST /triggers/{id}/enable`, `/disable`, `DELETE /triggers/{id}` | manage; the creator or an owner | Edit, turn on or off, delete (runs it started are kept). |
| `POST /triggers/{id}/rotate-secret` | manage; the creator or an owner | A new webhook secret, shown once; the old one stops working at once. |
| `POST /triggers/{id}/take-over` | owners | The trigger's runs run as the owner from now on. |
| `POST /hooks/{tenant}/{workspace}/{trigger}` | the trigger's secret (no session) | A signed delivery starts a run ([Webhooks](#webhooks)). |

**Permissions.** Owners and members hold `automation:manage`; viewers only `automation:read`. A member may change (edit, pause, delete) only automations and schedules they created; owners may change any. Members may run any automation. Every refusal says who may do it, for example `Only the automation's creator (user_a) or a workspace owner can change this automation.` The clients read the caller's role from `GET /workspace-memberships/current` and hide what the role cannot do, with a sentence saying why.

**Validation.** Errors are `422` with one message per field in `error.details` that says how to fix it: an unknown step action lists the available ones; a "Create agent task" step without a title or prompt says so; cron errors name the field (`Hour field: 25 is outside 0-23.`); an unknown zone asks for an IANA name; an expression that never fires says to check the day and month fields.

### Steps

Steps run in order. A run stops at the first step that fails, pauses, or waits for work outside its transaction.

| Action | Does |
|---|---|
| `task.create` | Creates a real agent task in the workspace (`input.title`, `input.prompt`), created by the run's actor, with a `task.created` event, and **runs it** like `POST /api/v1/tasks/{id}/run`: the actor's workspace role, the monthly model budget, ToolPolicy, autonomy and approvals all apply. The step waits (`waiting_for: task`, then `approval` while a person must approve the task's action) until the task completes; its output then carries `task_id`, `status`, `result` (the task's answer, at most 4,000 characters), `agent_run_id` and `approval_id`. `input.run: false` only creates the task (it waits under Tasks), as before. |
| `email.send` | Sends an email from a connected mailbox (`input.account_id`, `to`, `subject`, `body`). The text is stored as a draft of that mailbox and sending is asked for exactly as **Ask approval to send** does in the Inbox ([Email](email.md)): `send_email` is high risk, so a person always approves the exact recipients and text (with its payload hash) first. The step waits for that approval; a rejected or expired approval fails it. The mailbox must be visible to the run's actor. |
| `notify` | Notifies the run's actor (`automation.notification` event with `input.message`). `input.channels` is `["in_app"]` (default) or `["in_app", "push"]`: with `push` the owner's phones get a push too, gated by their phone-push switch ([Notifications](notifications.md)). |
| `pause` | Pauses the run until someone resumes it. |
| `fail` | Fails the run with `input.message`. |

New and edited automations must use these actions. Older stored workflows with free-form actions (for example `task.execute`) still run, and those steps are recorded as accepted without effect; editing such a workflow asks for a supported action.

A run keeps a snapshot of each step (action, input and retry policy) and the `workflow_version` it started with, so resuming a paused run after an edit runs the old steps.

#### Results in later steps

Any text input may name earlier results and the trigger's data with `{{...}}`: `{{steps.<step id>.result}}` (or any other output field of a finished step), `{{trigger.<field>}}` (the event payload, or `{{trigger.body.<field>}}` for a webhook), `{{run.id}}`, `{{workflow.name}}`. Unknown names become empty text. In an agent task's prompt, values that came from outside (trigger data, earlier results) are inserted as labelled untrusted-data blocks (`prompt_provenance.label_untrusted`), so a received email or webhook body is read as data, never as the owner's instructions. An email step whose `to` or body renders empty fails with that reason.

#### Execution

`task.create` (when it runs the task) and `email.send` do their work after the run's transaction commits, in the **automation dispatcher** (`anum_api/automation_dispatch.py`), one background task in every API process:

1. The engine records the step as running with `waiting_for` (`task` or `start`), sets the run's `wake_at` and `waiting_task_id`, and hands the work to the dispatcher with the commit.
2. The dispatcher resolves the run's actor's current role in the workspace (a run with no active owner or member to act as fails, and says to take over its schedule or trigger), then runs the agent task (in process, or queued for the durable worker with `ANUM_RUNTIME_BACKEND=temporal`) or asks to send the email, and reports back to the engine (`AutomationEngine.advance`).
3. Committed events about agent tasks (`agent_run.completed`, `agent_run.failed`, `approval.*`, `task.cancelled`) wake the runs waiting on that task at once (`wake_for_task`); the step completes, retries or fails, and the run continues with the next step.
4. **Recovery.** Every check is idempotent and reads the task's state, so work that a process could not finish is picked up by the scheduler pass: runs whose `wake_at` has passed are looked at again (every minute while a task runs, every 15 minutes while it waits for approval), a task that was queued but never started is started again after two minutes, and an email whose request was lost picks up the same approval (the draft id is derived from the run, step and attempt).

A run waiting on a task or an approval stays `running`; its current step says what it waits for. Cancelling the run stops it; the task it started keeps its own state under Tasks (cancel it there too if needed).

#### Retries

A step that fails for a reason that can pass (the agent task failed, the budget is used up, the mailbox needs reconnecting, an unexpected error) is tried again **in place**: the run stays `running`, the step shows `waiting_for: retry`, its `error` and `next_attempt_at`, and the next attempt starts after `retry_delay_seconds * 2^(attempt - 1)` (default 60 s, at most six hours) until `max_attempts` (default 3, at most 10) is used up. A `task.create` retry creates a new task. Failures that would fail again the same way are final at once: a rejected or expired approval, a cancelled task, a missing mailbox or recipient, a `fail` step. Due retries are started by the scheduler pass. Retrying a failed run (`/retry`) still starts a new run.

### Overlap policy

`overlap_policy` decides what happens when a run starts while another run of the same automation is active:

- `allow` (default): runs overlap.
- `skip`: at most one active run. Run now is refused with `409` naming the active run; a scheduled fire time is skipped (`last_outcome = skipped_overlap`) and the schedule moves on.
- `cancel_previous`: the new run cancels the active runs first.

With PostgreSQL, starting a run locks the automation row, so the policy holds when replicas start the same automation at once.

### Events and notifications

Runs publish `automation_run.completed`, `automation_run.paused`, `automation_run.failed` and `automation_run.cancelled` (subject: the run id; payload: `run_id`, `workflow_id`, `workflow_name`, `schedule_id`, `trigger_id`, `status`, `trigger`) through the same repository and outbox as other domain events ([Events](events.md)), in the engine's transaction. A failure adds `error`, `failed_step` and `notify_user_ids`: the run's actor and the automation's creator, each only when their **automation failed** notification preference (Settings, Notifications) is on.

The web app listens on the event stream: a failure or `notify` step addressed to the signed-in user shows an alert with **Open run**, which opens the automation with that run highlighted; with the **desktop enabled** preference on and browser permission granted it also raises an operating-system notification. With phone push set up, a failure and a `notify` step with the `push` channel also reach the named people's phones ([Notifications](notifications.md)). The **email enabled** preference has no sender yet; to email someone, use an `email.send` step.

Audit records: `automation.workflow.created`, `.updated`, `.paused`, `.resumed`, `.deleted`; `automation.schedule.created`, `.updated`, `.deleted`, `.taken_over`; `automation.trigger.created`, `.updated`, `.deleted`, `.secret_rotated`, `.taken_over`; and `email.send.requested` (with `automation_run_id`) for an email step.

### Triggers

A trigger starts its automation when something happens. Runs it starts have `trigger: event` or `webhook`, `trigger_id`, `trigger_type` and `trigger_payload` (what it delivered, at most 16 KB of JSON, available as `{{trigger.*}}`). The run's actor is the trigger's creator (`created_by`); with OIDC they must still be an active owner or member, otherwise the delivery is skipped (`owner_inactive`) until an owner [takes it over](#taking-over).

**Event triggers** name one event of the workspace (`GET /trigger-events`):

| Event | When | Payload fields |
|---|---|---|
| `agent_run.completed`, `agent_run.failed` | An agent task finished or failed | `task_id`, `approval_id` |
| `approval.approved`, `approval.rejected`, `approval.expired` | An approval was decided or lapsed | `task_id`, `reason` |
| `email.received` | New mail in a connected mailbox, found by an incremental sync (not the first sync of a mailbox, not mail the mailbox itself sent) | `account_id`, `message_id`, `from`, `subject`, `category`, `priority`, `private`. A private mailbox's events carry ids and `private: true` only. |
| `whatsapp.message_received` | A customer message arrived on the connected number | `conversation_id`, `message_id`, `from`, `contact_name`, `kind`, `category` |
| `file.uploaded` | A file was uploaded to the workspace | `file_id`, `name`, `content_type`, `size_bytes`, `uploaded_by` |

Every payload also has `event_id`, `event_type`, `event_subject` and `occurred_at`. **Conditions** (at most 10, all must hold) test a dotted payload path (`from`, `body.order.status`, `body.items.0.sku`) with `equals`, `not_equals`, `contains`, `starts_with` (case-insensitive text) or `exists`.

How events reach triggers:

- **At once.** The dispatcher listens to committed events in the process that committed them and starts matching triggers' runs (`fire_event`), with the idempotency key `event:<trigger id>:<event id>`, so an event never starts a trigger's automation twice.
- **Catch-up.** The scheduler pass reads, per enabled event trigger, the workspace's events of its type since the trigger's `cursor_at` (as the application role in the trigger's workspace, after discovering trigger ids as `anum_maintenance`) and fires what is missing: events another process committed (the Temporal worker's `agent_run.*`), or ones this process could not handle before it stopped. Turning a trigger on again, or pointing it at another event, starts from that moment; past events never start runs.
- **Loops.** An event about an agent task that a run of the same automation started is skipped (`loop_skipped`), and automation events cannot be chosen, so an automation never triggers itself.
- **Limits.** Each trigger starts at most `rate_limit_per_minute` runs (1-600, default 30); more are skipped (`rate_limited`). A paused automation skips deliveries (`workflow_paused`); the overlap policy applies (`skipped_overlap`). `last_outcome` and `fire_count` show what happened.

### Webhooks

A webhook trigger gives the automation an address on the API, `POST /api/v1/automation/hooks/{tenant}/{workspace}/{trigger}`, for a store, a marketplace or any system that can sign a request:

- **Secret.** Created with the trigger (`whsec_...`), shown once in the create (and rotate) answer, stored only as Fernet ciphertext with `ANUM_SECRETS_KEY` and returned afterwards as its last four characters. **New secret** replaces it at once.
- **Signature.** `X-Anum-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 over "<t>.<raw body>" with the secret>`, compared in constant time. Missing or wrong: `401` with how to sign.
- **Replay window.** A timestamp more than `replay_window_seconds` (30-3600, default 300) from the server's clock is refused (`401`), so a captured request cannot be sent again later.
- **Delivery id.** `X-Anum-Delivery` (up to 120 characters; the signature when absent) makes the delivery idempotent: the same id answers `200` with the run it already started (`duplicate: true`).
- **Rate limit.** Over the trigger's per-minute limit: `429` with `Retry-After`. Refused deliveries from one address are also limited per IP (bursts of 20, then one every five seconds).
- **Body.** At most 64 KB. A JSON object becomes `{{trigger.body.*}}`; other content is kept as text. A delivery that does not match the conditions answers `200 {"status": "ignored"}`.
- Unknown trigger: `404`. Turned off, automation paused, an active run under `skip`, owner inactive: `409` saying which. Accepted: `202 {run_id, status}`.

### Taking over

A schedule or trigger runs as the person who created it. When that person leaves or should no longer be the actor, a **workspace owner** takes it over (`POST /schedules/{id}/take-over`, `POST /triggers/{id}/take-over`): it runs as the owner from then on. Members cannot take over (they edit only their own), taking over one's own is refused (`409`), and each take-over is audited with the previous owner (`automation.schedule.taken_over`, `automation.trigger.taken_over`). The `owner_inactive` failure tells an owner to do this.

### Schedule descriptions in Arabic

`cron.describe(expression, "ar")` reads every supported expression in Arabic with the same structure as English (Arabic counting for minutes and hours, weekday and month names, `من الاثنين إلى الجمعة` for ranges); schedules return it as `cron_description_ar` and the preview as `description_ar`. Both clients show it when the app is in Arabic.

### Storage

| `ANUM_REPOSITORY_BACKEND` | Engine | Where |
|---|---|---|
| `memory` (local, tests) | `LocalAutomationEngine` | A SQLite file at `ANUM_AUTOMATION_DATABASE_PATH` (`.anum/automation.db`). One process only. Every statement is a fixed string, so there is no interpolated SQL and no bandit exception. |
| `postgresql` | `PostgresAutomationEngine` (`anum_api/db/automation_repository.py`) | `automation_workflows`, `automation_schedules` and `automation_runs` (migrations `0011_voice_automation` and `0031_automation_policy`: `overlap_policy`, `updated_by`, `workflow_version`, `last_outcome` and the per-workflow run index), and `automation_triggers` plus the runs' `trigger_id`, `trigger_type`, `trigger_payload`, `wake_at` and `waiting_task_id` (migration `0049_automation_triggers`, expand only: run statuses are unchanged, a waiting step is a running step). Shared by every API replica. |

In PostgreSQL every call is one transaction as the application role with the caller's tenant and workspace RLS context set. Queries also filter on both explicitly. All four tables have forced workspace RLS ([Multi-tenancy](multi-tenancy.md#control-plane-stores)).

- Writes need an onboarded workspace (`409` otherwise).
- Schedules and runs reference their workflow through a composite `(tenant_id, workspace_id, workflow_id)` foreign key, so they cannot point into another workspace.
- Cancel, resume and advancing a waiting step lock the run row (`FOR UPDATE`; the wake-ups skip a locked row). Firing a trigger locks the trigger row, so its rate limit holds across replicas.
- Triggers reference their workflow through the same composite key; deleting an automation deletes its triggers.
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

`ANUM_AUTOMATION_SCHEDULER_ENABLED=true` starts a background loop in each API process. It runs every `ANUM_AUTOMATION_SCHEDULER_POLL_SECONDS` (30); each pass fires up to `ANUM_AUTOMATION_SCHEDULER_BATCH_SIZE` (100) due schedules, then advances up to as many runs whose `wake_at` has passed (due retries, waiting steps to re-check) and catches event triggers up. It is off by default; until it is on, schedules are stored but never fire, retries are not started and only the in-process event path starts triggers (the dispatcher itself always runs, so Run now, event triggers and webhooks work without it).

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

### Step actions

Most steps only record that they ran. A feature can register a handler for its own action name with `automation.register_step_handler` (tyre planning registers `tyre.report`, docs/tyre-planning.md#reports). Handlers run after the run is committed (after a scheduler pass, and after `POST /workflows/{id}/runs` and `/runs/{id}/retry`), outside every engine transaction, so they may call external systems without holding a row lock. A failing handler is logged and never undoes the run; the feature records its own outcome (for tyre reports, on the report). A replayed run must not repeat work: `tyre.report` keys its report by run and step.

Deployment: the API login must be a member of `anum_maintenance` (`grant anum_maintenance to <api login> with inherit false, set true`; the code switches to it with `SET LOCAL ROLE`, and an inherited grant would let its cross-tenant discovery policies widen the API login's own queries). Without it, discovery fails with a permission error in the logs and nothing fires.

### From learned routines

[What ANUM learned](learning.md#routines) proposes routines mined from a user's own tasks. Accepting one creates a workflow (one `task.create` step per activity, with its prompt) and a **disabled** schedule through this engine, as the user (`automation:manage`); confirming enables the schedule. Its fire times now run those tasks as agent tasks under the user's role, budget and approvals, one after the other, each waiting for the previous one.

A step with `action: "report.generate"` and `input: {"type": "task_summary" | "automation_summary", "period_days": 1-366}` stores a shared report each time the workflow runs; a bad input fails the step ([Reports](reports.md)).

### Clients

- **Web** (`apps/web/src/AutomationWorkbench.tsx`, `AutomationTriggers.tsx`): the composer edits every action (run or only create the task; mailbox, recipients, subject and body for email; push for notify; attempts and the wait before a retry) and explains `{{...}}`; the **Triggers** section lists triggers with their event, conditions, limits, last outcome and owner, and adds and edits event triggers (event picker from `GET /trigger-events`, conditions with the event's fields) and webhooks (the address and secret shown once with copy buttons and a signing example; **New secret**; turn on or off; delete; **Take over** for owners). Schedules show the Arabic description in Arabic and offer **Take over** to owners. Runs show `Waiting for approval`, `Running a task` or `Retrying`, each step's attempt (`Attempt 1 of 3`), what it waits for, its result and a link to its task. Loading, empty, error and permission states on every section; English and Arabic; tests `apps/web/test/automationTriggers.test.ts`, `apps/web/e2e/automation-triggers.spec.ts` (1440x900 to 390x844 without overflow).
- **Flutter** (`lib/features/workspace/automations_screen.dart`, `lib/features/automations/automation_triggers_card.dart`): the same trigger card and editor sheet (secret dialog with copy), take-over for schedules and triggers, the step fields, run waiting states and results, Arabic schedule words; tests `apps/mobile/test/automation_triggers_test.dart` (320 to 1440 wide, 100% and 200% text, Arabic RTL).

## Not done yet

- Email delivery of failure notifications (the `email_enabled` preference) has no sender; use an `email.send` step to email from an automation.
- Event triggers on workspace events only: integration signals other than email, WhatsApp and files (for example marketplace orders) arrive through a webhook trigger.
- The Flutter step editor takes a mailbox id for `email.send` (the web picks from the connected mailboxes).
- Cancelling a run does not cancel the agent task it started; cancel it under Tasks.
- `python -m anum_api.rotate_secrets` does not re-encrypt webhook secrets; after retiring a key from `ANUM_SECRETS_KEY`, use **New secret** on each webhook trigger (an unreadable secret refuses deliveries with `401` and says to rotate).
- Inbound events (`email.received`, `whatsapp.message_received`, `file.uploaded`) are recorded right after the message or file is stored, in their own transaction: if the process stops between the two, the item is kept but automations miss it.

## Later

Add a natural-language automation builder, organization automation libraries, monitor dashboards, and preapproved low-risk action bundles.
