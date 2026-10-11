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

## Workspace Configuration

`GET|PUT /api/v1/integrations/{id}/configuration` lets a workspace owner disable an infrastructure integration or override its endpoint for health checks. With `ANUM_REPOSITORY_BACKEND=postgresql` the overrides live in the RLS-protected `integration_configurations` table, keyed by tenant, workspace and integration ([Multi-tenancy](multi-tenancy.md#control-plane-stores)); `GET /api/v1/integrations` reads them before probing, without holding a database session during the probes. Endpoints are stored and returned as given, so they must not carry credentials.

## Web Search

`web_search` is a read-only agent tool backed by a self-hosted [SearXNG](https://docs.searxng.org/) (`anum_api/web_search.py`). It is an ordinary registered tool, so every call takes the runtime's normal path: the planner proposes it, `ToolPolicy` decides (risk `low`, so no approval unless a workspace or organization rule asks for one; a policy pack can deny it), the run records the proposal and result steps, and the call is audited.

- **Configuration (operator only).** `ANUM_SEARXNG_URL` is SearXNG's base URL on the internal network (`http://searxng:8080` in `infra/docker/compose.yaml` and `infra/oracle/compose.yaml`). Empty means the tool is not registered at all, so no skill can select it, and the `searxng` entry in `GET /api/v1/integrations` is `disabled` with "Web search is off: set ANUM_SEARXNG_URL ...". A malformed value (not absolute http(s), credentials, a query) does not stop the API: the tool stays off and the health entry says what is wrong. `ANUM_WEB_SEARCH_TIMEOUT_SECONDS` (default 8, at most 30), `ANUM_WEB_SEARCH_MAX_RESULTS` (default 5, at most 20) and `ANUM_WEB_SEARCH_SAFE_SEARCH` (0 off, 1 moderate default, 2 strict) tune it. On Kubernetes set the URL through `extraEnv`; Render has no SearXNG, so the tool stays off there.
- **No SSRF.** The request always goes to `<ANUM_SEARXNG_URL>/search?format=json`. Nothing a tenant sends changes it: tool arguments other than `query` are ignored, and an endpoint a workspace owner saves under Integrations is never connected to (as for every integration). Redirects are not followed.
- **Per-workspace switch.** A workspace owner can turn the `searxng` integration off (`PUT /api/v1/integrations/searxng/configuration` with `enabled: false`, or the Integrations page). The tool then refuses before any request, the run fails with "Web search is turned off for this workspace", and the health entry says "Turned off for this workspace by an owner."
- **Query and results.** The query is the user's own prompt (whitespace collapsed, at most 400 characters), never retrieved text or model output (threat model G5). One overall deadline covers connect, send and read; the body is read up to `ANUM_TOOL_RESPONSE_MAX_BYTES` with identity encoding only (a larger or compressed answer fails the call). Results keep `title`, `url` and `snippet` (content), at most the configured number, duplicates removed, titles and snippets cut to 300 and 600 characters; only `http` and `https` links are kept (`javascript:`, `data:`, `ftp:` and the rest are dropped). The output is untrusted tool data (G3) and reaches prompts only through `tool_output_prompt_block`.
- **Failures.** A timeout, an HTTP error, an unreachable or unreadable service fails the run with a message naming the cause ("Web search timed out after 8 seconds.", "The search service answered HTTP 429."); an empty or overlong query is refused without a request.
- **Audit.** Each call writes a `tool.call` audit record (target `web_search`, outcome `succeeded`, `failed` or `blocked`) with `query_length`, `result_count`, `dropped_results`, the target host and the outcome code. Never the query text and never the results. Any tool can do the same by setting `ToolResult.audit_metadata` (counts and outcomes only); the runtime records it in the caller's tenant scope.
- **SearXNG itself** (`infra/searxng/settings.yml`): JSON format on, bot limiter off (it serves the API alone on an internal network and would otherwise need Valkey), no public-instance mode, no image proxy, metrics off, engine timeouts below the API deadline. The secret key comes from `SEARXNG_SECRET` (generated into `infra/oracle/.env`; a development placeholder locally). No published port and no Caddy route; 512 MB memory limit.
- **Clients.** The web Integrations page shows the `searxng` card with its health and whether "Agent tool web_search" is available, degraded or unavailable; owners switch it per workspace there (`apps/web/src/IntegrationsView.tsx`, `apps/web/e2e/integrations.spec.ts`). Tests: `services/api/tests/test_web_search.py`.

## MCP Strategy

MCP should be supported as an integration protocol, not as the entire runtime. ANUM can call MCP servers through mediated adapters while still applying tenant policy, approvals, audit logging, and tool schemas.

## Now

Support a small internal tool set, one external REST integration, one MCP-style adapter path, private web search through SearXNG, credential metadata, and approval-aware execution.

## Later

Add integration catalogs, per-integration sandboxes, webhook subscriptions, OAuth flows, rate-limit dashboards, and organization-level connector policies.