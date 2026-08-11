# Multi-Tenancy

ANUM should support multiple tenants from the first real implementation. A tenant represents a trust boundary. Workspaces, users, agents, memories, integrations, objects, approvals, events, and audit logs belong to a tenant unless explicitly marked as global system metadata.

## Tenant Model

A tenant contains workspaces. A user may belong to more than one tenant with different roles. Agents operate inside a tenant and usually inside a workspace. Integration credentials are granted to a tenant, workspace, user, or agent depending on the integration's risk and expected usage.

## Isolation Strategy

The default database pattern should be shared PostgreSQL tables with `tenant_id` columns and mandatory row-level security. Application code must set the tenant execution context for every request and worker job. Background workflows must carry tenant identity in their payloads and validate it when resumed.

## Authorization Layers

- Identity proves who the actor is.
- Membership proves which tenant and workspace the actor can access.
- Role and permission policy proves what action is allowed.
- RLS prevents data access outside the active tenant context.
- Tool policy limits what agents can do even after a user starts a task.

## Cross-Tenant Data

Cross-tenant analytics should use aggregated, non-sensitive data only. Product telemetry must avoid raw prompts, retrieved memory, tool payloads, secrets, and file contents unless explicitly configured for debugging in a controlled environment.

## Tenant Lifecycle

The platform should define tenant creation, suspension, export, deletion, and recovery flows. Deletion must cover relational data, vector records, object storage, cached state, scheduled workflows, and integration tokens.

## Now

Start with one shared database, strict `tenant_id` discipline, RLS, workspace membership, and tenant-scoped audit logs.

## Later

Add enterprise tenant isolation options, dedicated databases, regional residency, tenant-level encryption controls, quota enforcement, and admin policy simulation.