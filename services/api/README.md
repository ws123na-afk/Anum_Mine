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

PostgreSQL, RLS, Temporal, NATS, and Keycloak remain the next implementation boundaries after this executable foundation.