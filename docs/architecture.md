# Architecture

ANUM is a modular monorepo centered on a Python/FastAPI backend core and a custom agent runtime. Client surfaces communicate with the backend through versioned REST APIs, realtime channels, and event-backed workflows. The backend owns tenant boundaries, authorization, memory, tools, approvals, model routing, and orchestration.

## System Layers

1. Client surfaces: React+TypeScript+Vite web, Tauri desktop, Kotlin Android, and future voice clients.
2. API edge: FastAPI services exposing REST, streaming, webhook, and internal admin endpoints.
3. Domain core: users, tenants, workspaces, agents, tasks, memories, skills, tools, approvals, automations, and audit logs.
4. Agent runtime: planning, execution, tool mediation, memory retrieval, model calls, risk checks, and resumable state.
5. Workflow and messaging: Temporal for durable workflows and NATS JetStream for domain events, realtime fanout, and integration signals.
6. Data plane: PostgreSQL with pgvector, Valkey, and S3-compatible object storage.
7. Operations: Docker, OpenTofu, GitHub Actions, OpenTelemetry, logs, metrics, traces, and alerts.

## Core Direction

FastAPI should be the first backend interface because it is productive, typed, and fits Python agent infrastructure. React+Vite should be the first UI because it is fast to iterate and can be reused inside Tauri. Tauri should be preferred over Electron for a smaller native shell. Kotlin should be used for Android so the mobile client has native reliability and permission control.

## Service Boundaries

The first implementation can be a modular monolith with internal packages for identity, authorization, tasks, agents, memory, tools, and integrations. Separate deployable services should be introduced only when operational pressure justifies them. The architecture should still publish clear contracts so later extraction is straightforward.

## Runtime Flow

A user request becomes a task. The task creates or resumes an agent run. The runtime loads tenant-scoped policy, memory, skills, and available tools. It calls the model gateway, executes approved tools, writes events, persists state, and streams updates to subscribed clients. Risky actions pause for approval before execution.

## Now vs Later

Now: one backend core, one web client, documented contracts, durable task state, RLS, basic events, model gateway abstraction, and approval gates.

Later: distributed service extraction, marketplace skills, local desktop context, advanced voice, organization policy engines, cross-region routing, and specialized agent pools.