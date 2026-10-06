# Threat Model: Agent Tool Use and Prompt Injection

This is the Stage 5 threat model ([Production plan](production-plan.md)) for the part of ANUM that turns user intent into actions: the agent runtime, the model gateway, tool mediation, approvals and the data paths around them. It also records the review of the approval and risk policies ([Approvals and risk](approvals-and-risk.md)). It describes the code as of October 2026; update it whenever a tool, integration, model path or trust boundary changes.

Method: list the assets, draw the trust boundaries, walk STRIDE (spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege) across each boundary, and record for each threat the mitigation that exists in code (with a reference) and the gap that remains. Status: **Mitigated** (control in code and tested), **Partial**, or **Gap**.

## Assets

| Asset | Where it lives | Why it matters |
|---|---|---|
| Tenant content: task prompts, model replies, memory notes, workspace files, transcripts | PostgreSQL (RLS), S3-compatible storage under tenant/workspace prefixes | Confidentiality across tenants is the core promise. |
| Actions on external systems | Tools (`external.action` through the REST/MCP adapters) | Irreversible side effects: messages sent, data changed, money spent. |
| Credentials: model provider keys, webhook key, OIDC signing keys, database logins, `ANUM_SECRETS_KEY` | Secret store, `workspace_model_configs.api_key_ciphertext` (Fernet), environment | Turn a bug into account takeover or spend. |
| Approval decisions and audit trail | `approvals`, `agent_runs`, `domain_events`, `audit_records` | Accountability for what agents did and who allowed it. |
| Model spend | Provider accounts | Cost exhaustion. |
| Availability of runs | API, Temporal workers, Valkey locks, NATS | Users rely on runs finishing and status arriving. |
| Telemetry and backups | Collector, Tempo/Loki/Prometheus, dump files | Secondary copies of sensitive data if redaction or access control fails. |

## Actors

- **Workspace member or viewer** acting within their role, possibly malicious towards other tenants.
- **Workspace owner**, who controls model configuration and approves high-risk actions. Trusted for their own workspace, not for the platform.
- **Content author outside ANUM**: anyone who can put text in front of the model through a prompt, a shared document, a file, an integration response, a web page or speech. This is the prompt-injection attacker; they never need an account.
- **Compromised or malicious model provider or integration endpoint.**
- **Network attacker** between clients, API and providers.
- **Insider with infrastructure access** (database, telemetry, backups).

## Trust Boundaries

```
 client (web, desktop, mobile, voice)
   | B1: HTTPS + OIDC bearer token (Keycloak), CORS, rate limit, size limits
 API (FastAPI) --B2: tenant context + RLS--> PostgreSQL / S3 prefixes
   | B3: prompt leaves ANUM; reply comes back as untrusted text
 model gateway --> model provider (OpenAI-compatible, Ollama, per-workspace base_url)
   |
 agent runtime --B4: plan -> tool policy -> approval -> execute (outside the model)
   | B5: tool adapter, host allowlist, credential by reference
 external systems (webhook, MCP servers)
 API/worker --B6: outbox relay role, NATS subjects per tenant--> event bus --> SSE clients
 API/worker --B7: OTLP (redacted)--> collector --> telemetry stores
 backup role --B8: whole-database dump--> backup storage
```

The model sits inside B3/B4: everything it returns is data, never authority. The runtime, not the model, decides which tool runs, with which policy outcome, under which tenant context.

## Data Flow of One Run

