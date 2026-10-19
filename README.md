# ANUM

ANUM is a monorepo for a personal and organizational AI operating layer: a secure agent runtime, automation platform, memory system, and multi-surface application suite.

## Current Status

Phase 0 documentation is complete. Phase 1 has started with an executable foundation:

- FastAPI backend service under `services/api`.
- React+TypeScript+Vite web app under `apps/web`.
- Tauri v2 desktop shell under `apps/desktop`.
- Native Kotlin/Compose Android client under `apps/android` (frozen: builds in CI, no new features).
- Flutter Android/iOS/tablet client under `apps/mobile`, aligned to the approved Figma system; the shipping mobile app.
- Tenant-scoped voice sessions and browser push-to-talk task capture.
- Shared TypeScript contracts under `packages/contracts`.
- Local infrastructure composition under `infra/docker`.
- GitHub Actions CI for web/contracts, API tests, and Docker Compose validation.

The backend supports in-memory development storage and request-scoped PostgreSQL persistence with row-level tenant isolation for task, runtime, approval, event, and memory flows. `ANUM_AUTH_MODE=oidc` validates Keycloak tokens against persisted workspace memberships; stub tenant and role headers remain only for local/test environments. Committed canonical events can be published to NATS JetStream (`ANUM_EVENT_BUS=nats`) to drive the realtime status stream; Temporal is still a future boundary.

## Target Stack

