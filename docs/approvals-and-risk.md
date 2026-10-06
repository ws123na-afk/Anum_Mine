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

The October 2026 policy review is in the [Threat model](threat-model.md#approval-and-risk-policy-review). Its findings A1 to A3 are implemented (below). Still open before real external integrations: show the target endpoint of the integration on the approval, and capture a decision reason (A4).

## Implementation

Migration `0009_approval_integrity` adds the integrity columns to `approvals` (same forced tenant/workspace RLS policy). The logic is in `anum_api/approval_integrity.py`, `anum_api/runtime.py` and the approval routes in `anum_api/main.py`.

| Field | Meaning |
|---|---|
| `action` | The exact tool name the agent will call. |
| `arguments` | Every argument the tool will receive. Keys that look secret (`password`, `token`, `secret`, `api_key`, `authorization`, `credential`, `cookie`, `session`, ...) and values that look like credentials (bearer tokens, `sk-...`, GitHub and Slack tokens, AWS key ids, JWTs) are replaced by `[REDACTED]` for display only. |
| `run_id`, `step_id` | The run and the `tool_proposal` step the approval belongs to. |
| `payload_hash` | SHA-256 (hex) of the canonical JSON (sorted keys, no whitespace, UTF-8) of `{tool, arguments, task_id, run_id, step_id}` with the full, unredacted arguments. |
| `expires_at` | Request time plus `ANUM_APPROVAL_TTL_SECONDS` (default 86400, 24 hours). |
| `decided_by`, `decided_at` | User id and time of the approve or reject decision. |

Decision rules:

- `POST /api/v1/approvals/{id}/approve` requires the body `{"payload_hash": "<hash the client displayed>"}`. A missing or malformed hash is `422`; a hash that differs from the approval's is `409` and changes nothing. Approvals stored without a hash (created before 0009) can never be approved.
- `POST /api/v1/approvals/{id}/reject` accepts the same body optionally; if sent it must match.
- A pending approval past `expires_at` reads as `expired` in `GET /api/v1/approvals`. Deciding it answers `410 gone`, and in the same committed transaction marks it `expired`, records `approval.expired` (event and audit record) and fails the run (inline) or signals the workflow (Temporal). Deciding an approval already stored as `expired` (including one expired by cancelling its task) is also `410`; one already approved or rejected is `409`.
- Before executing, the runtime recomputes the payload hash from the run's checkpointed tool call. Inline and in the Temporal activity, a mismatch fails the run with "payload hash mismatch", writes an `approval.payload_mismatch` audit record (approved hash, checkpoint hash, decider) and never runs the tool. An approval decided after its expiry is also refused.
- The Temporal workflow waits for a decision signal at most until the approval's expiry (it gets `approval_expires_at` from the activity), so the next activity step marks a lapsed approval `expired` and fails the run without any client action.
- Every approve and reject writes an audit record with the decider as actor and the payload hash.

Clients: the web Approvals view (`apps/web/src/ApprovalsView.tsx`, helpers in `src/lib/approvals.ts`) and the Flutter approvals screen (`apps/mobile/lib/features/workspace/approvals_screen.dart`) show the tool, every argument as a key/value row (redacted values marked), the shortened payload hash, and the time left; they send back the displayed hash when approving, disable Approve for expired or unbound approvals, and show "Approved by <user> · <time>" in history. The Android client sends the hash too but does not yet render the arguments.

## Later

Add delegated approvals, organization approval chains, policy-driven auto-approval, approval templates, mobile push approvals, emergency revocation, and simulations that explain why a policy allowed or blocked an action.