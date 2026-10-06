# ANUM

ANUM is a monorepo for a personal and organizational AI operating layer: a secure agent runtime, automation platform, memory system, and multi-surface application suite.

## Current Status

Phase 0 documentation is complete. Phase 1 has started with an executable foundation:

- FastAPI backend service under `services/api`.
- React+TypeScript+Vite web app under `apps/web`.
- Tauri v2 desktop shell under `apps/desktop`.
- Native Kotlin/Compose Android client under `apps/android`.
- Flutter Android/iOS/tablet client under `apps/mobile`, aligned to the approved Figma system.
- Tenant-scoped voice sessions and browser push-to-talk task capture.
- Shared TypeScript contracts under `packages/contracts`.
- Local infrastructure composition under `infra/docker`.
- GitHub Actions CI for web/contracts, API tests, and Docker Compose validation.

The backend supports in-memory development storage and request-scoped PostgreSQL persistence with row-level tenant isolation for task, runtime, approval, event, and memory flows. `ANUM_AUTH_MODE=oidc` validates Keycloak tokens against persisted workspace memberships; stub tenant and role headers remain only for local/test environments. Committed canonical events can be published to NATS JetStream (`ANUM_EVENT_BUS=nats`) to drive the realtime status stream; Temporal is still a future boundary.

## Target Stack

- Backend core: Python with FastAPI
- Web app: React, TypeScript, and Vite
- Desktop app: Tauri shell around the web experience with native capabilities
- Android app: Kotlin, with shared contracts where practical
- Cross-platform mobile app: Flutter, using semantic design tokens and shared API contracts
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

Run the Flutter client after installing Flutter 3.22 or newer:

```bash
cd apps/mobile
flutter pub get
flutter run --dart-define=ANUM_API_URL=http://10.0.2.2:8000/
```

## Authentication

With `ANUM_AUTH_MODE=oidc` the API validates Keycloak access tokens (realm as code in `infra/keycloak/anum-realm.json`) and resolves the workspace membership; see [Identity and sign-in](docs/identity.md). The web and desktop clients sign in through Keycloak when built with `VITE_ANUM_OIDC_ISSUER` (see `apps/web/.env.example`), and the Flutter app with `--dart-define=ANUM_OIDC_ISSUER`; without them they keep the local development session. For local development only (`ANUM_ENVIRONMENT=local` or `test`), the default `ANUM_AUTH_MODE=headers` accepts explicit development headers or `anum_local_*` sessions; the API refuses to start in header mode anywhere else:

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
- [Threat model: agent tool use and prompt injection](docs/threat-model.md)
- [Identity and sign-in](docs/identity.md)
- [Multi-tenancy](docs/multi-tenancy.md)
- [Agent runtime](docs/agent-runtime.md)
- [Model gateway](docs/model-gateway.md)
- [Memory](docs/memory.md)
- [Skills](docs/skills.md)
- [Workspace files](docs/files.md)
- [Tools and integrations](docs/tools-and-integrations.md)
- [Automation](docs/automation.md)
- [Approvals and risk](docs/approvals-and-risk.md)
- [Data architecture](docs/data-architecture.md)
- [API contracts](docs/api-contracts.md)
- [Events](docs/events.md)
- [Realtime](docs/realtime.md)
- [Voice](docs/voice.md)
- [Voice research](docs/voice-research.md)
- [UI research](docs/ui-research.md)
- [Desktop](docs/desktop.md)
- [Android](docs/android.md)
- [Flutter mobile](docs/mobile.md)
- [Infrastructure](docs/infrastructure.md)
- [Observability](docs/observability.md)
- [Runbooks](docs/runbooks.md)
- [Development standards](docs/development-standards.md)
- [Production readiness gates](docs/production-readiness.md)
- [Production plan](docs/production-plan.md)
- [Figma mobile design system](docs/figma-mobile-design.md)
- [Repository structure](docs/repository-structure.md)
- [Scaling](docs/scaling.md)
- [Governance and scale](docs/governance-and-scale.md)
- [ADR-0001: Foundation](docs/decisions/ADR-0001-foundation.md)
- [ADR-0002: Custom agent runtime](docs/decisions/ADR-0002-custom-agent-runtime.md)

## Development Rule

Code should follow the contracts, boundaries, tenant model, and security expectations described in the documentation. Prototype shortcuts are acceptable only when they are isolated, named clearly, and replaced before production use.
