# Roadmap

The ANUM roadmap is staged to keep the system useful early while protecting long-term architecture. Each phase should end with working software, tests, docs, and a reviewable security posture.

## Phase 0: Documentation Foundation

Status: current.

Create the repository documentation baseline, decide the foundational stack, define security and tenant principles, and record architecture decisions. No production implementation code is expected in this phase.

## Phase 1: Thin Vertical Slice

Build the smallest complete ANUM path:

- Keycloak-backed sign-in through OIDC.
- Tenant and workspace creation.
- FastAPI task creation endpoint.
- Agent run record persisted in PostgreSQL.
- Model gateway with one provider adapter and a mock adapter.
- Basic memory write/read for task notes.
- Approval gate for one risky sample action.
- NATS event publication and web realtime status stream.
- GitHub Actions for lint, tests, and docs checks.

## Phase 2: Practical Agent Workbench

Add reusable skills, more tools, richer task timelines, file/object storage, Temporal-backed long-running workflows, Valkey-backed ephemeral coordination, and a usable React workbench. Agents should become resumable, auditable, and cancellable.

## Phase 3: Multi-Surface Product

Package the web app in Tauri, add native desktop permissions, ship the first Kotlin Android client, and introduce voice sessions. All clients should use the same backend contracts and auth model.

## Phase 4: Team and Organization Controls

Add tenant administration, role templates, policy packs, scoped integrations, audit export, memory governance, admin dashboards, and organization-level approval rules.

## Phase 5: Scale and Ecosystem

Add multi-region deployment patterns, advanced queues, skill distribution, integration marketplace concepts, fine-grained cost controls, model routing policies, and enterprise operations features.

## Roadmap Discipline

Avoid building broad autonomy before approvals, auditability, and tenant isolation are real. New capabilities should first be introduced behind explicit scopes, feature flags, and observable events.