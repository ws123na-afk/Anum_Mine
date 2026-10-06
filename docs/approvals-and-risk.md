# Approvals and Risk

ANUM should treat approval as a first-class runtime primitive. Agents may plan actions, but the system decides whether an action can run automatically, requires confirmation, or must be blocked.

## Risk Levels

Risk classification should be simple at first:

- Low: read-only or reversible actions with limited exposure.
- Medium: actions that modify private state, create durable records, or use paid services.
- High: actions that send messages, spend money, delete data, change permissions, publish content, or affect external systems.
- Blocked: actions outside policy, outside scope, or requiring unavailable credentials.

## Approval Objects

An approval should capture actor, tenant, workspace, agent run, proposed tool call, target integration, input summary, risk level, policy reason, expiration, approver, decision, and resulting execution event. Approval records must be immutable except for status transitions.

## Runtime Behavior

When an agent proposes a risky action, the runtime should pause execution, emit an event, notify subscribed clients, and wait for a decision. The runtime resumes only after approval is granted and the tool inputs still match the approved request.

## User Experience

Approval prompts should be concrete. Users need to see what will happen, which account or integration will be used, what data will be sent, what can be undone, and why ANUM is asking. The product should avoid vague prompts such as "approve this action".

## Now

Support basic approval records, task pausing, approve/reject decisions, audit logging, and one high-risk sample action.

