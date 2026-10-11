# Governance and Scale

This document is the implementation contract for roadmap Phases 4 and 5. A capability is complete only when its API and persistence are tenant-isolated, authorization and negative-path tests pass, audit events are emitted, the operator workflow is documented, and the production dependency has been exercised.

## Phase 4 Control Plane

Organization administration owns tenant settings, workspace lifecycle, membership, and versioned role templates. Administrative writes require explicit permissions and immutable audit records; disabling the last tenant owner must be rejected.

Policy packs are immutable once published. A new change creates a new version. Every evaluation records the exact pack version, matched rules, decision, principal, resource, and correlation ID. Deny takes precedence over approval and allow; simulation uses the same evaluator but cannot execute an action.

Audit exports are asynchronous, tenant-scoped, time-bounded artifacts. Completed exports include record count and SHA-256 digest, use short-lived download authorization, and generate request, completion, download, and expiry audit events. Export workers must stream records rather than load a tenant history into memory.

Governance policies cover memory, audit events, transcripts, and task artifacts. Retention deletion is idempotent, legal hold takes precedence, and residency restrictions constrain both primary storage and exports. Organization approval rules may increase risk or require additional approvers, but cannot weaken a platform-level deny.

Enforcement today: the agent runtime's `ToolPolicy` consults the tenant's enabled approval rules and the rules of its active policy pack versions on every evaluation (planning, execution, recovery); a `deny` blocks, approval rules and `require_approval` pause the call for approval at any risk level, and `allow` changes nothing. The approve route enforces a matching rule's `required_roles` and `minimum_approvers`. Patterns, conditions and the evaluation order are in [Approvals and risk](approvals-and-risk.md#organization-approval-rules-and-policy-packs). Policy pack simulation and a separate evaluation log are not built; the run's `tool_proposal` step records the matched rules and pack versions.

## Phase 5 Control Plane

Marketplace packages use immutable semantic versions, content digests, publisher signatures, requested scopes, review state, and revocation. Installation requires an administrator to grant a subset of requested scopes. Revocation prevents new execution without erasing historical evidence.

Regional placement records a tenant home region, allowed regions, residency mode, and failover policy. Requests must carry tenant and correlation context across regions. Automatic failover is permitted only to an allowed region and requires measured recovery-point and recovery-time objectives.

Model routing policies constrain provider, model, region, data classification, latency, and per-run cost. Each routing decision is auditable and records reason codes. Fallback may not bypass policy, residency, budget, or model allowlists.

Enterprise operations include tenant/workspace budgets, throttling, service-level objectives, regional health, incident records, queue age and dead-letter visibility, controlled replay, and break-glass actions with expiry and mandatory audit review.

## Who may change what

Governance settings are tenant-level: one policy pack, role template, approval rule, memory governance setting, organization model budget, routing target, marketplace catalog package or published skill version binds (or is offered to) every workspace of the organization. Changing them therefore needs the tenant-level permission `organization_policy:manage` (`Permission.ORG_POLICY_MANAGE`), which only the organization role `org_admin` grants (`anum_api/authorization.py`, `OrganizationRole`). No workspace role grants it, not even `owner`: owning one workspace is not enough to change policy for all the others.

