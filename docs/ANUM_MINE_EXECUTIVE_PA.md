# ANUM MINE executive PA and executive desk (master plan F10)

## What it is
A permissioned tool interface for the headquarters assistant, and a web desk (`#executive`, sidebar "Executive desk") that lists the workspaces the person may see, kept apart by kind, with only the minimum summary each owner granted.

It replaces the old demonstration commands of the office preview (`lib/officeTourNavigation.ts`, which only moved the camera and never called a tool). Those files stay for the concept preview; the live assistant uses the tools below.

## Server: `anum_api/executive_pa.py`
Routes (all need a signed-in member; every call is tenant and workspace scoped):

| Route | Purpose |
| --- | --- |
| `GET /api/v1/executive-pa/tools` | The catalogue: kind, risk, autonomy, the governed path, the permissions, and whether this person may use it. |
| `POST /api/v1/executive-pa/invoke` | Run one tool: `{tool, arguments, input_mode: typed or voice or click, confirm_hash?}`. |
| `GET /api/v1/executive-pa/tour` | Guided tour stops generated from the buildings, floors and rooms the person can access. |

Tools:

| Tool | Kind | Risk | Autonomy | Governed by |
| --- | --- | --- | --- | --- |
| `navigate` (building, floor, room, agent) | read | low | automatic | office registry (RLS) |
| `open_panel` | read | low | automatic | the panel's own permission |
| `summarise_workspace` | read | low | automatic | own workspace APIs, or an executive grant |
| `list_blocked_work` | read | low | automatic | tasks API |
| `list_pending_approvals` | read | low | automatic | approvals API (`approval:read`) |
| `start_task` | write | medium | confirm each time | `POST /tasks` then run: agent profile, ToolPolicy, autonomy, approvals |
| `delegate` | write | medium | confirm each time | same, with `agent_id` |
| `schedule_meeting` | write | medium | confirm each time | `POST /agent-meetings` (`workspace:manage`, PostgreSQL) |

Rules that make it safe:
- A write never runs on the first call. It returns `needs_confirmation` with a `payload_hash` bound to tenant, workspace, user, tool and arguments. Only a second call with that hash **and** `input_mode: "click"` runs it. `voice` and `typed` can never confirm (`needs_visual_confirmation`), so a spoken sentence cannot bypass the visual gate. A changed argument or another user gives `stale_confirmation`.
- Writes call the same code as the public routes, so a person can never do more through the PA than directly. If a run needs approval it stops at `waiting_approval` and the PA reports `approval_pending`. **There is no tool that approves or rejects**; an unknown tool such as `approve` is refused (422) and audited.
- Every invocation writes an audit record `executive_pa.invoke` (tool, input mode, outcome, payload hash, ids; never prompt text). Refusals are audited too. No migration was needed: the PA reuses the audit log.
- Tool outcomes use HTTP 200 with a `status` (`ok`, `done`, `approval_pending`, `denied`, `not_found`, `not_connected`, `not_granted`, `stale_confirmation`, `failed`) so refusals are audited in the same transaction; malformed input is 422.
- Other workspaces: `summarise_workspace` for a workspace other than the current one needs an executive grant. The grant source is `set_executive_data_source(source)` (protocol `ExecutiveDataSource`: `grant_for`, `summary`). Without a source the answer is `not_connected`; without a grant it is `not_granted`; only the keys `tasks_total`, `tasks_blocked`, `approvals_pending`, `cost_usd_month`, `meetings_active` are ever returned.

## Web
- `lib/executivePa.ts`: typed clients, the sentence parser (English and Arabic words; it only proposes, and never understands approve or reject), the overview reader (a summary is kept only for an own or granted workspace), `needsSwitch`, and `publishNavigation`.
- `ExecutivePanel.tsx`: typed and voice input (reuses `createPushToTalk` from the voice stack), a log, the confirm card, the tool list with risk and "asks first", and the guided tour.
- `ExecutiveCommandCenter.tsx`: the desk. Workspaces grouped Personal, Employment, ANUM OS, Businesses; "Not granted" and "Not connected" states; grant, change and revoke per workspace (a grant that needs approval says so and links to Approvals); an explicit "Switch to ..." before confidential content; empty, loading, error and owners-only states; English and Arabic.

## Contract with the data workstream
The desk reads `GET /api/v1/executive/overview` (`workspaces[]` with `workspace_id`, `tenant_id`, `name`, `kind`, `grant_state: own|granted|not_granted|not_connected`, `grant_id`, `summary`) and manages `GET|POST /api/v1/executive/grants`, `PATCH|DELETE /api/v1/executive/grants/{id}` (`workspace_id`, `scopes: tasks|approvals|costs|meetings`; a grant that waits for an owner answers `status: pending_approval` with `approval_id`). The data branch had not landed when this was written, so these shapes are this module's own minimal definition; reconcile with `packages/contracts` when it merges. A 404, 501 or 503 from the overview shows "not connected" and the current workspace from the PA's own summary.

## Scene navigation
`publishNavigation` dispatches `anum:hq-navigate` and keeps the target in `sessionStorage` (`anum.hq.pendingNavigation`). The visual workstream's `navigateTo` extension point was not available yet; connect it to that event (or replace `publishNavigation`). No scene file was edited.

## Not finished, and why
- The scene does not yet react to PA navigation (waits for the visual branch).
- Grants, the overview and the cost and meeting figures of other workspaces depend on the data branch.
- `schedule_meeting` creates a live meeting session; the meeting API has no start time, so nothing is scheduled for a time.
- Writes are not idempotent on retry (a double click is prevented in the UI only).
- Voice input reuses browser recognition; there is no spoken approval at all, by design.

## Tests
`services/api/tests/test_executive_pa.py`, `apps/web/test/executivePa.test.ts`, `apps/web/e2e/executive.spec.ts` (journey, honest states, Arabic, five viewport sizes without overflow).
