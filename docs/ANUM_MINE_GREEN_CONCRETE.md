# ANUM MINE: Green Concrete, its command center, seats and working hours

Status: built (API, contract, web, migrations `0115_desk_seats_shifts` and `0119_desk_work_hours`, tests). Web and API only; no mobile work. Working hours per person: the owner's answer of 28 October 2026.

The owner asked for one company in the campus named **Green Concrete**, with a command center inside it, the people at L shaped tables in a fixed order, a manager at the back with his own desk, and any work given to these people shown correctly at their desks. The first version had a day and a night shift; on 28 October 2026 the owner gave everyone's real working hours, and there is no night shift any more. This document is the spec. The words in the owner's request are kept as typed for names and roles; only obvious spelling was fixed.

## The owner's working hours (28 October 2026)

These are facts from the owner, stored on each person's seat and shown as the owner's hours (no "not confirmed" note). Each stays editable in the builder.

| Hours (building time zone) | People |
|---|---|
| 06:00 to 14:00, the morning shift | Ijaz, Ali Zeehan ("Zeeshan" in the owner's answer), Rana |
| 08:00 to 17:00 | Raed, Hazrat, Mai, Shahzeb, Badea, Ahmed Elbendari (confirmed by the owner on 28 October 2026) |
| 13:00 to 22:00 | Amjad |
| 09:00 to 18:00 | Nobody. The owner first said "some", then answered on 28 October 2026 that nobody works these hours (owner decision O15, answered) |

If someone moves to 09:00 to 18:00 later, it is one data change: the person keys go into `NINE_TO_SIX` in `services/api/anum_api/office_green_concrete.py`, and the web fixture `apps/web/test/fixtures/greenConcrete.json` is regenerated from the planner (a test checks that both agree). For a workspace that is already built, the owner changes those people's hours on their seats in the builder.

## The reading of the request

These are readings of the layout; each is a data value or a constant that is easy to change.

| Point | Reading used |
|---|---|
| Company | A building named exactly **Green Concrete** (kind business), one floor ("Ground floor", 22 by 16 m), one room of kind `command` named **Command Center** (16 by 12 m, door in the south wall, 12 places) |
| Roles | Raed: Business analyst. Hazrat, Mai, Badea and Ali Zeehan: Data monitoring. Ijaz, Amjad and Rana: Data collector. Shahzeb: Tyre planning engineer. Ahmed Elbendari: PMV manager. "Ali zeehsns" was read as Ali Zeehan, a person of his own, not part of Badea's name |
| Seats | Every person has a seat of their own, because the morning people and Amjad overlap with the 08:00 to 17:00 people (nobody shares a place by time any more). The long leg runs from the left end: Raed, Hazrat, Mai, Shahzeb (seats 1 to 4, looking north at the screen wall). The short leg at its right end runs towards the door: Ijaz and Badea (seats 5 and 6, looking east) |
| The former night people | Ali Zeehan, Amjad and Rana take the next free places: a second short leg at the **left** end of the long leg, running towards the door, seats 7, 8 and 9 in that order (looking west). The L becomes a U; both corners are drawn as one table. Two of the 12 places stay free |
| Corner seat | **Settled 28 October 2026** (the owner left this reading to us): a leg seat whose table already covers the corner needs no corner piece; the corner piece is drawn only where a row and a leg meet without one. Kept as built |
| Manager | Ahmed has his own desk at the back of the room (the door side, behind the team), looking north over the whole table. "Back" was read as the side away from the screen wall. He works 08:00 to 17:00 like everyone not named |
| Seat numbers | The order the places are filled: the long leg left to right, the right leg, then the left leg, each leg from the corner towards the door. Labels are "Seat 1" to "Seat 9" and "Manager desk" |

## What is built

| Part | File |
|---|---|
| Planner (pure): people, their hours, seats, geometry, hours windows, plan, hash | `services/api/anum_api/office_green_concrete.py` |
| Routes: preview and confirm | `services/api/anum_api/office_green_concrete_api.py` |
| Migrations (expand only) | `0115_desk_seats_shifts.py` (seat fields, the older day and night hours), `0119_desk_work_hours.py` (each seat's working hours) |
| Contract | `packages/contracts/src/officeGreenConcrete.ts`; `OfficeBuildingUpdate` and `OfficeDeskUpdate` in `officeBuilder.ts` |
| Hours model (pure) | `apps/web/src/lib/hqShifts.ts` (windows over midnight, who is on shift now, older day and night seats, desk lines, the room's hour groups) |
| Desk look (pure) | `apps/web/src/lib/hqDeskState.ts` (what a desk shows, including **Off shift**) |
| Seat tables (pure geometry) | `apps/web/src/lib/hqSeatTables.ts` (facing to turn, the corner piece of an L) |
| Working hours of a room and desk lines | `apps/web/src/HqShiftBar.tsx`, `apps/web/src/HqPanels.tsx` |
| The owner's action | `apps/web/src/hq-green-concrete/` (`GreenConcreteDialog`, `PlanMap`, `client`, css), messages `hqGreenConcrete.en.ts` and `.ar.ts` |
| Builder fields | `hqBuilder/DeskPanel.tsx` (seat fields with Starts and Ends), `hqBuilder/FloorTab.tsx` (day and night hours, shown only while older seats exist) |
| Tests | `tests/test_office_green_concrete.py`, `tests/test_postgres_office_green_concrete.py`, `test/hqGreenConcrete.test.ts`, `e2e/hq-green-concrete.spec.ts` with `e2e/support/hqGreenConcreteApi.ts` |

## How the owner runs it

1. Open ANUM MINE, then **Office tools**, then **Add Green Concrete**.
2. Read the plan: the records to create, the plan of the Command Center with the seat numbers, the ten people with role, working hours and seat, the hours grouped by window, the cost cap. An earlier build is listed as kept (see below).
3. Click **Create Green Concrete**. Only a workspace owner (`workspace:manage`) can; anyone else reads the plan and sees who can.
4. The people are paused agents. Nothing runs until the owner turns them on (for example Office tools, Staff my campus, Paused agents, or the agent page).
5. In the builder (Office tools, Manage spaces, the room, Room tab, Desks, pick a desk) each seat has **Seat** fields: **Starts** and **Ends** (the person's working hours, "HH:MM" in the building time zone; an end before the start runs over midnight; both empty is a seat with no hours, always on), place at the table, looks at, seat type and table group. **Save** writes them (`PATCH /desks/{id}` with `work_start` and `work_end`).

### An existing Green Concrete build

A workspace built before the working hours keeps everything as it is: the action stays idempotent, never moves a person who is already seated and never writes hours over an existing desk. The preview lists such desks in `older_seats` and the dialog says so. Those seats keep reading the old day and night shifts (the building's day and night hours, defaults 07:00 and 19:00 until saved), and where a day and a night seat share a place only the person on shift now is drawn there. To bring one person to the new model the owner, in the builder: Office tools, Manage spaces, the Command Center, Room tab, Desks, picks the person's seat, sets **Starts** and **Ends** (saving own hours also clears the old day or night shift), and moves the seat with **X in room** and **Z in room** (or by dragging it on the plan) to its own place, for example the places of seats 7 to 9 in the table above for Ali Zeehan, Amjad and Rana, with **Looks at** set to West wall. Then **Save**. Each change is one audited desk update and a layout revision.

## Endpoints

Under `/api/v1/office/green-concrete`. PostgreSQL only (the memory backend answers 503, never fake data).

- `GET /preview` (`task:read`, optional `timezone`, optional `default_look` and repeated `look=<person key>:<masculine|feminine|neutral>`; each person of the answer has `appearance`, the look a new agent is created with, or null): the building, floor, room, ten seats (each with `work_start` and `work_end`) and ten people (with `seat_kind`, `work_start`, `work_end`) with what a confirm WILL create (`action: "create"`) or skip (`"skip"` with `reason`: `exists`, `name_in_use`, `level_in_use`, `parent_unavailable`), per person the agent (`create` or `exists`) and the seating (`seat`, or skip with `seated`, `desk_taken`, `agent_elsewhere`), counts, `hours` (the windows with the people in each, `night_shift: false`, the time zone), `older_seats` (desks of an earlier build without hours of their own, kept as they are), the monthly cap, and a `payload_hash`. The response is version 2. Nothing is stored. `can_confirm` says whether the caller holds `workspace:manage`.
- `POST /confirm`: `workspace:manage`, `payload_hash` from the preview, `timezone`, `input_mode: "click"`, and the same looks as the preview (`default_look`, `appearance` as a map of person key to style; an unknown key or style is 422, looks other than the ones shown change the hash and answer 409). A spoken or typed confirmation is refused with 403 and no registered tool calls the route. The hash binds the person, workspace, time zone and the exact plan, every seat's hours and the list of older seats included: a changed record, another owner or another plan answers 409.

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
| Audit | One `office.green_concrete.add` record with ids and counts only (the seats' hours are in the stored desks, not in the audit) (never names; `looks_chosen` is a count), `payload_hash`, `input_mode: "click"`, and the usual `agent.created` records; events `office.layout_changed` and one `office.agent_provisioned` per seated agent |

Cost: each agent has a USD 3 monthly cap (the smallest cap of the staffing templates), so the most the ten can cost is USD 30 a month, and only after the owner turns them on.

## Working hours

Migration `0119_desk_work_hours` adds only nullable columns: `office_desks.work_start` and `work_end` ("HH:MM", read in the building's own time zone). Check constraints: each is a valid time, both are set or both are null, and they differ. An end before the start is a window over midnight (22:00 to 06:00), so an owner who later adds a night shift needs no change. Forced row level security is untouched (no new table), nothing is backfilled, and the previous release, which never reads the columns, keeps working against the new schema. `PATCH /desks/{id}` takes both together (one alone, a bad time or two equal times answer 422), `null` on both clears them; desk reads return them; a room copy keeps them; the desk audit records them (no names).

The model is the smallest that keeps the rules: the hours live on the seat, because the seat is where the person is drawn and counted, and a building may hold people with any number of different hours. Rules, in order (`seat_window` in Python, `deskShiftInfo` in `lib/hqShifts.ts`, the same in both):

1. A desk with its own `work_start` and `work_end`: the person is on shift inside those hours.
2. An older desk with `shift` day or night (migration 0115) and no own hours: its shift in the building's `shift_day_start` and `shift_night_start` (defaults 07:00 and 19:00 while nothing is stored, shown with a "not yet confirmed" note).
3. Any other desk: always on (an ordinary desk, a manager seat without hours).

Green Concrete seats are written with rule 1 (`shift` null). The time zone is the building's; an unknown zone reads UTC and never throws. The page reads the clock every 15 seconds and on focus, and the scene is rebuilt only when somebody's on shift state changes, not on every tick (`activeShiftKey`).

What the room shows:

- Every person at their own seat, listed in seat order with their role and **Works 08:00 to 17:00** (their own hours).
- A **Working hours** box in the room panel: **N of M on shift now**, the time zone, the hours of the room's people grouped by window with how many people are in each and whether the window is on now, and where the hours are changed (the builder). There is no Day or Night toggle any more.
- A person inside their hours is drawn from the platform's records: working, queued, waiting for approval, blocked, in a meeting, idle, paused, unknown, each with its own look, as everywhere in the office.
- A person outside their hours is **Off shift**: no figure sits at the desk, the screen is dark with a small moon marker and a dim light, never lit, whatever work is waiting. Their row and card say **Off shift**, and when a record backs work for them, **Off shift, work waiting**, with the real record's title. It never says absent.
- Every count follows the same rule and the same clock: a seated person outside their hours counts as **Off shift**, never as idle, paused or missing. This holds for the room and building label cards, the campus chip and board, the building panel, the Operations wall (the panel and the room's 3D wall screen) and Total agents. The open workspace computes it in the browser from the stored seats (`offShiftKeys`, `totalsOf` in `lib/hqPresence.ts`); every other workspace gets it from `GET /api/v1/office/campus`, where `agents.by_state` counts the people on shift and `agents.off_shift` the others (`off_shift_desks` in `office_green_concrete.py`, which reads `work_start`, `work_end`, `shift` and the building's hours).

On 26 October 2026 in Dubai (the browser test pins the clock): at 07:00 Ijaz, Ali Zeehan and Rana are on; at 12:00 everyone but Amjad; at 16:00 the 08:00 to 17:00 people and Amjad; at 18:30 and at 21:00 only Amjad.

## Seating and drawing

Desks are drawn at the position saved on the desk when it has a seat field (`shift`, working hours, `seat_kind` or `table_group`) and a position; other desks keep the grid. A seat's `facing` turns its station (north: the person looks north, sitting on the south side; east: the person sits on the west side and looks east). Seats of one table group are drawn as one table: where a row of seats looking north or south meets a short leg of seats looking east or west whose tables touch the row's right end (a right L) or its left end (a left L), a corner piece joins them, whether the leg runs towards the door or towards the wall (`hqSeatTables.ts`). The orientation is read from the stored desk geometry (positions and `facing`), so the owner turns the L by moving the short leg to the other end and setting its seats to look west in the builder. A corner seat, a leg seat whose table already covers the corner, needs no piece (settled 28 October 2026, see the readings above). The builder's plan draws the same tables and corner pieces under the desk markers. The scene code is in the lazy HQ chunk; nothing was added to the renderer chunk.

## Tests

API unit `tests/test_office_green_concrete.py` (the people and roles as the owner typed them, the owner's hours and no night shift, naming the 09:00 to 18:00 people is one data change, everyone at their own seat in the long leg order then the legs, no overlap and inside the room, windows over midnight, older day and night seats and their building hours, the plan and its idempotence with `older_seats`, nothing overwritten, the hash binds person, workspace, plan and hours, off shift desks by each person's hours at 07:00, 12:00, 16:00, 18:30, 21:00 and 23:00, and the web fixture equals the planner). `tests/test_office_layout_history.py` (snapshot version 3 keeps the hours, a restore brings them back, over midnight too, a version 2 revision leaves them alone, migration 0119 is expand only). PostgreSQL as the app role `tests/test_postgres_office_green_concrete.py` (preview stores nothing and lists the hours, click and hash rules, full build with every person at their own place and hours, paused agents, no tools, no avatar, audit without names, second and stale click, two confirms at once, re-run adds only what is missing, an earlier day and night build is never moved or overwritten and the owner's desk PATCH moves one person, a taken desk is never taken over, a same name company blocks, hours and seat fields validated and editable with RLS and constraints, the desk audit records hours without names, restore of version 3, 2 and 1 revisions, a room copy keeps every seat and its hours, the campus counts people outside their hours as `off_shift`, older seats by the building hours, another workspace sees nothing). Web unit `test/hqGreenConcrete.test.ts` (windows over midnight, who is on at the five times, facts not defaults, the scene key, an earlier build never draws two people at one place, desk lines, the room's hour groups, the U table with two corners, figures only for people on shift, Arabic labels, presence truth, every count). Browser `e2e/hq-green-concrete.spec.ts` (pinned page clock: every person at their seat with their hours; who is on at 07:00, 12:00, 16:00, 18:30 and 21:00 with the Operations wall agreeing; the clock moving on; work given to Raed shown at his desk; work given to Amjad before his hours shown as Off shift, work waiting; edited hours over midnight; an earlier build; the action with every state including an earlier build; the builder's Starts and Ends with every state and an older night seat; Arabic; five viewports; axe). Screenshots: `HQ_SHOTS_DIR=<dir>` keeps them, and `e2e/tools/hqGreenConcrete.shots.ts` renders the 3D room for review (not part of CI).

## Not done, said plainly

- Who works 09:00 to 18:00: nobody (owner decision O15, answered 28 October 2026). The constant `NINE_TO_SIX` stays empty and Raed, Hazrat, Mai, Badea, Shahzeb and Ahmed work 08:00 to 17:00.
- An earlier Green Concrete build is not moved to the new seats or hours by the action (by design: it never overwrites). The owner moves each person in the builder as described above.
- Layout history captures the working hours from snapshot version 3, the seat fields and the building's day and night hours from version 2 (see [layout history](ANUM_MINE_LAYOUT_HISTORY.md)). An older revision never recorded what came later: restoring it leaves those fields as they are now, and a desk it re-creates comes back without them.
- The building's day and night hours are one pair per building and are read only by older Day or Night seats. Restoring a revision of one floor restores them for every floor of that building. Saving hours writes no revision by itself; the next layout change records them.
- Copying a room keeps its desks' seat fields and hours (never their agents). Floor and building templates still keep desk labels and positions only.
- There are no days off, weekends or holidays: the hours apply every day.
- The table corner covers one row with a short leg at either end (an L or, as here, a U). Other shapes (two rows, diagonal seats) are drawn as separate desks.
- Arabic role titles are provided for the five roles of this action (`hqGc.role.*`), as translations to confirm. The names of the people have no Arabic form and are shown as typed; the company, floor and room have no Arabic name (`name_ar` is empty, the English name is the fallback).
- The 3D view looks from the south, so the people are seen from behind; the screen wall overlay of the command center covers part of the wall in the inside view.
