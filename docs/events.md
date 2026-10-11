# Events

Events are how ANUM records important state changes and informs clients, workers, and integrations. Events should be durable enough for reliable workflows but concise enough to avoid becoming hidden data dumps.

## Event Backbone

NATS JetStream is the event backbone. Canonical task, agent run, approval and membership events are published to it today (see Publication below); it should also carry task lifecycle events, agent run steps, approval requests, tool execution results, memory changes, integration signals, and system notifications.

## Event Shape

Each event should include an ID, type, version, tenant ID, workspace ID when applicable, actor, subject, timestamp, correlation ID, causation ID, and payload. Payloads should be minimal and should avoid raw secrets, full prompts, and large file contents.

## Example Event Types

- `task.created`
- `agent_run.started`
- `agent_run.step.completed`
- `approval.requested`
- `approval.decided`
- `tool.execution.completed`
- `memory.created`
- `integration.webhook.received`

Task lifecycle events published today: `task.created`, `task.cancelled`, `task.updated` (payload `fields`: the changed field names, never the instructions), `task.archived`, `task.unarchived` and `task.retried` (payload `previous_run_id`); every task event's correlation id is the task id, which is how `GET /api/v1/tasks/{id}/events` finds a task's history ([Tasks](tasks.md)).
Autonomy publishes `autonomy.action_taken` (an action ANUM took on its own and announces; payload `action_id`, `task_id`, `tool`, `category`, `level`, `summary`, optional `undo_until`), `autonomy.action_undone` and `autonomy.proposal_created` ([Autonomy](autonomy.md)).

