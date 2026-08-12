# ANUM API

FastAPI service for the Phase 1 ANUM foundation.

## Local Run

```bash
cd services/api
python -m pip install -e .[test]
uvicorn anum_api.main:app --reload --port 8000
```

Phase 1 uses stub tenant headers instead of full Keycloak/OIDC validation:

```text
x-tenant-id: tenant_local
x-workspace-id: workspace_foundation
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

The Alembic chain executes `migrations/0001_foundation.sql`, which creates the core tables, enables pgvector, and applies tenant RLS policies. Revision `0002_memory_retention` adds expiry metadata for durable task memory.

## Included Slice

- Health endpoint.
- Task creation and lookup.
- Deterministic mock model gateway.
- Custom runtime with approval-aware state transitions.
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

Keycloak token validation and persisted workspace membership remain required before the development header roles are production-safe. SQL-backed audit/idempotency records, a transactional event outbox, Temporal, NATS, and durable object storage remain subsequent implementation boundaries.
