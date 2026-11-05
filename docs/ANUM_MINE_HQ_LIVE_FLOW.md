# ANUM MINE headquarters: live flow, stuck work and office teams

Status: built in this change (API, contract, web, tests). Web and API only; no migration. Handler placement of orders and unowned leads was added by [agent provisioning](ANUM_MINE_AGENT_PROVISIONING.md) (migration 0106).

The headquarters shows work moving through the offices that handle it, shows where it is stuck, and shows the real team of every office. Everything on screen comes from a stored record. A marker moves only because the server's answer moved an item. Nothing wanders for decoration.

## Code

| Part | File |
|---|---|
| Flow and stuck rules (pure), roster builder | `services/api/anum_api/office_flow.py` |
| Routes | `services/api/anum_api/office_flow_api.py` |
| Thresholds | `services/api/anum_api/settings.py` (`ANUM_OFFICE_FLOW_*`) |
| Contract | `packages/contracts/src/officeFlow.ts` |
| Client, diff rules, words | `apps/web/src/lib/officeFlow.ts`, `lib/hqFlowProject.ts`, `useOfficeFlow.ts` |
| UI | `HqFlowDock.tsx`, `HqFlowOverlay.tsx`, `HqTeamPanel.tsx`, `hqFlow.css`, messages `hqFlow.en.ts` / `hqFlow.ar.ts` |
| Tests | `tests/test_office_flow.py`, `tests/test_postgres_office_flow.py`, `test/officeFlow.test.ts`, `e2e/hq-live-flow.spec.ts` |

## Records used

| Need | Source | Notes |
|---|---|---|
| Work items: tasks | `tasks`, newest `agent_runs` per task, `approvals` | Open tasks, tasks failed in the last 24 h, tasks completed in the last 30 min. Archived and cancelled tasks are left out. |
| Work items: leads | `sales_leads` | Open leads and leads closed in the last 30 min. Needs `whatsapp:read`. |
| Work items: orders | `commerce_orders` (and one `commerce_connectors` row) | Open orders of the last 30 days and orders shipped in the last 30 min. Needs `commerce:read`. |
| Where an item sits | `office_desks` joined to `office_rooms` and `office_floors` | A task sits in the office of its agent's desk. A lead sits in the office of the agent named in `assigned_to`. |
| Who works in an office | `agent_profiles` (through the agent store, so private agents stay private) joined to desks | Role is the agent's `department` and `description`. |
| Skills | `agent_skill_loadout.skill_states` (the same function as `GET /agents/{id}/skills`) | Loaded for teams of up to 30 agents. |
| Agent state | `office_presence.derive_presence` | The same states as the presence stream. |
| History of an item | `domain_events` (tasks), `sales_lead_events` (leads) | Only an allow-list of ids and statuses is returned, never payload text. |
| Live updates | the existing event stream `GET /api/v1/events/stream` | An event of type `task.`, `agent_run.`, `approval.`, `agent_meeting.`, `office.` or `work_item.` only makes the browser read `/office/flow` again. The state always comes from the records. |

## Endpoints

All under `/api/v1/office`, read only, `task:read`, one RLS scoped session per call, PostgreSQL only (the memory backend answers 503, never fake data).

- `GET /flow`: `{version, workspace_id, as_of, cursor, thresholds, sources, items, rooms, totals, truncated}`. At most 500 items; `truncated` says so. `sources` says `connected`, `no_access` or `not_connected` for tasks, approvals, leads and orders. A source that is not `connected` shows as such in the dock; its work is simply absent.
- `GET /flow/items/{task|lead|order}/{id}/trail`: stored events of the item. Orders answer `source: "not_recorded"` because synced orders carry no local history.
- `GET /team?room_id=|floor_id=|building_id=`: members, vacant desks, hidden assignments (desks of agents the viewer cannot see, counted, never named), counts, agents without a workstation, and skills when the scope has at most 30 agents.

## Item model

Each item has `kind`, `stage`, `status`, `location` (building, floor, room, desk, agent) or `location_reason`, `stuck` and `waiting`.

Stages: `arrived` (created or queued, not started), `in_office` (a running run, or an open lead that has an owner with a desk), `waiting_human` (a live pending approval), `blocked` (failed, expired approval, paused agent), `done` (finished in the last 30 minutes, then gone).

