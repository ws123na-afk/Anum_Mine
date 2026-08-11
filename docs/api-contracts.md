# API Contracts

ANUM APIs should be versioned, typed, and stable enough for web, desktop, Android, and automation clients to share. FastAPI should expose OpenAPI documentation, but the repository should also keep human-readable contract rules.

## API Styles

- REST for standard request/response resources.
- Server-sent events or WebSocket channels for realtime task updates.
- Webhooks for inbound integration events.
- Internal worker APIs only where process boundaries require them.

## Resource Patterns

Resource paths should be tenant-aware through authenticated context, not by trusting client-supplied tenant IDs alone. Example shapes:

```text
POST /v1/tasks
GET /v1/tasks/{task_id}
POST /v1/tasks/{task_id}/cancel
POST /v1/approvals/{approval_id}/decide
GET /v1/agent-runs/{run_id}/events
```

## Contract Rules

Requests should use explicit JSON schemas, idempotency keys for mutating external actions, pagination for lists, stable error codes, and correlation IDs. Responses should avoid leaking internal provider payloads or secrets.

## Error Model

Errors should distinguish validation failure, authentication failure, authorization failure, missing resource, conflict, rate limit, policy block, approval required, upstream failure, and internal failure.

## Now

Define initial REST contracts for auth context, workspaces, tasks, runs, events, approvals, memories, and tool proposals.

## Later

Add public API keys, SDK generation, webhook management, partner APIs, GraphQL only if a clear product need appears, and compatibility guarantees for external developers.