# ANUM MINE headquarters: assign work in place and add a building by asking

Status: built in this change (API, contract, web, tests). No migration.

Two things the owner asked for. First, assigning work to an agent must not take the person to the Agents page: the details are asked in a dialog on the same screen. Second, "add a building called X with 3 floors" typed or spoken in the assistant or in ANUM Command must produce a proposal that an owner confirms with a click.

## Code

| Part | File |
|---|---|
| Assign route (idempotent, audited) | `services/api/anum_api/office_assign_api.py` |
| Building plan (pure) | `services/api/anum_api/office_building_plan.py` |
| Building proposal and confirm routes | `services/api/anum_api/office_building_api.py` |
| Assistant tool `propose_add_building` | `services/api/anum_api/executive_pa.py` |
| Contract | `packages/contracts/src/officeAssign.ts` |
| Web: dialog, host, task side panel | `apps/web/src/hq-assign/` (`AssignDialog`, `AssignHost`, `TaskPanel`) |
| Web: proposal card and Command panel | `apps/web/src/hq-command/` |
| Web: pure helpers and clients | `apps/web/src/lib/hqAssign.ts`, `lib/hqBuildingCommand.ts`, messages `hqAssign.en.ts` / `.ar.ts` |
| Tests | `tests/test_office_building_plan.py`, `tests/test_postgres_office_assign_building.py`, `test/hqAssign.test.ts`, `test/hqBuildingCommand.test.ts`, `e2e/hq-assign-work.spec.ts` |

## Assign work

### How it was before

The office had no assign button. Cards offered "Open agent" (`#agents`), the flow dock's empty state and some next steps went to `#tasks`, and the assistant could only start a task through its own confirm card. Assigning meant leaving the office for the Agents or Tasks page.

### Where the dialog opens from

One `AssignHost`, mounted by the headquarters, listens for a window event. Any place calls `openAssign({ agentId?, roomId?, title?, instructions?, source })`:

| Place | Pre-filled | `source` |
|---|---|---|
| Agent card (side panel of an agent or desk) | the agent | `desk` or `agent_card` |
| Room panel, "Assign work in this office" | the agent seated in that room (first active one); the agent list shows who sits there | `room` |
| Team panel card ("Assign work to {name}") | the agent and the room | `team_panel` |
| Live flow dock: card with an owner agent, and the empty state | the agent, or none | `flow_dock` |
| Assistant: "ask {agent} to ...", "assign ... to {agent}", "start a task to ..." | agent (if one matches by name), title and instructions | `executive_pa` |

When the host is not mounted (the assistant on the Executive desk page) the assistant keeps its previous confirm card. A stuck task's next step ("Open task", "Retry") now opens the task in a side panel beside the office; other next steps still go to the page that owns them (Approvals, Agents, Commerce).

### The dialog

Fields: agent (searchable, paged, shows who sits in the room, paused agents marked), title, instructions, expected result (optional), linked context (optional), priority, due time (optional), "start it now". The task model has no fields for expected result or linked context, so they are appended to the prompt as "Expected result:" and "Context:" sections. This is said in the hint under the field.

Before sending the dialog shows what the agent can do, read from the agent record and the tool catalogue: for read, write and external tools whether it acts by itself, asks first or is off (never looser than the workspace floor), the tools it holds, the high risk ones, tools no longer provided, and that an approval stops the run and only a person can decide it. If the catalogue cannot be read the dialog says tool risk is not shown.

