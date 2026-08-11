# Tools and Integrations

Tools are the controlled actions ANUM can perform. Integrations connect ANUM to external systems such as GitHub, calendars, email, storage, CRMs, databases, and internal APIs. Every tool call must pass through the runtime.

## Integration Types

- REST integrations for standard web APIs.
- MCP integrations for structured tool ecosystems.
- Webhooks for inbound events.
- Browser or desktop tools for user-local actions in later phases.
- Internal tools for ANUM platform operations.

## Tool Contract

A tool should declare name, description, input schema, output schema, required permissions, risk level, timeout, retry policy, idempotency behavior, and audit fields. Tool results should distinguish successful output, recoverable failure, blocked action, and partial completion.

## Consent and Credentials

Integration credentials should be scoped to tenant, workspace, user, or agent. The system should show what access an integration has and allow revocation. Agents should never see raw secrets.

## MCP Strategy

MCP should be supported as an integration protocol, not as the entire runtime. ANUM can call MCP servers through mediated adapters while still applying tenant policy, approvals, audit logging, and tool schemas.

## Now

Support a small internal tool set, one external REST integration, one MCP-style adapter path, credential metadata, and approval-aware execution.

## Later

Add integration catalogs, per-integration sandboxes, webhook subscriptions, OAuth flows, rate-limit dashboards, and organization-level connector policies.