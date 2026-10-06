# Multi-Tenancy

ANUM should support multiple tenants from the first real implementation. A tenant represents a trust boundary. Workspaces, users, agents, memories, integrations, objects, approvals, events, and audit logs belong to a tenant unless explicitly marked as global system metadata.

## Tenant Model

A tenant contains workspaces. A user may belong to more than one tenant with different roles. Agents operate inside a tenant and usually inside a workspace. Integration credentials are granted to a tenant, workspace, user, or agent depending on the integration's risk and expected usage.

## Isolation Strategy

The default database pattern should be shared PostgreSQL tables with `tenant_id` columns and mandatory row-level security. Application code must set the tenant execution context for every request and worker job. Background workflows must carry tenant identity in their payloads and validate it when resumed.

The application role never bypasses RLS. Two narrow roles cross tenants:

- The event outbox relay reads unpublished event rows and updates their publication columns ([Events](events.md#relay-role-and-rls)).
- Maintenance jobs only discover which tenants have work ([Maintenance role](#maintenance-role)).

Their RLS policies and grants cover nothing else.

## Authorization Layers

- Identity proves who the actor is.
- Membership proves which tenant and workspace the actor can access.
- Role and permission policy proves what action is allowed.
- RLS prevents data access outside the active tenant context.
- Tool policy limits what agents can do even after a user starts a task.

Workspace owners add people with invitations and manage roles and deactivation; the last active owner is protected ([Identity](identity.md#invitations-and-membership-management)). In `ANUM_AUTH_MODE=oidc` the tenant comes from the token's IdP-managed `tenant_id` claim, the workspace from `x-workspace-id` (or the token's default `workspace_id`), and the persisted membership supplies the role. See [Identity and sign-in](identity.md#tenant-and-workspace-resolution).

## Control-Plane Stores

With `ANUM_REPOSITORY_BACKEND=postgresql` every control-plane store is a tenant table under forced RLS (migration `0008_control_plane_stores`); with `memory` each keeps an in-process implementation for local runs and tests. A store is opened per unit of work with the tenant and workspace session context set (`anum_api.scoped_store.open_scoped_store`), and writes for a tenant or workspace that has not been onboarded answer `409`.

| Scope | Tables | RLS predicate |
|---|---|---|
| Tenant | `skill_versions`, `policy_packs`, `role_templates`, `approval_rules`, `memory_governance`, `marketplace_packages`, `routing_targets` | `tenant_id` |
| Workspace | `skill_installations`, `marketplace_installs`, `integration_configurations`, `workspace_files`, `notification_preferences` (per user), `automation_workflows`, `automation_schedules`, `automation_runs` (migration `0009_voice_automation`) | `tenant_id` and `workspace_id` |
| User | `voice_sessions`, `voice_transcript_segments` (migration `0009_voice_automation`) | `tenant_id`, `workspace_id` and `user_id` |

User-level rows need `anum.user_id` in the session context as well (`set_tenant_context(..., user_id=...)`, or `open_scoped_store(..., user_scoped=True)`). Without it they are invisible, even to other users of the same workspace. Tenant-level settings are shared by all of a tenant's workspaces and invisible to other tenants. Governance changes write an `audit_records` row for the acting workspace in the same transaction. The marketplace catalog belongs to the tenant that published the package; an install in any of the tenant's workspaces blocks deleting the package through a foreign key, which PostgreSQL checks without RLS filtering.

## Maintenance Role

Some jobs must find work in every tenant:

- the automation scheduler (due schedules, [Automation](automation.md#scheduler));
- the voice transcript purge (expired 30-day transcripts, [Voice](voice.md#storage-and-retention));
- `python -m anum_api.rotate_secrets` (workspaces with an encrypted provider key, [Runbooks](runbooks.md#rotating-anum_secrets_key)).

They follow one pattern (`anum_api/maintenance.py`, migration `0009_voice_automation`):

1. **Discover.** A read-only transaction starts with `SET LOCAL ROLE anum_maintenance`, runs one query and always rolls back. The role is `NOLOGIN` and has no write privilege on any table. It can read only the columns below, and only the rows its own policies admit:

   | Table | Columns granted | Policy (`for select to anum_maintenance`) |
   |---|---|---|
   | `automation_schedules` | `id`, `tenant_id`, `workspace_id`, `next_run_at` | `enabled and next_run_at <= now()` |
   | `voice_sessions` | `id`, `tenant_id`, `workspace_id`, `user_id`, `expires_at` | `expires_at <= now() and transcript_purged_at is null` |
   | `workspace_model_configs` | `tenant_id`, `workspace_id` | `api_key_ciphertext is not null` |

2. **Act.** Each discovered scope gets its own transaction as the application role, with that tenant, workspace (and user) set as RLS context. Every content read (transcript text, workflow steps, ciphertexts) and every write is checked by the normal tenant-isolation policies. Audit records are written there too.

Why not a `SECURITY DEFINER` function: it runs as the table owner, and `FORCE ROW LEVEL SECURITY` applies to the owner as well. It would only work with an owner that bypasses RLS, which is what this design avoids.

Deployment:

- Grant `anum_maintenance` to the API login if the scheduler is enabled.
- Grant it to the operator login that runs the purge and rotation commands.
- Like `anum_outbox_relay`, the role is cluster-wide and survives a downgrade.

`tests/test_postgres_automation.py`, `tests/test_postgres_voice.py` and `tests/test_postgres_rotate_secrets.py` check that the role sees only those rows and columns and cannot write.

## Cross-Tenant Data

Cross-tenant analytics should use aggregated, non-sensitive data only. Product telemetry must avoid raw prompts, retrieved memory, tool payloads, secrets, and file contents unless explicitly configured for debugging in a controlled environment.

## Tenant Lifecycle

The platform should define tenant creation, suspension, export, deletion, and recovery flows. Deletion must cover relational data, vector records, object storage, cached state, scheduled workflows, and integration tokens.

## Now

Start with one shared database, strict `tenant_id` discipline, RLS, workspace membership, and tenant-scoped audit logs.

## Later

Add enterprise tenant isolation options, dedicated databases, regional residency, tenant-level encryption controls, quota enforcement, and admin policy simulation.