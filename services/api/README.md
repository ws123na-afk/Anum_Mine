# ANUM API

FastAPI service for the Phase 1 ANUM foundation.

## Local Run

```bash
cd services/api
python -m pip install -e .[test]
uvicorn anum_api.main:app --reload --port 8000
```

Set `ANUM_AUTH_MODE=oidc` to require Keycloak access tokens; the token contract,
workspace selection (`x-workspace-id`), and error statuses are specified in
[docs/identity.md](../../docs/identity.md). The default local mode
(`ANUM_AUTH_MODE=headers`) uses stub tenant headers and is refused at startup
unless `ANUM_ENVIRONMENT` is `local` or `test`:

```text
x-tenant-id: tenant_local
x-workspace-id: workspace_foundation

For the local web journey, create an expiring process-local session with
`POST /api/v1/auth/local/session`, then send the returned opaque token as a
Bearer token. The server stores only its SHA-256 hash and rejects this flow
unless `ANUM_ENVIRONMENT` is `local` or `test` and `ANUM_AUTH_MODE=headers`. Complete organization, workspace, and owner
membership setup idempotently with `PUT /api/v1/onboarding`.

Workspace model setup is available at `GET|PUT /api/v1/model-config`. Provider
credentials are write-only: responses contain only `credential_configured` and
the last four characters. With `ANUM_REPOSITORY_BACKEND=postgresql` model
configurations persist in the RLS-protected `workspace_model_configs` table with
the API key encrypted by `ANUM_SECRETS_KEY` (see `docs/model-gateway.md`). User
notification settings are available at `GET|PUT /api/v1/notification-preferences`
and are scoped by tenant, workspace, and user; that store is still process-local.

Local authentication also supports the Figma recovery and workspace-switching
flows. These endpoints return `404` outside local header/session mode:

- `POST /api/v1/auth/local/otp/request` and `/otp/verify`
- `POST /api/v1/auth/local/password/forgot` and `/password/reset`
- `POST /api/v1/auth/local/workspace/switch`

OTP and reset challenges expire, are single-use, retain only salted hashes, and
lock after five failed attempts. The raw `debug_secret` is returned only because
the entire route family is local-only; a production identity provider must own
delivery and recovery. Reset passwords are PBKDF2-HMAC hashed in the process-local
store and become mandatory for direct local sign-in. Workspace switching requires
an active membership and rotates the bearer session, invalidating the old token.
x-user-id: user_local
x-user-roles: owner,member
```

## Database Migrations

Run PostgreSQL locally first:

```bash
docker compose -f ../../infra/docker/compose.yaml up postgres
```

Apply migrations from `services/api`:

```bash
alembic upgrade head
```

Enable request-scoped PostgreSQL persistence after creating the tenant and workspace rows:

```text
ANUM_REPOSITORY_BACKEND=postgresql
```

The Alembic chain executes `migrations/0001_foundation.sql`, which creates the core tables, enables pgvector, and applies tenant RLS policies. Revision `0002_memory_retention` adds expiry metadata for durable task memory. Revision `0005_workspace_model_configs` adds per-workspace model configurations with encrypted provider keys and RLS.

## Included Slice

- Health endpoint.
- Task creation and lookup.
- Deterministic mock model gateway.
- Custom runtime with approval-aware state transitions.
- Structured agent planner with auditable skill selection.
- Declarative internal skill manifests for planning, drafting, and external actions.
- Governed tool registry with allow, approval, and blocked policy outcomes.
- Mediated internal response and mock external-action tool adapters.
- Live integration registry for PostgreSQL, Keycloak, NATS, Temporal, Valkey, and MinIO.
- Governed external REST tool adapter with host allowlisting and credential references.
- MCP-style tool adapter with tenant and actor context propagation.
- Tenant-scoped SSE event stream with task filters, cursors, and reconnect support.
- Approval approve/reject endpoints.
- Role-based authorization policy for owner, member, and viewer development claims.
- Stable API error envelopes and request correlation IDs.
- Tenant-scoped task lookup.
- Task-memory create, list, filter, retention, and delete flows.
- Repository boundaries around task, run, approval, event, and memory access.
- In-memory and request-scoped PostgreSQL repository adapters, including durable memory.
- SQLAlchemy model declarations for tenants, workspaces, tasks, runs, steps, approvals, events, and memories.
- Alembic migrations with pgvector, tenant RLS policies, and memory retention metadata.
- Contracts and focused tests for canonical events, audit records, and idempotency state.

## Persistence Direction

The API routes and runtime depend on ANUM repository boundaries instead of reaching directly into storage dictionaries. In-memory storage remains the local default; setting `ANUM_REPOSITORY_BACKEND=postgresql` selects SQLAlchemy adapters, applies tenant and workspace context to each request transaction, and durably stores task, run, approval, event, and memory changes.

Development header roles are never production-safe; shared environments must run `ANUM_AUTH_MODE=oidc`, where the persisted workspace membership role is authoritative. SQL-backed audit/idempotency records, a restart-durable (PostgreSQL) event outbox relay, Temporal, and durable object storage remain subsequent implementation boundaries.
## Event Bus

`ANUM_EVENT_BUS=nats` publishes committed canonical events to NATS JetStream (`ANUM_NATS_URL`, stream `ANUM_NATS_STREAM`, default `ANUM_EVENTS`) on `anum.<tenant>.<workspace>.<event type>` subjects and feeds `GET /api/v1/events/stream` from a JetStream consumer. The default, `memory`, keeps events in the repository only. Publishing never fails a request; while NATS is down events are queued and retried. See [Events](../../docs/events.md) and [Realtime](../../docs/realtime.md).

Integration tests marked `nats` run against a JetStream server and are skipped when it is unreachable:

```bash
docker compose -f ../../infra/docker/compose.yaml up -d nats
ANUM_TEST_NATS_URL=nats://127.0.0.1:4222 python -m pytest -m nats
```