`location` is null with a stated reason when no record places the item: `plain_runtime` (task without an agent), `agent_has_no_desk`, `no_owner`, `owner_is_person` (the lead belongs to a person, who has no workstation), `no_order_office` (no confirmed orders handler; with one, the order sits in the handler's office and `agent_has_no_desk` applies if the handler has no desk). Such items are listed as "Not in an office yet" and never drawn in a room.

## What stuck means

An item is stuck only when one rule below matches. The answer carries the rule `code`, `severity`, `since`, `age_seconds`, `threshold_seconds`, the rule's own `facts` and the next `action`. Thresholds are configurable (environment variable, default):

| Code | Severity | Rule | Threshold |
|---|---|---|---|
| `approval_waiting` | warning | Task `waiting_approval`, a pending, unexpired approval older than the limit. Younger approvals are shown as "waiting for a person", not stuck. | `ANUM_OFFICE_FLOW_APPROVAL_WAIT_MINUTES` 240 |
| `approval_expired` | critical | Task `waiting_approval` and its approval expired. | none |
| `approval_missing` | critical | Task `waiting_approval` and no approval row. | none |
| `run_failed` | critical | Task `failed` within the window; the run's recorded result is shown (300 characters). | `ANUM_OFFICE_FLOW_FAILED_WINDOW_HOURS` 24 |
| `run_silent` | warning | Running task whose running run has not updated for the limit. | `ANUM_OFFICE_FLOW_RUN_SILENT_MINUTES` 60 |
| `queued_too_long` | warning | Created or queued task not started for the limit. | `ANUM_OFFICE_FLOW_QUEUE_WAIT_MINUTES` 30 |
| `agent_paused` | warning | Open task of a paused agent. | none |
| `lead_unassigned` | warning | Lead `new` with no `assigned_to` for the limit. | `ANUM_OFFICE_FLOW_LEAD_UNASSIGNED_MINUTES` 240 |
| `lead_idle` | warning | Open lead unchanged for the limit. | `ANUM_OFFICE_FLOW_LEAD_IDLE_HOURS` 72 |
| `order_ship_overdue` | critical | Open order past `ship_by`. | `ANUM_OFFICE_FLOW_ORDER_SHIP_GRACE_MINUTES` 0 |

Also configurable: `ANUM_OFFICE_FLOW_RECENT_DONE_MINUTES` (30), how long finished work stays visible. A running task without a running run proves nothing and stays `arrived`. The dock lists the active thresholds under "Stuck rules".

Next actions open existing views: decide an approval (`#approvals`), open or retry a task (`#tasks`), resume an agent (`#agents`), assign a lead owner or open a lead (`#marketing`), open an order (`#commerce`).

## What moves in the scene

- Each office on the open floor gets a badge with its real counts: open items, waiting for a person, stuck (red ring for critical, amber for warning; an icon and number always accompany the colour). Selecting it opens the dock filtered to that office.
- `diffFlow` compares the previous answer with the new one. An arrival, a change of office, a new stuck reason or a finish produces one marker for five seconds. A marker travels only between two real offices (old and new `location`); an arrival or a lead that just got an owner pulses at its office because no record says where it came from. The first answer after opening produces no markers: nothing from the past is replayed.
- Reduced motion: markers do not travel and do not pulse; the arrival shows for five seconds. A polite live region announces each change in words.
- No WebGL: the existing 2D floor plan renderer is used and the same badges and dock work.
- Stuck highlights are static. Nothing animates while nothing changes.

## Connection and failure states

- Live: the event stream triggers a re-read within 400 ms; a 20 s timer is a safety net.
- Stream down: the bar says "Checking every 8 s" and re-reads every 8 s. The bar is a thin strip in the page flow under the quick views (never over the scene); its list opens as a sheet that rises from the bar. Its chip shows the real time of the last read ("Updated hh:mm:ss") and says Stale after 60 s without one ([page layout](ANUM_MINE_HQ_PAGE_LAYOUT.md)).
- Request failing: the last answer stays visible with "Offline. Showing the last answer" and a retry button; with no earlier answer the dock shows the error and a retry.
- 403: the dock says who can grant access. Sources the role cannot read say "No access".
- Empty: "Nothing is in progress" with a link to Tasks.

## Plugging into the scene extension points

This change does not edit the campus and building scene (PR #221 owns it). On `main` it mounts three self contained components in `HeadquartersLive`: `HqFlowOverlay` over the stage, `HqFlowDock` in the viewport and `HqTeamPanel` in the room inspector. They take plain props (`snapshot`, `rooms`, `onFocusRoom`, `onNavigate`) so the campus branch can host them without changes:

- `renderRoomPanel(room, { close })`: render `<HqTeamPanel roomId={room.id} .../>` and the room's items from `itemsInRoom(snapshot, room.id)`.
- `renderWorkstationPanel(agent, { close })`: show the agent's `OfficeTeamMember` (state, task, stuck reason, skills) from `GET /office/team?room_id=`.
- `hqCommands.navigateTo({ building, floor, room })` / `anum:hq-navigate`: pass as `onFocusRoom`; the dock calls it with a room id.
- `liveMeetingRoomIds` is untouched.
- For the building level cutaway, `snapshot.rooms[roomId]` carries `building_id`, `floor_id`, `items`, `waiting`, `stuck`, `worst` for every office, so a campus label can show a stuck count per building without another request.

## Gaps, said plainly

- Nothing records a task moving from one agent to another, so flow arrows between offices exist only when an item's own record changes office (for example a lead that gets an owner). Delegation between agents is not drawn.
- Orders have no office until an owner confirms a handler: no commerce record links an order to an agent. [Governed agent provisioning](ANUM_MINE_AGENT_PROVISIONING.md) builds the Orders Desk agent and records it as the orders handler; open orders then sit in its office (`location.via: "handler"`). Without a handler they stay listed with a ship overdue rule and shown as not in an office.
- Lead owners are free text (`assigned_to`); only a value equal to an agent id places a lead in an office.
- Agents have no separate role field; the team shows department and description.
- Other workspaces on the campus get no counts here (one workspace per request, by design).
- Leads and orders update by event only for task, run and approval events; they are picked up by the 20 s timer (or 8 s while polling).

## Assigning work from the flow
The dock and team cards open the shared [Assign work dialog](ANUM_MINE_ASSIGN_WORK.md) instead of leaving the office: a card with an owner agent offers "Assign work to {name}", the empty state offers "Assign work", and the next step of a stuck task ("Open task", "Retry") opens the task in a side panel. After an assignment the flow re-reads at once, so the task appears under the agent's office (or without an office when the agent has no desk, with the usual reason).