- Backend core: Python with FastAPI
- Web app: React, TypeScript, and Vite
- Desktop app: Tauri shell around the web experience with native capabilities
- Android and iOS app: Flutter (`apps/mobile`) with semantic design tokens and shared API contracts; the earlier Kotlin Android client is frozen
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
- [Business model and product master roadmap](docs/ANUM_OS_Business_and_Product_Master_Roadmap.md)
- [Business gap register (Order 001)](docs/business/ANUM_BUSINESS_GAP_REGISTER.md)
- [Reuse decision register: outside projects and their licenses](docs/business/REUSE_DECISION_REGISTER.md)
- [P0 CI remediation: commerce tests, journey 403, Rust advisories](docs/business/P0_CI_REMEDIATION.md)
- [P1 acceptance tests for the Business section](docs/business/P1_ACCEPTANCE_TESTS.md)
- [Product sales kits and the catalog loader (seed-products.py)](docs/business/products/index.md)
- [Sales kit: ANUM OS](docs/business/products/anum-os.md)
- [Sales kit: Tyre Pulse](docs/business/products/tyre-pulse.md)
- [Sales kit: VoiceQuote](docs/business/products/voicequote.md)
- [Sales kit: Anum360 Store](docs/business/products/anum360-store.md)
- [Security](docs/security.md)
- [Threat model: agent tool use and prompt injection](docs/threat-model.md)
- [Email Center: external penetration test plan](docs/email-pentest-plan.md)
- [Identity and sign-in](docs/identity.md)
- [Multi-tenancy](docs/multi-tenancy.md)
- [Agent runtime](docs/agent-runtime.md)
- [Agents: user-built agents and department assistants](docs/agents.md)
- [AI team org chart: owner, ANUM, departments and agents, live](docs/org-chart.md)
- [Module checklist: what a complete module page needs](docs/module-checklist.md)
- [Tasks and agent runs](docs/tasks.md)
- [ANUM Command: plan, run and follow one request](docs/command.md)
- [Model gateway](docs/model-gateway.md)
- [Conversations](docs/conversations.md)
- [Developer mode](docs/developer-mode.md)
- [WhatsApp Business inbox](docs/whatsapp.md)
- [Commerce: Amazon, noon and the own store](docs/commerce.md)
- [Finance: your own ledger, safe-to-spend, Guardian rules, read-only connectors, paper trading and the execution gateway (live off)](docs/finance.md)
- [Trading intelligence: market data, strategy lab and SIMULATED trading (live off)](docs/finance-trading-intel.md)
- [Trading intelligence gap assessment](docs/finance-trading-gap-assessment.md)
- [Trading intelligence readiness report (loop 10, live stays off)](docs/finance-trading-readiness.md)
- [Tyre planning](docs/tyre-planning.md)
- [Social media marketing](docs/marketing.md)
- [Email campaigns through Brevo (connection, drafts, approval-only sending, agent tools)](docs/brevo.md)
- [Customer billing: plans, subscriptions, invoices, payments, dunning and self-serve sign-up](docs/billing.md)
- [Business: the internal sales desk (catalog, leads, research, pipeline, proposals, projects; sending off)](docs/business.md)
- [Memory](docs/memory.md)
- [What ANUM learned: feedback, style, routines and improvement notes](docs/learning.md)
- [Knowledge base and favorites](docs/knowledge-base.md)
- [Reports](docs/reports.md)
- [Privacy: download and delete my data](docs/privacy.md)
- [Privacy policy template](docs/privacy-policy.md)
- [Public pages: legal documents, Trust and status](docs/legal-pages.md)
- [Terms of Service (draft)](docs/legal/terms-of-service.md)
- [Privacy Policy (draft, Saudi and UAE PDPL)](docs/legal/privacy-policy.md)
- [Data Processing Addendum (draft)](docs/legal/data-processing-addendum.md)
- [Acceptable Use Policy (draft)](docs/legal/acceptable-use-policy.md)
- [Subprocessor list (draft)](docs/legal/subprocessors.md)
- [Store data safety and app privacy answers](docs/store-data-safety.md)
- [Skills](docs/skills.md)
- [Workspace files](docs/files.md)
- [In-app help: task-based articles per module, bundled for offline use](docs/help.md)
- [Sales demo workspace: sample data, reset, sending off](docs/demo-workspace.md)
  - Help, Activity: [English](docs/help/activity.md), [Arabic](docs/help/activity.ar.md)
  - Help, Agents: [English](docs/help/agents.md), [Arabic](docs/help/agents.ar.md)
  - Help, Approvals: [English](docs/help/approvals.md), [Arabic](docs/help/approvals.ar.md)
  - Help, AI Chat: [English](docs/help/assistant.md), [Arabic](docs/help/assistant.ar.md)
  - Help, Assistants: [English](docs/help/assistants.md), [Arabic](docs/help/assistants.ar.md)
  - Help, Audit log: [English](docs/help/audit.md), [Arabic](docs/help/audit.ar.md)
  - Help, Automations: [English](docs/help/automation.md), [Arabic](docs/help/automation.ar.md)
  - Help, Budgets: [English](docs/help/budgets.md), [Arabic](docs/help/budgets.ar.md)
  - Help, Business: [English](docs/help/business.md), [Arabic](docs/help/business.ar.md)
  - Help, Calendar: [English](docs/help/calendar.md), [Arabic](docs/help/calendar.ar.md)
  - Help, Call: [English](docs/help/call.md), [Arabic](docs/help/call.ar.md)
  - Help, Phone calls: [English](docs/help/calls.md), [Arabic](docs/help/calls.ar.md)
  - Help, ANUM Command: [English](docs/help/command.md), [Arabic](docs/help/command.ar.md)
  - Help, Commerce: [English](docs/help/commerce.md), [Arabic](docs/help/commerce.ar.md)
  - Help, Demo workspace: [English](docs/help/demo.md), [Arabic](docs/help/demo.ar.md)
  - Help, Developer: [English](docs/help/developer.md), [Arabic](docs/help/developer.ar.md)
  - Help, Email: [English](docs/help/email.md), [Arabic](docs/help/email.ar.md)
  - Help, Enterprise: [English](docs/help/enterprise.md), [Arabic](docs/help/enterprise.ar.md)
  - Help, Favorites: [English](docs/help/favorites.md), [Arabic](docs/help/favorites.ar.md)
  - Help, Documents: [English](docs/help/files.md), [Arabic](docs/help/files.ar.md)
  - Help, Finance: [English](docs/help/finance.md), [Arabic](docs/help/finance.ar.md)
  - Help, Governance: [English](docs/help/governance.md), [Arabic](docs/help/governance.ar.md)
  - Help, Home: [English](docs/help/home.md), [Arabic](docs/help/home.ar.md)
  - Help, Inbox: [English](docs/help/inbox.md), [Arabic](docs/help/inbox.ar.md)
  - Help, Integrations: [English](docs/help/integrations.md), [Arabic](docs/help/integrations.ar.md)
  - Help, Knowledge Base: [English](docs/help/knowledge.md), [Arabic](docs/help/knowledge.ar.md)
  - Help, What ANUM learned: [English](docs/help/learning.md), [Arabic](docs/help/learning.ar.md)
  - Help, Marketing: [English](docs/help/marketing.md), [Arabic](docs/help/marketing.ar.md)
  - Help, Calls & Meetings: [English](docs/help/meetings.md), [Arabic](docs/help/meetings.ar.md)
  - Help, Members: [English](docs/help/members.md), [Arabic](docs/help/members.ar.md)
  - Help, Organisations: [English](docs/help/organisations.md), [Arabic](docs/help/organisations.ar.md)
  - Help, Billing console: [English](docs/help/billing-console.md), [Arabic](docs/help/billing-console.ar.md)
  - Help, Approval policy: [English](docs/help/policy.md), [Arabic](docs/help/policy.ar.md)
  - Help, Reports: [English](docs/help/reports.md), [Arabic](docs/help/reports.ar.md)
  - Help, Settings: [English](docs/help/settings.md), [Arabic](docs/help/settings.ar.md)
  - Help, Setup plan: [English](docs/help/setup.md), [Arabic](docs/help/setup.ar.md)
  - Help, ANUM Store: [English](docs/help/skills.md), [Arabic](docs/help/skills.ar.md)
  - Help, Contact opt-outs: [English](docs/help/suppressions.md), [Arabic](docs/help/suppressions.ar.md)
  - Help, Tasks: [English](docs/help/tasks.md), [Arabic](docs/help/tasks.ar.md)
  - Help, Team: [English](docs/help/team.md), [Arabic](docs/help/team.ar.md)
  - Help, Tyre planning: [English](docs/help/tyre.md), [Arabic](docs/help/tyre.ar.md)
  - Help, Voice: [English](docs/help/voice.md), [Arabic](docs/help/voice.ar.md)
  - Help, WhatsApp: [English](docs/help/whatsapp.md), [Arabic](docs/help/whatsapp.ar.md)
