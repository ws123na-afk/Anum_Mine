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

## Included Slice

- Health endpoint.
- Task creation and lookup.
- Deterministic mock model gateway.
- Custom runtime with approval-aware state transitions.
- Approval approve/reject endpoints.
- Tenant-scoped task lookup.
- In-memory store for first validation.
- SQLAlchemy model declarations for tenants, workspaces, tasks, runs, steps, approvals, and events.
- Initial PostgreSQL migration with pgvector and tenant RLS policies.

## Persistence Direction

The API still uses the in-memory store for endpoint behavior so the first vertical slice stays simple and deterministic. The database foundation prepares the next step: wiring task, run, approval, event, and memory operations through PostgreSQL with RLS enforced by `anum.tenant_id` session context.

Temporal, NATS, Keycloak token validation, and durable object storage remain the next implementation boundaries after PostgreSQL persistence is connected.