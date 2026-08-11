# ANUM

ANUM is planned as a monorepo for a personal and organizational AI operating layer: a secure agent runtime, automation platform, memory system, and multi-surface application suite. This repository currently contains documentation only. Implementation code should be added after the foundation decisions are reviewed and accepted.

## Current Status

This branch establishes the project documentation baseline. It defines the intended architecture, security model, tenant model, agent runtime, model gateway, memory layer, integrations, infrastructure, development standards, and roadmap. It deliberately separates what ANUM should support now from what can be added later.

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

## Repository Map

The future monorepo is expected to contain backend, web, desktop, Android, contracts, infrastructure, docs, and testing workspaces. See [docs/repository-structure.md](docs/repository-structure.md) for the proposed layout.

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
- [Scaling](docs/scaling.md)
- [ADR-0001: Foundation](docs/decisions/ADR-0001-foundation.md)
- [ADR-0002: Custom agent runtime](docs/decisions/ADR-0002-custom-agent-runtime.md)

## Development Rule

Until the foundation is approved, changes should stay documentation-first. Code added later should follow the contracts, boundaries, and security model described here rather than growing from ad hoc prototypes.