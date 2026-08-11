# Events

Events are how ANUM records important state changes and informs clients, workers, and integrations. Events should be durable enough for reliable workflows but concise enough to avoid becoming hidden data dumps.

## Event Backbone

NATS JetStream is the planned event backbone. It should carry task lifecycle events, agent run steps, approval requests, tool execution results, memory changes, integration signals, and system notifications.

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

## Now

Define event naming, publish core task and approval events, persist enough event history for task timelines, and stream relevant events to clients.

## Later

Add event replay tools, dead-letter dashboards, schema registry checks, tenant-level event exports, and external event subscriptions.