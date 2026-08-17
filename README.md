# ANUM

ANUM is a monorepo for a personal and organizational AI operating layer: a secure agent runtime, automation platform, memory system, and multi-surface application suite.

## Current Status

Phase 0 documentation is complete. Phase 1 has an executable foundation with a working dashboard, real identity, and a deployable stack, and Phase 2's "practical agent workbench" boundaries are now real, opt-in infrastructure rather than future placeholders:

- FastAPI backend service under `services/api`: task/approval/memory/event/file APIs, idempotency support, rate limiting, and security headers.
- React+TypeScript+Vite web app under `apps/web`: a dashboard covering Tasks, Agent activity, Approvals, Memory, and Settings, with a Vitest + Testing Library suite.
- Shared TypeScript contracts under `packages/contracts`.
- Local infrastructure composition under `infra/docker`, including a Keycloak realm with the claim mappers Phase 1 identity needs, and Valkey/NATS/Temporal/MinIO services the API now actually talks to (see below).
- Production Dockerfiles for both the API and web app, a CI workflow publishing images to GHCR, and Fly.io deploy configs (`services/api/fly.toml`, `fly.web.toml`, `infra/fly/`) — see [Deployment](docs/deployment.md).
- GitHub Actions CI for web/contracts, API tests, and Docker Compose validation.

The backend supports in-memory development storage and request-scoped PostgreSQL persistence with row-level tenant isolation for task, runtime, approval, event, memory, and file flows. Identity has two modes, selected by `ANUM_AUTH_MODE` (backend) / `VITE_ANUM_AUTH_MODE` (frontend build-time): the original stub tenant/role headers (`stub_headers`, still the default — nothing changes unless you opt in), or real Keycloak/OIDC login with Authorization Code + PKCE (`oidc`) — see [Tenant Headers for Phase 1](#tenant-headers-for-phase-1) below.

**Phase 2 boundaries** (each opt-in via its own `ANUM_*` setting, defaulting to today's Phase 1 behavior when unset):

- **Valkey-backed coordination** (`ANUM_VALKEY_URL`): idempotency records and rate-limit counters move from single-process memory into Valkey, so they survive restarts and coordinate across replicas.
- **NATS JetStream events + realtime SSE** (`ANUM_NATS_URL`): domain events publish to JetStream in addition to the durable repository log; `GET /api/v1/events/stream` gives clients a live Server-Sent-Events feed (falling back to short polling when NATS isn't configured, so it works with zero extra infrastructure too).
- **Temporal-backed durable task runs** (`ANUM_TEMPORAL_ADDRESS`): `POST /tasks/{id}/run`, approval decisions, and cancellation become a durable `TaskWorkflow` (see `services/api/anum_api/workflows/`) instead of one synchronous in-process call, run by a worker the API process itself hosts.
- **S3-compatible object storage** (`ANUM_OBJECT_STORAGE_ENDPOINT_URL`): `/api/v1/files` for task/memory attachments, backed by MinIO in `infra/docker/compose.yaml` or any S3-compatible bucket in production.
- **Skills and tools**: a skill-manifest registry (`anum_api/skills.py`, two shipped skills) and a tool contract + mediation framework (`anum_api/tools.py`) with internal tools, one REST integration adapter, and one MCP-style adapter path — built and tested, but not yet wired into the live agent run loop (`anum_api/runtime.py` still runs the Phase 1 mock model call directly; see that module's own notes on the intended integration point).

None of this is exercised unless you set the corresponding env var — `docker compose -f infra/docker/compose.yaml up` wires the API to every one of them locally (see that file's `api` service); a bare `uvicorn --reload` with no `.env` still behaves exactly like Phase 1.

**Phase 3 clients have started**, sharing `apps/web`'s contracts and OIDC identity rather than duplicating runtime logic:

- `apps/desktop` — a Tauri shell wrapping `apps/web`'s build output, plus one native capability (a user-initiated file picker). Actually compiled, built, and launched (under Xvfb) in this environment against real GTK/WebKit system libraries — see that app's README for exactly what was verified.
- `apps/android` — a Kotlin/Jetpack Compose client covering task capture and mobile approvals, using AppAuth for the same Authorization Code + PKCE flow the web app uses. **Not build-verified**: this environment has no Android SDK and cannot reach the Google Maven repository that serves the Android Gradle Plugin itself — see that app's README for the exact failure and what building it for real requires.

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
pnpm --filter @anum/web test
```

Run the API:

```bash
cd services/api
python -m pip install -e .[test]
uvicorn anum_api.main:app --reload --port 8000
python -m pytest -m "not database"
```

Run local infrastructure — Postgres, Keycloak (with the dev realm auto-imported), and, once you've built the images, the API and web app themselves:

```bash
docker compose -f infra/docker/compose.yaml up
```

To build and run the API/web app as containers instead of via `pnpm dev`/`uvicorn --reload`, or to deploy either one somewhere real, see [Deployment](docs/deployment.md).

## Tenant Headers for Phase 1

By default (`ANUM_AUTH_MODE=stub_headers`, unset is the same thing), API routes trust explicit development headers with no verification — convenient for local work, not for anything reachable outside your own machine:

```text
x-tenant-id: tenant_local
x-workspace-id: workspace_foundation
x-user-id: user_local
x-user-roles: owner,member
```

Set `ANUM_AUTH_MODE=oidc` on the API and `VITE_ANUM_AUTH_MODE=oidc` (plus `VITE_ANUM_KEYCLOAK_URL`/`VITE_ANUM_KEYCLOAK_REALM`/`VITE_ANUM_KEYCLOAK_CLIENT_ID`, build-time) on the web app to switch to real Keycloak login instead — the stub headers stop working entirely once that's on. `infra/docker/keycloak/` has a ready-to-import dev/test realm with the claim mappers this expects; see `infra/docker/keycloak/README.md` for how to sign in against it.

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
- [Deployment](docs/deployment.md)
- [Observability](docs/observability.md)
- [Development standards](docs/development-standards.md)
- [Repository structure](docs/repository-structure.md)
- [Scaling](docs/scaling.md)
- [ADR-0001: Foundation](docs/decisions/ADR-0001-foundation.md)
- [ADR-0002: Custom agent runtime](docs/decisions/ADR-0002-custom-agent-runtime.md)

## Development Rule

Code should follow the contracts, boundaries, tenant model, and security expectations described in the documentation. Prototype shortcuts are acceptable only when they are isolated, named clearly, and replaced before production use.
