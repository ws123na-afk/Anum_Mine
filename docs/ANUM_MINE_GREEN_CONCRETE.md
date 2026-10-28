# ANUM MINE: Green Concrete, its command center, seats and shifts

Status: built in this change (API, contract, web, one migration, tests). Web and API only; no mobile work.

The owner asked for one company in the campus named **Green Concrete**, with a command center inside it, the people at L shaped tables in a fixed order, a night shift, a manager at the back with his own desk, and any work given to these people shown correctly at their desks. This document is the spec of that change. The words in the owner's request are kept as typed for names and roles; only obvious spelling was fixed.

## The reading of the request (confirm with the owner)

These are my readings. The code follows them; each is a data value or a constant that is easy to change.

| Point | Reading used |
|---|---|
| Company | A building named exactly **Green Concrete** (kind business), one floor ("Ground floor", 22 by 16 m), one room of kind `command` named **Command Center** (16 by 12 m, door in the south wall, 12 places) |
| Roles | Raed: Business analyst. Hazrat, Mai, Badea and Ali Zeehan: Data monitoring. Ijaz, Amjad and Rana: Data collector. Shahzeb: Tyre planning engineer. Ahmed Elbendari: PMV manager. "Ali zeehsns" was read as Ali Zeehan, a person of his own, not part of Badea's name |
| Day shift, L table | The L runs from the left end: Raed, Hazrat, Mai, Shahzeb on the long leg (seats 1 to 4, looking at the screen wall on the north wall), then Ijaz and Badea on the short leg (seats 5 and 6, at the right end of the long leg, running towards the door, looking east) |
| Night shift | Ali Zeehan, Amjad and Rana sit at seats 1, 2 and 3 of the same tables (the same physical places as Raed, Hazrat and Mai). Seats 4 to 6 stay empty tables at night. This is a choice to confirm |
| Manager | Ahmed has his own desk at the back of the room (the door side, behind the team), looking north over the whole L, and is on in both shifts (a manager seat, not a shift seat). "Back" was read as the side away from the screen wall |
| Shift hours | Day 07:00 to 19:00 and night 19:00 to 07:00 in the building's time zone. These are **defaults the owner must confirm**: they are not stored, not presented as facts, and shown with a note until the owner saves hours in the builder |

## What is built

| Part | File |
|---|---|
| Planner (pure): people, seats, geometry, shift windows, plan, hash | `services/api/anum_api/office_green_concrete.py` |
| Routes: preview and confirm | `services/api/anum_api/office_green_concrete_api.py` |
| Migration (expand only) | `services/api/migrations/versions/0115_desk_seats_shifts.py` |
| Contract | `packages/contracts/src/officeGreenConcrete.ts`; `OfficeBuildingUpdate` and `OfficeDeskUpdate` in `officeBuilder.ts` |
| Shift model (pure) | `apps/web/src/lib/hqShifts.ts` (shift windows, the view of a building for a shift, desk lines) |
| Desk look (pure) | `apps/web/src/lib/hqDeskState.ts` (what a desk shows, including **Off shift**) |
| Seat tables (pure geometry) | `apps/web/src/lib/hqSeatTables.ts` (facing to turn, the corner piece of an L) |
| Shift toggle and desk lines | `apps/web/src/HqShiftBar.tsx`, `apps/web/src/HqPanels.tsx` |
| The owner's action | `apps/web/src/hq-green-concrete/` (`GreenConcreteDialog`, `PlanMap`, `client`, css), messages `hqGreenConcrete.en.ts` and `.ar.ts` |
| Builder fields | `hqBuilder/DeskPanel.tsx` (seat fields), `hqBuilder/FloorTab.tsx` (shift hours of the building) |
| Tests | `tests/test_office_green_concrete.py`, `tests/test_postgres_office_green_concrete.py`, `test/hqGreenConcrete.test.ts`, `e2e/hq-green-concrete.spec.ts` with `e2e/support/hqGreenConcreteApi.ts` |

## How the owner runs it

1. Open ANUM MINE, then **Office tools**, then **Add Green Concrete**.
2. Read the plan: the records to create, the plan of the Command Center with the seat numbers, the ten people with role, shift and seat, the defaults for the shift hours, the cost cap.
3. Click **Create Green Concrete**. Only a workspace owner (`workspace:manage`) can; anyone else reads the plan and sees who can.
4. The people are paused agents. Nothing runs until the owner turns them on (for example Office tools, Staff my campus, Paused agents, or the agent page).
5. In the builder (Office tools, Manage spaces, Floor tab, Building, **Shift hours**) the owner confirms or changes the shift hours. In the Desks list of a room, each desk has **Seat** fields (shift, place at the table, looks at, seat type, table group).

