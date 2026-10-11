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
| `GET /audit-records`, `GET /audit-records/actions`, `GET /audit-records/{id}` (the [audit log](#audit-log)) | workspace owner (`audit:read`), or `org_admin` with an active membership in the workspace (any role) |
| `GET /audit/export` (the acting workspace's audit trail, filtered) | workspace owner (`audit:export`), or `org_admin` with an active membership in the workspace |
| `PUT /approval-policy` (the workspace's two-person rule and medium-risk approval) | workspace owner (`policy:manage`); workspace-scoped, unchanged |

A refused change answers `403` with `Organization administrator role required: this setting applies to every workspace in the organization, so owning a workspace is not enough. Ask the administrator of your ANUM sign-in (Keycloak) to assign you the realm role org_admin (on a self-hosted server: infra/oracle/keycloak-2fa.sh grant-org-admin <your username>), then sign out and sign in again`, writes nothing and records no audit row. `GET /organization/governance` also returns `can_manage_organization_policy`, the API's answer for the caller, so clients do not guess from the workspace role; the web Governance and Enterprise views and the Flutter Organization screen use it to show policy packs, routing targets and the catalog read-only with an explanation. `GET /model-budgets` returns `can_manage_tenant_budget` the same way for the organization budget card (web Budgets view, Flutter Model budgets screen).

Where `org_admin` comes from:

- `ANUM_AUTH_MODE=oidc`: the Keycloak realm role `org_admin` in the access token (`realm_access.roles` or `roles`). Unlike the workspace realm roles, which the persisted membership replaces, it is kept next to the membership role, and it applies only to the token's IdP-managed `tenant_id`. Grant it in Keycloak to the organization's administrators ([Identity: Organization administrators](identity.md#organization-administrators)). The caller still needs an active membership in the request's workspace. Nobody holds it after an upgrade: Keycloak imports the realm only on its first start, so an existing realm first needs the role created and assigned (Oracle: `keycloak-2fa.sh grant-org-admin <username>`; Render: the admin console). Until then every change listed above answers `403`, the deployment's owner included; the web Governance view shows the same instructions in its read-only notice.
- `ANUM_AUTH_MODE=headers` (local and test only): `x-user-roles` containing `org_admin`, for example `owner,org_admin`. Local development sessions (`/api/v1/auth/local/*`) carry it, like the realm's DEV-ONLY `dev` user.

Organization administrators also read and export the [audit log](#audit-log) of every workspace they are a member of, and may rename, archive, restore and create workspaces ([Identity: Workspace lifecycle](identity.md#workspace-lifecycle)).

Not built yet: managing organization administrators inside ANUM (they are managed in the identity provider). No client publishes skill versions or marketplace packages yet (the Flutter repository can call the catalog routes, but no screen does); those routes are API-only for organization administrators. `routing:manage` is still granted to workspace owners but no route checks it any more.

## Persistence

With `ANUM_REPOSITORY_BACKEND=postgresql` policy packs, role templates, approval rules and memory governance are tenant-level tables, the marketplace catalog (`marketplace_packages`) and routing targets are per tenant, and marketplace installs are per workspace, all under RLS ([Multi-tenancy](multi-tenancy.md#control-plane-stores)). Every governance write records an `audit_records` row (append-only: RLS allows select and insert only) in the same transaction, so a rejected write leaves no audit record; `GET /audit/export` returns the acting workspace's audit trail, which also includes membership, workspace and privacy changes. With `ANUM_REPOSITORY_BACKEND=memory` governance audit records go to the same in-process store as every other audit record, so the log and the export show them together there too. Packages published with `PUT /marketplace/packages/{id}` (organization administrators) are visible only to the publishing tenant.

## Audit log

Every audited change in a workspace (members, invitations, workspace lifecycle, policy packs and other governance settings, model budgets, approval policy, privacy export and deletion, developer mode, and the audit export itself) is an append-only row in `audit_records`: id, actor (user id), action, target, outcome, correlation id, time and metadata (ids, counts and settings; keys that look like secrets are stored as `[REDACTED]`). RLS allows select and insert only, for the request's tenant and workspace. Implemented in `services/api/anum_api/audit_log.py`.

| Route | Effect |
| --- | --- |
| `GET /api/v1/audit-records?actor=&action=&target=&outcome=&correlation_id=&since=&until=&q=&limit=&cursor=` | One page, newest first (`created_at`, then id, descending): `{items, next_cursor, retention}`. `action` matches exactly or, ending in `*`, as a prefix (`workspace_member.*`; LIKE wildcards are literal). `since` is inclusive and `until` exclusive (ISO 8601, UTC when no offset; `since >= until` answers `422`). `q` matches actor, action or target as a case-insensitive substring. `limit` 1 to 200 (50 default). `next_cursor` is an opaque keyset cursor; an invalid one answers `400`. |
| `GET /api/v1/audit-records/actions` | The distinct actions recorded in this workspace (at most 500), for the action filter. |
| `GET /api/v1/audit-records/{id}` | One record of this workspace, else `404` (another workspace's record is not found). |
| `GET /api/v1/audit/export?format=json\|csv&<same filters>` | The filtered range, newest first, as a JSON array or CSV (`id, created_at, actor, action, target, outcome, correlation_id, tenant_id, workspace_id, metadata` with metadata as JSON). File name `audit-<workspace>-<UTC time>.<format>` in `Content-Disposition`. |

- **Who.** Workspace owners (`audit:read`, `audit:export`) and organization administrators (`org_admin`) with an active membership in the workspace, whatever their role there. Everyone else gets `403` with "Audit log access requires the owner role in this workspace or the organization administrator role. Ask a workspace owner to show you the log or change your role." Reads always run in the caller's tenant and workspace RLS scope, so the log and the export never include another workspace's records.
- **Export.** Recorded as `audit.export` (format and filters) in the request's transaction before the body is sent. The body is streamed in batches of 500, each read through its own short read-only transaction in the caller's RLS scope (never the request's session, which is closed by then), so a large log is not held in memory; the range is pinned to the request time, so the export does not include itself or later records. CSV cells that start with `=`, `+`, `-`, `@`, tab or carriage return are prefixed with `'` so spreadsheets do not run them as formulas. The file is produced synchronously; the asynchronous export with digest and expiring download described in [Phase 4](#phase-4-control-plane) is not built.
- **Retention.** Audit records are kept for as long as the workspace exists, including after it is [archived](identity.md#workspace-lifecycle); no retention job purges them and nobody can edit or delete them, owners included. The API returns this text as `retention` and both clients show it under the log. A configurable audit retention period and legal hold are not built.
- **Indexes.** `(tenant_id, workspace_id, created_at)` plus, from migration `0028_workspace_admin` (built concurrently), `(tenant_id, workspace_id, actor, created_at)` and `(tenant_id, workspace_id, action, created_at)` for the actor and action filters.
- **Web.** Audit log in the navigation (`apps/web/src/AuditLogView.tsx`, logic in `src/lib/audit.ts`): search, actor, action (the recorded actions plus "any" per family), target, outcome and a From/To day range (local days; To is inclusive, sent as the next day's start), Apply and Reset; a table (stacked rows below 760px) with "Load older records" (keyset); each row's time opens a detail drawer (dialog with focus moved in, Tab kept inside, Escape and Close return focus to the row) with every field and the metadata, and "Only this actor", "Only this target" and "Same correlation id" to narrow the log. Export CSV and Export JSON download the filtered range. Loading, error with Try again, permission denied (who can read it, and the API's words), empty (with Reset filters or, for an empty log, a way to Members) and the retention note are all shown.
- **Flutter.** Settings › Workspace administration › Audit log (`apps/mobile/lib/features/admin/audit_log_screen.dart`): read-only on the phone. Search, action and a date range, "Load older records", a detail sheet per record, and the same states. Filtered export is web-only; the Organization screen exports the whole log (JSON or CSV).
- Tests: `services/api/tests/test_audit_log.py` (paging, filters, detail scoping, permissions, export filters and its own audit record, CSV formula neutralising, more than one export batch), `test_postgres_audit_log.py` (the same under RLS as the app role, LIKE wildcards, batches through their own sessions), `apps/web/test/audit.test.ts`, `apps/web/e2e/audit.spec.ts` (paging, keyboard on the drawer, filters sent to the API, export file name, empty, error and denied states, layout at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844) and `apps/mobile/test/audit_admin_test.dart` (paging, filters, detail, denied and empty states, 320 to 1440 wide at 200% text).

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

The web app's Governance view lists every policy pack version with its status, the role templates and the organization approval rules to every member. Organization administrators (`can_manage_organization_policy`) can add the baseline pack, activate or archive a version, create role templates and add approval rules; everyone else, a workspace owner included, sees the same lists read-only with a notice that these settings apply to every workspace, so only an organization administrator can change them, and whom to ask. A `403` on a change is shown in place with the API's message. Tests: `apps/web/test/governance.test.ts` (`organizationPolicyAccess`) and `apps/web/e2e/governance.spec.ts`. The Enterprise view reports regions from the configured routing targets only: with zero or one serving region it says so ("Single region", failover "Not applicable") instead of a pending failover. Routing targets are tenant-wide, so only organization administrators create and edit them (`PUT /routing/targets/{id}`); the view loads `can_manage_organization_policy` from `GET /organization/governance` and, for everyone else, hides the add and edit controls and says why (read only: only an organization administrator can change routing targets or publish to the marketplace catalog, and how to get the role). If that capability check fails the page still shows the targets and catalog, read-only, with the error. Installing a catalog package into this workspace stays a workspace-owner action, so the Install button shows only for owners. The API has no delete, so a target leaves routing by being set `offline`. Each target card gives its name, region, provider, model and cost the card's full text width, with the status and edit control on their own line below, so the cards stay readable at 1024px and every other responsive-gate size. Tests: `apps/web/test/governance.test.ts` (`tenantWriteAccess`) and `apps/web/e2e/governance.spec.ts` (including the card layout at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844). The Integrations view gives owners a per-workspace configuration panel (enabled flag and endpoint) over `GET`/`PUT /integrations/{id}/configuration`. Health checks honor only the enabled flag: they always probe, and report, the operator-configured endpoint from Settings, never a workspace-saved one (SSRF).
