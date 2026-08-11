# ADR-0001: ANUM Foundation

## Status

Accepted for documentation foundation.

## Context

ANUM needs a clear foundation before implementation begins. The project is intended to become a secure AI operating layer with agents, memory, automation, integrations, and multiple client surfaces. Without an agreed baseline, early code could fragment into incompatible prototypes.

## Decision

ANUM will start as a monorepo with documentation-first planning and the following target stack:

- Python/FastAPI backend core.
- React+TypeScript+Vite web app.
- Tauri desktop app.
- Kotlin Android app.
- PostgreSQL with pgvector for canonical and semantic data.
- Valkey for ephemeral state.
- NATS JetStream for events.
- Temporal for durable workflows.
- Custom ANUM agent runtime.
- Model gateway adapters for provider independence.
- MCP and REST integration support through mediated tools.
- Keycloak/OIDC identity.
- ANUM authorization with PostgreSQL row-level security.
- S3-compatible object storage.
- OpenTelemetry, OpenTofu, Docker, and GitHub Actions.

## Consequences

This foundation favors explicit control, secure tenant isolation, and long-term extensibility over fastest possible prototype speed. The first implementation should be a modular monolith with clear internal boundaries. Separate services may be extracted later when scale or ownership requires it.

The architecture also means documentation, contracts, and tests must stay close to implementation. Any major change to runtime, identity, data, infrastructure, or client architecture should update these docs or add a new ADR.

## Now

Use this ADR to guide the first vertical slice and prevent accidental stack drift.

## Later

Revisit decisions when production scale, compliance requirements, or platform constraints provide real evidence for change.