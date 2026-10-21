# Live Agent Meeting Foundation (ANUM MINE F06/F07)

## Purpose and status
This independent workstream introduces a typed, testable domain for **new agent-run meetings**. It does not modify the existing `meetings.py` flow for imported Zoom/Teams/transcript recordings. Existing imported meetings remain intact.

**Current delivery:** immutable Pydantic meeting models, transition rules, participant scope checks, ordered turns, idempotent request-key semantics, and token/cost/time/turn/participant limits. Unit tests cover boundary and failure scenarios. The deterministic turn coordinator now selects only runtime-confirmed eligible agents, rotates speakers based on persisted transcript turns, and refuses execution on emergency stop, pending approvals, missing availability or insufficient reserved budgets.

**Not yet live:** there are no HTTP routes, database migration, SSE/outbox, LLM calls or UI meeting-room connections in this PR. It is a safe initial foundation, not a production-complete multi-agent conference system.

## State machine
- scheduled -> active or cancelled
- active -> paused, finished, cancelled or failed
- paused -> active, cancelled or finished
- finished, cancelled, failed -> terminal

A scheduled meeting must include at least one participant before it can start. Only an active meeting accepts turns. An authorized backend orchestrator must control all transitions; a client-provided list of agents is not permission evidence.

## Invariants
- Each participant must have the same tenant and workspace as the meeting. The service layer must independently check that every selected agent is visible, enabled and permitted to attend.
- Each turn has a consecutive sequence, persisted timestamp, participant identity, role, opaque idempotency key and estimated cost/token accounting.
- A repeat of the same request key and identical content is a no-op; reuse with differing content is rejected.
- Total token use, estimated USD cost, number of turns and wall-clock duration cannot exceed declared budgets. These constraints must also be enforced by transactional persistence with unique indexes and by the existing model budget gateway.
- No agent activity is fabricated. The domain never synthesizes dialogue, never executes tools, and never sends email, money or messages.
- Every change creates a new immutable object. Repository writes must use optimistic revision checks and a transaction, then emit a committed outbox event.

## Dependency and migration coordination
Do not name the new migration `0097` until the backend Office Registry workstream is merged/rebased. That branch already contains migration `0097_office_spaces`. Review the latest actual Alembic head first, then allocate a unique chained migration with the existing revision pattern. Separate tables are required: `agent_meeting_sessions`, `agent_meeting_participants`, `agent_meeting_messages`, `agent_meeting_decisions`, `agent_meeting_actions` and transactional outbox records (reuse an existing outbox if appropriate). All records must use forced PostgreSQL RLS and tenant/workspace composite foreign keys, and register for lifecycle export/purge.

## Next vertical increments
1. Transactional session repository with unique `(tenant, workspace, meeting, request_key)` and `(tenant, workspace, meeting, sequence)` constraints; audit and outbox in one commit.
2. Authenticated FastAPI create/start/pause/cancel/finish APIs with owner/participant permissions, denial of cross-tenant selection, budget limits and idempotent create.
3. The pure coordinator policy is implemented, but it is not wired to a provider. Connect it to the existing model gateway, tool governance and agent runtime. Persist a confirmed turn **before** emitting it; never bypass approvals or leak secrets.
4. Live room UI and SSE snapshot/delta replay with strict authorization; transcript export/retention/redaction.
5. PostgreSQL + multi-tenant integration, crash recovery, duplicate-event, provider-failure, budget exhaustion and large-room tests.
6. Follow-up owner verification for deployment limits, retention periods, and provider spend caps.

## Acceptance before marking F06/F07 complete
- Existing imported meeting APIs pass unchanged.
- PostgreSQL app-role isolation tests across two organizations and workspaces pass.
- Server enforces participant identity, limits, ownership and idempotency under concurrent requests.
- Active room messages are always persisted server events, not demo text.
- Queue/crash/reconnect paths are tested, model/tool budgets hold, and feature-flagged staging passes end-to-end tests.
- Owner approves any merge, deployment or paid agent execution.