| Route | Who |
|---|---|
| `POST /policy-packs`, `POST /policy-packs/{id}/activate`, `POST /policy-packs/{id}/archive` | `org_admin` with an active membership in the request's workspace (any role) |
| `POST /role-templates`, `POST /organization/approval-rules`, `PUT /organization/memory-governance` | same |
| `PUT /model-budgets/tenant` (the organization's monthly model budget) | same |
| `PUT /routing/targets/{id}` (tenant routing targets) | same |
| `PUT /marketplace/packages/{id}`, `DELETE /marketplace/packages/{id}` (the tenant's marketplace catalog) | same |
| `POST /skills/versions` (publishing a tenant-owned skill version) | same |
| `PUT /model-budgets/workspace`, `GET /model-budgets` | workspace owner (`organization:manage`); workspace-scoped, unchanged |
| `POST`/`DELETE /marketplace/packages/{id}/install` | workspace owner (`marketplace:manage`); installs into this workspace only |
| `POST /skills/installations`, `PATCH`/`DELETE /skills/installations/{skill_id}` | workspace owner (`policy:manage`); installs into this workspace only |
| `GET /routing/targets`, `POST /routing/decisions`, `GET /marketplace/packages`, `GET /marketplace/installs`, `GET /skills/versions` | any member (unchanged) |
| `GET /organization/governance`, `GET /policy-packs`, `GET /role-templates`, `GET /organization/approval-rules`, `GET /organization/memory-governance` | any member (`organization:read`) |
| `GET /audit/export` (the acting workspace's audit trail) | workspace owner (`audit:export`) |
| `PUT /approval-policy` (the workspace's two-person rule and medium-risk approval) | workspace owner (`policy:manage`); workspace-scoped, unchanged |

A refused change answers `403` with `Organization administrator role required: this setting applies to every workspace in the organization, so owning a workspace is not enough. Ask the administrator of your ANUM sign-in (Keycloak) to assign you the realm role org_admin (on a self-hosted server: infra/oracle/keycloak-2fa.sh grant-org-admin <your username>), then sign out and sign in again`, writes nothing and records no audit row. `GET /organization/governance` also returns `can_manage_organization_policy`, the API's answer for the caller, so clients do not guess from the workspace role; the web Governance and Enterprise views and the Flutter Organization screen use it to show policy packs, routing targets and the catalog read-only with an explanation. `GET /model-budgets` returns `can_manage_tenant_budget` the same way for the organization budget card (web Budgets view, Flutter Model budgets screen).

Where `org_admin` comes from:

- `ANUM_AUTH_MODE=oidc`: the Keycloak realm role `org_admin` in the access token (`realm_access.roles` or `roles`). Unlike the workspace realm roles, which the persisted membership replaces, it is kept next to the membership role, and it applies only to the token's IdP-managed `tenant_id`. Grant it in Keycloak to the organization's administrators ([Identity: Organization administrators](identity.md#organization-administrators)). The caller still needs an active membership in the request's workspace. Nobody holds it after an upgrade: Keycloak imports the realm only on its first start, so an existing realm first needs the role created and assigned (Oracle: `keycloak-2fa.sh grant-org-admin <username>`; Render: the admin console). Until then every change listed above answers `403`, the deployment's owner included; the web Governance view shows the same instructions in its read-only notice.
- `ANUM_AUTH_MODE=headers` (local and test only): `x-user-roles` containing `org_admin`, for example `owner,org_admin`. Local development sessions (`/api/v1/auth/local/*`) carry it, like the realm's DEV-ONLY `dev` user.

Not built yet: managing organization administrators inside ANUM (they are managed in the identity provider). No client publishes skill versions or marketplace packages yet (the Flutter repository can call the catalog routes, but no screen does); those routes are API-only for organization administrators. `routing:manage` is still granted to workspace owners but no route checks it any more.

## Persistence

With `ANUM_REPOSITORY_BACKEND=postgresql` policy packs, role templates, approval rules and memory governance are tenant-level tables, the marketplace catalog (`marketplace_packages`) and routing targets are per tenant, and marketplace installs are per workspace, all under RLS ([Multi-tenancy](multi-tenancy.md#control-plane-stores)). Every governance write records an `audit_records` row (append-only: RLS allows select and insert only) in the same transaction, so a rejected write leaves no audit record; `GET /audit/export` returns the acting workspace's audit trail, which also includes membership changes. Packages published with `PUT /marketplace/packages/{id}` (organization administrators) are visible only to the publishing tenant.

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

## Web console

The web app's Governance view lists every policy pack version with its status, the role templates and the organization approval rules to every member. Organization administrators (`can_manage_organization_policy`) can add the baseline pack, activate or archive a version, create role templates and add approval rules; everyone else, a workspace owner included, sees the same lists read-only with a notice that these settings apply to every workspace, so only an organization administrator can change them, and whom to ask. A `403` on a change is shown in place with the API's message. Tests: `apps/web/test/governance.test.ts` (`organizationPolicyAccess`) and `apps/web/e2e/governance.spec.ts`. The Enterprise view reports regions from the configured routing targets only: with zero or one serving region it says so ("Single region", failover "Not applicable") instead of a pending failover. Routing targets are tenant-wide, so only organization administrators create and edit them (`PUT /routing/targets/{id}`); the view loads `can_manage_organization_policy` from `GET /organization/governance` and, for everyone else, hides the add and edit controls and says why (read only: only an organization administrator can change routing targets or publish to the marketplace catalog, and how to get the role). If that capability check fails the page still shows the targets and catalog, read-only, with the error. Installing a catalog package into this workspace stays a workspace-owner action, so the Install button shows only for owners. The API has no delete, so a target leaves routing by being set `offline`. Tests: `apps/web/test/governance.test.ts` (`tenantWriteAccess`) and `apps/web/e2e/governance.spec.ts`. The Integrations view gives owners a per-workspace configuration panel (enabled flag and endpoint) over `GET`/`PUT /integrations/{id}/configuration`. Health checks honor only the enabled flag: they always probe, and report, the operator-configured endpoint from Settings, never a workspace-saved one (SSRF).