- [Calls & Meetings](docs/meetings.md)
- [Phone calls: contacts, dialpad, call log, AI summaries; provider and AI calls off until an owner connects them](docs/calls.md)
- [In-app calls between teammates](docs/in-app-calls.md)
- [Tools and integrations](docs/tools-and-integrations.md)
- [Connecting integrations: the owner's runbook](docs/integrations-setup.md)
- [Email: the unified inbox](docs/email.md)
- [Reports](docs/reports.md)
- [Calendar and next actions](docs/calendar.md)
- [Home and proactive insights](docs/home.md)
- [Automation](docs/automation.md)
- [Approvals and risk](docs/approvals-and-risk.md)
- [Notifications and phone push](docs/notifications.md)
- [Autonomy](docs/autonomy.md)
- [Team work and department assistants](docs/team-work.md)
- [Customer organisations](docs/customer-organisations.md)
- [Data architecture](docs/data-architecture.md)
- [API contracts](docs/api-contracts.md)
- [Events](docs/events.md)
- [Realtime](docs/realtime.md)
- [Voice](docs/voice.md)
- [Voice research](docs/voice-research.md)
- [UI research](docs/ui-research.md)
- [Web localization and accessibility](docs/web-localization.md)
- [Desktop](docs/desktop.md)
- [Android](docs/android.md)
- [Flutter mobile](docs/mobile.md)
- [Mobile journey tests](docs/mobile-journeys.md)
- [Mobile app features](docs/mobile-features.md)
- [Infrastructure](docs/infrastructure.md)
- [Deployment (Kubernetes and Helm)](docs/deployment.md)
- [Deploying on Render](docs/deploy-render.md)
- [Deploying on one Oracle Cloud VM](docs/deploy-oracle.md)
- [Monitoring the Oracle VM](docs/monitoring-oracle.md)
- [Observability](docs/observability.md)
- [Runbooks](docs/runbooks.md)
- [Development standards](docs/development-standards.md)
- [Production readiness gates](docs/production-readiness.md)
- [Production plan](docs/production-plan.md)
- [Pending work and owner steps (handoff)](docs/pending-work.md)
- [Full skills audit (October 2026)](docs/audits/2026-10-12-full-skills-audit.md)
- [Figma mobile design system](docs/figma-mobile-design.md)
- [ANUM 360 redesign (owner mockups)](docs/design/anum360.md)
- [Repository structure](docs/repository-structure.md)
- [Scaling](docs/scaling.md)
- [Governance and scale](docs/governance-and-scale.md)
- [Setup plan: apply a business blueprint](docs/setup-plan.md)
- [ADR-0001: Foundation](docs/decisions/ADR-0001-foundation.md)
- [ADR-0002: Custom agent runtime](docs/decisions/ADR-0002-custom-agent-runtime.md)

## Development Rule

Code should follow the contracts, boundaries, tenant model, and security expectations described in the documentation. Prototype shortcuts are acceptable only when they are isolated, named clearly, and replaced before production use.