The October 2026 policy review is in the [Threat model](threat-model.md#approval-and-risk-policy-review). Its findings A1 to A3, the decision reason (A4), the integration target on the approval (G2), the optional medium-risk approval policy (A5) and the optional two-person rule (A6) are implemented (below). The organization approval rules and active policy packs from governance are consulted by `ToolPolicy` too (A4 remainder, [below](#organization-approval-rules-and-policy-packs)). Still open: an owner screen for the workspace approval policy in the clients (the API exists), and approval chains that collect more than two approvers.

## Implementation

Migration `0010_approval_integrity` adds the integrity columns to `approvals`, and `0012_approval_policy` adds the decision reason, requester and target columns and the `workspace_approval_policies` table (both under forced tenant/workspace RLS). The logic is in `anum_api/approval_integrity.py`, `anum_api/runtime.py`, `anum_api/agent_tools.py` (`ToolPolicy`) and the approval routes in `anum_api/main.py`.

| Field | Meaning |
|---|---|
| `action` | The exact tool name the agent will call. |
| `arguments` | Every argument the tool will receive. Keys that look secret (`password`, `token`, `secret`, `api_key`, `authorization`, `credential`, `cookie`, `session`, ...) and values that look like credentials (bearer tokens, `sk-...`, GitHub and Slack tokens, AWS key ids, JWTs) are replaced by `[REDACTED]` for display only. |
| `run_id`, `step_id` | The run and the `tool_proposal` step the approval belongs to. |
| `payload_hash` | SHA-256 (hex) of the canonical JSON (sorted keys, no whitespace, UTF-8) of `{tool, arguments, task_id, run_id, step_id}` with the full, unredacted arguments. |
| `expires_at` | Request time plus `ANUM_APPROVAL_TTL_SECONDS` (default 86400, 24 hours). |
| `decided_by`, `decided_at` | User id and time of the approve or reject decision. |
| `decision_reason` | Optional reason the decider typed (A4): at most 500 characters, trimmed, blank stored as none; control characters other than newline and tab are refused (`422`) so a reason cannot forge log lines. Kept on the approval, in the `approval.approved`/`approval.rejected` audit record (`metadata.reason`) and in the decision event payload (`reason`). |
| `requested_by` | User id whose run proposed the call (the context that planned it). |
| `target` | The configured integration host the tool will contact (G2): host name only, never scheme, credentials, port, path or query; null for internal tools such as `anum.respond`. Comes from the tool definition (`ToolDefinition.target`, set from the REST adapter's endpoint), not from the model. |
| `risk_level` | The tool's risk level: `high`; `medium` when the workspace requires approval for medium risk; or the tool's own level (`low` or `medium`) when an organization approval rule or policy pack requires approval. |

Decision rules:

- `POST /api/v1/approvals/{id}/approve` requires the body `{"payload_hash": "<hash the client displayed>"}`. A missing or malformed hash is `422`; a hash that differs from the approval's is `409` and changes nothing. Approvals stored without a hash (created before 0009) can never be approved.
- Both decisions accept an optional `"reason"` (see `decision_reason`). `POST /api/v1/approvals/{id}/reject` accepts the body optionally, may carry only a reason, and if it sends a hash it must match.
- A pending approval past `expires_at` reads as `expired` in `GET /api/v1/approvals`. Deciding it answers `410 gone`, and in the same committed transaction marks it `expired`, records `approval.expired` (event and audit record) and fails the run (inline) or signals the workflow (Temporal). Deciding an approval already stored as `expired` (including one expired by cancelling its task) is also `410`; one already approved or rejected is `409`.
- Before executing, the runtime recomputes the payload hash from the run's checkpointed tool call. Inline and in the Temporal activity, a mismatch fails the run with "payload hash mismatch", writes an `approval.payload_mismatch` audit record (approved hash, checkpoint hash, decider) and never runs the tool. An approval decided after its expiry is also refused.
- The Temporal workflow waits for a decision signal at most until the approval's expiry (it gets `approval_expires_at` from the activity), so the next activity step marks a lapsed approval `expired` and fails the run without any client action.
- Every approve and reject writes an audit record with the decider as actor, the payload hash, the risk level, the target and the reason.
- If the integration target configured at execution differs from the `target` the approver saw, the run fails with "integration target changed" and an `approval.target_mismatch` audit record; the tool is never called.

## Workspace Approval Policy

Each workspace has an optional approval policy (A5, A6). Both settings default to off, which is the single-user behaviour described above.

| Setting | Effect |
|---|---|
| `two_person_rule` | A `high`-risk approval cannot be approved by the user who created the task (`tasks.created_by_user_id`) or by the user whose run proposed the call (`requested_by`). Another owner must approve it. The refused attempt answers `403` with "This workspace requires two people for high-risk actions: you created or started this task, so another owner must approve it. You can still reject it." and writes an `approval.self_approval_denied` audit record; the approval stays pending. Rejecting your own task is always allowed, and the rule does not apply to medium-risk approvals. Only owners hold `approval:decide`, so a workspace needs at least two owners before turning it on. |
| `medium_risk_requires_approval` | `medium`-risk tools pause for approval like `high` ones (`ToolPolicy.evaluate` with the workspace policy, re-checked at execution and on recovery). Off by default: medium tools stay approval-free, which is acceptable only while they are idempotent or reversible and paid-service tools stay behind the per-tenant model budgets. |

- `GET /api/v1/approval-policy` (any member, `approval:read`) returns `{two_person_rule, medium_risk_requires_approval, updated_by, updated_at}`.
- `PUT /api/v1/approval-policy` with `{two_person_rule, medium_risk_requires_approval}` is owner-only (`policy:manage`) and writes an `approval_policy.updated` audit record with the settings before and after.
- Storage: one row per workspace in `workspace_approval_policies` (migration `0012_approval_policy`), forced RLS on the tenant and workspace; the in-memory backend keeps it in the process store.
- The policy is evaluated by the runtime and the approval route, outside the model; nothing the model or a tool returns can change it.

### Policy screens

- Web: **Approval policy** in the navigation (`ApprovalPolicyView` in `apps/web/src/AdminViews.tsx`, helpers and wording in `src/lib/policy.ts`). Flutter: Settings › Workspace administration › Approval policy (`apps/mobile/lib/features/admin/policy_screen.dart`, model in `approval_policy.dart`, `ApprovalPolicyController`).
- Both show the policy in force from `GET /api/v1/approval-policy`, explain each switch in plain words ("when on" and "when off", the same sentences on both clients), and show who changed it last and when ("Never changed" while both are at their default).
- Who may change it comes from the API, never from token claims: the caller's role is read from `GET /api/v1/workspace-memberships/current`. Members and viewers see the switches read-only under "Owner access required". If the role cannot be read, the switches stay editable and the `PUT` decides; a `403` turns the screen read-only, restores the saved values and quotes the API's answer, like the Members and Budgets screens.
- Owners save both switches together with one explicit `PUT` (no save on toggle). Before saving, an owner who turns the two-person rule on while the workspace has fewer than two active owners (from `GET /api/v1/workspace-members`, which only owners may list) sees a warning that nobody could approve the high-risk actions of tasks they start.
- A decision refused with `403` (the two-person rule, or a role without `approval:decide`) is shown on that approval's card as "Decision refused" with the API's sentence, not as a page error; the approval stays pending, and Reject stays available.
- Tests: `apps/web/test/workspaces.test.ts` (mapping, wording, owner warning), `apps/web/e2e/policy.spec.ts` (owner save, read-only member, `403` on save, the two-person refusal on the card; desktop and mobile projects), `apps/mobile/test/admin_test.dart` (repository, controller and screen) and `apps/mobile/test/approvals_test.dart` (the refusal on the card).

### Approval cards

Clients: the web Approvals view (`apps/web/src/ApprovalsView.tsx`, helpers in `src/lib/approvals.ts`) and the Flutter approvals screen (`apps/mobile/lib/features/workspace/approvals_screen.dart`) show the tool, the integration target ("Sends to <host>"), every argument as a key/value row (redacted values marked), the shortened payload hash, and the time left; they offer an optional reason field (500 characters) sent with Approve or Reject, send back the displayed hash when approving, disable Approve for expired or unbound approvals, and show "Approved by <user> · <time>" and the reason in history. A two-person-rule refusal surfaces the API's message on the approval card ([Policy screens](#policy-screens)). The Android client sends the hash too but does not yet render the arguments, target or a reason field (its calls are unchanged and still valid).

## Organization Approval Rules and Policy Packs

Tenant-level governance (`POST /api/v1/organization/approval-rules`, `POST /api/v1/policy-packs`, [Governance and scale](governance-and-scale.md)) is part of the tool policy (A4). The runtime reads the tenant's **enabled** approval rules and the rules of its **active** policy pack versions through the repository, in the caller's tenant scope (RLS on `approval_rules` and `policy_packs`; the in-memory backend reads the governance store filtered by tenant), every time it evaluates a call: at planning, at execution (`begin_execution`, including after an approval) and on recovery of an interrupted run. A rule changed while a run waits takes effect at its next evaluation. A failure to read the rules fails the request; the call never runs on a guess. `anum_api/tool_governance.py` holds the matching; `ToolPolicy.evaluate` applies it.

Patterns (`action_pattern` of an approval rule, `action` of a policy pack rule) are case-insensitive shell globs:

| Pattern | Matches |
|---|---|
| `external.*` or `tool:external.*` | The tool name. |
| `target:*.example.com` or `integration:hooks.example.com` | The configured integration host of the tool (`ToolDefinition.target`, the same host the approval shows). Internal tools without a target never match. |
| `risk:medium` | Tools at or above the level (`low`, `medium`, `high`). An unknown level matches everything (fail closed). |

Policy pack rules may narrow a match with `conditions`: `risk_level` (a level or a list, exact) and `target` (a glob or a list). A condition the runtime does not know makes the rule match, and an unknown effect counts as `deny`, so a typo never silently disables a rule.

Order of evaluation (outside the model; rules can only add restrictions):

1. Unregistered tools, tools outside the runtime allowlist, missing roles and `blocked` tools are blocked (platform deny).
2. A matching policy pack `deny` blocks the call (`risk_level` `blocked`, reason "An organization policy pack denies this action."). Deny wins over approval and allow, also for an already approved call: the re-check at execution fails the run.
3. `high` risk needs approval (unchanged).
4. A matching enabled approval rule or policy pack `require_approval` rule needs approval, at any risk level ("An organization approval rule requires approval for this action.").
5. `medium` risk needs approval when the workspace policy says so.
6. Everything else runs. A policy pack `allow` never weakens steps 1 to 5.

The rules that shaped a decision are recorded on the run's `tool_proposal` step as `metadata.governance_rules` (`approval_rule:<name>` or `policy_pack:<name>@v<version>:<pattern>`), and the approval's `reason` starts with the policy reason. Recovery of an interrupted run repeats only idempotent calls that policy still allows without approval, so a rule added meanwhile turns a repeat into a failed run for a person to check.

When an approval is approved, the matching approval rules are re-read and enforced on the decider (rejecting is always allowed). Each refusal answers `403`, leaves the approval pending and writes an audit record with the rule names:

| Rule field | Enforcement | Audit action |
|---|---|---|
| `required_roles` | The decider must hold a role every matching rule lists (the default is `owner`; only owners hold `approval:decide`). | `approval.role_denied` |
| `minimum_approvers: 2` | Two people: the task creator and the user whose run requested the call cannot approve; another owner must (the two-person rule, for any risk level). | `approval.self_approval_denied` |
| `minimum_approvers` above 2 | Not collectable yet (one decision per approval), so the approval cannot be approved, only rejected (fail closed). | `approval.approvers_unavailable` |

Tests: `services/api/tests/test_tool_governance.py` (matching, policy order, planning, execution, recovery, decision-time rules, tenant isolation) and `test_postgres_tool_governance.py` (the same through PostgreSQL as the non-owner app role, and RLS on the rule reads). No migration was needed: the rules are read from the `approval_rules` and `policy_packs` tables of migration `0008_control_plane_stores`.

## Later

Add delegated approvals, organization approval chains, policy-driven auto-approval, approval templates, mobile push approvals, emergency revocation, and simulations that explain why a policy allowed or blocked an action.