Assign to: **An agent** (a task, as below) or **A person** (a Team work item through `POST /api/v1/work-items`, the Team page's own route and `draftBody`; only people the API marks assignable are offered; states: people loading, error with retry, no permission, nobody assignable with a link to Members, refusal keeps the text, done with a full page link to Team).

States: agents loading, error with retry, no agents (link to Agents), paused ("turn it on first", with a Turn it on button when the person may edit the agent), no permission or agent not shared (who to ask), agent gone, offline (sending disabled), validation (per field and a summary), server refusal (text is kept; a refusal because the agent was paused offers the fix), run not started (task exists, reason shown), waiting for approval (Review the approval opens it in a drawer in the office), done. Escape closes it and focus returns to the opener. The layout is a bottom sheet under 580 px. English and Arabic with right to left.

### The route

`POST /api/v1/office/assignments` with a required `Idempotency-Key` header. Body: `agent_id`, `title`, `instructions`, `expected_result?`, `context_note?`, `priority`, `due_at?`, `run`, `source`.

It adds no rules of its own. It calls `main.create_task` (the body of `POST /tasks`: `task:create`, the agent must exist and be active, plan capacity, the `task.created` event) and then `main.start_task_run` (the body of `POST /tasks/{id}/run`: `task:run`, run lock, monthly budget, agent profile snapshot, ToolPolicy, autonomy, approvals). A run that needs approval stops at `waiting_approval` and the answer includes the approval; the route cannot approve.

- Idempotency: the key is claimed in `office_create_keys` (kind `task`) in the same transaction as the task, so one key never makes two tasks. The same key and the same task answers 200 `replayed: true`; the same key with a different task is 409. A refused attempt keeps no claim. The key covers agent, title, composed prompt, priority, due time and `run`, not `source`.
- The run starts after the task is committed. If it cannot start (no run permission is checked first, so this is for example the budget) the task exists and the answer has `run_error`; a replay of the key tries the run again while the task is still `created`.
- Audit: `office.assignment.create` with ids, priority, source and flags, never the instructions. The usual `task.created` event feeds the live flow, so the task appears in the agent's office; the web re-reads the flow at once.
- The database work runs in a worker thread with its own loop, not on the server's loop.
- PostgreSQL only (503 otherwise).

After assigning, the flow, the desks and the open side panel read again at once, and when the agent's desk is on the open floor its workstation panel opens beside the scene with the new task in its work list. A toast links to the task once the dialog is closed (while it is open the dialog shows the same confirmation with its own "Open the task", and on a phone, where the dialog is a bottom sheet, the toast would cover its Close button); it goes after 12 seconds on screen. "Open the task" shows it in a drawer (see below). It never navigates unless the person clicks "Open full page".

## Work inside the office

Owner, 2 November 2026: "why any task I want to give goes back to other modules, not live inside ANUM (the HQ)". Every piece of work given or followed from the office now opens in a drawer over the scene (`src/hq-assign/WorkDrawer.tsx`), one at a time, at the inline end (full width under 580 px). Each drawer is a lazy chunk; Escape or the close button closes it and focus goes back to what opened it, or to the scene when that is gone. Each has an "Open full page" link to the module for the rare deep task; it is never the default. English and Arabic (right to left).

| Drawer | What it shows and does | Same code and rules as |
|---|---|---|
| Task (`TaskPanel.tsx`) | Title, status, instructions, the latest run's phase, result and steps; read again on every workspace event (`live`); Run, Retry, Resume, Cancel by `taskActions` and `roleAllows`; a task waiting for a person offers Review the approval; Show history. A history run record opens the task it belongs to | Tasks page (`lib/tasks.ts`, `lib/tasksApi.ts`) |
| Approval or approvals (`ApprovalPanel.tsx`) | One approval, the pending ones of a task, or the whole pending queue (paged). The cards are the Approvals page's own `PendingApproval` (exact tool, target, every argument, payload hash, routing, progress, expiry, reason field); decided ones show the decision, arguments and hash | Approvals page: `api.approveTask` / `api.rejectTask` send back the payload hash shown; the server keeps routing, the two person rule, expiry and audit |
| Meeting (`MeetingPanel.tsx`) | The Meetings module's `MeetingRoomPanel` (join, leave, hand, agenda, transcript, notes, summary), or its picker without an id, with Start a meeting (the office's own agent meetings panel) | Meetings module |
| Agent work (`AgentWorkPanel.tsx`) | The agent record and the tasks run as it, newest first, paged (`GET /api/v1/tasks?agent_id=`), each opening its task drawer; Assign work | Tasks list route and permission |

States in every drawer: loading, empty with a next action, error with Try again, permission denied (who can), record gone. No new API route.

### Places that used to leave the office

| Place | Before | Now |
|---|---|---|
| Desk work (`HqWorkstationWork.tsx`): a task | link to `#tasks` | Open task: task drawer |
| Desk work: approvals | link to `#approvals` | Review here per approval: approval drawer |
| Agent card (`HqPanels.tsx`) | only Open agent profile (`#agents/team/...`) | Work and results: agent drawer; the profile link stays as the full page |
| Team panel card (`HqTeamPanel.tsx`) | Open agent: `#agents` | Work and results of {name}: agent drawer; the member's current task opens its drawer |
| Room with a live meeting (`HqPanels.tsx`, `HqRoomModule.tsx`) | `#office/meeting/<id>` page | Open the meeting here, and the module link: meeting drawer (the href stays for a new tab) |
| Meeting room's module link | `#meetings` | meeting picker drawer |
| Office room whose module is Tasks | `#tasks` | the live flow dock filtered to that room |
| Live flow dock: Decide the approval | `#approvals` | approvals drawer of that task |
| Live flow dock: Resume the agent | `#agents` | agent drawer |
| History record (`HqHistoryDetail.tsx`): task, run, approval, meeting | only Open in the full page | Open the ... here: its drawer; the full page button stays |
| History empty state | Open tasks: `#tasks` | Assign work: the assign dialog |
| Assign dialog done: waiting for approval | `#approvals` | Review the approval: approval drawer |
| Missing roles proposal done (`ProposalDialog.tsx`) | Open Agents: `#agents` | Show the team: the office's team panel |
| Assistant dock (`main.tsx`, `ExecutivePanel.tsx`): open a task, Approvals, Meetings, Tasks, Agents, a briefing approval or meeting line | the module page | task drawer, approvals drawer, meeting picker, live flow, team panel, that approval or meeting |
| Meeting room links to a created task or linked work (`MeetingRoomParts.tsx`, `MeetingSummaryParts.tsx`) | `#tasks`, `#approvals` | in the office: that task or approval drawer (on the Meetings page they still navigate) |

