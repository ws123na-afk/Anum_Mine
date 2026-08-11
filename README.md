# ANUM

ANUM is a monorepo for a personal and organizational AI operating layer: a secure agent runtime, automation platform, memory system, and multi-surface application suite.

## Current Status

Phase 0 documentation is complete. Phase 1 has started with an executable foundation:

- FastAPI backend service under `services/api`.
- React+TypeScript+Vite web app under `apps/web`.
- Shared TypeScript contracts under `packages/contracts`.
- Local infrastructure composition under `infra/docker`.
- GitHub Actions CI for web/contracts, API tests, and Docker Compose validation.

The backend currently uses in-memory persistence and stub tenant headers so the task/runtime/approval flow can be exercised before PostgreSQL, RLS, Keycloak, Temporal, and NATS are fully wired.

## Target Stack

- Backend core: Python with FastAPI
- Web app: React, TypeScript, and Vite
- Desktop app: Tauri shell around the web experience with native capabilities
- Android app: Kotlin, with shared contracts where practical
- Data: PostgreSQL with pgvector, object storage through an S3-compatible API, and Valkey for fast ephemeral state
- Messaging and workflows: NATS JetStream for event streams and Temporal for durable workflows
- Agent runtime: custom ANUM runtime for planning, tool execution, memory access, approval gates, and risk controls
- AI access: model gateway adapters for multiple model providers
- Identity: Keycloak/OIDC with ANUM authorization and PostgreSQL row-level security
- Operations: Docker, OpenTofu, GitHub Actions, and OpenTelemetry

## Local Development

Install web dependencies and run checks:

```bash
pnpm install
pnpm check
pnpm build
```

Run the API:

```bash
cd services/api
python -m pip install -e .[test]
uvicorn anum_api.main:app --reload --port 8000
```

Run local infrastructure:

```bash
docker compose -f infra/docker/compose.yaml up
```

## Tenant Headers for Phase 1

Until OIDC is implemented, API routes require explicit development headers:

```text
x-tenant-id: tenant_local
x-workspace-id: workspace_foundation
x-user-id: user_local
x-user-roles: owner,member
```

## Documentation Index

- [Product vision](docs/product-vision.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Security](docs/security.md)
- [Multi-tenancy](docs/multi-tenancy.md)
- [Agent runtime](docs/agent-runtime.md)
- [Model gateway](docs/model-gateway.md)
- [Memory](docs/memory.md)
- [Skills](docs/skills.md)
- [Tools and integrations](docs/tools-and-integrations.md)
- [Automation](docs/automation.md)
- [Approvals and risk](docs/approvals-and-risk.md)
- [Data architecture](docs/data-architecture.md)
- [API contracts](docs/api-contracts.md)
- [Events](docs/events.md)
- [Realtime](docs/realtime.md)
- [Voice](docs/voice.md)
- [Desktop](docs/desktop.md)
- [Android](docs/android.md)
- [Infrastructure](docs/infrastructure.md)
- [Observability](docs/observability.md)
- [Development standards](docs/development-standards.md)
- [Repository structure](docs/repository-structure.md)
- [Scaling](docs/scaling.md)
- [ADR-0001: Foundation](docs/decisions/ADR-0001-foundation.md)
- [ADR-0002: Custom agent runtime](docs/decisions/ADR-0002-custom-agent-runtime.md)

## Development Rule

Code should follow the contracts, boundaries, tenant model, and security expectations described in the documentation. Prototype shortcuts are acceptable only when they are isolated, named clearly, and replaced before production use.