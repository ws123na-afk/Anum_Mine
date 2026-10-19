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

The in-app call stream (`GET /api/v1/app-calls/stream`) rides the same bus without replaying history ([In-app calls](in-app-calls.md#signaling)). The realtime hub hands every message that passed its scope checks to its observers (`RealtimeHub.add_observer`); the call wake hub (`services/api/anum_api/app_call_wake.py`) is one, and wakes the call streams of the event's tenant and workspace on `app_call.ringing`, `app_call.updated` and `app_call.signaled`. A woken stream re-reads its calls and signals in a worker thread, in its own short session. With `ANUM_EVENT_BUS=memory`, or while NATS is down, the same events reach the wake hub in process from the committing thread (it only schedules the wake-up on the stream's loop, `call_soon_threadsafe`) and the stream keeps its 1 s / 0.25 s poll; with NATS live the poll slows to a 10 s fallback. `app_call.updated` and `app_call.signaled` are left out of `/events` and `/events/stream`. Covered by `tests/test_app_call_wake.py`, `tests/test_app_calls_off_event_loop.py` and the NATS integration test.

Isolation: the hub routes a message only to listeners whose escaped tenant and workspace tokens equal the subject's, and only after checking that the decoded event's tenant, workspace, type and `Nats-Msg-Id` agree with the subject; mismatched messages are dropped and counted. Each listener re-checks the event against its own tenant context. Tenant-wide events (no workspace) reach every workspace of that tenant only. Tests in `services/api/tests/test_event_bus.py` cover cross-tenant and cross-workspace isolation, forged subjects and hostile identifiers.

## Event loop

The API runs one event loop per process (a single uvicorn worker on the Oracle VM). Database access is synchronous (SQLAlchemy sessions), so a store, repository or session used inside an `async def` route or an SSE body blocks every request of the process while it runs, and for as long as it waits for a pooled connection or a row lock. The rule:

- Assistant answers (`POST /api/v1/conversations/{id}/ask`, voice `ask`, `ask/stream` and `look/stream`) do every read and write in a worker thread (`asyncio.to_thread`), each in its own short unit of work, and keep no session open while the model answers. The voice routes open their unit of work inside the thread (`voice.prepare_ask_in_own_session`, which also checks the membership), instead of a request repository held across the model call.
- The budgeted model gateway (`model_budget.BudgetedModelGateway`) checks the budget and records usage in a worker thread; a stopped stream records its usage in the background. The retriever's index check and vector search run in a worker thread too.
- An SSE body never reads through the request's repository session (`list_events_for_stream`, `_store_reply`, `_store_voice_reply` and the call stream's `read_snapshot` and `settle_for_stream` open their own, in worker threads; `tests/test_app_calls_off_event_loop.py` covers the call stream's woken reads, polls and settles).
- The pool is sized for the streams and requests one user's tabs open and every wait is bounded: `ANUM_DATABASE_POOL_SIZE` (10), `ANUM_DATABASE_MAX_OVERFLOW` (20), `ANUM_DATABASE_POOL_TIMEOUT_SECONDS` (10; a request that cannot get a connection fails instead of waiting 30 s) and `ANUM_DATABASE_LOCK_TIMEOUT_MS` (10000; PostgreSQL `lock_timeout` on every API connection, 0 turns it off), so a wait for a row lock held by a stalled transaction cannot hold the process indefinitely.

Before this (October 2026, after the stream fix above) a chat question made dozens of blocking database round trips on the event loop and the voice `ask` kept a connection "idle in transaction" for the whole model call; on the Oracle VM one question in one tab stopped every other tab. `tests/test_ask_off_event_loop.py` fails if an assistant answer opens a store or unit of work on the event loop, and `tests/test_postgres_loop_responsiveness.py` (PostgreSQL, production pool settings, six open event streams, a slow model and a slow database) requires `/health` and an authenticated read to keep answering while an answer streams or an inline task runs, with no connection idle in transaction, and a Cancel during a run to answer within two seconds.

- Inline task runs (`POST /tasks/{id}/run`, retry, automations and chat tool cards) never hold a transaction or the task's row lock while the model or the tool works: they claim, plan, apply and settle in short units of work in worker threads ([Agent runtime](agent-runtime.md#inline-runs)). A run used to keep the task's `SELECT ... FOR UPDATE` lock across the model call; a Cancel of that task then waited for the lock on the event loop, the run could never resume to release it, and the API stopped answering indefinitely. The tyre grounding tool also runs with no transaction open. `POST /tasks/{id}/cancel` takes the task's row lock and writes the cancellation in a worker thread, not on the event loop, so other requests keep being answered while it waits for the database (`test_postgres_loop_responsiveness.py`).
- `repository_context` (and the identity, memory and provisioning variants) wrap plain generators, so FastAPI opens, checks membership and commits them in its thread pool. `tenant_context` reads the OIDC membership and the archived-workspace flag in a worker thread. The voice session and transcript routes are plain `def` routes.

### Commit order

A request's unit of work commits, and publishes its events, before the response starts. FastAPI closes a `yield` dependency with its default scope (`"request"`) only after the response has been sent, so a client could read a `200` and send its next request before the write was committed. The authenticated journey hit this in CI (runs 38814995240 and 39113185705): `PUT /api/v1/onboarding` answered 200, the SSE request that followed checked the membership on another connection before the onboarding transaction had committed, and was refused with `403 Active workspace membership required`. `repository_context`, `provisioning_repository_context`, `identity_repository_context` and `memory_repository_context` are therefore plain wrappers around their generators resolved with `scope="function"` (`services/api/anum_api/dependencies.py`); every route keeps `Depends(repository_context)`. A side effect: background tasks a route schedules run after the commit and see its rows. `tests/test_commit_before_response.py` checks the order against the in-memory store and, with PostgreSQL, that the membership onboarding creates is committed before the 200 and admits the next request.

Not yet covered: an `async def` route that uses `repository_context` still calls the repository on the event loop for its own short reads and writes, and the approval decision (`POST /approvals/{id}/approve`) and `POST /agent-runs/{id}/resume` still run the tool inside their request transaction (no model call). The pool and lock timeouts above bound those waits.

## Now

Support task-level SSE streams, reconnect behavior, event cursors, approval notifications, and basic status fanout.

## Later

Add WebSocket collaboration, presence, mobile push bridge, offline replay, shared cursors, and realtime voice coordination.