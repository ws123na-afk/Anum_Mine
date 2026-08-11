# Security

ANUM should be designed as a security-sensitive system from the beginning because agents can read private context, call external tools, and act on behalf of users. Security must be part of the product model, not an afterthought added around an autonomous core.

## Identity

Keycloak is the planned identity provider. ANUM should use OIDC for sign-in, token issuance, session management, MFA policy, and federation. Application services should validate tokens, map external identities to ANUM users, and avoid embedding identity assumptions in client code.

## Authorization

ANUM authorization should combine application-level policy with PostgreSQL row-level security. The backend decides whether a user, service, or agent may perform an action. The database enforces tenant and workspace isolation so accidental query mistakes do not leak data across boundaries.

## Secrets

Secrets must be stored outside source control. Provider keys, integration tokens, signing keys, and storage credentials should be delivered through environment-specific secret stores. Local development may use `.env` files, but sample files must contain placeholders only.

## Agent Safety

Agents must not receive raw unrestricted access to user accounts, files, or integrations. Each tool call should be mediated by the runtime, checked against policy, logged, and paused for approval when risk requires it. Prompt injection must be treated as an expected attack class, especially when agents read external content.

## Data Protection

Tenant data should be encrypted in transit and at rest by infrastructure defaults. Sensitive fields should be minimized, redacted in logs, and excluded from analytics payloads. Memory records should carry provenance, scope, and retention metadata.

## Auditability

Security-relevant events should be recorded: login, token refresh failures, tenant membership changes, role grants, integration consent, tool execution, approval decisions, memory deletion, policy changes, and administrative exports.

## Now

Implement OIDC validation, tenant isolation, RLS, minimal roles, audit tables, secure defaults, and approval gates before real external actions.

## Later

Add policy simulation, organization compliance exports, anomaly detection, device trust, per-integration token vaulting, customer-managed keys, and formal security review workflows.