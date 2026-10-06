# Realtime

Realtime communication makes agent work understandable while it is happening. ANUM clients should receive task status, run steps, approval requests, tool results, notifications, and presence updates without polling every endpoint.

## Transport

The first implementation can use server-sent events for task streams because SSE is simple, browser-friendly, and fits one-way progress updates. WebSockets can be added when bidirectional low-latency collaboration, voice control, or presence requires it.

## Stream Sources

Realtime streams should be backed by persisted task state and NATS events. Clients should be able to reconnect and recover recent events from the API rather than losing context when a connection drops.

## Client Behavior

Clients should show clear task state: queued, running, waiting for approval, waiting for user input, completed, failed, canceled, or blocked. Approval events should be prominent and actionable. Tool execution should be visible enough to build trust without exposing secrets.

## Security

Realtime subscriptions must be authorized by tenant, workspace, and resource. Stream payloads should use the same redaction rules as REST responses.

## Implementation

`GET /api/v1/events/stream` is an SSE endpoint scoped to the caller's tenant and workspace, optionally filtered by `task_id`, resumable with `Last-Event-ID`, with `follow=false` for a one-shot replay.

- `ANUM_EVENT_BUS=memory` (default): the stream polls the repository once a second.
- `ANUM_EVENT_BUS=nats`: each API process runs one ordered JetStream consumer on `anum.>` (deliver new) that feeds an in-process realtime hub (`services/api/anum_api/realtime.py`). A stream registers a listener for its tenant and workspace before replaying history from the repository after the `Last-Event-ID` cursor, then forwards live events; duplicates from replay overlap or at-least-once redelivery are suppressed by event id. If NATS is unavailable, or a slow listener overflows its 1,000-event queue, the stream falls back to catching up from the repository, so no event is lost to a client.

A stream ends when `request.is_disconnected()` reports that its client left; uvicorn drops writes to a closed connection silently, so this check is the stream's only way out. Middleware in front of the API must therefore be plain ASGI: Starlette's `BaseHTTPMiddleware` hides the disconnect, which once left every closed SSE stream running and blocked graceful shutdown (`CorrelationIdMiddleware` is plain ASGI for this reason, covered by `tests/test_api_contracts.py`).

The CI job "Authenticated journey" (`services/api/scripts/run_journey.sh`) opens this stream as a Keycloak-authenticated user with `ANUM_EVENT_BUS=nats` and checks that a task's `task.created`, `approval.requested`, `approval.approved` and `agent_run.completed` events arrive in order, are on the `ANUM_EVENTS` JetStream stream, and are persisted.

Isolation: the hub routes a message only to listeners whose escaped tenant and workspace tokens equal the subject's, and only after checking that the decoded event's tenant, workspace, type and `Nats-Msg-Id` agree with the subject; mismatched messages are dropped and counted. Each listener re-checks the event against its own tenant context. Tenant-wide events (no workspace) reach every workspace of that tenant only. Tests in `services/api/tests/test_event_bus.py` cover cross-tenant and cross-workspace isolation, forged subjects and hostile identifiers.

## Now

Support task-level SSE streams, reconnect behavior, event cursors, approval notifications, and basic status fanout.

## Later

Add WebSocket collaboration, presence, mobile push bridge, offline replay, shared cursors, and realtime voice coordination.