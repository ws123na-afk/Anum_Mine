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

## Now

Support task-level SSE streams, reconnect behavior, event cursors, approval notifications, and basic status fanout.

## Later

Add WebSocket collaboration, presence, mobile push bridge, offline replay, shared cursors, and realtime voice coordination.