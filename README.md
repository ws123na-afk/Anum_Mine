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
- [Sales CRM web dashboard](docs/sales-dashboard.md)
- [Sales CRM Flutter workspace](docs/sales-flutter.md)
- [Sales quotation approval, autonomous follow-ups and production verification](docs/sales-web-execution-readiness.md)
- [Marketing sales agent orchestration](docs/marketing-sales-agent-orchestration.md)
- [Marketing WhatsApp sales](docs/marketing-whatsapp-sales.md)
- [Sales CRM API](docs/sales-crm-api.md)
- [Sales lead lifecycle](docs/sales-lead-lifecycle.md)

- [ANUM MINE room geometry and branch reconciliation](docs/ANUM_MINE_GEOMETRY_AND_BRANCH_RECONCILIATION.md)

- [ANUM MINE release completion checkpoint](docs/ANUM_MINE_RELEASE_CHECKPOINT.md)

- [Product vision](docs/product-vision.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Business model and product master roadmap](docs/ANUM_OS_Business_and_Product_Master_Roadmap.md)
- [ANUM MINE F09 audited work history and replay API](docs/ANUM_MINE_F09_AUDITED_HISTORY_API.md)
- [Integration and release plan](docs/INTEGRATION_AND_RELEASE.md)
- [ANUM MINE virtual headquarters master plan (planned web office, guided tour and agent rooms)](docs/ANUM_MINE_VIRTUAL_HEADQUARTERS_MASTER_PLAN.md)
- [ANUM MINE Executive PA Guided Tour](docs/ANUM_MINE_EXECUTIVE_PA_TOUR.md)
- [ANUM MINE headquarters live flow, stuck work and office teams](docs/ANUM_MINE_HQ_LIVE_FLOW.md)
- [ANUM MINE headquarters history panel and room history slider](docs/ANUM_MINE_HQ_HISTORY_PANEL.md)
- [ANUM MINE headquarters live scene: presence stream, desk states, operations wall, Inside camera](docs/ANUM_MINE_HQ_LIVE_SCENE.md)
- [ANUM MINE headquarters governed agent provisioning](docs/ANUM_MINE_AGENT_PROVISIONING.md)
- [ANUM MINE headquarters: assign work in place and add a building by asking](docs/ANUM_MINE_ASSIGN_WORK.md)
- [ANUM MINE starter campus: one click Set up my campus](docs/ANUM_MINE_STARTER_CAMPUS.md)
- [ANUM MINE Arabic names of buildings, floors and rooms](docs/ANUM_MINE_ARABIC_NAMES.md)
- [ANUM MINE Green Concrete: command center, L tables, shifts and people](docs/ANUM_MINE_GREEN_CONCRETE.md)
- [ANUM MINE floor overview: the whole floor on one screen, and the fitted building camera](docs/ANUM_MINE_FLOOR_OVERVIEW.md)
- [ANUM MINE headquarters label cards and scene chrome: room and agent cards, density rule, floor selector, live strips](docs/ANUM_MINE_HQ_LABELS.md)
- [ANUM MINE Staff my campus: a reviewed paused agent for every empty desk](docs/ANUM_MINE_STAFF_DESKS.md)
- [ANUM MINE departments with real modules, agent roles for them and meetings tied to rooms](docs/ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md)
- [ANUM MINE headquarters building modules, command centre room kind, personal office and floor transitions](docs/ANUM_MINE_HQ_BUILDING_MODULES.md)
- [ANUM MINE Executive PA tools and executive desk](docs/ANUM_MINE_EXECUTIVE_PA.md)
- [ANUM MINE Live Employee Workstations](docs/ANUM_MINE_WORKSTATIONS_MODULE.md)
- [ANUM MINE Reception & Visitor Operations](docs/ANUM_MINE_RECEPTION_MODULE.md)
- [ANUM MINE Office Space & Desk Registry](docs/ANUM_MINE_SPACE_REGISTRY.md)
- [ANUM MINE live agent meeting PostgreSQL 0098](docs/ANUM_MINE_LIVE_MEETING_POSTGRES_0098.md)
- [ANUM MINE headquarters integration and release gates](docs/INTEGRATION_AND_RELEASE.md)
- [ANUM MINE autonomous meeting rounds, API and safety gates](docs/ANUM_MINE_AUTONOMOUS_MEETINGS.md)
- [ANUM MINE headquarters market research and office builder plan](docs/ANUM_MINE_HQ_MARKET_RESEARCH.md)
- [ANUM MINE Office Registry production backend](docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md)
- [ANUM MINE layout history, restore and templates](docs/ANUM_MINE_LAYOUT_HISTORY.md)
- [ANUM MINE headquarters: campus, building cutaway and extension points](docs/ANUM_MINE_HQ_EXTENSION_POINTS.md)
- [ANUM MINE headquarters page layout and side panels (docked panels, live status, states, keyboard)](docs/ANUM_MINE_HQ_PAGE_LAYOUT.md)
- [ANUM MINE headquarters baseline audit (code to feature matrix, CI, issue register)](docs/ANUM_MINE_HQ_BASELINE_AUDIT.md)
- [ANUM MINE feature completion matrix (requirement by requirement status, evidence, gaps, top 10 actions)](docs/ANUM_MINE_FEATURE_COMPLETION_MATRIX.md)
- [ANUM MINE headquarters performance budget](docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md)
- [ANUM MINE headquarters graphics check, frame governor, GPU recovery and the owner-run GPU script](docs/ANUM_MINE_HQ_GPU_CHECK.md)
- [ANUM MINE headquarters streaming and load testing](docs/ANUM_MINE_HQ_LOAD_TESTING.md)
- [ANUM MINE headquarters security review](docs/ANUM_MINE_HQ_SECURITY_REVIEW.md)
- [ANUM MINE headquarters deployment checklist and rollback](docs/ANUM_MINE_HQ_DEPLOYMENT_AND_ROLLBACK.md)
- [ANUM MINE headquarters realism pass: glTF models, gap analysis against the concept renders, licences and measurements](docs/ANUM_MINE_HQ_REALISM_ASSETS.md)
- [ANUM MINE headquarters people: the character kit, three looks (masculine, feminine, neutral), choosing the look in the Team panel and in Add Green Concrete](docs/ANUM_MINE_HQ_PEOPLE_CHARACTERS.md)
- [ANUM MINE headquarters building and campus look pass: what differed from each concept render, what changed, what still differs](docs/ANUM_MINE_HQ_BUILDING_LOOK.md)
- [ANUM MINE headquarters photographic look: quality tiers 0 to 4, procedural PBR materials, baked and screen space occlusion, contact shadows, post effects, measured cost, how to reach the top tier](docs/ANUM_MINE_HQ_PHOTOREAL.md)
- [ANUM MINE headquarters glass plan: the rooms seen through the glass are the real floors, the shell follows the floor plans, occupancy truth, budgets](docs/ANUM_MINE_HQ_GLASS_PLAN.md)
- [ANUM MINE headquarters interiors: room kits, floor shell, door sides, service core, ambient scenery, starter campus completeness](docs/ANUM_MINE_HQ_INTERIORS.md)
- [ANUM MINE headquarters cutaway section: front walls and roof removed by camera azimuth, all floors, Cutaway and Closed, 90 degree rotate snaps, label hook](docs/ANUM_MINE_HQ_CUTAWAY.md)
- [ANUM MINE headquarters ambient scenery motion: what moves, what never moves, quality levels and reduced motion](docs/ANUM_MINE_HQ_SCENERY_MOTION.md)
- [ANUM MINE headquarters campus motion: ambient traffic with turning wheels, and record-backed work motion (trucks, tyres) with a Because line per event, the table of every motion, and what Tyre Pulse data is readable](docs/ANUM_MINE_HQ_CAMPUS_MOTION.md)
- [ANUM MINE headquarters agent walkers: figures of real agents walking between buildings only because a stored record says they moved, the table of every trigger, what location data really exists, and what stays disconnected](docs/ANUM_MINE_HQ_AGENT_WALKERS.md)
- [ANUM MINE headquarters building billboards: a board in front of each building and a card with what it does and its real work details, every field and its source, Not connected rule](docs/ANUM_MINE_HQ_BILLBOARDS.md)
- [ANUM MINE headquarters live screens: the Operations wall and desk monitors drawn on the 3D screens with real figures, every field and its source, caps, distance gate](docs/ANUM_MINE_HQ_LIVE_SCREENS.md)
- [ANUM MINE headquarters logo and brand signs: the owner's own logo, brand colour and sign style per building, upload steps, API, security, what is not done](docs/ANUM_MINE_HQ_LOGO_SIGNS.md)
- [ANUM MINE headquarters centre plaza: the big fountain, roundabout and avenues, how buildings stand round the centre, walkways to every door, budgets](docs/ANUM_MINE_HQ_CENTER_PLAZA.md)
- [ANUM MINE campus, live presence, workstation work and executive overview](docs/ANUM_MINE_PRESENCE_AND_CAMPUS.md)
- [Business gap register (Order 001)](docs/business/ANUM_BUSINESS_GAP_REGISTER.md)
- [Reuse decision register: outside projects and their licenses](docs/business/REUSE_DECISION_REGISTER.md)
- [P0 CI remediation: commerce tests, journey 403, Rust advisories](docs/business/P0_CI_REMEDIATION.md)
- [P1 acceptance tests for the Business section](docs/business/P1_ACCEPTANCE_TESTS.md)
- [Harvey parity: prospecting and outreach capabilities in ANUM](docs/business/HARVEY_PARITY.md)
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
- [Broker onboarding, statement reconciliation and custody safeguards (live off)](docs/finance-broker-onboarding.md)
- [SAR 500 stocks and crypto paper simulation](docs/finance-sar500-paper-integration.md)
- [Tyre planning](docs/tyre-planning.md)
- [Social media marketing](docs/marketing.md)
- [Email campaigns through Brevo (connection, drafts, approval-only sending, agent tools)](docs/brevo.md)
- [Customer billing: plans, subscriptions, invoices, payments, dunning and self-serve sign-up](docs/billing.md)
- [Business: the internal sales desk (catalog, leads, research, pipeline, proposals, projects, change orders, invoices; sending off)](docs/business.md)
- [Business: the customer portal (access links, offers, change orders, invoices, requests)](docs/business-portal.md)
- [Memory](docs/memory.md)
- [What ANUM learned: feedback, style, routines and improvement notes](docs/learning.md)
- [Knowledge base and favorites](docs/knowledge-base.md)
- [Reports](docs/reports.md)
- [Privacy: download and delete my data](docs/privacy.md)
- [Organisation export and deletion](docs/organisation-lifecycle.md)
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
  - Help, Agent controls: [English](docs/help/agent-controls.md), [Arabic](docs/help/agent-controls.ar.md)
  - Help, Business: [English](docs/help/business.md), [Arabic](docs/help/business.ar.md)
  - Help, Calendar: [English](docs/help/calendar.md), [Arabic](docs/help/calendar.ar.md)
  - Help, Executive desk: [English](docs/help/executive.md), [Arabic](docs/help/executive.ar.md)
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
  - Help, Office history: [English](docs/help/office-history.md), [Arabic](docs/help/office-history.ar.md)
  - Help, Office builder: [English](docs/help/office-builder.md), [Arabic](docs/help/office-builder.ar.md)
  - Help, Settings: [English](docs/help/settings.md), [Arabic](docs/help/settings.ar.md)
  - Help, Setup plan: [English](docs/help/setup.md), [Arabic](docs/help/setup.ar.md)
  - Help, ANUM Store: [English](docs/help/skills.md), [Arabic](docs/help/skills.ar.md)
  - Help, Contact opt-outs: [English](docs/help/suppressions.md), [Arabic](docs/help/suppressions.ar.md)
  - Help, Tasks: [English](docs/help/tasks.md), [Arabic](docs/help/tasks.ar.md)
  - Help, Team: [English](docs/help/team.md), [Arabic](docs/help/team.ar.md)
  - Help, Tyre planning: [English](docs/help/tyre.md), [Arabic](docs/help/tyre.ar.md)
  - Help, ANUM MINE headquarters: [English](docs/help/office.md), [Arabic](docs/help/office.ar.md)
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
- [Agent controls: emergency stop and per-agent cost caps](docs/agent-controls.md)
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
- [Integration and release checklist](docs/INTEGRATION_AND_RELEASE.md)
- [Production plan](docs/production-plan.md)
- [Pending work and owner steps (handoff)](docs/pending-work.md)
- [Full skills audit (October 2026)](docs/audits/2026-10-12-full-skills-audit.md)
- [Figma mobile design system](docs/figma-mobile-design.md)
- [ANUM 360 redesign (owner mockups)](docs/design/anum360.md)
- [Visual identity: graphite and jade](docs/design/visual-identity.md)
- [Repository structure](docs/repository-structure.md)
- [Scaling](docs/scaling.md)
- [Governance and scale](docs/governance-and-scale.md)
- [Setup plan: apply a business blueprint](docs/setup-plan.md)
- [ADR-0001: Foundation](docs/decisions/ADR-0001-foundation.md)
- [ADR-0002: Custom agent runtime](docs/decisions/ADR-0002-custom-agent-runtime.md)
- [ADR-0003: Office projection and live agent meetings](docs/decisions/ADR-0003-office-projection-and-live-meetings.md)

## Development Rule

Code should follow the contracts, boundaries, tenant model, and security expectations described in the documentation. Prototype shortcuts are acceptable only when they are isolated, named clearly, and replaced before production use.

- [ANUM MINE virtual headquarters master plan](docs/ANUM_MINE_VIRTUAL_HEADQUARTERS_MASTER_PLAN.md)
- [ANUM MINE visual reference and build plan (owner-approved concept renders, data-truth rules, workstreams)](docs/ANUM_MINE_VISUAL_REFERENCE.md)
- [ANUM MINE persistent live meeting management API](docs/ANUM_MINE_LIVE_MEETING_MANAGEMENT_API.md)
- [ANUM MINE live meeting room UI and room API](docs/ANUM_MINE_LIVE_MEETING_ROOM_UI.md)
- [ANUM MINE live meetings: monthly budget, owner-only transcripts, turn audit](docs/ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md)
- [ANUM MINE live meetings: summary, model-drafted decisions, decision to work links, JSON record](docs/ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md)
- [ANUM MINE live agent meetings foundation](docs/ANUM_MINE_LIVE_AGENT_MEETING_FOUNDATION.md)

- [Sales dashboard](docs/sales-dashboard.md)
- [Sales Flutter](docs/sales-flutter.md)
