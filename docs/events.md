# Events

Events are how ANUM records important state changes and informs clients, workers, and integrations. Events should be durable enough for reliable workflows but concise enough to avoid becoming hidden data dumps.

## Event Backbone

NATS JetStream is the event backbone. Canonical task, agent run and approval events are published to it today (see Publication below); it should also carry task lifecycle events, agent run steps, approval requests, tool execution results, memory changes, integration signals, and system notifications.

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

## Delivery Rules

Consumers must be idempotent. Event handlers should tolerate duplicate delivery, out-of-order arrival where possible, and replay. Schema versions should evolve additively unless a new event type is introduced.

## Publication (implemented)

The bus is selected with `ANUM_EVENT_BUS=memory|nats` (default `memory`). In `memory` mode events are only persisted through the repository and realtime streams poll it. In `nats` mode (`services/api/anum_api/event_bus.py`):

- Subjects carry tenant context explicitly: `anum.<tenant_id>.<workspace_id>.<event type>`, e.g. `anum.tenant_a.workspace_a.task.created`. Events without a workspace use the reserved token `~`: `anum.<tenant_id>.~.<event type>`. Identifiers are escaped (any character outside `[A-Za-z0-9_-]`, including `.`, `*`, `>` and `~`, becomes `~xx` hex) so an id can never inject wildcards, add subject tokens or collide with another scope.
- All subjects live on one JetStream stream, `ANUM_EVENTS` (`ANUM_NATS_STREAM`), subjects `anum.>`, file storage, 7-day max age, 10-minute duplicate window. The API creates or updates the stream on connect.
- The payload is the persisted `DomainEvent` JSON (same shape as `GET /api/v1/events`). The `Nats-Msg-Id` header is the event id, so JetStream drops republished copies inside the duplicate window.
- Outbox flow: the request's repository records events in its transaction; only after the transaction commits are the recorded events handed to an in-process outbox. Rolled-back events are never published. The outbox publishes in order and waits for the JetStream acknowledgement (at-least-once). A failed publish keeps the event queued and retries with exponential backoff (0.5 s doubling to 30 s); the API reconnects to NATS in the background with its own backoff.
- Publishing never fails a request and the API runs without NATS: the repository stays the source of truth, `GET /api/v1/events` and SSE replay keep working, and queued events are published once NATS is back.
- Limits: the outbox is in process memory and bounded (10,000 events; overflow drops the oldest from publication with a warning). Events still queued when the process exits are not republished; they remain in the repository. A durable PostgreSQL outbox relay that survives restarts is a follow-up.

## Now

Define event naming, publish core task and approval events to NATS JetStream, persist enough event history for task timelines, and stream relevant events to clients.

## Later

Add a durable PostgreSQL outbox relay, event replay tools, dead-letter dashboards, schema registry checks, tenant-level event exports, and external event subscriptions.