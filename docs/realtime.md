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

- `ANUM_EVENT_BUS=memory` (default, and the Oracle VM): the stream polls the repository once a second, reading only the events after its cursor (`list_events_after`), so a poll stays cheap as the history grows.
- `ANUM_EVENT_BUS=nats`: each API process runs one ordered JetStream consumer on `anum.>` (deliver new) that feeds an in-process realtime hub (`services/api/anum_api/realtime.py`). A stream registers a listener for its tenant and workspace before replaying history from the repository after the `Last-Event-ID` cursor, then forwards live events; duplicates from replay overlap or at-least-once redelivery are suppressed by event id. If NATS is unavailable, or a slow listener overflows its 1,000-event queue, the stream falls back to catching up from the repository, so no event is lost to a client.

An open stream holds no database connection. Streaming routes that need the request repository (this one and `/audit/export`) take it with `Depends(repository_context, scope="function")`; the voice `ask/stream` and `look/stream` and the chat `ask` take none and read in their own short sessions in worker threads (see [Event loop](#event-loop)): FastAPI's default `"request"` scope keeps a `yield` dependency open until the response ends, which for a stream is the life of the tab. Each open tab then held a pooled PostgreSQL connection "idle in transaction"; about a dozen tabs, phones or reconnects used up the pool (5 + 10 overflow), every poll waited for a connection on the event loop, and the whole API, `/health` included, stopped answering for minutes (the "web app hangs after a short while" report on the Oracle VM, October 2026). The body reads through `list_events_for_stream`, a short session per poll, in a worker thread (`asyncio.to_thread`), never on the event loop. `tests/test_stream_sessions.py` fails if a streaming route holds a session dependency for the whole stream, and `tests/test_postgres_event_stream_pool.py` keeps more streams open than a small pool has connections and requires the API to keep answering.

A stream ends when `request.is_disconnected()` reports that its client left; uvicorn drops writes to a closed connection silently, so this check is the stream's only way out. Middleware in front of the API must therefore be plain ASGI: Starlette's `BaseHTTPMiddleware` hides the disconnect, which once left every closed SSE stream running and blocked graceful shutdown (`CorrelationIdMiddleware` is plain ASGI for this reason, covered by `tests/test_api_contracts.py`).

The CI job "Authenticated journey" (`services/api/scripts/run_journey.sh`) opens this stream as a Keycloak-authenticated user with `ANUM_EVENT_BUS=nats` and checks that a task's `task.created`, `approval.requested`, `approval.approved` and `agent_run.completed` events arrive in order, are on the `ANUM_EVENTS` JetStream stream, and are persisted.

Isolation: the hub routes a message only to listeners whose escaped tenant and workspace tokens equal the subject's, and only after checking that the decoded event's tenant, workspace, type and `Nats-Msg-Id` agree with the subject; mismatched messages are dropped and counted. Each listener re-checks the event against its own tenant context. Tenant-wide events (no workspace) reach every workspace of that tenant only. Tests in `services/api/tests/test_event_bus.py` cover cross-tenant and cross-workspace isolation, forged subjects and hostile identifiers.

## Event loop

The API runs one event loop per process (a single uvicorn worker on the Oracle VM). Database access is synchronous (SQLAlchemy sessions), so a store, repository or session used inside an `async def` route or an SSE body blocks every request of the process while it runs, and for as long as it waits for a pooled connection or a row lock. The rule:

- Assistant answers (`POST /api/v1/conversations/{id}/ask`, voice `ask`, `ask/stream` and `look/stream`) do every read and write in a worker thread (`asyncio.to_thread`), each in its own short unit of work, and keep no session open while the model answers. The voice routes open their unit of work inside the thread (`voice.prepare_ask_in_own_session`, which also checks the membership), instead of a request repository held across the model call.
- The budgeted model gateway (`model_budget.BudgetedModelGateway`) checks the budget and records usage in a worker thread; a stopped stream records its usage in the background. The retriever's index check and vector search run in a worker thread too.
- An SSE body never reads through the request's repository session (`list_events_for_stream`, `_store_reply`, `_store_voice_reply` open their own).
- The pool is sized for the streams and requests one user's tabs open and every wait is bounded: `ANUM_DATABASE_POOL_SIZE` (10), `ANUM_DATABASE_MAX_OVERFLOW` (20), `ANUM_DATABASE_POOL_TIMEOUT_SECONDS` (10; a request that cannot get a connection fails instead of waiting 30 s) and `ANUM_DATABASE_LOCK_TIMEOUT_MS` (10000; PostgreSQL `lock_timeout` on every API connection, 0 turns it off), so a wait for a row lock held by a stalled transaction cannot hold the process indefinitely.

Before this (October 2026, after the stream fix above) a chat question made dozens of blocking database round trips on the event loop and the voice `ask` kept a connection "idle in transaction" for the whole model call; on the Oracle VM one question in one tab stopped every other tab. `tests/test_ask_off_event_loop.py` fails if an assistant answer opens a store or unit of work on the event loop, and `tests/test_postgres_loop_responsiveness.py` (PostgreSQL, production pool settings, six open event streams, a slow model and a slow database) requires `/health` and an authenticated read to keep answering while the answer streams, with no connection idle in transaction.

Not yet covered: most other `async def` routes still make a few short database calls on the event loop, and an inline task run (`POST /tasks/{id}/run`, chat tool cards with `ANUM_RUNTIME_BACKEND=inline`) keeps its session and the task's row lock while the model answers. The pool and lock timeouts above bound those waits; moving them off the loop is follow-up work.

## Now

Support task-level SSE streams, reconnect behavior, event cursors, approval notifications, and basic status fanout.

## Later

Add WebSocket collaboration, presence, mobile push bridge, offline replay, shared cursors, and realtime voice coordination.