1. `POST /api/v1/tasks` stores the prompt (max 8,000 characters) under the caller's tenant and workspace; `POST /tasks/{id}/run` starts a run inline or on Temporal (`anum_api/main.py`, `anum_api/durable_runs.py`).
2. `AgentPlanner.plan` (`anum_api/agent_planning.py`) selects skills by keyword triggers in the prompt (`SkillRegistry.select`, `anum_api/agent_skills.py:29`), calls the model gateway with the prompt, and builds exactly one tool call: `external.action` when the external-action skill matched, otherwise `anum.respond` with the model's text.
3. `AgentRuntime.plan_run` (`anum_api/runtime.py`) evaluates `ToolPolicy.evaluate` (`anum_api/agent_tools.py:74`): unregistered, outside the allowlist, missing role or `blocked` risk is blocked; `high` risk pauses for approval (and `medium` when the workspace's approval policy requires it); everything else runs.
4. A high-risk call is checkpointed (`run.checkpoint.tool_call`) and an approval row is created (`_pause_for_approval` in `runtime.py`) carrying the exact tool name and arguments (secret-looking values redacted for display), a SHA-256 `payload_hash` of the canonical, unredacted call bound to its task, run and proposal step (`anum_api/approval_integrity.py`), and `expires_at` (`ANUM_APPROVAL_TTL_SECONDS`, 24 hours). The approval also carries the configured integration target host and the requesting user. Only an owner can decide (`Permission.APPROVAL_DECIDE`, `anum_api/authorization.py`); with the workspace two-person rule on, not the owner who created the task or started the run; approving must send back the hash the client displayed, and the decider's user id and optional reason are recorded.
5. On approval, `begin_execution` (`runtime.py`) re-reads the checkpointed call, recomputes its payload hash and fails the run (with an `approval.payload_mismatch` audit record) unless it equals the approved hash, re-evaluates policy, commits `executing`, and only then runs the tool. An approval past `expires_at` is marked `expired` and its run fails instead. A crash mid-tool never repeats a non-idempotent or approved high-risk call (`recover_interrupted_execution`, `runtime.py:214`).
6. The tool adapter (`RestToolAdapter`, `anum_api/integration_tools.py`) posts the call arguments to the configured endpoint with tenant headers and a credential resolved by reference, reads at most `ANUM_TOOL_RESPONSE_MAX_BYTES` of the response within one overall deadline, and marks a cut-off response `truncated`.

## Prompt Injection

Prompt injection is expected, not exceptional ([Security](security.md#agent-safety)). Sources of attacker-controlled text that reach the model or the tool arguments:

| Source | Reaches the model today? | Notes |
|---|---|---|
| The task prompt itself (direct injection) | Yes | The user may be the attacker, or may paste attacker text (an email, a page). |
| Voice transcripts | Yes, through task creation and the voice assistant | Wake-word commands; interim or replayed transcripts cannot become commands (`tests/test_voice.py`). The assistant's question reaches its prompt as a labeled `user_speech` block. |
| Memory notes, workspace files | No | Not retrieved into prompts yet. Becomes an indirect channel when retrieval lands. |
| Tool and integration responses | No | Capped at `ANUM_TOOL_RESPONSE_MAX_BYTES` and shown to the user; not fed back to the model (single-step runs). When multi-step runs land, they reach the model only through `tool_output_prompt_block` (labeled untrusted data, below). |
| Skill manifests | Selection only | Built-in triggers; skill versions and installations are in-memory and do not change prompts. |

What an injected instruction can do today, and what stops it:

| Goal of the injection | Outcome |
|---|---|
| Call a tool that is not registered or not allowed | Blocked: the model never names the tool; the planner does, from a fixed registry, and policy re-checks at execution. |
| Trigger the external action | Possible by including a trigger word, but the call is `high` risk and pauses for an owner's approval. |
| Change what the external action sends | Possible: the arguments are the prompt and the model's reply (`planned_response`). The approval now shows them exactly (A1), so the owner sees the injected content before anything is sent. |
| Exfiltrate other tenants' data | Blocked: the model sees only the prompt; every repository query runs under RLS with the caller's tenant (`anum_api/db/session.py:set_tenant_context`). |
| Redirect a tool to another host | Blocked: endpoints come from operator configuration and the adapter enforces a host allowlist (`integration_tools.py:38`). The model cannot supply URLs. |
| Spend money with long outputs or loops | Bounded: one model call per plan, timeouts and bounded retries (`model_gateway.py` `RetryPolicy`); monthly per-tenant and per-workspace model budgets refuse calls once used up (`anum_api/model_budget.py`, M5). |
| Leak the prompt into logs or telemetry | Blocked: gateway logs and spans carry metadata only; the span exporter strips query strings and exception messages (`anum_api/telemetry.py`, `tests/test_telemetry.py`). |

## Threats

### Agent runtime and tools (B4, B5)

| # | STRIDE | Threat | Mitigation in code | Status |
|---|---|---|---|---|
| T1 | E | Model output selects a dangerous tool. | The planner chooses from the registry; `ToolPolicy.evaluate` blocks unknown, disallowed, role-gated and `blocked` tools (`agent_tools.py:74`); `tests/test_agent_modules.py`. | Mitigated |
| T2 | T | Approved call is swapped for another between approval and execution. | The call is checkpointed before the approval exists and re-read from the checkpoint, not re-planned; the approval stores a SHA-256 of the canonical call (tool, arguments, task, run, step), the decision must carry the hash the approver saw, and execution (inline and in the Temporal activity) recomputes it and refuses a mismatch with an audit record; policy is evaluated again at execution. | Mitigated (A2). An attacker who can rewrite both the checkpoint and the approval row in PostgreSQL is out of scope (B6). |
| T3 | T | Retries or worker crashes repeat a side effect. | `executing` committed before the tool runs; non-idempotent or approved high-risk calls are never repeated, the run fails as "outcome unknown" (`runtime.py:214`, `tests/test_durable_runs.py`, `tests/test_temporal_worker.py`). | Mitigated |
| T4 | E | Two replicas run the same task concurrently. | Row locks plus optional Valkey run locks with token-checked release (`anum_api/valkey.py`, `tests/test_valkey.py`). | Mitigated |
| T5 | I | Tool sends tenant data to an unintended host. | Host allowlist per adapter, endpoint from configuration only (`integration_tools.py:38`, `configured_external_handler`). | Mitigated for configured adapters |
| T6 | I | Integration credential exposed to the model or logs. | Credentials resolved by reference at call time, never in arguments (`CredentialProvider`, `integration_tools.py`); not logged. | Mitigated |
| T7 | D | Integration endpoint hangs or returns a huge body. | The REST adapter streams the body and stops at `ANUM_TOOL_RESPONSE_MAX_BYTES` (default 256 KiB; the result is marked `truncated`, recorded on the `tool_result` step, and truncated JSON is kept as text, never parsed); one overall deadline covers connect, send and the whole read, so a slow never-ending body cannot hold a worker; it asks for `identity` encoding and refuses compressed bodies (no decompression bombs). MCP outputs are capped the same way (`anum_api/integration_tools.py`, `tests/test_tool_output_safety.py`). | Mitigated |
| T8 | R | No record of who approved what. | `approval.approved`/`rejected` events and run steps in PostgreSQL; the approval row stores `decided_by`, `decided_at` and the optional `decision_reason`, and every decision (with reason, target and risk), expiry, payload or target mismatch, refused self-approval and approval-policy change is written to the append-only `audit_records` table; membership and governance changes are audited there too. | Mitigated |

### Model gateway (B3)

| # | STRIDE | Threat | Mitigation in code | Status |
|---|---|---|---|---|
| M1 | I | Provider or network observer reads prompts. | Prompts leave ANUM by design. HTTPS required for workspace `base_url` outside local and test (`anum_api/model_egress.py`), except for hosts an operator lists in `ANUM_MODEL_ALLOWED_HOSTS`. | Accepted risk; provider terms and region are owner decisions. |
| M2 | S/I | Workspace owner points `base_url` at an internal address (SSRF: cloud metadata, internal services); the connection test reveals reachability through its error messages. | Outside `local`/`test` the SSRF guard (`anum_api/model_egress.py`) refuses non-HTTPS schemes, credentials, non-default ports, non-canonical IP literals and hosts resolving to loopback, private, unique-local, link-local/metadata, multicast, unspecified or reserved addresses (IPv4 embedded in IPv6 included), at save time and on every request; each request resolves once and connects to that address with the original Host and TLS name (no DNS rebinding); redirects are never followed; the connection test returns one generic error. Operator allow-list `ANUM_MODEL_ALLOWED_HOSTS` for self-hosted models. Only owners can set it (`Permission.ORGANIZATION_MANAGE`). Tests: `tests/test_model_egress.py`. | Mitigated (G1) |
| M3 | I | Provider key leaks from the database or a backup. | Fernet encryption with rotation (`anum_api/secret_box.py`), `credential_hint` so reads never decrypt, RLS on `workspace_model_configs`. | Mitigated; rotation procedure in [Runbooks](runbooks.md#rotating-anum_secrets_key). |
| M4 | I | Prompts or replies in logs, traces, metrics. | Metadata-only logging (`model_gateway.py` `_log_call`); spans with token counts only; redacting span exporter; tests assert absence. | Mitigated |
| M5 | D | Cost exhaustion through many or large runs. | Rate limits per client, request size limits, one model call per plan, timeouts and bounded retries, cost metrics and `AnumModelCostSpike`/`AnumModelHourlySpendHigh` alerts. Monthly (UTC) estimated-cost and token budgets per tenant and per workspace (`anum_api/model_budget.py`, RLS tables `model_budgets` and `model_usage_monthly`), checked before each task or voice model call (402, or a spoken reply), usage recorded from the gateway's usage metadata, 80%/100% logs and `anum.model.budget.*` metrics. | Mitigated (G4). The check does not reserve spend, so concurrent calls can overshoot a limit by their own usage; unpriced models count tokens only. |
| M6 | T | Malicious provider returns crafted output (markup, links, instructions). | Output is data: stored and rendered as text by clients (React escapes), never executed or used as a URL. | Partial: it can still shape the external action's payload (A1). |

### Identity, API and tenancy (B1, B2)

| # | STRIDE | Threat | Mitigation in code | Status |
|---|---|---|---|---|
| I1 | S | Forged or replayed tokens. | Issuer, audience, expiry and RS256 signature against a rotation-aware JWKS (`anum_api/identity.py` `OidcValidator`); header auth and local sessions refused outside `local`/`test`. | Mitigated |
| I2 | E | Cross-tenant reads through a query bug. | RLS enabled and forced on every tenant table; tenant and workspace set per transaction; tests run as a non-superuser role (`tests/test_postgres_rls.py`); backups verify RLS after restore (`infra/backup/anum_backup.py`). | Mitigated |
| I3 | E | Token claims grant workspace power. | The persisted membership decides the role, not realm roles. | Mitigated |
| I4 | D | Request floods or oversized bodies. | Token bucket rate limit, body limits (`anum_api/hardening.py`), 429/413 counted in metrics. | Mitigated (per replica unless Valkey backend) |
| I5 | I | Cross-tenant events on the bus. | Tenant/workspace tokens in subjects; SSE filters by scope; relay role reads only unpublished rows (`migrations/versions/0007_event_outbox.py`). | Mitigated |

### Operations (B6 to B8)

| # | STRIDE | Threat | Mitigation in code | Status |
|---|---|---|---|---|
| O1 | I | Telemetry stores collect tenant content. | Metric attributes exclude tenant ids; spans carry only tenant/workspace ids; redacting exporter and collector processors (`infra/docker/otel-collector.yaml`). | Mitigated in code; access control on production telemetry stores is open (cloud). |
| O2 | I | Backups leak every tenant. | `0600` files, checksum, documented encrypted storage; restores only into new `anum_restore_*` databases. | Partial: encryption and access control depend on the storage the owner chooses. |
| O3 | T | Restored database silently loses RLS or rows. | Drill verifies policies, FORCE RLS, exact counts and isolation as a non-superuser (`tests/test_backup_restore.py`). | Mitigated |

## Approval and Risk Policy Review

Reviewed against [Approvals and risk](approvals-and-risk.md). The basic flow holds: high-risk actions pause, only owners decide, the decided call is the checkpointed call, policy is re-checked before execution, and a crash never repeats an approved side effect. Findings:

| # | Finding | Recommendation | Severity |
|---|---|---|---|
| A1 | **Fixed (0010_approval_integrity).** The approval shows the tool name and the first 240 characters of the prompt (`runtime.py:270`), not the arguments that will be sent. For `external.action` those include the model's reply (`planned_response`), which an injected instruction can shape. | Show the exact arguments (or a faithful summary plus the full payload on demand) and the target integration on the approval, as the doc requires ("what data will be sent"). Done: the approval carries `action` (exact tool) and `arguments` (every argument, secret-looking keys and values replaced by `[REDACTED]`); the web and Flutter approval cards render them as key/value rows with the payload hash and expiry. The target integration is implied by the tool name; showing the configured endpoint is open. | High |
| A2 | **Fixed (0010_approval_integrity).** Approvals were not bound to the arguments. | Store a hash of the canonical tool call on the approval and refuse execution if the checkpointed call no longer matches. Done: `payload_hash` = SHA-256 of canonical JSON of tool, full arguments, task, run and proposal step; `POST .../approve` requires the hash the client displayed (`409` if stale); the inline runtime and the Temporal activity recompute it before executing and fail the run with an `approval.payload_mismatch` audit record otherwise. | Medium |
| A3 | **Fixed (0010_approval_integrity).** No expiry: `ApprovalStatus.EXPIRED` existed but nothing set it; a forgotten approval could be granted weeks later, after context changed. | Add `expires_at` (proposal: 24 hours) and treat expired approvals as rejected at execution. Done: `expires_at` = request time + `ANUM_APPROVAL_TTL_SECONDS` (default 86400); reads show a lapsed approval as `expired`; deciding it answers `410` and commits the expiry and the failed run; the Temporal workflow caps its wait at the expiry so the activity expires it on time; the decider (`decided_by`) and `decided_at` are recorded and shown in both clients' history. | Medium |
| A4 | **Mostly fixed (0012_approval_policy).** The approval row records `decided_by`, `decided_at` and an optional `decision_reason` (500 characters, control characters refused), which also goes into the audit record and the decision event. Open: governance approval rules and policy packs are not consulted by `ToolPolicy`. | Wire organization approval rules into the policy now that the governance store is in PostgreSQL. | Medium |
| A5 | **Kept, with an opt-in (0012_approval_policy).** `medium` risk tools run without approval by default. The doc defines medium as "modify private state, create durable records, or use paid services". | Done: a per-workspace `medium_risk_requires_approval` setting (owner-only, audited) makes medium tools pause like high ones. Still required: `medium` tools must be idempotent or reversible, and paid-service tools stay behind the per-tenant budgets (G4). | Low |
| A6 | **Fixed as an option (0012_approval_policy).** A single owner can create and approve their own high-risk task. | Done: per-workspace `two_person_rule` (owner-only, audited). When on, neither the task creator nor the user whose run proposed the call can approve a high-risk action (`403` with a clear message, `approval.self_approval_denied` audit record); another owner must. Off by default for single-user workspaces. | Low |
| A7 | Skill selection is by keyword (`agent_skills.py:29`): any text containing a trigger word proposes the external action. | Acceptable while approval gates it; document the triggers and log selection reasons. | Low |

## Gaps

| # | Gap | Proposed fix | Needed before |
|---|---|---|---|
| G1 | SSRF through workspace `base_url` and the model connection test (M2). | **Done**: `anum_api/model_egress.py` validates at save and request time with the resolved address pinned, refuses redirects, allow-lists self-hosted hosts only through `ANUM_MODEL_ALLOWED_HOSTS`, and the connection test answers generically outside `local`/`test` ([Model gateway](model-gateway.md#outbound-guard-ssrf)). | Real tenants |
| G2 | Approval content and binding (A1, A2, A3). Closed: exact arguments shown, decision bound to a payload hash, expiry and decider recorded. | **Done**: the approval carries `target`, the configured integration host (host only: no scheme, credentials, port, path or query), from the tool definition rather than the model; clients show it, and execution refuses (`approval.target_mismatch`) if the configured target changed after approval. | Real external integrations |
| G3 | Tool responses are unbounded and untrusted (T7), and will become prompt input with multi-step runs. | **Done**: responses capped (T7); `tool_output_prompt_block` (`anum_api/agent_tools.py`) serializes tool output as JSON inside a provenance-labeled untrusted block (below), and every prompt carrying one must include `UNTRUSTED_DATA_RULES`. The policy check stays outside the model. Multi-step runs must feed tool output back only through this helper. | Multi-step runs |
| G4 | No per-tenant model budget or quota (M5). | **Done**: monthly tenant and workspace budgets (estimated cost and tokens) enforced in front of the gateway, owner-only API with audit records, threshold metrics with bounded labels ([Model gateway](model-gateway.md#monthly-budgets)). Open: per-workspace cost dashboards. | General availability |
| G5 | Memory and file retrieval into prompts will add indirect injection. | Design and labeling helper done (below, `anum_api/prompt_provenance.py`); retrieval itself is not built. When it lands: label every retrieved item, scope retrieval by tenant, workspace and the caller's permission under RLS, and derive no tool authority from retrieved text. | Memory retrieval |
| G6 | Approval decider and decision reason are persisted (`decided_by`, `decision_reason`, audit records). | **Done** for approvals (A4). Governance approval rules into `ToolPolicy` remain (A4). | Stage 3 exit |

## Provenance Labels for Untrusted Prompt Text (G3, G5)

Anything that did not come from ANUM's own instructions is data in a prompt, never instructions: tool and integration responses, retrieved memory notes and workspace files, web content and speech transcripts. `anum_api/prompt_provenance.py` is the one way to put such text into a prompt:

- `label_untrusted(content, source=..., origin=..., truncated=..., max_chars=...)` wraps the text as

  ```
  <<untrusted-data id=<16 hex nonce> source=<tool_output|memory|file|user_speech|web> origin=<tool@host | memory id | file id> truncated=<true|false>>>
  ...content...
  <<end-untrusted-data id=<same nonce>>>
  ```

  The nonce is fresh for every block, so content cannot guess the closing marker; marker-like text inside the content is defanged as well (a block always has exactly one opening and one closing marker). Attribute values are reduced to `[A-Za-z0-9._:@/-]` and 120 characters, so a source or origin cannot inject newlines or markers. `max_chars` caps the content placed in the prompt and marks it truncated.
- `UNTRUSTED_DATA_RULES` (or `with_untrusted_rules(instructions, *blocks)`, which puts the instructions first, then the rules, then the blocks) tells the model that labeled text is data from the named source and that instructions, role changes or tool directions inside it must never be followed.
- Tool output: `tool_output_prompt_block(call, result, target=...)` serializes `{status, summary, output}` as JSON with `source=tool_output origin=<tool>@<target host>` and carries the response's `truncated` flag.
- Voice: the voice assistant already sends the user's question as a `user_speech` block with these rules.
- Retrieval (when memory or file retrieval into prompts lands): each retrieved note or file chunk becomes its own block with `source=memory` or `source=file` and the record id as `origin` (never a user-controlled name); retrieval runs under the caller's tenant and workspace RLS context and the caller's permission (memory reads, file reads) before anything is labeled; retrieved text never selects tools, changes policy or grants approval.

Labels are defence in depth, not a control: a model can still be persuaded by data. The controls stay outside the model: the planner picks tools from the registry, `ToolPolicy` and the workspace approval policy decide the outcome, approvals bind the exact payload and target, and RLS scopes every read.
| G7 | External penetration test. | Stage 5 item; scope it on this document's boundaries. | General availability |
