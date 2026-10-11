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

Tenant-wide policy (policy packs, role templates, organization approval rules, memory governance, the organization model budget, routing targets, the marketplace catalog and skill version publishing) is a separate, tenant-level permission, `organization_policy:manage`, held only by the organization administrator role `org_admin`; a workspace owner does not have it ([Governance](governance-and-scale.md#who-may-change-what)).

Workspace owners add people with invitations and manage roles and deactivation; the last active owner is protected ([Identity](identity.md#invitations-and-membership-management)). In `ANUM_AUTH_MODE=oidc` the tenant comes from the token's IdP-managed `tenant_id` claim, the workspace from `x-workspace-id` (or the token's default `workspace_id`), and the persisted membership supplies the role. See [Identity and sign-in](identity.md#tenant-and-workspace-resolution).

## Control-Plane Stores

With `ANUM_REPOSITORY_BACKEND=postgresql` every control-plane store is a tenant table under forced RLS (migration `0008_control_plane_stores`); with `memory` each keeps an in-process implementation for local runs and tests. A store is opened per unit of work with the tenant and workspace session context set (`anum_api.scoped_store.open_scoped_store`), and writes for a tenant or workspace that has not been onboarded answer `409`.

| Scope | Tables | RLS predicate |
|---|---|---|
| Tenant | `skill_versions`, `policy_packs`, `role_templates`, `approval_rules`, `memory_governance`, `marketplace_packages`, `routing_targets` | `tenant_id` |
| Workspace | `skill_installations`, `marketplace_installs`, `integration_configurations`, `integration_health_checks`, `integration_credentials` (migration `0030_integration_health`; `anum_maintenance` sees only the scope columns of credentials, for key rotation), `workspace_files`, `notification_preferences` (per user), `automation_workflows`, `automation_schedules`, `automation_runs` (migration `0011_voice_automation`), `retrieval_sources`, `retrieval_chunks` (migration `0014_retrieval_index`, [Memory](memory.md#retrieval)) | `tenant_id` and `workspace_id` |
| User | `voice_sessions`, `voice_transcript_segments` (migration `0011_voice_automation`), `conversations`, `conversation_turns` (migration `0018_conversations`), `push_devices` (migration `0027_push_devices`, [Notifications](notifications.md#devices)), `learning_signals`, `learning_profiles`, `learning_preferences`, `learning_routines`, `learning_notes`, `learning_suggestions` (migration `0036_learning`, [What ANUM learned](learning.md)) | `tenant_id`, `workspace_id` and `user_id` |
| User, tenant-wide | `user_preferences` (migration `0025_user_preferences`, [Memory](memory.md#per-user-switch)) | `tenant_id` and `user_id` |
| Workspace, with private rows | `memories`, `retrieval_sources` (owner column and restrictive policy from migration `0021_memory_sources`, [Memory](memory.md#from-conversations)) | `tenant_id` and `workspace_id`; a row with an `owner_user_id` also needs `anum.user_id` = that owner |

User-level rows need `anum.user_id` in the session context as well (`set_tenant_context(..., user_id=...)`, or `open_scoped_store(..., user_scoped=True)`). Without it they are invisible, even to other users of the same workspace. Tenant-level settings are shared by all of a tenant's workspaces and invisible to other tenants. Governance changes write an `audit_records` row for the acting workspace in the same transaction. The marketplace catalog belongs to the tenant that published the package; an install in any of the tenant's workspaces blocks deleting the package through a foreign key, which PostgreSQL checks without RLS filtering.

## Tenants Table

`tenants` (id, name, status) is under forced RLS too (migration `0020_tenants_rls`): the policy `tenant_isolation_tenants` admits only the row whose `id` equals `anum.tenant_id`, for reads (`using`) and writes (`with check`). A request or job therefore sees its own tenant row and nothing else; without tenant context it sees no tenant at all. Creating a tenant (`POST /api/v1/tenants`, `PUT /api/v1/onboarding`) still works because those routes set the caller's tenant as context and insert exactly that id. No narrow role is granted anything on `tenants`: the outbox relay, the scheduler, the retention purge and key rotation find their work through other tables and never read it. Foreign keys to `tenants` are checked by PostgreSQL without row security, so every tenant table keeps its reference. Tests: `tests/test_postgres_rls.py` (`test_tenants_rls_*`: other tenants are invisible by listing and by id, updates and deletes of them match nothing, a tenant can create only its own row).

Merge note: the revision is chained after `0018_conversations`; if a `0019_*` revision lands first, re-chain `0020_tenants_rls` onto it and update `tests/test_migrations.py`.

## Maintenance Role

Some jobs must find work in every tenant:

- the automation scheduler (due schedules, [Automation](automation.md#scheduler));
- the voice transcript purge (expired 30-day transcripts, [Voice](voice.md#storage-and-retention));
- the retrieval index purge (index rows of expired memories, [Memory](memory.md#retention); migration `0015_retrieval_retention`);
- the conversation retention purge (conversations idle past `ANUM_CONVERSATION_RETENTION_DAYS`, [Conversations](conversations.md#retention); migration `0019_conversation_retention`);
- `python -m anum_api.rotate_secrets` (workspaces with an encrypted provider key, and developer projects with an encrypted token: `id`, `tenant_id` and `workspace_id` only, migration `0032_developer_projects`; [Runbooks](runbooks.md#rotating-anum_secrets_key)).
- the developer watcher (developer requests still in progress, refreshed so phone push can announce a ready pull request, [Notifications](notifications.md#developer-mode); migration `0027_push_devices`).

They follow one pattern (`anum_api/maintenance.py`, migration `0011_voice_automation`):

1. **Discover.** A read-only transaction starts with `SET LOCAL ROLE anum_maintenance`, runs one query and always rolls back. The role is `NOLOGIN` and has no write privilege on any table. It can read only the columns below, and only the rows its own policies admit:

   | Table | Columns granted | Policy (`for select to anum_maintenance`) |
   |---|---|---|
   | `automation_schedules` | `id`, `tenant_id`, `workspace_id`, `next_run_at` | `enabled and next_run_at <= now()` |
   | `voice_sessions` | `id`, `tenant_id`, `workspace_id`, `user_id`, `expires_at` | `expires_at <= now() and transcript_purged_at is null` |
   | `workspace_model_configs` | `tenant_id`, `workspace_id` | `api_key_ciphertext is not null` |
   | `retrieval_sources` (`0015`) | `tenant_id`, `workspace_id`, `source_type`, `source_id`, `source_expires_at` | `source_type = 'memory' and source_expires_at <= now()` |
   | `whatsapp_accounts` (`0031`) | `tenant_id`, `workspace_id`, `phone_number_id`, `verify_token_hash` | `true` (the WhatsApp webhook finds the workspace that owns a phone number id, and whether any workspace saved a verify token; never the encrypted token or app secret, [WhatsApp](whatsapp.md#webhook)) |
   | `tyre_connections` (`0037`) | `tenant_id`, `workspace_id` | `secret_ciphertext is not null` |
   | `conversations` (`0019`) | `id`, `tenant_id`, `workspace_id`, `user_id`, `updated_at` | `updated_at <= now() - interval '1 day'` (the purge then filters on the configured retention) |
   | `developer_requests` (`0026`) | `id`, `tenant_id`, `workspace_id`, `status` | `status in ('queued', 'coding', 'ci_running', 'ci_failed', 'waiting_approval')` |
   | `developer_projects` (`0031`) | `id`, `tenant_id`, `workspace_id` | `token_ciphertext is not null` |

2. **Act.** Each discovered scope gets its own transaction as the application role, with that tenant, workspace (and user) set as RLS context. Every content read (transcript text, workflow steps, ciphertexts) and every write is checked by the normal tenant-isolation policies. Audit records are written there too.

Why not a `SECURITY DEFINER` function: it runs as the table owner, and `FORCE ROW LEVEL SECURITY` applies to the owner as well. It would only work with an owner that bypasses RLS, which is what this design avoids.

Deployment:

- Grant `anum_maintenance` to the API login if the scheduler or the developer watcher (phone push with developer mode) runs, `WITH INHERIT FALSE, SET TRUE` as `infra/helm/bootstrap-database.sql` does. Its policies admit rows from every tenant; an inheriting grant would apply them to the API login's own queries too, so the RLS backstop would no longer keep those reads to one tenant.
- Grant it to the operator login that runs the purge and rotation commands.
- Like `anum_outbox_relay`, the role is cluster-wide and survives a downgrade.

`tests/test_postgres_automation.py`, `tests/test_postgres_voice.py`, `tests/test_postgres_rotate_secrets.py`, `tests/test_postgres_retrieval.py` and `tests/test_postgres_conversations.py` check that the role sees only those rows and columns and cannot write; the last three also check that a grant `WITH INHERIT FALSE` keeps the application role's own reads in one workspace while an inheriting grant would not.

## Membership Directory Role

`GET /api/v1/me/workspace-memberships` ([Identity](identity.md#my-workspaces)) lists the caller's own active memberships across the workspaces of their tenant. The tenant-isolation policies admit one workspace at a time, so the application role cannot do that read. Migration `0013_approvers_and_directory` creates `anum_membership_reader` for it, following the maintenance role's pattern:

- `NOLOGIN`, no `BYPASSRLS`, no write privilege, no `SECURITY DEFINER` function. Column grants: `tenant_id, workspace_id, user_id, role, active` on `workspace_memberships` and `tenant_id, id, name` on `workspaces`, plus `archived_at` on `workspaces` from migration `0028_workspace_admin` (to mark archived workspaces in the list).
- Policies `for select to anum_membership_reader`: memberships with `tenant_id = anum.tenant_id`, `user_id = anum.user_id` and `active`; workspaces of that tenant in which that user has an active membership. Without either setting it sees nothing.
- The API (`anum_api/membership_directory.py`) uses it in a separate transaction that starts with `SET LOCAL ROLE anum_membership_reader`, sets the tenant and user from the authenticated identity (no workspace), runs one query and always rolls back.
- Grant it to the API login **`WITH INHERIT FALSE, SET TRUE`** (`infra/helm/bootstrap-database.sql`). PostgreSQL applies a policy `TO` a role to every member that inherits that role's privileges, so an inheriting grant would let the application role's ordinary queries see the caller's memberships in other workspaces too. The same applies to any future narrow role whose policies widen visibility.
- Like the other NOLOGIN roles it is cluster-wide and survives a downgrade without privileges.

`tests/test_postgres_membership_directory.py` checks the rows, columns, writes and the `INHERIT FALSE` behaviour.

## Cross-Tenant Data

Cross-tenant analytics should use aggregated, non-sensitive data only. Product telemetry must avoid raw prompts, retrieved memory, tool payloads, secrets, and file contents unless explicitly configured for debugging in a controlled environment.

## Tenant Lifecycle

The platform should define tenant creation, suspension, export, deletion, and recovery flows. Deletion must cover relational data, vector records, object storage, cached state, scheduled workflows, and integration tokens.

## Now

Start with one shared database, strict `tenant_id` discipline, RLS, workspace membership, and tenant-scoped audit logs.

## Later

Add enterprise tenant isolation options, dedicated databases, regional residency, tenant-level encryption controls, quota enforcement, and admin policy simulation.