## Endpoints

Under `/api/v1/office/green-concrete`. PostgreSQL only (the memory backend answers 503, never fake data).

- `GET /preview` (`task:read`, optional `timezone`, optional `default_look` and repeated `look=<person key>:<masculine|feminine|neutral>`; each person of the answer has `appearance`, the look a new agent is created with, or null): the building, floor, room, ten seats and ten people with what a confirm WILL create (`action: "create"`) or skip (`"skip"` with `reason`: `exists`, `name_in_use`, `level_in_use`, `parent_unavailable`), per person the agent (`create` or `exists`) and the seating (`seat`, or skip with `seated`, `desk_taken`, `agent_elsewhere`), counts, the shift hours in force and whether they are defaults, the monthly cap, and a `payload_hash`. Nothing is stored. `can_confirm` says whether the caller holds `workspace:manage`.
- `POST /confirm`: `workspace:manage`, `payload_hash` from the preview, `timezone`, `input_mode: "click"`, and the same looks as the preview (`default_look`, `appearance` as a map of person key to style; an unknown key or style is 422, looks other than the ones shown change the hash and answer 409). A spoken or typed confirmation is refused with 403 and no registered tool calls the route. The hash binds the person, workspace, time zone and the exact plan: a changed record, another owner or another plan answers 409.

## How the rules are met

| Rule | How |
|---|---|
| Real data only, never hard coded into a scene | The names, roles and seats are data in the planner and become records only through the confirm. The scene draws a figure only at a desk with an assigned agent and a screen is lit only for a fresh working, queued, waiting or meeting presence of a person on shift |
| Idempotent | Stable ids (`gc-green-concrete`, `gc-green-concrete-l0`, `gc-green-concrete-command`, `gc-green-concrete-command-d1` to `d10`) and stable agent markers (`gc:raed` and so on). A retry, a double click or a partial failure adds only what is missing; an agent that already carries its marker is adopted, never built twice |
| Never overwrites | Nothing that exists is changed. A desk that has another agent is `desk_taken`, an agent seated at another desk is `agent_elsewhere`, a room the owner renamed is left as it is, a building that already carries the name Green Concrete under another id blocks the action (`name_in_use`) so the company is never duplicated |
| One writer | The confirm holds the per-workspace advisory lock of the starter campus for the whole run; the plan is re-read under the lock and compared with the hash |
| Same governed paths | Building, floor and room go through `SqlOfficeSpaceStore` (`create`, `create_planned_room`: floor bounds, overlap, capacity); each person goes through `agent_profiles.create_agent_governed` (the body of `POST /agents`), status paused, then a monthly cost cap through the agent controls store (as in Staff my campus) |
| Paused, no tools, no autonomy to write or act outside | Each agent has no tool and no skill, autonomy read auto, write off, external off, shared with the workspace. Nothing can be "working" because of this action |
| No guessing about people | The preview has a **Look** dropdown per person (Neutral by default, Masculine, Feminine). The dialog always sends `default_look: neutral`, so a new agent is created with an explicit neutral look until the owner chooses another; the choice is part of the hash and stored only for agents this action creates. Without any look sent (an old client) the avatar is left unset. No gender, pronoun or background is derived from a name. See [the people doc](ANUM_MINE_HQ_PEOPLE_CHARACTERS.md) |
| Tenant context | The caller's tenant, workspace and user set the RLS session. Tests run as the non-owner app role |
| Audit | One `office.green_concrete.add` record with ids and counts only (never names; `looks_chosen` is a count), `payload_hash`, `input_mode: "click"`, and the usual `agent.created` records; events `office.layout_changed` and one `office.agent_provisioned` per seated agent |

Cost: each agent has a USD 3 monthly cap (the smallest cap of the staffing templates), so the most the ten can cost is USD 30 a month, and only after the owner turns them on.

## Shifts

Migration `0115_desk_seats_shifts` adds only nullable columns:

