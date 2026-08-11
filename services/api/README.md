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

The current Alembic baseline executes `migrations/0001_foundation.sql`, which creates the core tables, enables pgvector, and applies tenant RLS policies.

## Included Slice

- Health endpoint.
- Task creation and lookup.
- Deterministic mock model gateway.
- Custom runtime with approval-aware state transitions.
- Approval approve/reject endpoints.
- Tenant-scoped task lookup.
- Repository boundary around task, run, approval, and event access.
- In-memory repository adapter for first validation.
- SQLAlchemy model declarations for tenants, workspaces, tasks, runs, steps, approvals, and events.
- Alembic baseline for the initial PostgreSQL migration with pgvector and tenant RLS policies.

## Persistence Direction

The API routes and runtime now depend on an ANUM repository boundary instead of reaching directly into storage dictionaries. The active adapter remains in-memory so the first vertical slice stays simple and deterministic. The next persistence step is to implement a PostgreSQL-backed repository using the SQLAlchemy models, migration, and RLS session context already in place.

Temporal, NATS, Keycloak token validation, and durable object storage remain the next implementation boundaries after PostgreSQL persistence is connected.