Kept as full page links on purpose (setup, not work): no agents yet (Agents), nobody assignable (Members), Documents, calendar quick move, office staffing connections and the Executive desk, the workstation evidence overlay's agent profile.

Extension: any code calls `openHqPanel({ kind: 'task' | 'run' | 'approval' | 'approvals' | 'meeting' | 'agent', ... })` or `openViewInHq(view)` from `lib/hqWorkPanels.ts`; both return false when no headquarters is mounted, and the caller navigates as before. Ids are checked (`validPanel`) before anything opens. Tests: `test/hqWorkPanels.test.ts`, `e2e/hq-stay-inside.spec.ts` (assign from a desk and see it on the desk and in the flow, task drawer with Escape and focus, approval decided in place with the payload hash, flow dock approvals, agent work log, a meeting joined in place, five sizes in English and Arabic with axe), `e2e/hq-live-flow.spec.ts`.

## Add a building by asking

### Flow

1. The person types or says "add a building called Tyre Pulse with 3 floors" in the assistant dock or in ANUM Command. The parser (English and Arabic, digits and number words) only recognises the request; it never creates.
2. If the name or the floor count is missing, one short question is asked ("What is the building called?" / "How many floors does X have?") and the next reply fills exactly that field.
3. The read only tool `propose_add_building` (risk low, automatic, `task:read`) returns a proposal: the building, floors, template rooms with ids and placements, bounds, totals and a `payload_hash` bound to tenant, workspace, user and content. Nothing is stored. Another user, another workspace or a changed name gives a different hash.
4. The proposal card in the conversation shows exactly what will be created and has Confirm and Cancel. Cancel creates nothing.
5. Confirm sends `POST /api/v1/office/building-proposals/confirm` with the shown hash and `input_mode: "click"`. Voice and typed modes are refused with 403 before anything runs; no tool calls this route (a spoken sentence cannot confirm).

### What is created

One building (time zone of the existing buildings, else Asia/Dubai), N floors (levels 0 to N-1, "Ground floor", "Floor 1" and so on, 60 by 40 metres), and per floor the fixed room templates: reception and open office on the ground floor, open office and meeting room above. Rooms are placed by the builder's own free spot search. No desks and no agents. Up to 20 floors. A building whose name already exists (any case) is refused.

### Governance

- Confirm needs `workspace:manage`; the proposal is readable with `task:read` and says whether the reader can confirm.
- The server recomputes the proposal from the current records and compares the hash in constant time; a difference is 409 ("review again").
- Everything is created by the builder store in one transaction, all or nothing, with the builder's own audit records (`office.building.create`, `office.floor.create`, `office.room.create`) plus `office.building.create_from_proposal` (hash, input mode, counts), and `office.layout_changed` events.
- Idempotency: an optional `Idempotency-Key` makes a retry return the building already made (200 `replayed: true`); the key used for another building is 409. The web card sends one key per card.
- Every assistant call, including this tool, is audited as `executive_pa.invoke`.

## For the person using it

Assign: press **Assign work** on a room, team card, agent card or flow item; the agent is already chosen; write a title and instructions (optionally expected result, linked context, priority, due time); read **What this agent can do**; press **Assign and start**; **Open the task** shows it beside the office. A paused agent says "turn it on first". Add a building: type or say "add a building called X with N floors" in the assistant or Command; answer the one question if asked; an owner presses **Confirm and add the building**. In-app help articles (`docs/help/office*.md`, `executive*.md`) are shared with the mobile app and were not changed here; add these steps to them in a change that includes the mobile help review.

## Not done, said plainly

- Linked project: tasks have no project or context field. "Linked context" is free text inside the task prompt.
- Expected result is also prompt text, not a stored field, so it is not shown separately in Tasks.
- The Command page recognises the building request itself and shows the card; other Command sentences still go to the normal plan preview. The Command plan screen has no "assign to an agent" step.
- The assignment key is generated per dialog attempt in the browser; two different browsers retrying the same text make two tasks.
- Floors and rooms use fixed templates; a custom layout is edited afterwards in the office builder.
- The assistant opens the dialog only while the headquarters is open. On the Executive desk page it keeps its confirm card.
- Work given to a person is a Team work item: it has no drawer in the office yet (its done view links to Team), and people have no desks in the scene.
- A drawer replaced by another (approval, then its task) returns focus to the scene, not to the first opener.
- The 3D desk screens are drawn by the renderer from presence; this change re-reads presence after an assignment but does not change how screens draw.