- `office_desks.shift` (`day` or `night`; null means always on), `seat_order` (1 is the left end of the table), `facing` (the wall the seated person looks at), `seat_kind` (`team` or `manager`), `table_group` (desks with the same group are drawn as one table).
- `office_buildings.shift_day_start` and `shift_night_start` ("HH:MM", read in the building's own time zone). Both null means the defaults 07:00 and 19:00. They are stored on the building because the time zone lives there; the building is the "workspace" of the owner's request.

Forced row level security is untouched (no new table), nothing is backfilled, and the previous release keeps working against the new schema.

A desk may belong to a shift. Day and night people share physical places: they are separate desk records at the same position, which the room draws once (the people of the shown shift stand at the table; a table of the other shift that nobody uses in the shown shift stays as an empty table). The shift in force comes from the clock and the hours. `lib/hqShifts.ts` reads the building's time zone (an unknown zone reads UTC, never throws), wraps the night over midnight and rebuilds the scene only when the shift changes, not on every clock tick (the page reads the clock every 15 seconds and on focus).

What the room shows:

- By default the room shows the shift in force (and the manager). A **shift bar** in the room panel says which shift is shown, whether it is on now, its hours and the time zone, notes that default hours are not yet confirmed, and has a toggle to look at the other shift.
- A person of the shown shift who is on shift now is drawn from the platform's records: working, queued, waiting for approval, blocked, in a meeting, idle, paused, unknown, each with its own look, as everywhere in the office.
- A person of the other shift (shown through the toggle) is **Off shift**: a dark screen with a small moon marker and a dim light, never lit, whatever work is waiting. Their card and desk line say **Off shift**, and when a record backs work for them, **Off shift, work waiting**, with the real record's title. It never says absent.
- The manager seat is always on.
- The person card shows the shift and its hours, the real state, the title of the work and **Verified at** (the record's time).
- Room and building cards count only people on shift as working. The campus card counts agents from the presence records and is not shift aware (presence is per agent, not per shift).

## Seating and drawing

Desks are drawn at the position saved on the desk when it has a seat field (`shift`, `seat_kind` or `table_group`) and a position; other desks keep the grid. A seat's `facing` turns its station (north: the person looks north, sitting on the south side; east: the person sits on the west side and looks east). Seats of one table group are drawn as one table: where a long leg of seats looking north meets a short leg of seats looking east, a corner piece joins them into an L (`hqSeatTables.ts`; only this east-end turn is built; other shapes are drawn as separate desks). The scene code is in the lazy HQ chunk; nothing was added to the renderer chunk.

## Tests

API unit `tests/test_office_green_concrete.py` (the people and roles as the owner typed them, the seat order and the L geometry, no overlap and inside the room, shift windows over midnight, the plan and its idempotence, nothing overwritten, the hash binds person, workspace and plan, and the web fixture equals the planner). PostgreSQL as the app role `tests/test_postgres_office_green_concrete.py` (preview stores nothing, click and hash rules, full build with paused agents, no tools, no avatar, audit without names, second and stale click, two confirms at once, re-run adds only what is missing and never overwrites, a taken desk is never taken over, a same name company blocks, shift hours and seat fields validated and editable with RLS and constraints, another workspace sees nothing). Web unit `test/hqGreenConcrete.test.ts` (shift windows in a time zone, projection keeps the seat positions, day view, other shift, night, edited hours, desk lines, L table, scene figures, off shift look, Arabic labels, presence truth). Browser `e2e/hq-green-concrete.spec.ts` (pinned page clock: the day order along the L and the manager; the toggle; work given to Raed shown at his desk with the record and its time; work given to a night person during the day shown as Off shift, work waiting; the clock moving to night shows the night people and sends the day people off shift; saved hours; the action with every state; Arabic; five viewports; axe). Screenshots: `HQ_SHOTS_DIR=<dir>` keeps them, and `e2e/tools/hqGreenConcrete.shots.ts` renders the 3D room for review (not part of CI).

## Not done, said plainly

- Layout history does not capture the seat fields: restoring an old revision keeps a desk's current shift and a desk recreated by a restore comes back without its shift.
- Copying a room does not copy the seat fields of its desks.
- The L corner is built for one shape (a long leg and a short leg at its east end, going south). Any other shape draws separate desks.
- Arabic role titles are provided for the five roles of this action (`hqGc.role.*`), as translations to confirm. The names of the people have no Arabic form and are shown as typed; the company, floor and room have no Arabic name (`name_ar` is empty, the English name is the fallback).
- The campus card counts and the operations wall count agents from the presence records, which know nothing of shifts; only the room and building label cards count working people by shift.
- Seats of the shift that is not shown appear as empty tables, not as people: the room never draws two people at one place.
- The hours are one pair per building. There are no per person shifts, days off or holidays.
- The 3D view looks from the south, so the people are seen from behind; the screen wall overlay of the command center covers part of the wall in the inside view.