Approval decisions are published today as `approval.approved`, `approval.rejected` and `approval.expired` (after `approval.requested`). In an [approval chain](approvals-and-risk.md#approval-chains) every approval that does not yet complete the chain publishes `approval.partially_approved` (subject: the approval id; payload `task_id`, `approvals`, `required_approvals`, `approvers` and the optional `reason`), and the completing `approval.approved` adds the same counts.

## Delivery Rules

Consumers must be idempotent. Event handlers should tolerate duplicate delivery, out-of-order arrival where possible, and replay. Schema versions should evolve additively unless a new event type is introduced.

## Publication (implemented)

The bus is selected with `ANUM_EVENT_BUS=memory|nats` (default `memory`). In `memory` mode events are only persisted through the repository and realtime streams poll it. In `nats` mode (`services/api/anum_api/event_bus.py`):

- Subjects carry tenant context explicitly: `anum.<tenant_id>.<workspace_id>.<event type>`, e.g. `anum.tenant_a.workspace_a.task.created`. Events without a workspace use the reserved token `~`: `anum.<tenant_id>.~.<event type>`. Identifiers are escaped (any character outside `[A-Za-z0-9_-]`, including `.`, `*`, `>` and `~`, becomes `~xx` hex) so an id can never inject wildcards, add subject tokens or collide with another scope.
- All subjects live on one JetStream stream, `ANUM_EVENTS` (`ANUM_NATS_STREAM`), subjects `anum.>`, file storage, 7-day max age, 10-minute duplicate window. The API creates or updates the stream on connect.
- The payload is the persisted `DomainEvent` JSON (same shape as `GET /api/v1/events`). The `Nats-Msg-Id` header is the event id, so JetStream drops republished copies inside the duplicate window.
- Publishing never fails a request and the API runs without NATS: the repository stays the source of truth, `GET /api/v1/events` and SSE replay keep working, and unpublished events are published once NATS is back.
- How committed events reach the bus depends on the repository backend, as described below. Either way only committed events are published, delivery is at least once, and the `Nats-Msg-Id` dedupe window drops copies re-sent within 10 minutes.

### Durable PostgreSQL outbox (`ANUM_REPOSITORY_BACKEND=postgresql`)

Every `domain_events` row is its own outbox entry (migration `0007_event_outbox`). The row carries `published_at` (null until JetStream acknowledged it), `publish_attempts`, `publish_next_attempt_at` and `publish_last_error`. The event and its unpublished state are written by the same insert in the request's transaction, so a crash after commit cannot lose an event and a rolled-back event is never published. The relay (`services/api/anum_api/outbox_relay.py`) runs in every API process:

- A pass opens a transaction, runs `SET LOCAL ROLE anum_outbox_relay`, and claims up to `ANUM_OUTBOX_BATCH_SIZE` (100) due rows with `select ... where published_at is null and publish_next_attempt_at <= now() order by created_at, id for update skip locked`. It publishes them in that order, sets `published_at = now()` on the acknowledged ones and commits. Any number of API instances can relay concurrently: a row claimed by one relay is skipped by the others.
- A failed publish increments `publish_attempts`, stores the error and schedules the row `0.5 s * 2^(attempts-1)` later (capped at 30 s), then ends the pass; rows after it are released untouched and the next pass picks them up, so a single failing row does not block the rest. While NATS is disconnected the relay does not try, so outages do not burn attempts.
- An event that can never be published (its type is not a valid subject suffix) is parked: `publish_next_attempt_at = 'infinity'` with the error kept. Clear it by fixing the row and setting `publish_next_attempt_at = now()`.
- The relay wakes when a request commits events, on reconnect, and every `ANUM_OUTBOX_POLL_SECONDS` (1 s) to pick up rows other instances or a previous process left behind. Stopping the API loses nothing: unpublished rows wait in PostgreSQL for the next relay.
- A relay that crashes after JetStream acknowledged a publish but before it committed leaves the row unpublished, so it is published again; `Nats-Msg-Id` deduplicates that inside the 10-minute window. Ordering is by `created_at` per pass, not guaranteed across relays or retries; consumers already tolerate out-of-order and duplicate delivery (see Delivery Rules).
- The migration marks all history that existed before it as published, so switching the outbox on does not republish old events. Events recorded while `ANUM_EVENT_BUS=memory` stay unpublished in the table; switching that deployment to `nats` publishes that backlog.

#### Relay role and RLS

The relay must read events of every tenant, but the application role must keep its tenant-isolation policy and must never bypass RLS. The relay therefore runs as a dedicated `NOLOGIN` role, `anum_outbox_relay`, created by the migration (needs `CREATEROLE`; a DBA may create the role beforehand, then the migration leaves it as is):

| Privilege | Scope |
|---|---|
| Schema | `usage` on `public` only |
| `domain_events` select | Columns needed to publish (`id`, scope, type, version, subject, correlation id, payload, timestamps, attempts) |
| `domain_events` update | Only `published_at`, `publish_attempts`, `publish_next_attempt_at`, `publish_last_error` (column-level grant) |
| RLS policy `outbox_relay_read` | `for select to anum_outbox_relay using (published_at is null or published_at >= now())`: unpublished rows of all tenants, plus a row it is marking inside its own transaction (PostgreSQL checks an updated row against select policies too). History published by earlier transactions is invisible. |
| RLS policy `outbox_relay_mark` | `for update to anum_outbox_relay using (published_at is null)` |
| Everything else | Nothing: no insert or delete, no other table (tasks, memberships, invitations, audit records, memories all fail with `permission denied`). It is not `BYPASSRLS`. |

The two policies name `anum_outbox_relay` in their `TO` clause, so they never apply to the application role; `tenant_isolation_domain_events` is unchanged. The tests in `services/api/tests/test_postgres_outbox.py` check each of these limits.

Deployment: give the relay its own login that is a member of `anum_outbox_relay` and nothing else, and set `ANUM_OUTBOX_DATABASE_URL` to it. Without that setting the relay uses `ANUM_DATABASE_URL`, whose login must then be granted `anum_outbox_relay`; the relay still drops to that role for every transaction, but the API login could then assume it, so use a dedicated login in shared environments. The role is cluster-wide and is not dropped by the migration's downgrade.

### In-process outbox (`ANUM_REPOSITORY_BACKEND=memory`)

The in-memory repository has no durable storage, so after the request finishes the recorded events are handed to an in-process outbox. It publishes in order, waits for the JetStream acknowledgement, and retries a failed publish with exponential backoff (0.5 s doubling to 30 s); the API reconnects to NATS in the background with its own backoff. The queue is bounded (10,000 events; overflow drops the oldest from publication with a warning) and events still queued when the process exits are not republished. This mode is for local development.

### Monitoring

Both outboxes report `anum.outbox.backlog`, `anum.outbox.oldest_unpublished_age` and `anum.outbox.parked` gauges plus published, failed and rejected counters ([Observability](observability.md#metrics)). The PostgreSQL relay reads the backlog as `anum_outbox_relay` from its own loop, also while NATS is down. Each publish is a `publish ANUM_EVENTS` producer span, and the message carries a W3C `traceparent` header next to `Nats-Msg-Id` so consumers can continue the trace. Alerts and the procedure are in [Runbooks](runbooks.md#outbox-backlog).

## Developer Request Events

A refresh of a developer-mode request (when someone views it, or the developer watcher with phone push on, [Notifications](notifications.md#developer-mode)) that changes its status records `developer_request.ready_for_review` (now `waiting_approval`: the pull request is open with green checks) or `developer_request.failed` (now `ci_failed` or `failed`). Subject: the request id. Payload: `status`, `previous_status` and `pr_number`; never the request text or the pull request title.

## In-process Consumers

`EventBusRuntime.add_listener` registers a callback that receives every committed batch in the process that committed it, next to the outbox hand-off: the API request path, `tenant_unit_of_work` and the worker. Listeners must only queue work and never raise; phone push is the one listener today ([Notifications](notifications.md#delivery)). They see each event once per committing process, not once per cluster, and nothing is replayed after a restart.
## Automation Events

Automation runs publish `automation_run.completed`, `automation_run.paused`, `automation_run.failed` and `automation_run.cancelled`, and a `notify` step publishes `automation.notification` ([Automation](automation.md#events-and-notifications)). They are written in the automation engine's transaction. Failure and notification payloads carry `notify_user_ids`, the users the clients alert (for failures only those whose "automation failed" notification preference is on); payloads never carry step inputs or task prompts.

## Membership Events

Workspace invitations and membership management ([Identity](identity.md#invitations-and-membership-management)) emit `workspace_invitation.created`, `workspace_invitation.accepted`, `workspace_invitation.resent`, `workspace_invitation.revoked`, `workspace_member.added`, `workspace_member.role_changed`, `workspace_member.deactivated` and `workspace_member.reactivated`. The workspace lifecycle ([Identity](identity.md#workspace-lifecycle)) emits `workspace.created` (in the new workspace), `workspace.renamed`, `workspace.archived` and `workspace.restored`. Payloads carry ids, roles, the expiry and whether an invitation is email-bound; never the invitation token or the invitee's email (the email is kept in the audit record only).

## Now

Define event naming, publish core task and approval events to NATS JetStream, persist enough event history for task timelines, and stream relevant events to clients.

## Later

Add an outbox backlog metric and alert, event replay tools, dead-letter dashboards, schema registry checks, tenant-level event exports, and external event subscriptions.