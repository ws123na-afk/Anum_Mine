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
| `GET /api/v1/executive-pa/briefing?lang=en or ar` | The daily briefing ([below](#daily-briefing)), built from real records only. |

Tools:

| Tool | Kind | Risk | Autonomy | Governed by |
| --- | --- | --- | --- | --- |
| `navigate` (building, floor, room, agent) | read | low | automatic | office registry (RLS) |
| `open_panel` | read | low | automatic | the panel's own permission |
| `summarise_workspace` | read | low | automatic | own workspace APIs, or an executive grant |
| `list_blocked_work` | read | low | automatic | tasks API |
| `list_pending_approvals` | read | low | automatic | approvals API (`approval:read`) |
| `daily_briefing` (`lang`) | read | low | automatic | the stores behind approvals, tasks, calendar, live meetings, agents and the model budget, each with the caller's own permissions; facts only ([Daily briefing](#daily-briefing)) |
| `propose_add_building` | read | low | automatic | office builder; proposal only, an owner's click on `POST /office/building-proposals/confirm` creates ([Assign work](ANUM_MINE_ASSIGN_WORK.md#add-a-building-by-asking)) |
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

## Contract with the data workstream (merged)
The desk uses the real executive API of `anum_api/executive_api.py` through `lib/executiveClient.ts` and `packages/contracts/src/executive.ts`; this PR defines no executive routes of its own.
- `GET /api/v1/executive/overview`: granted workspaces with only the granted scopes (`counts`, `tasks`, `approvals`, `cost`, `meetings`), `not_granted` items (members open them only by an explicit workspace switch), and `unavailable` items. A scope that was not granted shows "Not shared", never 0. A 403 shows the denied state, a 503 "not available" with the current workspace's own figures.
- Grants of the current workspace (owner only): `GET|POST /executive/grants`, `POST /grants/{id}/approve` with the `payload_hash` shown to the owner (the desk displays the hash and sends back exactly that value), `POST /grants/{id}/reject`, `PATCH /grants/{id}` (narrow only, `expected_revision`), `DELETE /grants/{id}`.
- `summarise_workspace` for another workspace calls the executive API's own grant-checked reader (`_granted_rows`, `_read_granted_workspace`), so the read goes through the narrow reader role, the workspace's own RLS context and an audit record there. This module has no grant store or data source of its own. The summary uses the contract's `ExecutiveSummary` shape for the current workspace too.

## The assistant dock
`ExecutivePanel.tsx` (also exported as `ExecutivePaDock`) is laid out as the right-side dock of the owner's concept renders: avatar glyph, a status that reads "Ready" only when the tools endpoint answered, one welcome line, quick actions, "You can also say" examples and the typed box with a microphone. It fits any container up to about 380 px wide, so the visual workstream can mount it in a campus slot (`<ExecutivePaDock onOpenView onLocation />`).
- Every quick action calls a real tool: Start tour (navigate per stop), Open calendar and Open meetings (`open_panel`), List blocked work, Pending approvals, Summarise this workspace. Buttons the concept shows without a backing tool (Show today's schedule items, Take me to my office, Show logistics activity, Show recent orders) are omitted. The persona name, photo, greeting facts and schedule items are not invented.
- The examples are built from the real first room of the tour and the real tools. Writes still use the proposal, click and hash gate.

## Daily briefing
"Brief me" (typed or spoken, English or Arabic: "brief me", "daily briefing", "what is my day", "أحطني علما", "الإحاطة اليومية") calls the read-only `daily_briefing` tool, or the dock's Brief me button does. The server (`anum_api/pa_briefing.py`) reads seven sources with the caller's own permissions in the caller's tenant and workspace, and composes nothing it did not read:

| Section | Source | Lines link to |
| --- | --- | --- |
| Approvals | pending approvals (`approval:read`) | Approvals |
| Blocked work | tasks waiting for approval or failed | the task |
| Tasks due | open tasks with a due time: overdue first, then due today | the task |
| Agent runs | tasks running now | the task |
| Meetings today | calendar events overlapping today (`calendar:read`), plus live meeting sessions in progress | Calendar, Meetings |
| Agents needing attention | agents that are paused, or have a failed task | Agents |
| Cost against budget | this month's model cost against the workspace budget (owners only, `organization:manage`) | Budgets |

Each section answers one of four states: `ok` (a fixed sentence with its counts, then up to five records), `empty` ("Nothing to report."), `not_connected` ("Not connected.": the source could not be read on this server, with no error text shown) or `restricted` ("Not available for your role."). The live meeting sessions need PostgreSQL; without it the meetings section still shows the calendar and says "Live meeting sessions: not connected." A source that returned 200 or more records says it shows the first 200 only.

No model is called. The sentence of every section is a template (English or Arabic) filled from the section's own counts, and `figures_match` checks that every figure in the rendered text is one of the section's facts before it is shown (the pattern of `voice_figures.py`); a sentence that fails is replaced by "Not connected.". If a later version rephrases with a model, it must pass the same check against the structured facts. Days are UTC days: the workspace has no time zone setting yet.

The briefing cannot decide anything. The tool is `read`, `low`, `automatic`; a voice or typed call returns the same read; no briefing line carries an approve, reject or confirm control, only a link that opens the source; every call is audited as `executive_pa.invoke` with the input mode. Voice cannot confirm approvals or writes, as everywhere else. Web: `PaBriefingCard.tsx` (a lazy chunk loaded when a briefing is asked for) shows loading, empty, not connected, restricted, failed (with Retry) and denied states in English and Arabic, right to left. A briefing line for a task opens that task; the other lines open the source screen (those screens do not take a record id yet).

## Guided tour
There is one guided tour. The dock's tour and the headquarters' "Guided visit" (Office tools) both read `GET /executive-pa/tour`: only the buildings, floors and rooms the person can access, in the office registry (building, floor level, room name order). A stop shows the registry's own facts (room, floor, building, kind, desks, capacity) and nothing else: no staff, no example roles, no demo disclaimer. Moving to a stop uses `hqCommands.navigateTo` (building name, floor id, room id), the same path the assistant uses, and says so if the move did not happen. The visit has loading, failed (Retry), empty and "registry not connected" states. Words and narration follow the page language: Arabic narration is spoken with `ar-SA`, English with `en-US`, and the panel mirrors right to left. The concept preview (`OfficeTourView`, invented rooms) has no guided visit. Visit history is kept in memory only.

## Add a building
`propose_add_building` returns a proposal (building, floors, template rooms, bounds, exactly what will be created) with a `payload_hash`, or `needs_info` with the one missing field (name or floors), or `invalid`. It creates nothing and cannot confirm. The web shows the proposal as a card in the conversation (assistant dock and ANUM Command) with Confirm and Cancel; Confirm calls the builder confirm route with the hash and `input_mode: "click"` (`workspace:manage`, one transaction, idempotent, audited). Voice and typed input cannot confirm. Details: [Assign work and add a building](ANUM_MINE_ASSIGN_WORK.md).

## Assigning work from the assistant
Inside the open headquarters, "ask {agent} to ..." and "start a task to ..." open the shared Assign work dialog pre-filled instead of the in-dock confirm card; the dialog's Assign button is the visual confirmation and uses `POST /office/assignments` (same governed path as `start_task`, with an idempotency key). Outside the headquarters the confirm card and the `start_task` and `delegate` tools work as before.

## Scene navigation
`publishNavigation` dispatches `anum:hq-navigate` with the headquarters' own target (`{building, floor, room, agent}`, ids accepted) and keeps the PA's location in `sessionStorage` (`anum.hq.pendingNavigation`). The visual workstream's `navigateTo` extension point was not available yet; connect it to that event (or replace `publishNavigation`). No scene file was edited.

## Not finished, and why
- The scene does not yet react to PA navigation (waits for the visual branch).
- `schedule_meeting` creates a live meeting session; the meeting API has no start time, so nothing is scheduled for a time.
- Writes are not idempotent on retry (a double click is prevented in the UI only).
- Voice input reuses browser recognition; there is no spoken approval at all, by design.

## Tests
`services/api/tests/test_executive_pa.py`, `services/api/tests/test_pa_briefing.py` (every source, states, tenant scope, Arabic, the figure guard, voice and typed, no decision tool), `apps/web/test/executivePa.test.ts`, `apps/web/test/guidedExecutiveTour.test.ts`, `apps/web/e2e/executive.spec.ts` (journey, honest states, Arabic, five viewport sizes without overflow), `apps/web/e2e/pa-briefing.spec.ts` (typed and spoken briefing, links, states, Arabic, five sizes) and `apps/web/e2e/hq-guided-visit.spec.ts` (the visit on real rooms, states, Arabic narration, five sizes). The briefing adds no SQL and no migration: it reads existing stores, so no new PostgreSQL test was needed.
