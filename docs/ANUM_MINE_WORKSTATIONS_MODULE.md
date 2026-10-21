# ANUM MINE Live Employee Workstations

## What this complete UI module does
It adds an authenticated **Live Employee Workstations** action to the full-screen `#office-preview` route. The operations panel loads the **existing workspace Org Chart agents and open tasks** through the existing authenticated API client, not a newly invented privileged endpoint. Employees are searchable by agent name, assigned department and task. Filter by department and status. Select an employee to inspect profile status, task titles, task IDs, data provenance and UTC-derived observation time. The view refreshes periodically while visible and on visibility changes. It tracks partial source failures, scope changes, request sequence and stale snapshots.

## Code ownership
- `src/lib/officePresence.ts` pure presence projection
- `src/lib/officeWorkstations.ts` authenticated data adapter, scope validation, pagination safeguards, department grouping
- `src/OfficeWorkstations.tsx` complete directory, filter, drill-down, errors and stale indicators
- `src/officeWorkstations.css` responsive, accessible styling using ANUM typography tokens
- `test/officePresence.test.ts` baseline status tests
- `test/officeWorkstations.test.ts` tenant isolation and data completeness tests
- `src/OfficeTourView.tsx` opens panel inside the existing full-screen office

## Sources and security
The loader calls `loadOrgSources(api.apiRequest)`, sharing the Org Chart's paginated, authenticated, workspace-scoped APIs. Every agent/task is additionally filtered by returned `tenant_id` and `workspace_id`. Workspace changes during loading cause the response to be rejected. No agent run, approval or external action is triggered from this panel.

Statuses represent **task evidence**, not human physical presence:
- working: associated running task
- queued: created/queued task, not necessarily executing
- waiting_approval: a task in approval state
- blocked: explicit blocked task state when exposed
- paused: agent profile inactive/paused
- idle: no open work in a complete snapshot
- unknown: insufficient data, including failed open-task requests
- stale: timestamp older than 90 seconds, replacing the displayed status label

The underlying Org Chart API loader caps task pagination at 500 records; a snapshot at this cap is considered incomplete. Partial errors are disclosed, not silently discarded.

## Known limitations and production gates
- It is a **production-oriented frontend read module**, but full production acceptance depends on CI, service integration tests and owner review.
- There is **no persisted room-to-agent mapping** or physically accurate desk placement yet.
- Agent profile status mapping needs alignment with every possible profile state before release, rather than classifying all non-active statuses as paused.
- Polling every 30 seconds is present; resilient SSE event synchronization is a later enhancement.
- Task IDs are not clickable execution controls. Users must use existing Tasks/Agents pages to act with approvals.
- No real-time meeting bot or fabricated transcript is included.

## Acceptance tests
1. Sign into two workspaces; only see permitted agents and task evidence of current workspace.
2. Search a name and task title; filter department and status; inspect a selected profile.
3. Refresh manually and after tab visibility restoration. Simulate expired snapshot and failed open-task source.
4. Verify no false idle if task results missing or 500-item pagination ceiling reached.
5. Verify keyboard focus, screen reader labels, desktop/tablet overflow and responsive layout.
6. Run `pnpm --filter @anum/web check`, `pnpm --filter @anum/web build` and targeted browser integration.
7. Keep draft PR and never merge into main until acceptance.
