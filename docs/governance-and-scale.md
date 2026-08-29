# Governance and Scale

This document is the implementation contract for roadmap Phases 4 and 5. A capability is complete only when its API and persistence are tenant-isolated, authorization and negative-path tests pass, audit events are emitted, the operator workflow is documented, and the production dependency has been exercised.

## Phase 4 Control Plane

Organization administration owns tenant settings, workspace lifecycle, membership, and versioned role templates. Administrative writes require explicit permissions and immutable audit records; disabling the last tenant owner must be rejected.

Policy packs are immutable once published. A new change creates a new version. Every evaluation records the exact pack version, matched rules, decision, principal, resource, and correlation ID. Deny takes precedence over approval and allow; simulation uses the same evaluator but cannot execute an action.

Audit exports are asynchronous, tenant-scoped, time-bounded artifacts. Completed exports include record count and SHA-256 digest, use short-lived download authorization, and generate request, completion, download, and expiry audit events. Export workers must stream records rather than load a tenant history into memory.

Governance policies cover memory, audit events, transcripts, and task artifacts. Retention deletion is idempotent, legal hold takes precedence, and residency restrictions constrain both primary storage and exports. Organization approval rules may increase risk or require additional approvers, but cannot weaken a platform-level deny.

## Phase 5 Control Plane

Marketplace packages use immutable semantic versions, content digests, publisher signatures, requested scopes, review state, and revocation. Installation requires an administrator to grant a subset of requested scopes. Revocation prevents new execution without erasing historical evidence.

Regional placement records a tenant home region, allowed regions, residency mode, and failover policy. Requests must carry tenant and correlation context across regions. Automatic failover is permitted only to an allowed region and requires measured recovery-point and recovery-time objectives.

Model routing policies constrain provider, model, region, data classification, latency, and per-run cost. Each routing decision is auditable and records reason codes. Fallback may not bypass policy, residency, budget, or model allowlists.

Enterprise operations include tenant/workspace budgets, throttling, service-level objectives, regional health, incident records, queue age and dead-letter visibility, controlled replay, and break-glass actions with expiry and mandatory audit review.

## Acceptance Matrix

| Capability | Source acceptance | Production acceptance |
| --- | --- | --- |
| Organization administration | Tenant-isolated CRUD, role-template versioning, last-owner protection, audit tests | OIDC group/role synchronization exercised |
| Policy packs | Publish/version/simulate/evaluate APIs, precedence tests, evaluation evidence | Load and adversarial policy testing |
| Audit export | Streaming worker, digest, expiry, scoped download, audit tests | Object-store lifecycle and large-tenant test |
| Governance | Retention/legal-hold/residency enforcement and deletion tests | Backup, restore, deletion, and legal-hold exercise |
| Marketplace | Signed manifest verification, scope grant, review/revoke lifecycle | Publisher trust process and sandbox test |
| Multi-region | Placement enforcement, idempotency, replication/failover telemetry | Two-region failover and recovery exercise |
| Advanced routing | Deterministic policy evaluation, constrained fallback, decision audit | Provider failure and cost/latency load test |
| Enterprise operations | Budgets, SLOs, incident and replay controls with authorization | Alerts, runbooks, on-call and disaster-recovery exercise |

## Shared Contracts

The TypeScript definitions in `packages/contracts/src/governance.ts` are transport-neutral control-plane contracts. Backend schemas and client types should preserve their field semantics. Secrets, access tokens, raw credentials, and provider keys must never appear in these resources or their audit payloads.

## External Readiness Gates

Production completion requires identity-provider administration, an object store with lifecycle policy, signing-key custody, at least two deployed regions, a replicated persistence design, provider credentials, production telemetry, alert delivery, and an approved incident/disaster-recovery process. Source code alone cannot satisfy these gates.
