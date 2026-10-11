# API Contracts

ANUM APIs should be versioned, typed, and stable enough for web, desktop, Android, and automation clients to share. FastAPI should expose OpenAPI documentation, but the repository should also keep human-readable contract rules.

## API Styles

- REST for standard request/response resources.
- Server-sent events or WebSocket channels for realtime task updates.
- Webhooks for inbound integration events.
- Internal worker APIs only where process boundaries require them.

## Resource Patterns

Resource paths should be tenant-aware through authenticated context, not by trusting client-supplied tenant IDs alone. Example shapes:

```text
POST /v1/tasks
GET /v1/tasks?status=&q=&created_by=&created_after=&created_before=&archived=&limit=&cursor=
GET /v1/tasks/summary
GET /v1/tasks/{task_id}
PUT /v1/tasks/{task_id}
GET /v1/tasks/{task_id}/latest-run
GET /v1/tasks/{task_id}/runs
GET /v1/tasks/{task_id}/events
GET /v1/tasks/{task_id}/export?format=json|markdown
POST /v1/tasks/{task_id}/run
POST /v1/tasks/{task_id}/retry
POST /v1/tasks/{task_id}/cancel
POST /v1/tasks/{task_id}/archive
POST /v1/tasks/{task_id}/unarchive
POST /v1/tasks/bulk                        (see tasks.md)
POST /v1/approvals/{approval_id}/approve   (body: {"payload_hash": "..."}, see approvals-and-risk.md)
POST /v1/approvals/{approval_id}/reject
GET /v1/me/workspace-memberships           (the caller's active memberships in their tenant, see identity.md)
GET /v1/agent-runs/{run_id}/events
```

## Contract Rules

Requests should use explicit JSON schemas, idempotency keys for mutating external actions, pagination for lists, stable error codes, and correlation IDs. Responses should avoid leaking internal provider payloads or secrets.

List paging today:

- `GET /api/v1/tasks?limit=&cursor=` returns tasks newest first (`limit` 1-100, default 50). `cursor` is opaque to clients: pass the `id` of the last task on the previous page. The server pages by keyset on `(created_at, id)` (index `ix_tasks_scope_created`, migration `0017_tasks_scope_created`); a cursor that names no task in the caller's workspace answers `400`, never an empty page. The same list takes `status` (repeatable), `q`, `created_by` (`me` or a user id), `created_after`/`created_before` and `archived`, and archived tasks are left out unless `archived=true` ([Tasks](tasks.md#list-filters)).
- `GET /api/v1/tasks/{id}/runs` (`limit` 1-100, default 20) and `GET /api/v1/tasks/{id}/events` (`limit` 1-200, default 50) page the same way, newest first; runs use `ix_agent_runs_task_created` (migration `0026_task_archive`).
- `GET /api/v1/memories?limit=` returns at most `limit` notes (1-500, default 200): the newest matches, still ordered oldest first.

## Error Model

Errors should distinguish validation failure, authentication failure, authorization failure, missing resource, conflict, rate limit, policy block, approval required, upstream failure, and internal failure.

Every error uses one envelope, `{"error": {"code", "message", "correlation_id", "details"}}`, with the codes in `services/api/anum_api/errors.py` (`model_budget_exceeded` is `402`; `account_pending` is the `403` for a signed-in account without an organization, [Identity: Pending accounts](identity.md#pending-accounts)). Clients keep the status and code instead of flattening them to text: the web client throws `ApiError` (`apps/web/src/lib/errors.ts`) and Flutter `ApiException` (`code`, `isBudgetExceeded`, `isPermissionDenied`), so screens can tell a permission refusal (`403`) or a used-up model budget (`402`) from other failures and show the API's own message.

## Shared Types

`packages/contracts` holds the camelCase types the web and desktop clients use. `src/admin.ts` covers workspace members and invitations (`WorkspaceMember`, `WorkspaceInvitation`, `CreatedInvitation`, `AcceptedInvitation`), monthly model budgets (`ModelBudgetOverview`, `ModelBudgetScopeView`, `ModelBudgetLimits`) and `ApiErrorCode`; `apps/web/src/lib/admin.ts` maps the API's snake_case JSON to them. `src/index.ts` also has `TaskSummary`, `TaskBulkResult` and, on `Task`, `archivedAt`/`archivedBy` and, on `AgentRun`, `createdAt` and `phase` (the checkpoint phase) for the Tasks module ([Tasks](tasks.md)); `apps/web/src/lib/tasksApi.ts` maps them. It also has `CallerMembership` (`GET /api/v1/me/workspace-memberships`) and, on `Approval`, `requiredApprovals` and `approvers` (`ApprovalApprover`) for approval chains; `apps/web/src/lib/api.ts` maps them. `src/account.ts` holds the caller's own settings and data as the API sends them (snake_case, like `src/governance.ts`): `PrivacySummary`, `PrivacyExport` (`anum.privacy-export/v1`), `PrivacyDeleteRequest` and `PrivacyDeleteResult` ([Privacy](privacy.md)), `UserPreferencesView` and `UserPreferencesUpdate` (`/api/v1/me/preferences`), `WorkspaceModelConfig`, `WorkspaceModelConfigWrite` and `ModelConnectionTest` (`/api/v1/model-config`, with the `anthropic` routing fields), and `RetrievalSourceName`, the camelCase form the web parses from `GET /api/v1/retrieval/sources`. `apps/web/src/lib/privacy.ts`, `chatMemory.ts`, `modelConfig.ts` and `retrieval.ts` use these types instead of their own copies.

## Now

Define initial REST contracts for auth context, workspaces, tasks, runs, events, approvals, memories, and tool proposals.

## Later

Add public API keys, SDK generation, webhook management, partner APIs, GraphQL only if a clear product need appears, and compatibility guarantees for external developers.
